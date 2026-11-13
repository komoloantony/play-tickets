"""M-Pesa Daraja and Airtel Africa payment API clients."""

import base64
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


HTTP_TIMEOUT_SECONDS = 15
KENYA_TIMEZONE = timezone(timedelta(hours=3))


class PaymentError(Exception):
    """A safe-to-display payment failure, optionally with an uncertain outcome."""

    def __init__(self, message, *, outcome_unknown=False):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True)
class PaymentStart:
    checkout_id: str
    status: str


@dataclass(frozen=True)
class PaymentStatus:
    status: str
    transaction_id: str = ""


_airtel_token_lock = threading.Lock()
_airtel_cached_token = None
_airtel_cached_until = 0.0
_airtel_cached_key = None


def _environment(provider):
    if provider == "mpesa":
        required = (
            "MPESA_CONSUMER_KEY",
            "MPESA_CONSUMER_SECRET",
            "MPESA_SHORTCODE",
            "MPESA_PASSKEY",
        )
        mode = os.environ.get("MPESA_ENV", "production").lower()
        if mode not in {"production", "sandbox"}:
            raise PaymentError("MPESA_ENV must be production or sandbox.")
        root = (
            "https://api.safaricom.co.ke"
            if mode == "production"
            else "https://sandbox.safaricom.co.ke"
        )
        return required, root

    if provider == "airtel":
        required = ("AIRTEL_CLIENT_ID", "AIRTEL_CLIENT_SECRET")
        mode = os.environ.get("AIRTEL_ENV", "production").lower()
        if mode not in {"production", "sandbox"}:
            raise PaymentError("AIRTEL_ENV must be production or sandbox.")
        root = (
            "https://openapi.airtel.africa"
            if mode == "production"
            else "https://openapiuat.airtel.africa"
        )
        return required, root

    raise PaymentError("Unsupported payment provider.")


def is_configured(provider):
    """Return whether the selected provider has credentials and a callback URL."""
    required, _root = _environment(provider)
    if provider == "mpesa" and os.environ.get(
        "MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline"
    ) not in {"CustomerPayBillOnline", "CustomerBuyGoodsOnline"}:
        raise PaymentError(
            "MPESA_TRANSACTION_TYPE must be CustomerPayBillOnline or CustomerBuyGoodsOnline."
        )
    if any(not os.environ.get(name) for name in required):
        return False
    return bool(_callback_url(provider))


