"""JSON backend for Play Tickets; the frontend is a separate client of this API."""

import os
import math
import hmac
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Flask, jsonify, request
from database import (
    close_db,
    expire_pending_reservations,
    get_db,
    inserted_id,
    init_db,
    lock_row,
    remaining_tickets,
)
from payments import PaymentError, check_payment, is_configured, start_payment
from ticket_service import generate_qr_svg, generate_ticket_svg


# Keep a ticket on hold for this long while a buyer is paying.
RESERVATION_HOLD_MINUTES = 15
PAYMENT_MODE = os.environ.get("PLAY_TICKETS_PAYMENT_MODE", "disabled").lower()
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("PLAY_TICKETS_SECRET", "local-development-key")
logger = logging.getLogger(__name__)


app.teardown_appcontext(close_db)


@app.after_request
def add_local_frontend_cors(response):
    """Allow the separately served local frontend to call the API during development."""
    origin = request.headers.get("Origin")
    allowed_origins = {
        value.strip()
        for value in os.environ.get(
            "PLAY_TICKETS_CORS_ORIGINS",
            "http://127.0.0.1:8000,http://localhost:8000,"
            "http://127.0.0.1:8001,http://localhost:8001",
        ).split(",")
        if value.strip()
    }
    if origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Vary"] = "Origin"
    return response


def admin_required(view):
    """Only let requests with the private admin token open buyer records."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected_token = os.environ.get("PLAY_TICKETS_ADMIN_TOKEN")
        if not expected_token:
            return jsonify({"error": "Admin access is not configured on this server."}), 503

        scheme, _, supplied_token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(supplied_token, expected_token):
            return jsonify({"error": "A valid admin token is required."}), 401, {
                "WWW-Authenticate": "Bearer"
            }
        return view(*args, **kwargs)

    return wrapped


def ticket_type_json(ticket_type):
    """Turn one database ticket row into simple JSON for the frontend."""
    return {
        "id": ticket_type["id"],
        "name": ticket_type["name"],
        "price": ticket_type["price"],
        "capacity": ticket_type["capacity"],
        "available": remaining_tickets(ticket_type),
    }


def event_json(event):
    """Turn an event row and its ticket types into JSON for the frontend."""
    db = get_db()
    ticket_types = db.execute(
        "SELECT * FROM ticket_types WHERE event_id = ? ORDER BY price", (event["id"],)
    ).fetchall()
    return {
        "id": event["id"],
        "name": event["name"],
        "venue": event["venue"],
        "starts_at": event["starts_at"],
        "description": event["description"],
        "ticket_types": [ticket_type_json(ticket) for ticket in ticket_types],
    }


@app.get("/")
def api_root():
    """Show the frontend where it can check whether the API is running."""
    return jsonify({"service": "Play Tickets API", "version": "v1", "health": "/api/v1/health"})


# JSON API for the independently developed frontend.
@app.get("/api/v1/events")
def api_list_events():
    """Give the frontend all events, ticket prices, and tickets left."""
    events = get_db().execute(
        "SELECT * FROM events ORDER BY starts_at, id"
    ).fetchall()
    return jsonify({"events": [event_json(event) for event in events]})


@app.get("/api/v1/health")
def api_health():
    """A small check used to see if the server is awake."""
    return jsonify({"status": "ok"})


@app.post("/api/v1/events")
@admin_required
def api_create_event():
    """Save a new event and its first ticket type from an admin's JSON."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    name = str(data.get("name", "")).strip()
    venue = str(data.get("venue", "")).strip()
    starts_at = str(data.get("starts_at", "")).strip()
    description = str(data.get("description", "")).strip()
    ticket_data = data.get("ticket_type")
    if not isinstance(ticket_data, dict):
        return jsonify({"error": "ticket_type must include name, price, and capacity."}), 400

    ticket_name = str(ticket_data.get("name", "")).strip()
    # Check the event name, whole-KES ticket price, and ticket limit before saving.
    try:
        datetime.fromisoformat(starts_at)
        price_decimal = Decimal(str(ticket_data["price"]))
        capacity = int(ticket_data["capacity"])
        if (
            not name
            or not venue
            or not ticket_name
            or not price_decimal.is_finite()
            or price_decimal < 1
            or price_decimal != price_decimal.to_integral_value()
            or capacity < 1
        ):
            raise ValueError
        price = float(price_decimal)
        if not math.isfinite(price):
            raise ValueError
    except (KeyError, InvalidOperation, TypeError, ValueError, OverflowError):
        return jsonify({
            "error": (
                "Provide an event name, venue, valid ISO date/time, ticket name, "
                "whole-number KES price of at least 1, and positive capacity."
            )
        }), 400

    # Save the show and its first ticket type together in the database.
    db = get_db()
    cursor = db.execute(
        "INSERT INTO events (name, venue, starts_at, description, created_at) "
        "VALUES (?, ?, ?, ?, ?) RETURNING id",
        (name, venue, starts_at, description, datetime.now(timezone.utc).isoformat()),
    )
    event_id = inserted_id(cursor)
    db.execute(
        "INSERT INTO ticket_types (event_id, name, price, capacity) VALUES (?, ?, ?, ?)",
        (event_id, ticket_name, price, capacity),
    )
    db.commit()
    event = db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return jsonify({"event": event_json(event)}), 201


