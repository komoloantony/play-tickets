"""Send confirmed Play Tickets orders to buyers by email."""

import os
import smtplib
import ssl
from email.message import EmailMessage
from html import escape


SMTP_TIMEOUT_SECONDS = 15


class TicketEmailError(Exception):
    """A ticket email could not be configured or delivered."""


def send_ticket_email(
    *,
    recipient,
    buyer_name,
    event_name,
    payment_reference,
    payment_amount,
    payment_currency,
    tickets,
    tracking_url,
):
    """Email printable SVG tickets after a payment has been approved."""
    host = os.environ.get("PLAY_TICKETS_SMTP_HOST", "").strip()
    username = os.environ.get("PLAY_TICKETS_SMTP_USERNAME", "").strip()
    password = os.environ.get("PLAY_TICKETS_SMTP_PASSWORD", "")
    sender = os.environ.get("PLAY_TICKETS_SMTP_FROM", username).strip()
    if not host or not sender:
        raise TicketEmailError(
            "Ticket email is not configured. Set PLAY_TICKETS_SMTP_HOST and PLAY_TICKETS_SMTP_FROM."
        )
    if bool(username) != bool(password):
        raise TicketEmailError(
            "Set both PLAY_TICKETS_SMTP_USERNAME and PLAY_TICKETS_SMTP_PASSWORD, or neither."
        )
    try:
        port = int(os.environ.get("PLAY_TICKETS_SMTP_PORT", "587"))
    except ValueError as error:
        raise TicketEmailError("PLAY_TICKETS_SMTP_PORT must be a valid port number.") from error
    if not 1 <= port <= 65535:
        raise TicketEmailError("PLAY_TICKETS_SMTP_PORT must be between 1 and 65535.")

    use_ssl = os.environ.get("PLAY_TICKETS_SMTP_USE_SSL", "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    amount = f"{payment_currency} {int(payment_amount):,}"
    message = EmailMessage()
    message["Subject"] = f"Your {event_name} tickets · Order {payment_reference}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"Hello {buyer_name},\n\n"
        f"Your payment for {event_name} has been verified. "
        f"Your order total was {amount}.\n"
        f"Order reference: {payment_reference}\n\n"
        "Your printable tickets are attached. Each ticket contains its own entry QR code.\n"
        + (
            f"Check your order status or download the tickets again here: {tracking_url}\n"
            if tracking_url else
            "Keep your order reference so you can check your ticket status on the event website.\n"
        )
        + "\nFirebrand Tickets"
    )
    safe_name = escape(buyer_name)
    safe_event = escape(event_name)
    safe_reference = escape(payment_reference)
    ticket_list = "".join(
        f"<li>{escape(ticket['ticket_type'])} — Ticket {ticket['ticket_number']}</li>"
        for ticket in tickets
    )
    tracking_link = (
        f'<p><a href="{escape(tracking_url, quote=True)}">View your order and tickets</a></p>'
        if tracking_url else ""
    )
    message.add_alternative(
        f"""\
<html>
  <body>
    <p>Hello {safe_name},</p>
    <p>Your payment for <strong>{safe_event}</strong> has been verified.</p>
    <p>Order <strong>{safe_reference}</strong> · {escape(amount)}</p>
    <p>Your printable tickets are attached. Each ticket has its own entry QR code.</p>
    <ul>{ticket_list}</ul>
    {tracking_link}
    <p>Firebrand Tickets</p>
  </body>
</html>
""",
        subtype="html",
    )
    for ticket in tickets:
        message.add_attachment(
            ticket["ticket_pdf"],
            maintype="application",
            subtype="pdf",
            filename=(
                f"ticket-{ticket['ticket_number']}-"
                f"{ticket['ticket_type'].replace(' ', '-')}.pdf"
            ),
        )

    try:
        client_type = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with client_type(host, port, timeout=SMTP_TIMEOUT_SECONDS) as client:
            if not use_ssl:
                client.starttls(context=ssl.create_default_context())
            if username:
                client.login(username, password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException, ValueError) as error:
        raise TicketEmailError("The ticket email could not be delivered.") from error