def _callback_url(provider):
    public_url = os.environ.get("PLAY_TICKETS_PUBLIC_URL", "").rstrip("/")
    callback_token = os.environ.get("PLAY_TICKETS_PAYMENT_CALLBACK_TOKEN", "")
    try:
        parsed_url = urlsplit(public_url)
    except ValueError:
        return ""
    if (
        len(callback_token) < 32
        or parsed_url.scheme != "https"
        or not parsed_url.netloc
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        return ""
    return (
        f"{public_url}/api/v1/payments/callbacks/{provider}/"
        f"{quote(callback_token, safe='')}"
    )


def _request_json(url, *, method="GET", body=None, headers=None, outcome_unknown=False):
    request_headers = {"Accept": "application/json"}
    request_headers.update(headers or {})
    payload = None
    if body is not None:
        request_headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode("utf-8")
    request = Request(url, data=payload, headers=request_headers, method=method)

    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise PaymentError(
            "The payment provider rejected the request.",
            outcome_unknown=outcome_unknown and error.code >= 500,
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise PaymentError(
            "The payment provider could not be reached.",
            outcome_unknown=outcome_unknown,
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PaymentError(
            "The payment provider returned an invalid response.",
            outcome_unknown=outcome_unknown,
        ) from error

    if not isinstance(result, dict):
        raise PaymentError(
            "The payment provider returned an invalid response.",
            outcome_unknown=outcome_unknown,
        )
    return result


def _mpesa_access_token(root):
    key = os.environ["MPESA_CONSUMER_KEY"]
    secret = os.environ["MPESA_CONSUMER_SECRET"]
    credentials = base64.b64encode(f"{key}:{secret}".encode("utf-8")).decode("ascii")
    result = _request_json(
        f"{root}/oauth/v1/generate?grant_type=client_credentials",
        headers={"Authorization": f"Basic {credentials}"},
    )
    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        raise PaymentError("M-Pesa authentication failed.")
    return token


def _airtel_access_token(root):
    global _airtel_cached_token, _airtel_cached_until, _airtel_cached_key

    client_id = os.environ["AIRTEL_CLIENT_ID"]
    client_secret = os.environ["AIRTEL_CLIENT_SECRET"]
    cache_key = (root, client_id, client_secret)
    with _airtel_token_lock:
        if (
            _airtel_cached_token
            and _airtel_cached_until > time.monotonic()
            and _airtel_cached_key == cache_key
        ):
            return _airtel_cached_token

        result = _request_json(
            f"{root}/auth/oauth2/token",
            method="POST",
            body={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
        )
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise PaymentError("Airtel authentication failed.")
        try:
            expires_in = int(result.get("expires_in", 3600))
        except (TypeError, ValueError):
            expires_in = 3600
        _airtel_cached_token = token
        _airtel_cached_key = cache_key
        _airtel_cached_until = time.monotonic() + max(60, expires_in - 60)
        return token


def start_payment(provider, *, payment_reference, phone, amount):
    """Ask the selected provider to prompt the buyer's phone to pay."""
    required, root = _environment(provider)
    if any(not os.environ.get(name) for name in required):
        raise PaymentError(f"{provider.title()} payment credentials are not configured.")
    callback_url = _callback_url(provider)
    if not callback_url:
        raise PaymentError("A public HTTPS payment callback URL is not configured.")

    if provider == "mpesa":
        return _start_mpesa(root, payment_reference, phone, amount, callback_url)
    if provider == "airtel":
        return _start_airtel(root, payment_reference, phone, amount)
    raise PaymentError("Unsupported payment provider.")


def _start_mpesa(root, payment_reference, phone, amount, callback_url):
    shortcode = os.environ["MPESA_SHORTCODE"]
    transaction_type = os.environ.get(
        "MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline"
    )
    if transaction_type not in {"CustomerPayBillOnline", "CustomerBuyGoodsOnline"}:
        raise PaymentError(
            "MPESA_TRANSACTION_TYPE must be CustomerPayBillOnline or CustomerBuyGoodsOnline."
        )
    timestamp = datetime.now(KENYA_TIMEZONE).strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(
        f"{shortcode}{os.environ['MPESA_PASSKEY']}{timestamp}".encode("utf-8")
    ).decode("ascii")
    token = _mpesa_access_token(root)
    result = _request_json(
        f"{root}/mpesa/stkpush/v1/processrequest",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
        body={
            "BusinessShortCode": shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": transaction_type,
            "Amount": amount,
            "PartyA": phone,
            "PartyB": shortcode,
            "PhoneNumber": phone,
            "CallBackURL": callback_url,
            "AccountReference": f"PT{payment_reference[-10:]}",
            "TransactionDesc": "Play Tickets",
        },
        outcome_unknown=True,
    )
    if str(result.get("ResponseCode")) != "0":
        raise PaymentError("M-Pesa could not start the payment request.")
    checkout_id = result.get("CheckoutRequestID")
    if not isinstance(checkout_id, str) or not checkout_id:
        raise PaymentError(
            "M-Pesa returned no checkout reference.",
            outcome_unknown=True,
        )
    return PaymentStart(checkout_id=checkout_id, status="processing")


def _start_airtel(root, payment_reference, phone, amount):
    token = _airtel_access_token(root)
    result = _request_json(
        f"{root}/merchant/v1/payments/",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Country": "KE",
            "X-Currency": "KES",
        },
        body={
            "reference": payment_reference,
            "subscriber": {
                "country": "KE",
                "currency": "KES",
                "msisdn": phone[-9:],
            },
            "transaction": {
                "amount": amount,
                "country": "KE",
                "currency": "KES",
                "id": payment_reference,
            },
        },
        outcome_unknown=True,
    )
    status = result.get("status", {})
    response_data = result.get("data")
    transaction = (
        response_data.get("transaction", {})
        if isinstance(response_data, dict)
        else {}
    )
    if not isinstance(transaction, dict):
        transaction = {}
    accepted = (
        isinstance(status, dict)
        and (status.get("success") is True or str(status.get("code")) == "200")
    )
    if not accepted:
        raise PaymentError("Airtel Money could not start the payment request.")
    checkout_id = transaction.get("id") or payment_reference
    if not isinstance(checkout_id, str):
        raise PaymentError(
            "Airtel Money returned no payment reference.",
            outcome_unknown=True,
        )
    return PaymentStart(checkout_id=checkout_id, status="processing")


def check_payment(provider, *, payment_reference, checkout_id, amount):
    """Query Airtel's transaction status; M-Pesa is confirmed by its callback."""
    if provider != "airtel":
        return PaymentStatus(status="processing")

    required, root = _environment(provider)
    if any(not os.environ.get(name) for name in required):
        raise PaymentError("Airtel Money credentials are not configured.")
    token = _airtel_access_token(root)
    result = _request_json(
        f"{root}/standard/v1/payments/{quote(checkout_id, safe='')}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Country": "KE",
            "X-Currency": "KES",
        },
    )
    response_data = result.get("data")
    transaction = (
        response_data.get("transaction")
        if isinstance(response_data, dict)
        else None
    )
    if not isinstance(transaction, dict):
        raise PaymentError("Airtel Money returned an invalid payment status.")

    actual_amount = transaction.get("amount")
    if actual_amount is not None:
        try:
            if int(actual_amount) != amount:
                raise PaymentError("Airtel Money returned a mismatched payment amount.")
        except (TypeError, ValueError) as error:
            raise PaymentError("Airtel Money returned an invalid payment amount.") from error

    provider_status = str(transaction.get("status", "")).upper()
    if provider_status in {"TS", "SUCCESS", "SUCCESSFUL"}:
        status = "succeeded"
    elif provider_status in {"TF", "FAILED", "FAILURE"}:
        status = "failed"
    else:
        status = "processing"
    transaction_id = transaction.get("airtel_money_id", "")
    return PaymentStatus(
        status=status,
        transaction_id=str(transaction_id) if transaction_id else "",
    )
