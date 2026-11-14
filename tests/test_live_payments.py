import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ["PLAY_TICKETS_DATABASE"] = str(Path(_TEST_DIRECTORY.name) / "payments.db")
os.environ["PLAY_TICKETS_PAYMENT_MODE"] = "live"

import app as app_module
import payments
from database import get_db, inserted_id


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

    def create_reservation(self, provider="mpesa", quantity=2):
        return self.client.post(
            f"/api/v1/ticket-types/{self.ticket_type_id}/reservations",
            json={
                "buyer_name": "Amina",
                "buyer_email": "amina@example.test",
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
