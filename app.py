"""JSON backend for Play Tickets; the frontend is a separate client of this API."""

import os
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, g, jsonify, request


BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("PLAY_TICKETS_DATABASE", BASE_DIR / "play_tickets.db"))
RESERVATION_HOLD_MINUTES = 15
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("PLAY_TICKETS_SECRET", "local-development-key")


def get_db():
    """Reuse one SQLite connection per Flask request, then close it at teardown."""
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create the local schema and add reservation expiry to older databases."""
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            venue TEXT NOT NULL,
            starts_at TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ticket_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            price REAL NOT NULL CHECK(price >= 0),
            capacity INTEGER NOT NULL CHECK(capacity > 0)
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_type_id INTEGER NOT NULL REFERENCES ticket_types(id),
            buyer_name TEXT NOT NULL,
            buyer_email TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            expires_at TEXT
        );
        """
    )
    order_columns = {row["name"] for row in db.execute("PRAGMA table_info(orders)")}
    if "expires_at" not in order_columns:
        db.execute("ALTER TABLE orders ADD COLUMN expires_at TEXT")
    # Older prototype reservations should not hold inventory forever.
    db.execute(
        "UPDATE orders SET expires_at = created_at WHERE status = 'pending' AND expires_at IS NULL"
    )
    db.commit()


def remaining_tickets(ticket_type):
    """Count confirmed sales and active holds; expired holds no longer reserve stock."""
    sold = get_db().execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM orders "
        "WHERE ticket_type_id = ? AND (status = 'confirmed' "
        "OR (status = 'pending' AND expires_at > ?))",
        (ticket_type["id"], datetime.now(timezone.utc).isoformat()),
    ).fetchone()[0]
    return ticket_type["capacity"] - sold


def expire_pending_reservations(db):
    db.execute(
        "UPDATE orders SET status = 'expired' "
        "WHERE status = 'pending' AND expires_at <= ?",
        (datetime.now(timezone.utc).isoformat(),),
    )


def ticket_type_json(ticket_type):
    return {
        "id": ticket_type["id"],
        "name": ticket_type["name"],
        "price": ticket_type["price"],
        "capacity": ticket_type["capacity"],
        "available": remaining_tickets(ticket_type),
    }


def event_json(event):
    """Convert a database row into the event shape returned to frontend clients."""
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
    """Tell frontend developers where to find the versioned API health check."""
    return jsonify({"service": "Play Tickets API", "version": "v1", "health": "/api/v1/health"})


# JSON API for the independently developed frontend.
@app.get("/api/v1/events")
def api_list_events():
    """Return every event with its ticket types and current available quantities."""
    events = get_db().execute(
        "SELECT * FROM events ORDER BY starts_at, id"
    ).fetchall()
    return jsonify({"events": [event_json(event) for event in events]})


@app.get("/api/v1/health")
def api_health():
    return jsonify({"status": "ok"})


@app.post("/api/v1/events")
def api_create_event():
    """Create an event and its initial ticket type from the frontend's JSON payload."""
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
    try:
        datetime.fromisoformat(starts_at)
        price = float(ticket_data["price"])
        capacity = int(ticket_data["capacity"])
        if not name or not venue or not ticket_name or not math.isfinite(price) or price < 0 or capacity < 1:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        return jsonify({"error": "Provide an event name, venue, valid ISO date/time, ticket name, non-negative price, and positive capacity."}), 400

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
    """Return one event, or a JSON 404 response when the ID does not exist."""
    event = get_db().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        return jsonify({"error": "Event not found."}), 404
    return jsonify({"event": event_json(event)})


@app.post("/api/v1/ticket-types/<int:ticket_type_id>/reservations")
def api_reserve_tickets(ticket_type_id):
    """Create a 15-minute pending hold; payment confirmation is a separate future step."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    buyer_name = str(data.get("buyer_name", "")).strip()
    buyer_email = str(data.get("buyer_email", "")).strip()
    try:
        quantity = int(data.get("quantity", 1))
    except (TypeError, ValueError, OverflowError):
        quantity = 0
    if not buyer_name or "@" not in buyer_email or quantity < 1 or quantity > 10:
        return jsonify({"error": "Provide a name, valid email, and quantity from 1 to 10."}), 400

    db = get_db()
    ticket_type = db.execute(
        "SELECT t.*, e.id AS event_id, e.name AS event_name FROM ticket_types t "
        "JOIN events e ON e.id = t.event_id WHERE t.id = ?",
        (ticket_type_id,),
    ).fetchone()
    if ticket_type is None:
        return jsonify({"error": "Ticket type not found."}), 404

    # Mark stale holds before checking inventory, then lock writes so concurrent requests
    # cannot both see the same last ticket as available.
    expire_pending_reservations(db)
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    available = remaining_tickets(ticket_type)
    if quantity > available:
        db.rollback()
        return jsonify({"error": "Not enough tickets remain.", "available": available}), 409
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(minutes=RESERVATION_HOLD_MINUTES)
    cursor = db.execute(
        "INSERT INTO orders (ticket_type_id, buyer_name, buyer_email, quantity, status, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (ticket_type_id, buyer_name, buyer_email, quantity, created_at.isoformat(), expires_at.isoformat()),
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
            "expires_at": expires_at.isoformat(),
        },
        "message": "Reservation created. It is not confirmed until payment is verified.",
    }), 201


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
