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
from io import BytesIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Flask, jsonify, request, send_file
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
from ticket_service import generate_qr_svg, generate_ticket_pdf, generate_ticket_svg
from ticket_email import TicketEmailError, send_ticket_email


# Keep a ticket on hold for this long while a buyer is paying.
RESERVATION_HOLD_MINUTES = 15
PAYMENT_MODE = os.environ.get("PLAY_TICKETS_PAYMENT_MODE", "manual").lower()
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("PLAY_TICKETS_SECRET", "local-development-key")
logger = logging.getLogger(__name__)


app.teardown_appcontext(close_db)


@app.after_request
def add_local_frontend_cors(response):
    """Allow trusted local and deployed frontends to call the API."""
    origin = request.headers.get("Origin")
    allowed_origins = {
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://localhost:63342",
        "https://firebrandtickets.com",
        "https://www.firebrandtickets.com",
        "https://play-tickets-1.onrender.com",
    }
    allowed_origins.update(
        value.strip()
        for value in os.environ.get(
            "PLAY_TICKETS_CORS_ORIGINS", ""
        ).split(",")
        if value.strip()
    )
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


def _lock_buyer_order(db, buyer_email):
    """Serialize order creation for one normalized buyer email."""
    email_key = buyer_email.strip().lower()
    db.execute(
        "INSERT INTO buyer_order_locks (buyer_email) VALUES (?) "
        "ON CONFLICT DO NOTHING",
        (email_key,),
    )
    db.commit()
    lock_row(
        db,
        "SELECT buyer_email FROM buyer_order_locks WHERE buyer_email = ?",
        (email_key,),
    )
    return email_key


def _lock_row_in_transaction(db, statement, parameters=()):
    """Lock another row while preserving an already-open SQLite transaction."""
    if db.is_postgres:
        statement += " FOR UPDATE"
    return db.execute(statement, parameters)


def _pending_order_for_buyer(db, email_key):
    return db.execute(
        "SELECT payment_status FROM orders "
        "WHERE lower(buyer_email) = ? AND status = 'pending' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (email_key,),
    ).fetchone()


def _buyer_order_conflict(db, email_key):
    """Return a conflict response while releasing the buyer lock."""
    active_order = _pending_order_for_buyer(db, email_key)
    if active_order is None:
        return None
    db.rollback()
    return jsonify({
        "error": (
            "You already have an order awaiting payment or verification. "
            "Return to your existing order to follow its status; you can buy "
            "again after it is approved, rejected, or expires."
        ),
        "active_order": {
            "payment_status": active_order["payment_status"],
            "reservation_status": "pending",
        },
    }), 409


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


@app.get("/api/v1/payment-options")
def api_payment_options():
    """Expose the selected checkout mode and public M-Pesa payment instructions."""
    options = {"mode": PAYMENT_MODE}
    if PAYMENT_MODE == "manual":
        till_number = os.environ.get(
            "PLAY_TICKETS_MPESA_TILL_NUMBER", "0711422001"
        ).strip()
        # A Till/Paybill is 5-7 digits; a Pochi la Biashara number is a 10-digit phone number.
        if not re.fullmatch(r"\d{5,10}", till_number):
            return jsonify({
                "error": "Set PLAY_TICKETS_MPESA_TILL_NUMBER to a valid M-Pesa number (5-10 digits)."
            }), 503
        options["mpesa"] = {
            "method": os.environ.get(
                "PLAY_TICKETS_MPESA_METHOD_LABEL", "Pochi la Biashara"
            ).strip() or "Pochi la Biashara",
            "till_number": till_number,
            "account_name": os.environ.get(
                "PLAY_TICKETS_MPESA_ACCOUNT_NAME", "Evans Kariuki"
            ).strip() or "Evans Kariuki",
        }
    return jsonify({"payment_options": options})


