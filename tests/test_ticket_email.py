import os
import unittest
from unittest.mock import patch

from ticket_email import TicketEmailError, send_ticket_email
from ticket_service import generate_ticket_pdf


class TicketEmailTests(unittest.TestCase):
    def test_ticket_pdf_is_generated_for_unicode_order_details(self):
        pdf = generate_ticket_pdf(
            event_name="Café 🎭",
            ticket_type="Regular",
            buyer_name="Zoë",
            ticket_number=1,
            ticket_code="PTK-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            starts_at="2026-12-13T15:00:00",
            venue="Nairobi",
        )
        self.assertTrue(pdf.startswith(b"%PDF-"))

    def test_sends_tickets_as_pdf_attachments(self):
        ticket = {
            "ticket_number": 1,
            "ticket_type": "VIP",
            "ticket_pdf": b"%PDF-1.4\n%ticket",
        }
        with patch.dict(os.environ, {
            "PLAY_TICKETS_SMTP_HOST": "smtp.example.test",
            "PLAY_TICKETS_SMTP_PORT": "587",
            "PLAY_TICKETS_SMTP_FROM": "tickets@example.test",
        }, clear=True), patch("ticket_email.smtplib.SMTP") as smtp:
            client = smtp.return_value.__enter__.return_value
            send_ticket_email(
                recipient="buyer@example.test",
                buyer_name="Amina",
                event_name="Behind the Sins",
                payment_reference="PT-ABC123",
                payment_amount=500,
                payment_currency="KES",
                tickets=[ticket],
                tracking_url="https://tickets.example.test/event.html?order=PT-ABC123",
            )

        message = client.send_message.call_args.args[0]
        self.assertEqual(message["To"], "buyer@example.test")
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "ticket-1-VIP.pdf")
        self.assertEqual(attachments[0].get_content_type(), "application/pdf")
        client.starttls.assert_called_once()

    def test_missing_smtp_configuration_is_reported(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(TicketEmailError, "not configured"):
                send_ticket_email(
                    recipient="buyer@example.test",
                    buyer_name="Amina",
                    event_name="Behind the Sins",
                    payment_reference="PT-ABC123",
                    payment_amount=500,
                    payment_currency="KES",
                    tickets=[],
                    tracking_url="",
                )


if __name__ == "__main__":
    unittest.main()
