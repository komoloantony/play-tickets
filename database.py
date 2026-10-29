  """SQLite setup and helper functions for Play Tickets."""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import g


BASE_DIR = Path(__file__).resolve().parent
# Let us move the database later without changing the code.
DATABASE = Path(os.environ.get("PLAY_TICKETS_DATABASE", BASE_DIR / "play_tickets.db"))


def get_db():
    """Open one database connection for this web request and reuse it."""
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    """Close the connection when Flask has finished answering the request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create the tables, or add the new expiry field to an older database."""
    db = get_db()
    db.executescript(
        """
        -- An event is a show, like a play happening at a theatre.
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            venue TEXT NOT NULL,
            starts_at TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        -- Each event can offer one or more kinds of tickets.
        CREATE TABLE IF NOT EXISTS ticket_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            price REAL NOT NULL CHECK(price >= 0),
            capacity INTEGER NOT NULL CHECK(capacity > 0)
        );
        -- An order records a buyer's request for tickets and its payment state.
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
    # Old holds have no timer, so let them expire now and free their tickets.
    db.execute(
        "UPDATE orders SET expires_at = created_at WHERE status = 'pending' AND expires_at IS NULL"
    )
    db.commit()


def remaining_tickets(ticket_type):
    """Count tickets already sold and tickets still being held for payment."""
    sold = get_db().execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM orders "
        "WHERE ticket_type_id = ? AND (status = 'confirmed' "
        "OR (status = 'pending' AND expires_at > ?))",
        (ticket_type["id"], datetime.now(timezone.utc).isoformat()),
    ).fetchone()[0]
    return ticket_type["capacity"] - sold


def expire_pending_reservations(db):
    """Mark old unpaid holds as expired so their tickets can be bought again."""
    db.execute(
        "UPDATE orders SET status = 'expired' "
        "WHERE status = 'pending' AND expires_at <= ?",
        (datetime.now(timezone.utc).isoformat(),),
    )