@app.post("/api/v1/events/<int:event_id>/orders")
def api_create_manual_order(event_id):
    """Create one manual-M-Pesa order that can contain multiple ticket types."""
    if PAYMENT_MODE != "manual":
        return jsonify({"error": "Manual M-Pesa checkout is not enabled."}), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    buyer_name = str(data.get("buyer_name", "")).strip()
    buyer_email = str(data.get("buyer_email", "")).strip().lower()
    raw_items = data.get("ticket_items")
    if not buyer_name or "@" not in buyer_email or not isinstance(raw_items, list):
        return jsonify({
            "error": "Provide a buyer name, valid email, and ticket_items list."
        }), 400
    if not 1 <= len(raw_items) <= 10:
        return jsonify({"error": "Choose between 1 and 10 ticket categories."}), 400

    requested = []
    seen_ticket_types = set()
    total_quantity = 0
    for item in raw_items:
        if not isinstance(item, dict):
            return jsonify({"error": "Each ticket item must be an object."}), 400
        try:
            ticket_type_id = int(item["ticket_type_id"])
            quantity = int(item["quantity"])
            if (
                isinstance(item["ticket_type_id"], bool)
                or isinstance(item["quantity"], bool)
                or ticket_type_id < 1
                or not 1 <= quantity <= 10
                or ticket_type_id in seen_ticket_types
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError):
            return jsonify({
                "error": "Each ticket type must be unique and have a quantity from 1 to 10."
            }), 400
        seen_ticket_types.add(ticket_type_id)
        total_quantity += quantity
        requested.append((ticket_type_id, quantity))
    if total_quantity > 10:
        return jsonify({"error": "An order can contain at most 10 tickets."}), 400

    db = get_db()
    placeholders = ", ".join("?" for _ in requested)
    ticket_types = db.execute(
        "SELECT t.*, e.id AS event_id, e.name AS event_name "
        "FROM ticket_types t JOIN events e ON e.id = t.event_id "
        f"WHERE e.id = ? AND t.id IN ({placeholders}) ORDER BY t.id",
        (event_id, *(ticket_type_id for ticket_type_id, _ in requested)),
    ).fetchall()
    if len(ticket_types) != len(requested):
        return jsonify({"error": "One or more ticket types were not found for this event."}), 404

    types_by_id = {ticket_type["id"]: ticket_type for ticket_type in ticket_types}
    priced_items = []
    total_amount = 0
    for ticket_type_id, quantity in requested:
        ticket_type = types_by_id[ticket_type_id]
        try:
            unit_price = Decimal(str(ticket_type["price"]))
            if unit_price < 1 or unit_price != unit_price.to_integral_value():
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            return jsonify({"error": "Ticket prices must be whole-number KES amounts."}), 400
        unit_price = int(unit_price)
        total_amount += unit_price * quantity
        priced_items.append((ticket_type, quantity, unit_price))

    expire_pending_reservations(db)
    db.commit()
    email_key = _lock_buyer_order(db, buyer_email)
    buyer_conflict = _buyer_order_conflict(db, email_key)
    if buyer_conflict:
        return buyer_conflict
    lock_placeholders = ", ".join("?" for _ in ticket_types)
    locked_types = _lock_row_in_transaction(
        db,
        f"SELECT id FROM ticket_types WHERE id IN ({lock_placeholders}) ORDER BY id",
        tuple(ticket_type["id"] for ticket_type in ticket_types),
    ).fetchall()
    if len(locked_types) != len(ticket_types):
        db.rollback()
        return jsonify({"error": "One or more ticket types are no longer available."}), 409
    for ticket_type, quantity, _unit_price in priced_items:
        available = remaining_tickets(ticket_type)
        if quantity > available:
            db.rollback()
            return jsonify({
                "error": f"Not enough {ticket_type['name']} tickets remain.",
                "available": available,
            }), 409

    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(minutes=RESERVATION_HOLD_MINUTES)
    payment_reference = f"PT-{uuid.uuid4().hex.upper()}"
    cursor = db.execute(
        "INSERT INTO orders (ticket_type_id, buyer_name, buyer_email, quantity, status, "
        "created_at, expires_at, payment_provider, payment_reference, payment_amount, "
        "payment_currency, payment_status) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?, 'manual_mpesa', ?, ?, 'KES', 'pending') "
        "RETURNING id",
        (
            priced_items[0][0]["id"],
            buyer_name,
            buyer_email,
            total_quantity,
            created_at.isoformat(),
            expires_at.isoformat(),
            payment_reference,
            total_amount,
        ),
    )
    order_id = inserted_id(cursor)
    db.executemany(
        "INSERT INTO order_items (order_id, ticket_type_id, quantity, unit_price) "
        "VALUES (?, ?, ?, ?)",
        [
            (order_id, ticket_type["id"], quantity, unit_price)
            for ticket_type, quantity, unit_price in priced_items
        ],
    )
    db.commit()
    return jsonify({
        "order": {
            "id": order_id,
            "payment_reference": payment_reference,
            "buyer_name": buyer_name,
            "quantity": total_quantity,
            "ticket_items": [
                {
                    "ticket_type": ticket_type["name"],
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "subtotal": unit_price * quantity,
                }
                for ticket_type, quantity, unit_price in priced_items
            ],
            "payment_amount": total_amount,
            "payment_currency": "KES",
            "payment_status": "pending",
            "status": "pending",
            "expires_at": expires_at.isoformat(),
        }
    }), 201