@app.post("/api/v1/events/<int:event_id>/ticket-types")
@admin_required
def api_create_ticket_type(event_id):
    """Add another purchasable ticket category to an existing event."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    name = str(data.get("name", "")).strip()
    try:
        price_decimal = Decimal(str(data["price"]))
        capacity = int(data["capacity"])
        if (
            not name
            or not price_decimal.is_finite()
            or price_decimal < 1
            or price_decimal != price_decimal.to_integral_value()
            or capacity < 1
        ):
            raise ValueError
        price = float(price_decimal)
        if not math.isfinite(price):
            raise ValueError
    except (KeyError, InvalidOperation, TypeError, ValueError, OverflowError):
        return jsonify({
            "error": "Provide a ticket name, whole-number KES price of at least 1, and positive capacity."
        }), 400

    db = get_db()
    event = db.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        return jsonify({"error": "Event not found."}), 404
    cursor = db.execute(
        "INSERT INTO ticket_types (event_id, name, price, capacity) "
        "VALUES (?, ?, ?, ?) RETURNING id",
        (event_id, name, price, capacity),
    )
    ticket_type_id = inserted_id(cursor)
    db.commit()
    ticket_type_cursor = db.execute(
        "SELECT * FROM ticket_types WHERE id = ?", (ticket_type_id,)
    )
    ticket_type = ticket_type_cursor.fetchone()
    ticket_type_cursor.close()
    return jsonify({"ticket_type": ticket_type_json(ticket_type)}), 201


@app.get("/api/v1/events/<int:event_id>")
def api_get_event(event_id):
    """Give the frontend one event, or say it could not be found."""
    event = get_db().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        return jsonify({"error": "Event not found."}), 404
    return jsonify({"event": event_json(event)})


@app.post("/api/v1/ticket-types/<int:ticket_type_id>/reservations")
def api_reserve_tickets(ticket_type_id):
    """Hold tickets for 15 minutes while the buyer tries to pay."""
    if PAYMENT_MODE not in {"sandbox", "live"}:
        return jsonify({"error": "Payments are disabled on this server."}), 503
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    buyer_name = str(data.get("buyer_name", "")).strip()
    buyer_email = str(data.get("buyer_email", "")).strip()
    payment_provider = str(data.get("payment_provider", "")).strip().lower()
    payment_phone = re.sub(r"[\s()-]", "", str(data.get("payment_phone", "")))
    if payment_phone.startswith("+"):
        payment_phone = payment_phone[1:]
    if payment_phone.startswith("0"):
        payment_phone = "254" + payment_phone[1:]
    valid_phone = re.fullmatch(r"254[17]\d{8}", payment_phone)
    try:
        quantity = int(data.get("quantity", 1))
    except (TypeError, ValueError, OverflowError):
        quantity = 0
    if (not buyer_name or "@" not in buyer_email or quantity < 1 or quantity > 10
            or payment_provider not in {"mpesa", "airtel"} or not valid_phone):
        return jsonify({
            "error": "Provide a name, valid email, quantity from 1 to 10, "
                     "payment_provider (mpesa or airtel), and a Kenyan mobile number."
        }), 400
    if PAYMENT_MODE == "live":
        try:
            configured = is_configured(payment_provider)
        except PaymentError as error:
            return jsonify({"error": str(error)}), 503
        if not configured:
            return jsonify({
                "error": f"{payment_provider.title()} payment credentials and an HTTPS callback are required."
            }), 503

    db = get_db()
    ticket_type = db.execute(
        "SELECT t.*, e.id AS event_id, e.name AS event_name FROM ticket_types t "
        "JOIN events e ON e.id = t.event_id WHERE t.id = ?",
        (ticket_type_id,),
    ).fetchone()
    if ticket_type is None:
        return jsonify({"error": "Ticket type not found."}), 404

    try:
        payment_amount = Decimal(str(ticket_type["price"])) * quantity
        if payment_amount < 1 or payment_amount != payment_amount.to_integral_value():
            raise InvalidOperation
        payment_amount = int(payment_amount)
    except (InvalidOperation, TypeError, ValueError):
        return jsonify({"error": "Ticket prices must total a whole number of KES and be at least KES 1."}), 400

    # Clear old holds first. Then lock the database while checking tickets so two
    # buyers cannot both grab what looks like the very last ticket.
    expire_pending_reservations(db)
    db.commit()
    locked_ticket_type = lock_row(
        db, "SELECT id FROM ticket_types WHERE id = ?", (ticket_type_id,)
    ).fetchone()
    if locked_ticket_type is None:
        db.rollback()
        return jsonify({"error": "Ticket type not found."}), 404
    available = remaining_tickets(ticket_type)
    if quantity > available:
        db.rollback()
        # HTTP 409 means the request is okay, but the tickets are no longer available.
        return jsonify({"error": "Not enough tickets remain.", "available": available}), 409
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(minutes=RESERVATION_HOLD_MINUTES)
    payment_reference = f"PT-{uuid.uuid4().hex.upper()}"
    cursor = db.execute(
        "INSERT INTO orders (ticket_type_id, buyer_name, buyer_email, quantity, status, created_at, expires_at, "
        "payment_provider, payment_phone, payment_reference, provider_checkout_id, payment_amount, payment_currency, payment_status) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, 'KES', 'pending') RETURNING id",
        (ticket_type_id, buyer_name, buyer_email, quantity, created_at.isoformat(), expires_at.isoformat(),
         payment_provider, payment_phone, payment_reference,
         payment_reference if PAYMENT_MODE == "live" and payment_provider == "airtel" else None,
         payment_amount),
    )
    order_id = inserted_id(cursor)
    db.commit()
    reservation = {
        "id": order_id,
        "event_id": ticket_type["event_id"],
        "event_name": ticket_type["event_name"],
        "ticket_type": ticket_type["name"],
        "quantity": quantity,
        "status": "pending",
        "payment_required": True,
        "payment_provider": payment_provider,
        "payment_phone": payment_phone,
        "payment_reference": payment_reference,
        "payment_amount": payment_amount,
        "currency": "KES",
        "payment_status": "pending",
        "payment_mode": PAYMENT_MODE,
        "expires_at": expires_at.isoformat(),
    }

    if PAYMENT_MODE == "live":
        try:
            payment = start_payment(
                payment_provider,
                payment_reference=payment_reference,
                phone=payment_phone,
                amount=payment_amount,
            )
        except PaymentError as error:
            logger.warning(
                "Payment initiation failed for reservation %s via %s: %s",
                payment_reference,
                payment_provider,
                error,
            )
            if error.outcome_unknown:
                payment_status = (
                    "unknown" if payment_provider == "mpesa" else "processing"
                )
                db.execute(
                    "UPDATE orders SET payment_status = ? WHERE id = ?",
                    (payment_status, order_id),
                )
                db.commit()
                reservation["payment_status"] = payment_status
                return jsonify({
                    "reservation": reservation,
                    "message": (
                        "The request may have reached M-Pesa, but its status could not be confirmed. "
                        "Do not pay again until the organizer checks it."
                        if payment_status == "unknown"
                        else "The payment request may have reached your phone. Check its status before trying again."
                    ),
                }), 202
            db.execute(
                "UPDATE orders SET status = 'failed', payment_status = 'failed' WHERE id = ?",
                (order_id,),
            )
            db.commit()
            return jsonify({
                "error": str(error),
                "payment_reference": payment_reference,
                "payment_status": "failed",
                "reservation_status": "failed",
            }), 502

        db.execute(
            "UPDATE orders SET provider_checkout_id = ?, payment_status = ? WHERE id = ?",
            (payment.checkout_id, payment.status, order_id),
        )
        db.commit()
        reservation["payment_status"] = payment.status

    return jsonify({
        "reservation": reservation,
        "message": (
            "Payment request sent. Approve it on your phone to confirm the reservation."
            if PAYMENT_MODE == "live"
            else "Reservation created. It is not confirmed until payment is verified."
        ),
    }), 201


def _issue_tickets(db, order_id):
    order = db.execute(
        "SELECT quantity FROM orders WHERE id = ?", (order_id,)
    ).fetchone()
    if order is None:
        raise LookupError(f"Reservation {order_id} was not found while issuing tickets.")

    existing_numbers = {
        row["ticket_number"]
        for row in db.execute(
            "SELECT ticket_number FROM tickets WHERE order_id = ?", (order_id,)
        ).fetchall()
    }
    issued_at = datetime.now(timezone.utc).isoformat()
    for ticket_number in range(1, order["quantity"] + 1):
        if ticket_number in existing_numbers:
            continue
        ticket_code = f"PTK-{secrets.token_hex(16).upper()}"
        db.execute(
            "INSERT INTO tickets "
            "(order_id, ticket_number, ticket_code, status, issued_at) "
            "VALUES (?, ?, ?, 'issued', ?) ON CONFLICT DO NOTHING",
            (order_id, ticket_number, ticket_code, issued_at),
        )


def _tickets_for_order(db, order_id):
    rows = db.execute(
        "SELECT tickets.ticket_number, tickets.ticket_code, tickets.status, "
        "tickets.issued_at, tickets.checked_in_at, ticket_types.name AS ticket_type, "
        "events.name AS event_name, events.starts_at AS event_starts_at, "
        "events.venue AS event_venue "
        "FROM tickets "
        "JOIN orders ON orders.id = tickets.order_id "
        "JOIN ticket_types ON ticket_types.id = orders.ticket_type_id "
        "JOIN events ON events.id = ticket_types.event_id "
        "WHERE tickets.order_id = ? ORDER BY tickets.ticket_number",
        (order_id,),
    ).fetchall()
    return [{
        "ticket_number": row["ticket_number"],
        "ticket_code": row["ticket_code"],
        "qr_svg": generate_qr_svg(row["ticket_code"]),
        "ticket_svg": generate_ticket_svg(
            event_name=row["event_name"],
            ticket_type=row["ticket_type"],
            ticket_number=row["ticket_number"],
            ticket_code=row["ticket_code"],
            starts_at=row["event_starts_at"],
            venue=row["event_venue"],
        ),
        "status": row["status"],
        "issued_at": row["issued_at"],
        "checked_in_at": row["checked_in_at"],
        "ticket_type": row["ticket_type"],
        "event_name": row["event_name"],
    } for row in rows]


def _record_provider_result(db, order, succeeded, transaction_id=""):
    """Apply one provider result without resurrecting an expired reservation."""
    payment_status = "succeeded" if succeeded else "failed"
    reservation_status = order["status"]
    if succeeded:
        reservation_is_active = (
            reservation_status == "pending"
            and order["expires_at"] > datetime.now(timezone.utc).isoformat()
        )
        if reservation_is_active:
            reservation_status = "confirmed"
        elif reservation_status == "pending":
            reservation_status = "expired"
    elif reservation_status == "pending":
        reservation_status = "failed"

    db.execute(
        "UPDATE orders SET payment_status = ?, status = ?, "
        "provider_transaction_id = COALESCE(NULLIF(?, ''), provider_transaction_id) "
        "WHERE id = ?",
        (payment_status, reservation_status, transaction_id, order["id"]),
    )
    if succeeded and reservation_status == "confirmed":
        _issue_tickets(db, order["id"])
    db.commit()
    return payment_status, reservation_status


@app.get("/api/v1/payments/<payment_reference>")
def api_payment_status(payment_reference):
    """Return a buyer-safe payment state and refresh Airtel's provider status."""
    db = get_db()
    order = db.execute(
        "SELECT id, status, expires_at, payment_provider, payment_amount, payment_status, "
        "provider_checkout_id, provider_transaction_id FROM orders WHERE payment_reference = ?",
        (payment_reference,),
    ).fetchone()
    if order is None:
        return jsonify({"error": "Payment reference not found."}), 404

    if (
        PAYMENT_MODE == "live"
        and order["payment_provider"] == "airtel"
        and order["payment_status"] == "processing"
        and order["provider_checkout_id"]
    ):
        try:
            provider_status = check_payment(
                "airtel",
                payment_reference=payment_reference,
                checkout_id=order["provider_checkout_id"],
                amount=order["payment_amount"],
            )
        except PaymentError as error:
            logger.warning(
                "Could not refresh Airtel status for reservation %s: %s",
                payment_reference,
                error,
            )
            return jsonify({"error": str(error)}), 503
        if provider_status.status in {"succeeded", "failed"}:
            payment_status, reservation_status = _record_provider_result(
                db,
                order,
                provider_status.status == "succeeded",
                provider_status.transaction_id,
            )
            order = dict(order)
            order["status"] = reservation_status
            order["payment_status"] = payment_status

    if (
        order["status"] == "pending"
        and order["expires_at"] <= datetime.now(timezone.utc).isoformat()
    ):
        expire_pending_reservations(db)
        db.commit()
        order = dict(order)
        order["status"] = "expired"

    tickets = (
        _tickets_for_order(db, order["id"])
        if order["payment_status"] == "succeeded" and order["status"] == "confirmed"
        else []
    )
    return jsonify({
        "payment_reference": payment_reference,
        "payment_provider": order["payment_provider"],
        "payment_status": order["payment_status"],
        "reservation_status": order["status"],
        "expires_at": order["expires_at"],
        "refund_or_support_required": (
            order["payment_status"] == "succeeded"
            and order["status"] != "confirmed"
        ),
        "manual_review_required": order["payment_status"] == "unknown",
        "tickets": tickets,
    })


