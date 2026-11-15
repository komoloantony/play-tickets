
from flask import Flask, jsonify, request, render_template
from datetime import datetime, timezone
from uuid import uuid4

from booking_database import get_connection, initialize_database
from booking_email import send_booking_email

app = Flask(__name__)

# Initial event configuration for testing.
EVENT_NAME = "Behind the Sins"
TICKET_TYPE = "General admission"
TICKET_PRICE = 200
TICKET_CAPACITY = 20


@app.get("/")
def home():
    return render_template("booking.html")


@app.post("/api/bookings")
def create_booking():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request."}), 400

    customer_name = str(data.get("customer_name", "")).strip()
    customer_email = str(data.get("customer_email", "")).strip()

    try:
        quantity = int(data.get("quantity", 0))
    except (TypeError, ValueError):
        quantity = 0

    if not customer_name or "@" not in customer_email:
        return jsonify({"error": "Enter your name and a valid email."}), 400

    if quantity < 1 or quantity > 10:
        return jsonify({"error": "Choose between 1 and 10 tickets."}), 400

    total_amount = TICKET_PRICE * quantity
    booking_reference = f"FB-{uuid4().hex[:12].upper()}"
    created_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as db:
        existing = db.execute(
            """
            SELECT COALESCE(SUM(quantity), 0) AS reserved
            FROM bookings
            WHERE event_name = ?
              AND booking_status = 'reserved'
            """,
            (EVENT_NAME,)
        ).fetchone()

        remaining = TICKET_CAPACITY - existing["reserved"]

        if quantity > remaining:
            return jsonify({
                "error": f"Only {remaining} tickets remain."
            }), 409

        db.execute(
            """
            INSERT INTO bookings (
                booking_reference,
                customer_name,
                customer_email,
                event_name,
                ticket_type,
                quantity,
                total_amount,
                payment_status,
                booking_status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 'reserved', ?)
            """,
            (
                booking_reference,
                customer_name,
                customer_email,
                EVENT_NAME,
                TICKET_TYPE,
                quantity,
                total_amount,
                created_at
            )
        )

        booking_details = {
        "reference": booking_reference,
        "customer_name": customer_name,
        "customer_email": customer_email,
        "event_name": EVENT_NAME,
        "ticket_type": TICKET_TYPE,
        "quantity": quantity,
        "total_amount": total_amount,
        "currency": "KES",
        "payment_status": "pending"
    }

    email_sent = send_booking_email(booking_details)

    return jsonify({
        "message": "Your booking has been recorded.",
        "booking": booking_details,
        "email_sent": email_sent
    }), 201


if __name__ == "__main__":
    initialize_database()
    app.run(host="127.0.0.1", port=5001, debug=True)
