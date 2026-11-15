
import sqlite3
from pathlib import Path

DATABASE_PATH = Path(__file__).resolve().parent / "bookings.db"


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with get_connection() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_reference TEXT NOT NULL UNIQUE,
                customer_name TEXT NOT NULL,
                customer_email TEXT NOT NULL,
                event_name TEXT NOT NULL,
                ticket_type TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 10),
                total_amount INTEGER NOT NULL CHECK (total_amount >= 1),
                payment_status TEXT NOT NULL DEFAULT 'pending',
                booking_status TEXT NOT NULL DEFAULT 'reserved',
                created_at TEXT NOT NULL
            )
        """)

        db.commit()


if __name__ == "__main__":
    initialize_database()
    print("Booking database initialized successfully.")
    print(f"Database location: {DATABASE_PATH}")
