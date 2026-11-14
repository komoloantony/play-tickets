"""Copy a local Play Tickets SQLite database to an empty PostgreSQL database."""

import os
import sqlite3
from pathlib import Path

from database import BASE_DIR, connect_database, initialize_database


TABLE_COLUMNS = {
    "events": ("id", "name", "venue", "starts_at", "description", "created_at"),
    "ticket_types": ("id", "event_id", "name", "price", "capacity"),
    "orders": (
        "id",
        "ticket_type_id",
        "buyer_name",
        "buyer_email",
        "quantity",
        "status",
        "created_at",
        "expires_at",
        "payment_provider",
        "payment_phone",
        "payment_reference",
        "provider_checkout_id",
        "provider_transaction_id",
        "payment_amount",
        "payment_currency",
        "payment_status",
    ),
    "tickets": (
        "id",
        "order_id",
        "ticket_number",
        "ticket_code",
        "status",
        "issued_at",
        "checked_in_at",
    ),
}


def migrate():
    database_url = (
        os.environ.get("PLAY_TICKETS_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not database_url:
        raise RuntimeError(
            "Set DATABASE_URL to the Render PostgreSQL internal connection string."
        )

    source_path = Path(
        os.environ.get("PLAY_TICKETS_DATABASE", BASE_DIR / "play_tickets.db")
    ).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source database not found: {source_path}")

    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = connect_database(database_url=database_url)
    try:
        if not target.is_postgres:
            raise RuntimeError("DATABASE_URL must point to a PostgreSQL database.")
        initialize_database(target)
        existing_rows = {
            table: target.execute(
                f"SELECT COUNT(*) AS row_count FROM {table}"
            ).fetchone()["row_count"]
            for table in TABLE_COLUMNS
        }
        if any(existing_rows.values()):
            raise RuntimeError(
                "The PostgreSQL database is not empty. No data was copied; "
                "use an empty database or migrate into it manually."
            )

        source_tables = {
            row["name"]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table, expected_columns in TABLE_COLUMNS.items():
            if table not in source_tables:
                continue
            available_columns = {
                row["name"]
                for row in source.execute(f"PRAGMA table_info({table})")
            }
            columns = [name for name in expected_columns if name in available_columns]
            if not columns:
                continue

            names = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)
            insert = f"INSERT INTO {table} ({names}) VALUES ({placeholders})"
            cursor = source.execute(f"SELECT {names} FROM {table} ORDER BY id")
            copied = 0
            while True:
                rows = cursor.fetchmany(500)
                if not rows:
                    break
                insert_cursor = target.executemany(
                    insert, [tuple(row) for row in rows]
                )
                insert_cursor.close()
                copied += len(rows)
            print(f"Copied {copied} {table} rows.")

            if copied and "id" in columns:
                target.execute(
                    "SELECT setval(pg_get_serial_sequence(?, 'id'), "
                    f"(SELECT MAX(id) FROM {table}), true)",
                    (table,),
                )

        target.commit()
        print("SQLite data migration completed.")
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    migrate()
