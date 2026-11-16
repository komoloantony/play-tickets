import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


_TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ["PLAY_TICKETS_DATABASE"] = str(Path(_TEST_DIRECTORY.name) / "payments.db")
os.environ["PLAY_TICKETS_PAYMENT_MODE"] = "live"

import app as app_module
import payments
from database import expire_pending_reservations, get_db, inserted_id, remaining_tickets


CALLBACK_TOKEN = "a" * 64


class PaymentFlowTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()
        self.environment = patch.dict(os.environ, {
            "PLAY_TICKETS_PAYMENT_MODE": "live",
            "PLAY_TICKETS_PUBLIC_URL": "https://tickets.example.test",
            "PLAY_TICKETS_PAYMENT_CALLBACK_TOKEN": CALLBACK_TOKEN,
            "MPESA_CONSUMER_KEY": "test-key",
            "MPESA_CONSUMER_SECRET": "test-secret",
            "MPESA_SHORTCODE": "123456",
            "MPESA_PASSKEY": "test-passkey",
            "AIRTEL_CLIENT_ID": "airtel-test-id",
            "AIRTEL_CLIENT_SECRET": "airtel-test-secret",
        })
        self.environment.start()
        with app_module.app.app_context():
            db = get_db()
            db.execute("DELETE FROM orders")
            db.execute("DELETE FROM ticket_types")
            db.execute("DELETE FROM events")
            event_cursor = db.execute(
                "INSERT INTO events (name, venue, starts_at, created_at) "
                "VALUES ('Behind the Sins', 'Nairobi', '2026-12-13T15:00:00', '2026-01-01T00:00:00') "
                "RETURNING id"
            )
            self.event_id = inserted_id(event_cursor)
            ticket_cursor = db.execute(
                "INSERT INTO ticket_types (event_id, name, price, capacity) "
                "VALUES (?, ?, ?, ?) RETURNING id",
                (self.event_id, "General admission", 200, 20),
            )
            self.ticket_type_id = inserted_id(ticket_cursor)
            db.commit()

    def tearDown(self):
        self.environment.stop()

    def create_reservation(
        self, provider="mpesa", quantity=2, buyer_email="amina@example.test"
    ):
        return self.client.post(
            f"/api/v1/ticket-types/{self.ticket_type_id}/reservations",
            json={
                "buyer_name": "Amina",
                "buyer_email": buyer_email,
                "quantity": quantity,
                "payment_provider": provider,
                "payment_phone": "0712345678",
            },
        )

    def test_mpesa_callback_confirms_only_matching_amount_and_phone(self):
        with patch(
            "app.start_payment",
            return_value=payments.PaymentStart("checkout-123", "processing"),
        ):
            response = self.create_reservation()
        self.assertEqual(response.status_code, 201)
        reservation = response.get_json()["reservation"]
        self.assertEqual(reservation["payment_amount"], 400)

        payload = {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": "checkout-123",
                    "ResultCode": 0,
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": 400},
                            {"Name": "MpesaReceiptNumber", "Value": "QWERTY123"},
                            {"Name": "PhoneNumber", "Value": 254712345678},
                        ]
                    },
                }
            }
        }
        callback_url = f"/api/v1/payments/callbacks/mpesa/{CALLBACK_TOKEN}"
        mismatch = dict(payload)
        mismatch["Body"] = {
            "stkCallback": {
                **payload["Body"]["stkCallback"],
                "CallbackMetadata": {"Item": [
                    {"Name": "Amount", "Value": 399},
                    {"Name": "MpesaReceiptNumber", "Value": "QWERTY123"},
                    {"Name": "PhoneNumber", "Value": 254712345678},
                ]},
            }
        }
        rejected = self.client.post(callback_url, json=mismatch)
        self.assertEqual(rejected.status_code, 400)

        confirmed = self.client.post(callback_url, json=payload)
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.get_json()["reservation_status"], "confirmed")

        status = self.client.get(
            f"/api/v1/payments/{reservation['payment_reference']}"
        ).get_json()
        self.assertEqual(status["payment_status"], "succeeded")
        self.assertEqual(status["reservation_status"], "confirmed")
        self.assertNotIn("buyer_email", status)
        self.assertEqual(len(status["tickets"]), 2)
        self.assertEqual(len({ticket["ticket_code"] for ticket in status["tickets"]}), 2)
        self.assertIn("<svg", status["tickets"][0]["qr_svg"])
        ticket = status["tickets"][0]
        self.assertEqual(ticket["buyer_name"], "Amina")
        self.assertNotIn("buyer_email", ticket)
        self.assertIn("Behind the Sins", ticket["ticket_svg"])
        self.assertIn("General admission", ticket["ticket_svg"])
        self.assertIn("Amina", ticket["ticket_svg"])
        self.assertIn(ticket["ticket_code"], ticket["ticket_svg"])
        self.assertIn("#d8b45a", ticket["ticket_svg"])

    def test_paid_tickets_can_be_checked_in_only_once(self):
        with patch.object(app_module, "PAYMENT_MODE", "sandbox"), patch.dict(
            os.environ, {"PLAY_TICKETS_ADMIN_TOKEN": "test-admin-token"}
        ):
            created = self.create_reservation(quantity=2)
            reservation = created.get_json()["reservation"]
            unpaid_status = self.client.get(
                f"/api/v1/payments/{reservation['payment_reference']}"
            ).get_json()
            self.assertEqual(unpaid_status["tickets"], [])
            forged_ticket = self.client.post(
                "/api/v1/admin/tickets/check-in",
                headers={"Authorization": "Bearer test-admin-token"},
                json={"ticket_code": "PTK-" + "A" * 32},
            )
            confirmed = self.client.post(
                f"/api/v1/sandbox/payments/{reservation['payment_reference']}/result",
                headers={"Authorization": "Bearer test-admin-token"},
                json={"result": "success"},
            )
            status = self.client.get(
                f"/api/v1/payments/{reservation['payment_reference']}"
            ).get_json()
            ticket_code = status["tickets"][0]["ticket_code"]
            checked_in = self.client.post(
                "/api/v1/admin/tickets/check-in",
                headers={"Authorization": "Bearer test-admin-token"},
                json={"ticket_code": ticket_code},
            )
            duplicate = self.client.post(
                "/api/v1/admin/tickets/check-in",
                headers={"Authorization": "Bearer test-admin-token"},
                json={"ticket_code": ticket_code},
            )

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(forged_ticket.status_code, 404)
        self.assertEqual(len(status["tickets"]), 2)
        self.assertEqual(checked_in.status_code, 200)
        self.assertEqual(checked_in.get_json()["ticket"]["status"], "checked_in")
        self.assertEqual(duplicate.status_code, 409)

    def test_live_checkout_requires_provider_credentials_before_holding_inventory(self):
        with patch.dict(os.environ, {"AIRTEL_CLIENT_SECRET": ""}):
            response = self.create_reservation(provider="airtel")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("airtel-test-secret", response.get_data(as_text=True))
        with app_module.app.app_context():
            self.assertEqual(
                get_db().execute(
                    "SELECT COUNT(*) AS order_count FROM orders"
                ).fetchone()["order_count"],
                0,
            )

    def test_unknown_mpesa_outcome_requires_manual_review_without_retry(self):
        error = payments.PaymentError("Provider timed out.", outcome_unknown=True)
        with patch("app.start_payment", side_effect=error):
            response = self.create_reservation()
        self.assertEqual(response.status_code, 202)
        reservation = response.get_json()["reservation"]
        self.assertEqual(reservation["payment_status"], "unknown")
        status = self.client.get(
            f"/api/v1/payments/{reservation['payment_reference']}"
        ).get_json()
        self.assertEqual(status["reservation_status"], "pending")
        self.assertTrue(status["manual_review_required"])

    def test_admin_can_add_whole_kes_ticket_category(self):
        with patch.dict(os.environ, {"PLAY_TICKETS_ADMIN_TOKEN": "test-admin-token"}):
            created = self.client.post(
                f"/api/v1/events/{self.event_id}/ticket-types",
                headers={"Authorization": "Bearer test-admin-token"},
                json={"name": "VIP", "price": 500, "capacity": 4},
            )
            rejected = self.client.post(
                f"/api/v1/events/{self.event_id}/ticket-types",
                headers={"Authorization": "Bearer test-admin-token"},
                json={"name": "Fractional", "price": 500.5, "capacity": 4},
            )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["ticket_type"]["name"], "VIP")
        self.assertEqual(rejected.status_code, 400)

    def test_admin_can_load_reservations_for_event_without_orders(self):
        with patch.dict(os.environ, {"PLAY_TICKETS_ADMIN_TOKEN": "test-admin-token"}):
            response = self.client.get(
                f"/api/v1/admin/events/{self.event_id}/reservations",
                headers={"Authorization": "Bearer test-admin-token"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["reservations"], [])
        self.assertEqual(data["pagination"]["total"], 0)

    def test_manual_mpesa_order_accepts_multiple_ticket_types_and_issues_each_type(self):
        with app_module.app.app_context():
            db = get_db()
            vip_cursor = db.execute(
                "INSERT INTO ticket_types (event_id, name, price, capacity) "
                "VALUES (?, 'VIP', 500, 5) RETURNING id",
                (self.event_id,),
            )
            vip_ticket_type_id = inserted_id(vip_cursor)
            db.commit()

        with patch.object(app_module, "PAYMENT_MODE", "manual"), patch.dict(
            os.environ,
            {
                "PLAY_TICKETS_ADMIN_TOKEN": "test-admin-token",
                "PLAY_TICKETS_MPESA_TILL_NUMBER": "0711422001",
            },
        ), patch.object(app_module, "send_ticket_email") as send_ticket_email:
            options = self.client.get("/api/v1/payment-options").get_json()
            created = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "amina@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 2},
                        {"ticket_type_id": vip_ticket_type_id, "quantity": 1},
                    ],
                },
            )
            order = created.get_json()["order"]
            submitted = self.client.post(
                f"/api/v1/payments/{order['payment_reference']}/manual-receipt",
                json={"transaction_code": "QJK82ABC91"},
            )
            awaiting_review = self.client.get(
                f"/api/v1/payments/{order['payment_reference']}"
            ).get_json()
            reviewed = self.client.post(
                f"/api/v1/admin/payments/{order['payment_reference']}/review",
                headers={"Authorization": "Bearer test-admin-token"},
                json={"decision": "approve"},
            )
            status = self.client.get(
                f"/api/v1/payments/{order['payment_reference']}"
            ).get_json()
            downloaded_ticket = self.client.get(
                f"/api/v1/payments/{order['payment_reference']}/tickets/"
                f"{status['tickets'][0]['ticket_number']}.pdf"
            )
            admin_data = self.client.get(
                f"/api/v1/admin/events/{self.event_id}/reservations",
                headers={"Authorization": "Bearer test-admin-token"},
            ).get_json()

        self.assertEqual(options["payment_options"]["mpesa"]["till_number"], "0711422001")
        self.assertEqual(
            options["payment_options"]["mpesa"]["method"], "Pochi la Biashara"
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(order["payment_amount"], 900)
        self.assertEqual(
            order["ticket_items"],
            [
                {
                    "ticket_type": "General admission",
                    "quantity": 2,
                    "unit_price": 200,
                    "subtotal": 400,
                },
                {"ticket_type": "VIP", "quantity": 1, "unit_price": 500, "subtotal": 500},
            ],
        )
        self.assertEqual(order["quantity"], 3)
        self.assertEqual(submitted.status_code, 202)
        self.assertEqual(awaiting_review["payment_status"], "submitted")
        self.assertEqual(awaiting_review["tickets"], [])
        self.assertEqual(reviewed.get_json()["reservation_status"], "confirmed")
        self.assertEqual(reviewed.get_json()["ticket_email_status"], "sent")
        send_ticket_email.assert_called_once()
        self.assertEqual(status["payment_status"], "succeeded")
        self.assertEqual(status["ticket_email_status"], "sent")
        self.assertEqual(downloaded_ticket.status_code, 200)
        self.assertEqual(downloaded_ticket.mimetype, "application/pdf")
        self.assertTrue(downloaded_ticket.data.startswith(b"%PDF-"))
        self.assertIn(".pdf", downloaded_ticket.headers["Content-Disposition"])
        self.assertEqual(len(status["tickets"]), 3)
        self.assertEqual(
            sorted(ticket["ticket_type"] for ticket in status["tickets"]),
            ["General admission", "General admission", "VIP"],
        )
        self.assertEqual(
            admin_data["reservations"][0]["manual_transaction_code"], "QJK82ABC91"
        )
        self.assertEqual(admin_data["reservations"][0]["ticket_email_status"], "sent")

    def test_submitted_manual_order_stays_held_until_review_after_hold_deadline(self):
        with patch.object(app_module, "PAYMENT_MODE", "manual"), patch.dict(
            os.environ, {"PLAY_TICKETS_ADMIN_TOKEN": "test-admin-token"}
        ), patch.object(app_module, "send_ticket_email") as send_ticket_email:
            created = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "amina@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 1}
                    ],
                },
            )
            order = created.get_json()["order"]
            submitted = self.client.post(
                f"/api/v1/payments/{order['payment_reference']}/manual-receipt",
                json={"transaction_code": "QJK82ABC91"},
            )
            old_expiry = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
            with app_module.app.app_context():
                db = get_db()
                db.execute(
                    "UPDATE orders SET expires_at = ? WHERE payment_reference = ?",
                    (old_expiry, order["payment_reference"]),
                )
                expire_pending_reservations(db)
                db.commit()
                ticket_type = db.execute(
                    "SELECT id, capacity FROM ticket_types WHERE id = ?",
                    (self.ticket_type_id,),
                ).fetchone()
                available = remaining_tickets(ticket_type)

            waiting = self.client.get(
                f"/api/v1/payments/{order['payment_reference']}"
            ).get_json()
            approved = self.client.post(
                f"/api/v1/admin/payments/{order['payment_reference']}/review",
                headers={"Authorization": "Bearer test-admin-token"},
                json={"decision": "approve"},
            )
            final_status = self.client.get(
                f"/api/v1/payments/{order['payment_reference']}"
            ).get_json()
            duplicate_email = self.client.post(
                f"/api/v1/admin/payments/{order['payment_reference']}/ticket-email",
                headers={"Authorization": "Bearer test-admin-token"},
            )

        self.assertEqual(submitted.status_code, 202)
        self.assertEqual(available, ticket_type["capacity"] - 1)
        self.assertEqual(waiting["reservation_status"], "pending")
        self.assertEqual(waiting["payment_status"], "submitted")
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.get_json()["reservation_status"], "confirmed")
        self.assertEqual(len(final_status["tickets"]), 1)
        self.assertEqual(final_status["ticket_email_status"], "sent")
        self.assertEqual(duplicate_email.status_code, 200)
        send_ticket_email.assert_called_once()

    def test_smtp_failure_does_not_undo_approval_and_can_be_retried(self):
        with patch.object(app_module, "PAYMENT_MODE", "manual"), patch.dict(
            os.environ,
            {
                "PLAY_TICKETS_ADMIN_TOKEN": "test-admin-token",
                "PLAY_TICKETS_SMTP_HOST": "",
                "PLAY_TICKETS_SMTP_FROM": "",
            },
        ):
            created = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "amina@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 1}
                    ],
                },
            )
            reference = created.get_json()["order"]["payment_reference"]
            self.client.post(
                f"/api/v1/payments/{reference}/manual-receipt",
                json={"transaction_code": "QJK82ABC91"},
            )
            approved = self.client.post(
                f"/api/v1/admin/payments/{reference}/review",
                headers={"Authorization": "Bearer test-admin-token"},
                json={"decision": "approve"},
            )
            status = self.client.get(f"/api/v1/payments/{reference}").get_json()
            retry = self.client.post(
                f"/api/v1/admin/payments/{reference}/ticket-email",
                headers={"Authorization": "Bearer test-admin-token"},
            )

        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.get_json()["reservation_status"], "confirmed")
        self.assertEqual(approved.get_json()["ticket_email_status"], "failed")
        self.assertEqual(status["reservation_status"], "confirmed")
        self.assertEqual(len(status["tickets"]), 1)
        self.assertEqual(status["ticket_email_status"], "failed")
        self.assertEqual(retry.status_code, 502)
        self.assertIn("not configured", retry.get_json()["error"])

    def test_manual_payment_options_require_valid_number_and_allow_custom_label(self):
        with patch.object(app_module, "PAYMENT_MODE", "manual"):
            with patch.dict(os.environ, {}, clear=True):
                defaults = self.client.get("/api/v1/payment-options")
            self.assertEqual(defaults.status_code, 200)
            self.assertEqual(
                defaults.get_json()["payment_options"]["mpesa"],
                {
                    "method": "Pochi la Biashara",
                    "till_number": "0711422001",
                    "account_name": "Evans Kariuki",
                },
            )

            with patch.dict(os.environ, {"PLAY_TICKETS_MPESA_TILL_NUMBER": ""}):
                missing = self.client.get("/api/v1/payment-options")
            self.assertEqual(missing.status_code, 503)

            with patch.dict(os.environ, {
                "PLAY_TICKETS_MPESA_TILL_NUMBER": "12345",
                "PLAY_TICKETS_MPESA_METHOD_LABEL": "M-Pesa Till",
            }):
                configured = self.client.get("/api/v1/payment-options")
            self.assertEqual(configured.status_code, 200)
            self.assertEqual(
                configured.get_json()["payment_options"]["mpesa"],
                {
                    "method": "M-Pesa Till",
                    "till_number": "12345",
                    "account_name": "Evans Kariuki",
                },
            )

            with patch.dict(os.environ, {"PLAY_TICKETS_MPESA_TILL_NUMBER": "1234"}):
                invalid = self.client.get("/api/v1/payment-options")
            self.assertEqual(invalid.status_code, 503)

    def test_manual_receipt_code_cannot_be_reused_for_another_order(self):
        with patch.object(app_module, "PAYMENT_MODE", "manual"):
            created = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "amina@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 1}
                    ],
                },
            )
            reference = created.get_json()["order"]["payment_reference"]
            first = self.client.post(
                f"/api/v1/payments/{reference}/manual-receipt",
                json={"transaction_code": "QJK82ABC91"},
            )
            second = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "another@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 1}
                    ],
                },
            )
            duplicate = self.client.post(
                f"/api/v1/payments/{second.get_json()['order']['payment_reference']}/manual-receipt",
                json={"transaction_code": "qjk82abc91"},
            )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("already submitted", duplicate.get_json()["error"])

    def test_buyer_cannot_create_another_order_until_manual_review_finishes(self):
        headers = {"Authorization": "Bearer test-admin-token"}
        with patch.object(app_module, "PAYMENT_MODE", "manual"), patch.dict(
            os.environ, {"PLAY_TICKETS_ADMIN_TOKEN": "test-admin-token"}
        ), patch.object(app_module, "send_ticket_email"):
            first = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "Amina@Example.Test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 1}
                    ],
                },
            )
            reference = first.get_json()["order"]["payment_reference"]
            blocked_before_receipt = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "amina@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 1}
                    ],
                },
            )
            submitted = self.client.post(
                f"/api/v1/payments/{reference}/manual-receipt",
                json={"transaction_code": "QJK82ABC91"},
            )
            blocked_during_review = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "AMINA@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 1}
                    ],
                },
            )
            approved = self.client.post(
                f"/api/v1/admin/payments/{reference}/review",
                headers=headers,
                json={"decision": "approve"},
            )
            allowed_after_approval = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "amina@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 1}
                    ],
                },
            )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(blocked_before_receipt.status_code, 409)
        self.assertEqual(
            blocked_before_receipt.get_json()["active_order"]["payment_status"],
            "pending",
        )
        self.assertEqual(submitted.status_code, 202)
        self.assertEqual(blocked_during_review.status_code, 409)
        self.assertEqual(
            blocked_during_review.get_json()["active_order"]["payment_status"],
            "submitted",
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(allowed_after_approval.status_code, 201)

    def test_live_buyer_cannot_create_a_second_order_while_payment_is_pending(self):
        with patch("app.start_payment", return_value=payments.PaymentStart(
            "checkout-pending", "processing"
        )):
            first = self.create_reservation()
            second = self.create_reservation(
                buyer_email="AMINA@example.test"
            )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertIn("already have an order", second.get_json()["error"])

    def test_rejecting_manual_payment_releases_held_tickets(self):
        with patch.object(app_module, "PAYMENT_MODE", "manual"), patch.dict(
            os.environ, {"PLAY_TICKETS_ADMIN_TOKEN": "test-admin-token"}
        ):
            created = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "amina@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 2}
                    ],
                },
            )
            order = created.get_json()["order"]
            self.client.post(
                f"/api/v1/payments/{order['payment_reference']}/manual-receipt",
                json={"transaction_code": "QJK82ABC91"},
            )
            rejected = self.client.post(
                f"/api/v1/admin/payments/{order['payment_reference']}/review",
                headers={"Authorization": "Bearer test-admin-token"},
                json={"decision": "reject"},
            )
            status = self.client.get(
                f"/api/v1/payments/{order['payment_reference']}"
            ).get_json()
            available = self.client.get(
                f"/api/v1/events/{self.event_id}"
            ).get_json()["event"]["ticket_types"][0]["available"]
            another_order = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "AMINA@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 1}
                    ],
                },
            )

        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(status["reservation_status"], "rejected")
        self.assertEqual(status["tickets"], [])
        self.assertEqual(available, 20)
        self.assertEqual(another_order.status_code, 201)

    def test_manual_order_rejects_duplicate_ticket_types_and_over_limit(self):
        with patch.object(app_module, "PAYMENT_MODE", "manual"):
            duplicate_categories = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "amina@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 1},
                        {"ticket_type_id": self.ticket_type_id, "quantity": 1},
                    ],
                },
            )
            too_many = self.client.post(
                f"/api/v1/events/{self.event_id}/orders",
                json={
                    "buyer_name": "Amina",
                    "buyer_email": "amina@example.test",
                    "ticket_items": [
                        {"ticket_type_id": self.ticket_type_id, "quantity": 10},
                        {"ticket_type_id": self.ticket_type_id + 1, "quantity": 1},
                    ],
                },
            )

        self.assertEqual(duplicate_categories.status_code, 400)
        self.assertEqual(too_many.status_code, 400)

    def test_event_creation_rejects_free_and_fractional_ticket_prices(self):
        headers = {"Authorization": "Bearer test-admin-token"}
        event_data = {
            "name": "Opening Night",
            "venue": "Nairobi",
            "starts_at": "2026-12-13T15:00:00",
            "ticket_type": {
                "name": "General admission",
                "price": 1200,
                "capacity": 10,
            },
        }
        with patch.dict(os.environ, {"PLAY_TICKETS_ADMIN_TOKEN": "test-admin-token"}):
            created = self.client.post(
                "/api/v1/events",
                headers=headers,
                json=event_data,
            )
            for price in (0, 1200.5):
                invalid_data = {
                    **event_data,
                    "ticket_type": {**event_data["ticket_type"], "price": price},
                }
                rejected = self.client.post(
                    "/api/v1/events",
                    headers=headers,
                    json=invalid_data,
                )
                self.assertEqual(rejected.status_code, 400)

        self.assertEqual(created.status_code, 201)
        self.assertEqual(
            created.get_json()["event"]["ticket_types"][0]["price"],
            1200,
        )

    def test_airtel_status_is_verified_server_to_server(self):
        with patch(
            "app.start_payment",
            return_value=payments.PaymentStart("airtel-order-1", "processing"),
        ):
            response = self.create_reservation(provider="airtel", quantity=1)
        reservation = response.get_json()["reservation"]
        with patch(
            "app.check_payment",
            return_value=payments.PaymentStatus("succeeded", "airtel-receipt-1"),
        ) as check_payment:
            status = self.client.get(
                f"/api/v1/payments/{reservation['payment_reference']}"
            ).get_json()
        check_payment.assert_called_once()
        self.assertEqual(status["payment_status"], "succeeded")
        self.assertEqual(status["reservation_status"], "confirmed")

    def test_late_payment_is_not_used_to_revive_expired_tickets(self):
        with patch(
            "app.start_payment",
            return_value=payments.PaymentStart("checkout-late", "processing"),
        ):
            response = self.create_reservation(quantity=1)
        reservation = response.get_json()["reservation"]
        with app_module.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE orders SET expires_at = '2000-01-01T00:00:00+00:00' "
                "WHERE payment_reference = ?",
                (reservation["payment_reference"],),
            )
            db.commit()

        late_callback = self.client.post(
            f"/api/v1/payments/callbacks/mpesa/{CALLBACK_TOKEN}",
            json={
                "Body": {
                    "stkCallback": {
                        "CheckoutRequestID": "checkout-late",
                        "ResultCode": 0,
                        "CallbackMetadata": {"Item": [
                            {"Name": "Amount", "Value": 200},
                            {"Name": "MpesaReceiptNumber", "Value": "LATE123"},
                            {"Name": "PhoneNumber", "Value": 254712345678},
                        ]},
                    }
                }
            },
        )
        self.assertEqual(late_callback.status_code, 200)
        status = self.client.get(
            f"/api/v1/payments/{reservation['payment_reference']}"
        ).get_json()
        self.assertEqual(status["payment_status"], "succeeded")
        self.assertEqual(status["reservation_status"], "expired")
        self.assertTrue(status["refund_or_support_required"])
        self.assertEqual(status["tickets"], [])