@app.post("/api/v1/admin/tickets/check-in")
@admin_required
def api_admin_ticket_check_in():
    """Validate a paid ticket and atomically mark it as checked in."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    ticket_code = str(data.get("ticket_code", "")).strip().upper()
    if not re.fullmatch(r"PTK-[A-F0-9]{32}", ticket_code):
        return jsonify({"error": "Provide a valid ticket QR code."}), 400

    db = get_db()
    locked_ticket = lock_row(
        db, "SELECT id FROM tickets WHERE ticket_code = ?", (ticket_code,)
    ).fetchone()
    if locked_ticket is None:
        db.rollback()
        return jsonify({"error": "Ticket code not found."}), 404
    ticket = db.execute(
        "SELECT tickets.id, tickets.ticket_number, tickets.status, tickets.checked_in_at, "
        "orders.status AS reservation_status, orders.payment_status, orders.buyer_name, "
        "ticket_types.name AS ticket_type, events.name AS event_name "
        "FROM tickets "
        "JOIN orders ON orders.id = tickets.order_id "
        "JOIN ticket_types ON ticket_types.id = orders.ticket_type_id "
        "JOIN events ON events.id = ticket_types.event_id "
        "WHERE tickets.ticket_code = ?",
        (ticket_code,),
    ).fetchone()
    if ticket is None:
        db.rollback()
        return jsonify({"error": "Ticket code not found."}), 404
    if ticket["reservation_status"] != "confirmed" or ticket["payment_status"] != "succeeded":
        db.rollback()
        return jsonify({"error": "Ticket is not backed by a confirmed payment."}), 409
    if ticket["status"] == "checked_in":
        db.rollback()
        return jsonify({
            "error": "Ticket has already been checked in.",
            "checked_in_at": ticket["checked_in_at"],
        }), 409

    checked_in_at = datetime.now(timezone.utc).isoformat()
    updated = db.execute(
        "UPDATE tickets SET status = 'checked_in', checked_in_at = ? "
        "WHERE id = ? AND status = 'issued'",
        (checked_in_at, ticket["id"]),
    )
    if updated.rowcount != 1:
        db.rollback()
        return jsonify({"error": "Ticket was checked in by another scanner."}), 409
    db.commit()
    return jsonify({
        "ticket": {
            "ticket_number": ticket["ticket_number"],
            "status": "checked_in",
            "checked_in_at": checked_in_at,
            "ticket_type": ticket["ticket_type"],
            "event_name": ticket["event_name"],
            "buyer_name": ticket["buyer_name"],
        },
    })


@app.post("/api/v1/payments/callbacks/<provider>/<callback_token>")
def api_provider_payment_callback(provider, callback_token):
    """Accept token-protected provider callbacks and verify their correlation."""
    expected_token = os.environ.get("PLAY_TICKETS_PAYMENT_CALLBACK_TOKEN", "")
    if PAYMENT_MODE != "live" or len(expected_token) < 32:
        return jsonify({"error": "Provider callbacks are not configured."}), 404
    if provider not in {"mpesa", "airtel"}:
        return jsonify({"error": "Provider must be mpesa or airtel."}), 404
    if not hmac.compare_digest(callback_token, expected_token):
        return jsonify({"error": "Invalid callback token."}), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Callback body must be a JSON object."}), 400

    db = get_db()
    if provider == "mpesa":
        body = data.get("Body")
        callback = body.get("stkCallback") if isinstance(body, dict) else None
        if not isinstance(callback, dict):
            return jsonify({"error": "M-Pesa callback is malformed."}), 400
        checkout_id = str(callback.get("CheckoutRequestID", "")).strip()
        try:
            result_code = int(callback["ResultCode"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "M-Pesa callback has no valid result code."}), 400
        if not checkout_id:
            return jsonify({"error": "M-Pesa callback has no checkout reference."}), 400
        order = db.execute(
            "SELECT id, status, expires_at, payment_amount, payment_phone, payment_status "
            "FROM orders WHERE payment_provider = 'mpesa' AND provider_checkout_id = ?",
            (checkout_id,),
        ).fetchone()
        if order is None:
            return jsonify({"error": "M-Pesa checkout reference not found."}), 404
        if order["payment_status"] == "succeeded":
            return jsonify({"accepted": True, "payment_status": "succeeded"})

        if result_code == 0:
            callback_metadata = callback.get("CallbackMetadata")
            metadata = (
                callback_metadata.get("Item")
                if isinstance(callback_metadata, dict)
                else None
            )
            if not isinstance(metadata, list):
                return jsonify({"error": "M-Pesa callback metadata is malformed."}), 400
            values = {
                item.get("Name"): item.get("Value")
                for item in metadata
                if isinstance(item, dict) and isinstance(item.get("Name"), str)
            }
            try:
                paid_amount = Decimal(str(values["Amount"]))
                paid_phone = re.sub(r"[\s()-]", "", str(values["PhoneNumber"]))
                if paid_phone.startswith("+"):
                    paid_phone = paid_phone[1:]
                if paid_phone.startswith("0"):
                    paid_phone = "254" + paid_phone[1:]
            except (KeyError, InvalidOperation, TypeError, ValueError):
                return jsonify({"error": "M-Pesa callback is missing payment details."}), 400
            receipt = str(values.get("MpesaReceiptNumber", "")).strip()
            if (
                paid_amount != Decimal(order["payment_amount"])
                or paid_phone != order["payment_phone"]
                or not receipt
            ):
                return jsonify({"error": "M-Pesa callback does not match the reservation."}), 400
            payment_status, reservation_status = _record_provider_result(
                db, order, True, receipt
            )
        else:
            payment_status, reservation_status = _record_provider_result(db, order, False)

    else:
        transaction = data.get("transaction")
        if not isinstance(transaction, dict):
            callback_data = data.get("data")
            transaction = (
                callback_data.get("transaction")
                if isinstance(callback_data, dict)
                else None
            )
        if not isinstance(transaction, dict):
            return jsonify({"error": "Airtel callback is malformed."}), 400
        checkout_id = str(
            transaction.get("id")
            or data.get("reference")
            or ""
        ).strip()
        if not checkout_id:
            return jsonify({"error": "Airtel callback has no transaction reference."}), 400
        order = db.execute(
            "SELECT id, status, expires_at, payment_amount, payment_reference, payment_status, "
            "provider_checkout_id FROM orders WHERE payment_provider = 'airtel' "
            "AND (provider_checkout_id = ? OR payment_reference = ?)",
            (checkout_id, checkout_id),
        ).fetchone()
        if order is None:
            return jsonify({"error": "Airtel transaction reference not found."}), 404
        if order["payment_status"] == "succeeded":
            return jsonify({"accepted": True, "payment_status": "succeeded"})
        try:
            provider_status = check_payment(
                "airtel",
                payment_reference=order["payment_reference"],
                checkout_id=order["provider_checkout_id"],
                amount=order["payment_amount"],
            )
        except PaymentError as error:
            logger.warning(
                "Could not verify Airtel callback for reservation %s: %s",
                order["payment_reference"],
                error,
            )
            return jsonify({"error": str(error)}), 503
        if provider_status.status == "processing":
            return jsonify({"accepted": True, "payment_status": "processing"}), 202
        payment_status, reservation_status = _record_provider_result(
            db,
            order,
            provider_status.status == "succeeded",
            provider_status.transaction_id,
        )

    return jsonify({
        "accepted": True,
        "payment_status": payment_status,
        "reservation_status": reservation_status,
    })


@app.post("/api/v1/sandbox/payments/<payment_reference>/result")
@admin_required
def api_sandbox_payment_result(payment_reference):
    """Simulate a provider result while sandbox mode is explicitly enabled."""
    if PAYMENT_MODE != "sandbox":
        return jsonify({"error": "Sandbox payment simulation is disabled."}), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("result") not in {"success", "failure"}:
        return jsonify({"error": "Set result to success or failure."}), 400

    db = get_db()
    order = db.execute(
        "SELECT id, status, expires_at FROM orders WHERE payment_reference = ?",
        (payment_reference,),
    ).fetchone()
    if order is None:
        return jsonify({"error": "Payment reference not found."}), 404
    if order["status"] != "pending" or order["expires_at"] <= datetime.now(timezone.utc).isoformat():
        expire_pending_reservations(db)
        db.commit()
        return jsonify({"error": "Reservation is no longer payable."}), 409

    succeeded = data["result"] == "success"
    if succeeded:
        payment_status, reservation_status = _record_provider_result(db, order, True)
    else:
        payment_status, reservation_status = "failed", "pending"
        db.execute(
            "UPDATE orders SET payment_status = ?, status = ? WHERE id = ? AND status = 'pending'",
            (payment_status, reservation_status, order["id"]),
        )
        db.commit()
    return jsonify({
        "payment_reference": payment_reference,
        "payment_status": payment_status,
        "reservation_status": reservation_status,
    })


@app.post("/api/v1/sandbox/callbacks/<provider>/<callback_token>")
def api_sandbox_callback(provider, callback_token):
    """Receive a mock provider callback through a temporary HTTPS tunnel."""
    expected_token = os.environ.get("PLAY_TICKETS_SANDBOX_CALLBACK_TOKEN", "")
    if PAYMENT_MODE != "sandbox" or not expected_token:
        return jsonify({"error": "Sandbox callbacks are not configured."}), 404
    if not hmac.compare_digest(callback_token, expected_token):
        return jsonify({"error": "Invalid callback token."}), 404
    if provider not in {"mpesa", "airtel"}:
        return jsonify({"error": "Provider must be mpesa or airtel."}), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("result") not in {"success", "failure"}:
        return jsonify({"error": "Callback JSON must set result to success or failure."}), 400
    payment_reference = str(data.get("payment_reference", "")).strip()
    db = get_db()
    order = db.execute(
        "SELECT id, status, expires_at, payment_provider FROM orders WHERE payment_reference = ?",
        (payment_reference,),
    ).fetchone()
    if order is None or order["payment_provider"] != provider:
        return jsonify({"error": "Matching sandbox payment not found."}), 404
    if order["status"] != "pending" or order["expires_at"] <= datetime.now(timezone.utc).isoformat():
        expire_pending_reservations(db)
        db.commit()
        return jsonify({"error": "Reservation is no longer payable."}), 409

    succeeded = data["result"] == "success"
    if succeeded:
        payment_status, reservation_status = _record_provider_result(db, order, True)
    else:
        payment_status, reservation_status = "failed", "pending"
        db.execute(
            "UPDATE orders SET payment_status = ?, status = ? WHERE id = ? AND status = 'pending'",
            (payment_status, reservation_status, order["id"]),
        )
        db.commit()
    return jsonify({
        "payment_reference": payment_reference,
        "payment_status": payment_status,
        "reservation_status": reservation_status,
    })


@app.get("/api/v1/admin/events")
@admin_required
def api_admin_event_sales():
    """Show admins ticket capacity, confirmed sales, active holds, and tickets left."""
    db = get_db()
    expire_pending_reservations(db)
    db.commit()
    events = db.execute("SELECT id, name, venue, starts_at FROM events ORDER BY starts_at, id").fetchall()

    result = []
    for event in events:
        rows = db.execute(
            "SELECT t.id, t.name, t.price, t.capacity, "
            "COALESCE(SUM(CASE WHEN o.status = 'confirmed' THEN o.quantity ELSE 0 END), 0) AS sold, "
            "COALESCE(SUM(CASE WHEN o.status = 'pending' THEN o.quantity ELSE 0 END), 0) AS pending "
            "FROM ticket_types t LEFT JOIN orders o ON o.ticket_type_id = t.id "
            "WHERE t.event_id = ? GROUP BY t.id ORDER BY t.price, t.id",
            (event["id"],),
        ).fetchall()
        ticket_types = []
        for row in rows:
            sold = row["sold"]
            pending = row["pending"]
            ticket_types.append({
                "id": row["id"],
                "name": row["name"],
                "price": row["price"],
                "capacity": row["capacity"],
                "sold": sold,
                "pending": pending,
                "available": row["capacity"] - sold - pending,
            })
        result.append({
            "id": event["id"],
            "name": event["name"],
            "venue": event["venue"],
            "starts_at": event["starts_at"],
            "ticket_types": ticket_types,
        })
    return jsonify({"events": result})


@app.get("/api/v1/admin/events/<int:event_id>/reservations")
@admin_required
def api_admin_reservations(event_id):
    """Show buyer and reservation details for one event, newest reservations first."""
    db = get_db()
    event = db.execute("SELECT id, name FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        return jsonify({"error": "Event not found."}), 404

    try:
        limit = int(request.args.get("limit", "50"))
        offset = int(request.args.get("offset", "0"))
        if limit < 1 or limit > 100 or offset < 0:
            raise ValueError
    except ValueError:
        return jsonify({"error": "Use a limit from 1 to 100 and an offset of 0 or more."}), 400

    expire_pending_reservations(db)
    db.commit()
    rows = db.execute(
        "SELECT o.id, o.buyer_name, o.buyer_email, o.quantity, o.status, o.created_at, o.expires_at, "
        "o.payment_provider, o.payment_phone, o.payment_reference, o.payment_amount, o.payment_currency, o.payment_status, "
        "t.id AS ticket_type_id, t.name AS ticket_type "
        "FROM orders o JOIN ticket_types t ON t.id = o.ticket_type_id "
        "WHERE t.event_id = ? ORDER BY o.created_at DESC, o.id DESC LIMIT ? OFFSET ?",
        (event_id, limit, offset),
    ).fetchall()
    total = db.execute(
        "SELECT COUNT(*) AS count FROM orders o JOIN ticket_types t ON t.id = o.ticket_type_id"
        " WHERE t.event_id = ?",
        (event_id,),
    ).fetchone()["count"]
    reservations = [{
        "id": row["id"],
        "buyer_name": row["buyer_name"],
        "buyer_email": row["buyer_email"],
        "ticket_type_id": row["ticket_type_id"],
        "ticket_type": row["ticket_type"],
        "quantity": row["quantity"],
        "status": row["status"],
        "payment_provider": row["payment_provider"],
        "payment_phone": row["payment_phone"],
        "payment_reference": row["payment_reference"],
        "payment_amount": row["payment_amount"],
        "payment_currency": row["payment_currency"],
        "payment_status": row["payment_status"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    } for row in rows]
    return jsonify({
        "event": {"id": event["id"], "name": event["name"]},
        "reservations": reservations,
        "pagination": {"limit": limit, "offset": offset, "total": total},
    })


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=PAYMENT_MODE != "live" and os.environ.get("FLASK_DEBUG") == "1")
