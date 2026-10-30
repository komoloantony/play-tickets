"""JSON backend for Play Tickets; the frontend is a separate client of this API."""

import os
import math
import hmac
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Flask, jsonify, request
from database import close_db, expire_pending_reservations, get_db, init_db, remaining_tickets


# Keep a ticket on hold for this long while a buyer is paying.
RESERVATION_HOLD_MINUTES = 15
PAYMENT_MODE = os.environ.get("PLAY_TICKETS_PAYMENT_MODE", "disabled").lower()
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("PLAY_TICKETS_SECRET", "local-development-key")


app.teardown_appcontext(close_db)


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
    # Check the event name, ticket price, and ticket limit before saving anything.
    try:
        datetime.fromisoformat(starts_at)
        price = float(ticket_data["price"])
        capacity = int(ticket_data["capacity"])
        if not name or not venue or not ticket_name or not math.isfinite(price) or price < 0 or capacity < 1:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        return jsonify({"error": "Provide an event name, venue, valid ISO date/time, ticket name, non-negative price, and positive capacity."}), 400

    # Save the show and its first ticket type together in the database.
    db = get_db()
    cursor = db.execute(
        "INSERT INTO events (name, venue, starts_at, description, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, venue, starts_at, description, datetime.now(timezone.utc).isoformat()),
    )
    event_id = cursor.lastrowid
    db.execute(
        "INSERT INTO ticket_types (event_id, name, price, capacity) VALUES (?, ?, ?, ?)",
        (event_id, ticket_name, price, capacity),
    )
    db.commit()
    event = db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return jsonify({"event": event_json(event)}), 201


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
    if PAYMENT_MODE != "sandbox":
        return jsonify({"error": "Payments are not configured. Enable sandbox mode or configure a payment provider."}), 503
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
    db.execute("BEGIN IMMEDIATE")
    available = remaining_tickets(ticket_type)
    if quantity > available:
        db.rollback()
        # HTTP 409 means the request is okay, but the tickets are no longer available.
        return jsonify({"error": "Not enough tickets remain.", "available": available}), 409
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(minutes=RESERVATION_HOLD_MINUTES)
    payment_reference = f"PT-{uuid.uuid4().hex[:16].upper()}"
    cursor = db.execute(
        "INSERT INTO orders (ticket_type_id, buyer_name, buyer_email, quantity, status, created_at, expires_at, "
        "payment_provider, payment_phone, payment_reference, payment_amount, payment_currency, payment_status) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 'KES', 'pending')",
        (ticket_type_id, buyer_name, buyer_email, quantity, created_at.isoformat(), expires_at.isoformat(),
         payment_provider, payment_phone, payment_reference, payment_amount),
    )
    db.commit()
    return jsonify({
        "reservation": {
            "id": cursor.lastrowid,
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
        },
        "message": "Reservation created. It is not confirmed until payment is verified.",
    }), 201


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
    db.execute(
        "UPDATE orders SET payment_status = ?, status = ? WHERE id = ? AND status = 'pending'",
        ("succeeded" if succeeded else "failed", "confirmed" if succeeded else "pending", order["id"]),
    )
    db.commit()
    return jsonify({
        "payment_reference": payment_reference,
        "payment_status": "succeeded" if succeeded else "failed",
        "reservation_status": "confirmed" if succeeded else "pending",
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
    db.execute(
        "UPDATE orders SET payment_status = ?, status = ? WHERE id = ? AND status = 'pending'",
        ("succeeded" if succeeded else "failed", "confirmed" if succeeded else "pending", order["id"]),
    )
    db.commit()
    return jsonify({
        "payment_reference": payment_reference,
        "payment_status": "succeeded" if succeeded else "failed",
        "reservation_status": "confirmed" if succeeded else "pending",
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
        "SELECT COUNT(*) FROM orders o JOIN ticket_types t ON t.id = o.ticket_type_id "
        "WHERE t.event_id = ?",
        (event_id,),
    ).fetchone()[0]
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
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