class ProviderRequestTests(unittest.TestCase):
    def test_mpesa_start_sends_checkout_request_with_callback(self):
        values = {
            "MPESA_CONSUMER_KEY": "test-key",
            "MPESA_CONSUMER_SECRET": "test-secret",
            "MPESA_SHORTCODE": "123456",
            "MPESA_PASSKEY": "test-passkey",
            "PLAY_TICKETS_PUBLIC_URL": "https://tickets.example.test",
            "PLAY_TICKETS_PAYMENT_CALLBACK_TOKEN": CALLBACK_TOKEN,
            "MPESA_ENV": "sandbox",
        }
        with patch.dict(os.environ, values, clear=True):
            with patch(
                "payments._request_json",
                side_effect=[
                    {"access_token": "oauth-token"},
                    {"ResponseCode": "0", "CheckoutRequestID": "checkout-456"},
                ],
            ) as request_json:
                payment = payments.start_payment(
                    "mpesa",
                    payment_reference="PT-0123456789ABCDEF",
                    phone="254712345678",
                    amount=400,
                )
        self.assertEqual(payment.checkout_id, "checkout-456")
        request = request_json.call_args_list[1]
        self.assertEqual(request.args[0], "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest")
        self.assertEqual(request.kwargs["body"]["Amount"], 400)
        self.assertEqual(request.kwargs["body"]["PartyA"], "254712345678")
        self.assertIn(CALLBACK_TOKEN, request.kwargs["body"]["CallBackURL"])

    def test_airtel_start_uses_kenya_collection_fields(self):
        values = {
            "AIRTEL_CLIENT_ID": "airtel-id",
            "AIRTEL_CLIENT_SECRET": "airtel-secret",
            "PLAY_TICKETS_PUBLIC_URL": "https://tickets.example.test",
            "PLAY_TICKETS_PAYMENT_CALLBACK_TOKEN": CALLBACK_TOKEN,
            "AIRTEL_ENV": "sandbox",
        }
        with patch.dict(os.environ, values, clear=True):
            with patch(
                "payments._airtel_access_token",
                return_value="airtel-token",
            ):
                with patch(
                    "payments._request_json",
                    return_value={
                        "status": {"success": True, "code": "200"},
                        "data": {"transaction": {"id": "airtel-checkout"}},
                    },
                ) as request_json:
                    payment = payments.start_payment(
                        "airtel",
                        payment_reference="PT-0123456789ABCDEF",
                        phone="254712345678",
                        amount=500,
                    )
        self.assertEqual(payment.checkout_id, "airtel-checkout")
        request = request_json.call_args
        self.assertEqual(request.args[0], "https://openapiuat.airtel.africa/merchant/v1/payments/")
        self.assertEqual(request.kwargs["headers"]["X-Country"], "KE")
        self.assertEqual(request.kwargs["body"]["subscriber"]["msisdn"], "712345678")
        self.assertEqual(request.kwargs["body"]["transaction"]["amount"], 500)


if __name__ == "__main__":
    unittest.main()