@app.post("/api/v1/payments/<payment_reference>/manual-receipt")
def api_submit_manual_receipt(payment_reference):
    """Record a buyer's M-Pesa receipt code without treating it as proof of payment."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    transaction_code = str(data.get("transaction_code", "")).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{6,20}", transaction_code):
        return jsonify({"error": "Enter a valid M-Pesa transaction code."}), 400

    db = get_db()
    expire_pending_reservations(db)
    db.commit()
    locked_order = lock_row(
        db,
        "SELECT id FROM orders WHERE payment_reference = ?",
        (payment_reference,),
    ).fetchone()
    if locked_order is None:
        db.rollback()
        return jsonify({"error": "Order reference not found."}), 404
    order = db.execute(
        "SELECT id, status, expires_at, payment_provider, payment_status "
        "FROM orders WHERE id = ?",
        (locked_order["id"],),
    ).fetchone()
    if order["payment_provider"] != "manual_mpesa":
        db.rollback()
        return jsonify({"error": "This order does not use manual M-Pesa payment."}), 409
    if order["status"] != "pending" or order["expires_at"] <= datetime.now(timezone.utc).isoformat():
        db.rollback()
        return jsonify({"error": "This order has expired or is no longer payable."}), 409
    if order["payment_status"] != "pending":
        db.rollback()
        return jsonify({"error": "A payment receipt has already been submitted for this order."}), 409

    submitted_at = datetime.now(timezone.utc).isoformat()
    inserted = db.execute(
        "INSERT INTO manual_payment_submissions "
        "(order_id, transaction_code, submitted_at) VALUES (?, ?, ?) "
        "ON CONFLICT DO NOTHING",
        (order["id"], transaction_code, submitted_at),
    )
    if inserted.rowcount != 1:
        existing = db.execute(
            "SELECT order_id FROM manual_payment_submissions WHERE transaction_code = ?",
            (transaction_code,),
        ).fetchone()
        db.rollback()
        if existing and existing["order_id"] == order["id"]:
            return jsonify({"error": "A payment receipt has already been submitted for this order."}), 409
        return jsonify({"error": "That M-Pesa transaction code was already submitted."}), 409
    db.execute(
        "UPDATE orders SET payment_status = 'submitted' WHERE id = ?",
        (order["id"],),
    )
    db.commit()
    return jsonify({
        "payment_reference": payment_reference,
        "payment_status": "submitted",
        "reservation_status": "pending",
    }), 202


@app.post("/api/v1/ticket-types/<int:ticket_type_id>/reservations")
def api_reserve_tickets(ticket_type_id):
    """Hold tickets for 15 minutes while the buyer tries to pay."""
    if PAYMENT_MODE not in {"sandbox", "live"}:
        return jsonify({"error": "Payments are disabled on this server."}), 503
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    buyer_name = str(data.get("buyer_name", "")).strip()
    buyer_email = str(data.get("buyer_email", "")).strip().lower()
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
    email_key = _lock_buyer_order(db, buyer_email)
    buyer_conflict = _buyer_order_conflict(db, email_key)
    if buyer_conflict:
        return buyer_conflict
    locked_ticket_type = _lock_row_in_transaction(
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
    db.execute(
        "INSERT INTO order_items (order_id, ticket_type_id, quantity, unit_price) "
        "VALUES (?, ?, ?, ?)",
        (order_id, ticket_type_id, quantity, int(Decimal(str(ticket_type["price"])))),
    )
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

    items = db.execute(
        "SELECT ticket_type_id, quantity FROM order_items "
        "WHERE order_id = ? ORDER BY ticket_type_id",
        (order_id,),
    ).fetchall()
    existing_numbers = {
        row["ticket_number"]
        for row in db.execute(
            "SELECT ticket_number FROM tickets WHERE order_id = ?", (order_id,)
        ).fetchall()
    }
    issued_at = datetime.now(timezone.utc).isoformat()
    ticket_number = 0
    for item in items:
        for _ in range(item["quantity"]):
            ticket_number += 1
            if ticket_number in existing_numbers:
                continue
            ticket_code = f"PTK-{secrets.token_hex(16).upper()}"
            db.execute(
                "INSERT INTO tickets "
                "(order_id, ticket_type_id, ticket_number, ticket_code, status, issued_at) "
                "VALUES (?, ?, ?, ?, 'issued', ?) ON CONFLICT DO NOTHING",
                (
                    order_id,
                    item["ticket_type_id"],
                    ticket_number,
                    ticket_code,
                    issued_at,
                ),
            )


def _tickets_for_order(db, order_id):
    rows = db.execute(
        "SELECT tickets.ticket_number, tickets.ticket_code, tickets.status, "
        "tickets.issued_at, tickets.checked_in_at, ticket_types.name AS ticket_type, "
        "orders.buyer_name AS buyer_name, events.name AS event_name, "
        "events.starts_at AS event_starts_at, "
        "events.venue AS event_venue "
        "FROM tickets "
        "JOIN orders ON orders.id = tickets.order_id "
        "JOIN ticket_types ON ticket_types.id = tickets.ticket_type_id "
        "JOIN events ON events.id = ticket_types.event_id "
        "WHERE tickets.order_id = ? ORDER BY tickets.ticket_number",
        (order_id,),
    ).fetchall()
    return [{
        "ticket_number": row["ticket_number"],
        "ticket_code": row["ticket_code"],
        "buyer_name": row["buyer_name"],
        "qr_svg": generate_qr_svg(row["ticket_code"]),
        "ticket_svg": generate_ticket_svg(
            event_name=row["event_name"],
            ticket_type=row["ticket_type"],
            buyer_name=row["buyer_name"],
            ticket_number=row["ticket_number"],
            ticket_code=row["ticket_code"],
            starts_at=row["event_starts_at"],
            venue=row["event_venue"],
        ),
        "event_starts_at": row["event_starts_at"],
        "event_venue": row["event_venue"],
        "status": row["status"],
        "issued_at": row["issued_at"],
        "checked_in_at": row["checked_in_at"],
        "ticket_type": row["ticket_type"],
        "event_name": row["event_name"],
    } for row in rows]


def _record_provider_result(
    db, order, succeeded, transaction_id="", queue_ticket_email=False
):
    """Apply one provider result without resurrecting an expired reservation."""
    payment_status = "succeeded" if succeeded else "failed"
    reservation_status = order["status"]
    if succeeded:
        order_keys = order.keys()
        submitted_manual_payment = (
            "payment_provider" in order_keys
            and "payment_status" in order_keys
            and order["payment_provider"] == "manual_mpesa"
            and order["payment_status"] == "submitted"
        )
        reservation_is_active = (
            reservation_status == "pending"
            and (
                submitted_manual_payment
                or order["expires_at"] > datetime.now(timezone.utc).isoformat()
            )
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
        if queue_ticket_email:
            db.execute(
                "INSERT INTO ticket_email_outbox (order_id, status, attempts) "
                "VALUES (?, 'pending', 0) ON CONFLICT DO NOTHING",
                (order["id"],),
            )
    db.commit()
    return payment_status, reservation_status


def _ticket_tracking_url(payment_reference):
    frontend_url = os.environ.get("PLAY_TICKETS_FRONTEND_URL", "").strip()
    if not frontend_url:
        return ""
    parsed = urlsplit(frontend_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["order"] = payment_reference
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _attempt_ticket_email_delivery(db, order_id):
    """Send a queued order email once, leaving failures available for admin retry."""
    email_row = lock_row(
        db,
        "SELECT status, attempts, last_attempt_at FROM ticket_email_outbox "
        "WHERE order_id = ?",
        (order_id,),
    ).fetchone()
    if email_row is None:
        db.rollback()
        return {"status": "not_queued"}
    if email_row["status"] == "sent":
        db.rollback()
        return {"status": "sent"}
    if email_row["status"] == "sending" and email_row["last_attempt_at"]:
        try:
            last_attempt = datetime.fromisoformat(email_row["last_attempt_at"])
        except ValueError:
            last_attempt = datetime.min.replace(tzinfo=timezone.utc)
        if last_attempt > datetime.now(timezone.utc) - timedelta(minutes=10):
            db.rollback()
            return {"status": "sending"}

    attempted_at = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE ticket_email_outbox SET status = 'sending', attempts = attempts + 1, "
        "last_attempt_at = ?, last_error = NULL WHERE order_id = ?",
        (attempted_at, order_id),
    )
    db.commit()

    order = db.execute(
        "SELECT buyer_name, buyer_email, payment_reference, payment_amount, "
        "payment_currency FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    tickets = _tickets_for_order(db, order_id)
    if order is None or not tickets:
        error_message = "Approved order or issued tickets could not be found."
        db.execute(
            "UPDATE ticket_email_outbox SET status = 'failed', last_error = ? "
            "WHERE order_id = ?",
            (error_message, order_id),
        )
        db.commit()
        logger.error("Could not send tickets for order %s: %s", order_id, error_message)
        return {"status": "failed", "error": error_message}

    try:
        tickets_with_pdfs = [
            {
                **ticket,
                "ticket_pdf": generate_ticket_pdf(
                    event_name=ticket["event_name"],
                    ticket_type=ticket["ticket_type"],
                    buyer_name=ticket["buyer_name"],
                    ticket_number=ticket["ticket_number"],
                    ticket_code=ticket["ticket_code"],
                    starts_at=ticket["event_starts_at"],
                    venue=ticket["event_venue"],
                ),
            }
            for ticket in tickets
        ]
        send_ticket_email(
            recipient=order["buyer_email"],
            buyer_name=order["buyer_name"],
            event_name=tickets[0]["event_name"],
            payment_reference=order["payment_reference"],
            payment_amount=order["payment_amount"],
            payment_currency=order["payment_currency"] or "KES",
            tickets=tickets_with_pdfs,
            tracking_url=_ticket_tracking_url(order["payment_reference"]),
        )
    except (TicketEmailError, TypeError, ValueError) as error:
        error_message = (
            str(error)
            if isinstance(error, TicketEmailError)
            else "The ticket email contains invalid order or recipient details."
        )
        db.execute(
            "UPDATE ticket_email_outbox SET status = 'failed', last_error = ? "
            "WHERE order_id = ?",
            (error_message, order_id),
        )
        db.commit()
        logger.warning(
            "Ticket email for order %s was not delivered: %s",
            order["payment_reference"],
            error_message,
        )
        return {"status": "failed", "error": error_message}

    db.execute(
        "UPDATE ticket_email_outbox SET status = 'sent', sent_at = ?, last_error = NULL "
        "WHERE order_id = ?",
        (datetime.now(timezone.utc).isoformat(), order_id),
    )
    db.commit()
    return {"status": "sent"}


@app.get("/api/v1/payments/<payment_reference>")
def api_payment_status(payment_reference):
    """Return a buyer-safe payment state and refresh Airtel's provider status."""
    db = get_db()
    order = db.execute(
        "SELECT id, status, expires_at, payment_provider, payment_amount, payment_currency, "
        "payment_status, provider_checkout_id, provider_transaction_id "
        "FROM orders WHERE payment_reference = ?",
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
        and not (
            order["payment_provider"] == "manual_mpesa"
            and order["payment_status"] == "submitted"
        )
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
    items = db.execute(
        "SELECT ticket_types.name AS ticket_type, order_items.quantity, "
        "order_items.unit_price, order_items.quantity * order_items.unit_price AS subtotal "
        "FROM order_items JOIN ticket_types ON ticket_types.id = order_items.ticket_type_id "
        "WHERE order_items.order_id = ? ORDER BY ticket_types.id",
        (order["id"],),
    ).fetchall()
    email = db.execute(
        "SELECT status FROM ticket_email_outbox WHERE order_id = ?",
        (order["id"],),
    ).fetchone()
    return jsonify({
        "payment_reference": payment_reference,
        "payment_provider": order["payment_provider"],
        "payment_status": order["payment_status"],
        "reservation_status": order["status"],
        "expires_at": order["expires_at"],
        "payment_amount": order["payment_amount"],
        "payment_currency": order["payment_currency"] or "KES",
        "items": [dict(item) for item in items],
        "ticket_email_status": email["status"] if email else "not_sent",
        "refund_or_support_required": (
            order["payment_status"] == "succeeded"
            and order["status"] != "confirmed"
        ),
        "manual_review_required": order["payment_status"] == "unknown",
        "tickets": tickets,
    })


@app.get("/api/v1/payments/<payment_reference>/tickets/<int:ticket_number>.pdf")
def api_download_ticket_pdf(payment_reference, ticket_number):
    """Download a single issued ticket as a PDF after its order is approved."""
    db = get_db()
    order = db.execute(
        "SELECT id, status, payment_status FROM orders WHERE payment_reference = ?",
        (payment_reference,),
    ).fetchone()
    if order is None:
        return jsonify({"error": "Order reference not found."}), 404
    if order["status"] != "confirmed" or order["payment_status"] != "succeeded":
        return jsonify({"error": "Tickets are available after payment approval."}), 409
    ticket = next(
        (
            item for item in _tickets_for_order(db, order["id"])
            if item["ticket_number"] == ticket_number
        ),
        None,
    )
    if ticket is None:
        return jsonify({"error": "Ticket not found for this order."}), 404
    pdf_bytes = generate_ticket_pdf(
        event_name=ticket["event_name"],
        ticket_type=ticket["ticket_type"],
        buyer_name=ticket["buyer_name"],
        ticket_number=ticket["ticket_number"],
        ticket_code=ticket["ticket_code"],
        starts_at=ticket["event_starts_at"],
        venue=ticket["event_venue"],
    )
    response = send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"ticket-{ticket_number}-{payment_reference[-8:]}.pdf",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


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
        "JOIN ticket_types ON ticket_types.id = tickets.ticket_type_id "
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


@app.post("/api/v1/admin/payments/<payment_reference>/review")
@admin_required
def api_admin_review_manual_payment(payment_reference):
    """Approve or reject a submitted manual M-Pesa receipt."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("decision") not in {"approve", "reject"}:
        return jsonify({"error": "Set decision to approve or reject."}), 400

    db = get_db()
    expire_pending_reservations(db)
    db.commit()
    locked_order = lock_row(
        db,
        "SELECT id FROM orders WHERE payment_reference = ?",
        (payment_reference,),
    ).fetchone()
    if locked_order is None:
        db.rollback()
        return jsonify({"error": "Order reference not found."}), 404
    order = db.execute(
        "SELECT id, status, expires_at, payment_provider, payment_status "
        "FROM orders WHERE id = ?",
        (locked_order["id"],),
    ).fetchone()
    if order["payment_provider"] != "manual_mpesa":
        db.rollback()
        return jsonify({"error": "This order does not use manual M-Pesa payment."}), 409
    if order["status"] != "pending" or order["payment_status"] != "submitted":
        db.rollback()
        return jsonify({"error": "This order has no reviewable payment submission."}), 409
    submission = db.execute(
        "SELECT transaction_code FROM manual_payment_submissions WHERE order_id = ?",
        (order["id"],),
    ).fetchone()
    if submission is None:
        db.rollback()
        return jsonify({"error": "The M-Pesa transaction code was not found."}), 409

    if data["decision"] == "approve":
        payment_status, reservation_status = _record_provider_result(
            db, order, True, submission["transaction_code"], queue_ticket_email=True
        )
        email_result = _attempt_ticket_email_delivery(db, order["id"])
    else:
        db.execute(
            "UPDATE orders SET status = 'rejected', payment_status = 'rejected', "
            "provider_transaction_id = ? WHERE id = ?",
            (submission["transaction_code"], order["id"]),
        )
        db.commit()
        payment_status, reservation_status = "rejected", "rejected"
        email_result = {"status": "not_sent"}

    return jsonify({
        "payment_reference": payment_reference,
        "payment_status": payment_status,
        "reservation_status": reservation_status,
        "ticket_email_status": email_result["status"],
    })


@app.post("/api/v1/admin/payments/<payment_reference>/ticket-email")
@admin_required
def api_admin_retry_ticket_email(payment_reference):
    """Retry delivery of printable tickets for an already-approved order."""
    db = get_db()
    order = db.execute(
        "SELECT id FROM orders WHERE payment_reference = ? "
        "AND status = 'confirmed' AND payment_status = 'succeeded'",
        (payment_reference,),
    ).fetchone()
    if order is None:
        return jsonify({"error": "No approved order was found for this reference."}), 404
    db.execute(
        "INSERT INTO ticket_email_outbox (order_id, status, attempts) "
        "VALUES (?, 'pending', 0) ON CONFLICT DO NOTHING",
        (order["id"],),
    )
    db.commit()
    result = _attempt_ticket_email_delivery(db, order["id"])
    if result["status"] != "sent":
        return jsonify({
            "error": result.get("error", "Ticket email delivery is already in progress."),
            "ticket_email_status": result["status"],
        }), 502
    return jsonify({"ticket_email_status": "sent"})


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
            "COALESCE(SUM(CASE WHEN o.status = 'confirmed' THEN i.quantity ELSE 0 END), 0) AS sold, "
            "COALESCE(SUM(CASE WHEN o.status = 'pending' THEN i.quantity ELSE 0 END), 0) AS pending "
            "FROM ticket_types t LEFT JOIN order_items i ON i.ticket_type_id = t.id "
            "LEFT JOIN orders o ON o.id = i.order_id "
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
        "t.id AS ticket_type_id, t.name AS ticket_type, "
        "m.transaction_code AS manual_transaction_code, "
        "e.status AS ticket_email_status, e.last_error AS ticket_email_error "
        "FROM orders o JOIN ticket_types t ON t.id = o.ticket_type_id "
        "LEFT JOIN manual_payment_submissions m ON m.order_id = o.id "
        "LEFT JOIN ticket_email_outbox e ON e.order_id = o.id "
        "WHERE t.event_id = ? ORDER BY o.created_at DESC, o.id DESC LIMIT ? OFFSET ?",
        (event_id, limit, offset),
    ).fetchall()
    items_by_order = {}
    if rows:
        order_ids = [row["id"] for row in rows]
        placeholders = ", ".join("?" for _ in order_ids)
        item_rows = db.execute(
            "SELECT i.order_id, i.ticket_type_id, i.quantity, t.name AS ticket_type "
            "FROM order_items i JOIN ticket_types t ON t.id = i.ticket_type_id "
            f"WHERE i.order_id IN ({placeholders}) ORDER BY i.order_id, t.id",
            tuple(order_ids),
        ).fetchall()
        for item in item_rows:
            items_by_order.setdefault(item["order_id"], []).append(item)
    total = db.execute(
        "SELECT COUNT(*) AS count FROM orders o JOIN ticket_types t ON t.id = o.ticket_type_id"
        " WHERE t.event_id = ?",
        (event_id,),
    ).fetchone()["count"]
    reservations = []
    for row in rows:
        items = items_by_order.get(row["id"], [])
        reservations.append({
            "id": row["id"],
            "buyer_name": row["buyer_name"],
            "buyer_email": row["buyer_email"],
            "ticket_type_id": row["ticket_type_id"] if len(items) <= 1 else None,
            "ticket_type": ", ".join(
                f"{item['quantity']} × {item['ticket_type']}" for item in items
            ) or row["ticket_type"],
            "quantity": row["quantity"],
            "status": row["status"],
            "payment_provider": row["payment_provider"],
            "payment_phone": row["payment_phone"],
            "payment_reference": row["payment_reference"],
            "payment_amount": row["payment_amount"],
            "payment_currency": row["payment_currency"],
            "payment_status": row["payment_status"],
            "ticket_email_status": row["ticket_email_status"] or "not_sent",
            "ticket_email_error": row["ticket_email_error"],
            "manual_transaction_code": row["manual_transaction_code"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        })
    return jsonify({
        "event": {"id": event["id"], "name": event["name"]},
        "reservations": reservations,
        "pagination": {"limit": limit, "offset": offset, "total": total},
    })


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=PAYMENT_MODE != "live" and os.environ.get("FLASK_DEBUG") == "1")
