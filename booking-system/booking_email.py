
import os
import smtplib
import logging
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def send_booking_email(booking):
    sender = os.getenv("SMTP_EMAIL")
    app_password = os.getenv("SMTP_APP_PASSWORD")

    if not sender or not app_password:
        logger.warning("Gmail SMTP settings are missing.")
        return False

    message = EmailMessage()
    message["Subject"] = f"Your Firebrand booking - {booking['reference']}"
    message["From"] = sender
    message["To"] = booking["customer_email"]

    message.set_content(f"""
Hello {booking['customer_name']},

Your booking has been successfully recorded!

BOOKING DETAILS

Event: {booking['event_name']}
Ticket type: {booking['ticket_type']}
Number of tickets: {booking['quantity']}
Total amount: KES {booking['total_amount']}

Booking reference:
{booking['reference']}

PAYMENT STATUS: PENDING

Your reservation is recorded, but payment has not yet been confirmed.

Please keep your booking reference safe. Your entry QR ticket
will only be issued after your payment has been verified.

Thank you for choosing Firebrand Tickets!

Firebrand Tickets Team
""")

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
            server.starttls()
            server.login(sender, app_password.replace(" ", ""))
            server.send_message(message)

        return True

    except Exception:
        logger.exception("Could not send booking confirmation email.")
        return False
