# Play Tickets Backend

This folder contains the Python backend and the partner-built static frontend for Play Tickets. The backend stores events and reservations. The API is the messenger between the frontend and backend, and JSON is the simple text format they use to send information to each other.

## What each file is for

- **`app.py`** — The Python app. It contains API routes for events, ticket reservations, and protected admin data.
- **`requirements.txt`** — Flask and the QR-code renderer used for entry tickets.
- **`database.py`** — Database connections and schema for PostgreSQL and local SQLite.
- **`.gitignore`** — A list of local files Git should leave out when sending the project to GitHub, such as the local database and private settings.
- **`README.md`** — This guide. It explains how to start the app and how the frontend can talk to the API.
- **`Frontend/`** — The API-connected event listing, checkout, and admin pages.
- **`partner-frontend/`** — The event-branded storefront. Its event page reads ticket prices, availability, and payment status from the API.
- **`play_tickets.db`** — The local database where the app saves events, ticket types, and reservations. The app creates this file when it starts; it is not included in GitHub.

## Run locally

1. Create and activate a Python virtual environment.
2. Install dependencies: `python -m pip install -r requirements.txt`.
3. Start the backend: `python app.py`.
4. Check that it is responding at `http://127.0.0.1:5000/api/v1/health`.

Without a database URL, the app uses local SQLite and creates `play_tickets.db`. Set `PLAY_TICKETS_DATABASE_URL` or `DATABASE_URL` to use PostgreSQL instead. The local database and private settings are not uploaded to GitHub. Admin routes and event creation require a shared secret token for development; this is not a full sign-in system, so the API is not ready to open publicly as-is.

## PostgreSQL and Render

The backend uses PostgreSQL when `DATABASE_URL` (or `PLAY_TICKETS_DATABASE_URL`) is set; Render's internal PostgreSQL connection string can be used as `DATABASE_URL`. Keep the connection string in Render's environment settings—never commit or paste it into source files. The app creates or upgrades its tables during startup. Configure the Render web service with:

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn app:app`
- **Environment:** `DATABASE_URL` set from the Render PostgreSQL database's internal connection string.

Set `PLAY_TICKETS_ADMIN_TOKEN`, `PLAY_TICKETS_SECRET`, payment credentials, and trusted CORS origins through the Render environment settings as appropriate. Do not enable live payments until provider credentials, HTTPS callbacks, and production settings have been verified.

### Manual M-Pesa checkout (default)

The site defaults to manual payment by **Pochi la Biashara: 0711422001**. Buyers choose ticket quantities, see the calculated amount and order reference, pay that exact amount, and submit the M-Pesa transaction code. The backend attaches the code to that order and prevents reuse. The 15-minute ticket hold applies while the buyer is paying; after a transaction code is submitted, the tickets remain held until the organizer approves or rejects the payment. No Daraja, IntaSend, or M-Pesa sandbox credentials are needed.

The order reference is saved in the page URL and, when browser storage is available, on that device. The buyer can safely leave and return to check the latest status. After approval, the backend issues tickets and attempts to email PDF tickets with their QR codes. Each ticket can also be downloaded individually as a PDF from the order status page. Email delivery uses the persistent database outbox; a failed delivery does not undo ticket issuance, and an admin can retry it from the reservations table.

To enable ticket emails, configure the following environment variables in the backend host. Use a provider-issued SMTP app password where required; never put SMTP credentials in frontend files or commit them to Git:

```text
PLAY_TICKETS_SMTP_HOST=<SMTP server>
PLAY_TICKETS_SMTP_PORT=587
PLAY_TICKETS_SMTP_USERNAME=<SMTP username>
PLAY_TICKETS_SMTP_PASSWORD=<SMTP password>
PLAY_TICKETS_SMTP_FROM=<verified sender address>
PLAY_TICKETS_SMTP_USE_SSL=false
PLAY_TICKETS_FRONTEND_URL=https://tickets.example.com/event.html
```

Port 587 uses STARTTLS by default. Set `PLAY_TICKETS_SMTP_USE_SSL=true` for a provider that requires implicit TLS (commonly port 465). `PLAY_TICKETS_FRONTEND_URL` is used to add a return-to-order link to the email. Without SMTP configuration, approval and ticket issuance still work, but the admin page will show the email as failed and offer a retry button.

The number and label can be overridden with environment variables. For a Render deployment, set these in the web service's environment settings so it uses this checkout even if an older payment mode is configured:

```text
PLAY_TICKETS_PAYMENT_MODE=manual
PLAY_TICKETS_MPESA_TILL_NUMBER=0711422001
PLAY_TICKETS_MPESA_METHOD_LABEL=Pochi la Biashara
PLAY_TICKETS_ADMIN_TOKEN=<private-long-random-value>
```

For local testing, set an admin token before starting the backend:

```powershell
$env:PLAY_TICKETS_ADMIN_TOKEN = "use-a-long-private-random-value"
python app.py
```

Keep the admin token private. Enter it on `Frontend/admin.html`; the reservations table shows each ticket type and quantity, total amount, submitted transaction code, email-delivery status, and approve/reject controls. Approval is a manual organizer action after checking the payment in M-Pesa. If email delivery fails, configure or correct the SMTP settings and use **Retry ticket email** on that reservation.

### Copy existing SQLite records to PostgreSQL

The repository does not contain `play_tickets.db`, but the local file can be copied to an **empty** PostgreSQL database. The migration script preserves event, ticket-type, reservation, and issued-ticket IDs and refuses to copy into a database that already contains records. Keep the SQLite file as a backup until the destination is verified.

1. Make sure PostgreSQL is available and the `psycopg` dependency is installed.
2. Set `DATABASE_URL` to Render's **External Database URL** in a private local shell. The Internal URL only works from services on Render's private network. Do not commit or share the connection string.
3. If the SQLite file is not at the default path, set `PLAY_TICKETS_DATABASE` to its path.
4. Run `python migrate_sqlite_to_postgres.py` once, then verify the event and reservation counts in PostgreSQL before deploying the web service.

The migration does not remove or modify the SQLite file. Do not run it against a database already containing production records.

## Frontend

The API-connected frontend lives in `Frontend/`: `index.html` loads events from the API and `event.html` creates reservations. The branded storefront is in `partner-frontend/`. Start either static site from its own directory in a second PowerShell window:

```powershell
Set-Location partner-frontend
python -m http.server 8000
```

Then open `http://127.0.0.1:8000/` in a browser. Opening `partner-frontend/event.html` directly also shows the event details and ticket prices, but booking requires both local servers and the `http://127.0.0.1:8000/event.html` address. The backend allows browser requests from local ports 8000 and 8001 by default. Set `PLAY_TICKETS_CORS_ORIGINS` to a comma-separated list to use other trusted frontend origins. Create a `Behind the Sins` event and its ticket categories with the admin API before booking it.

`Frontend/checkout.js` and `partner-frontend/event.js` create reservations through the API. The partner event page loads ticket prices and availability from the backend. In manual mode, buyers select their ticket quantities, pay the displayed total to the prominently displayed, copyable Pochi la Biashara number, and submit the M-Pesa transaction code for organizer verification. Each approved order receives one unique QR entry ticket per quantity purchased. The default local API base is `http://127.0.0.1:5000`; when hosting the frontend and API on different origins, define `window.PLAY_TICKETS_API_BASE` before loading the page script and allow that frontend origin in `PLAY_TICKETS_CORS_ORIGINS`.

### Admin review page

The admin page is in `Frontend/`, not `partner-frontend/`. Start a second static server from `Frontend/`:

```powershell
Set-Location Frontend
python -m http.server 8001
```

Open `http://127.0.0.1:8001/admin.html`. Enter the admin token configured for the backend to view ticket sales, pending holds, and the latest reservations, or use the ticket check-in tool. Staff can scan using a supported browser camera, a USB QR scanner, or enter the printed ticket code. The token is held in browser memory for this tab and is not saved in the frontend files.

## API for the frontend

All API routes use the `/api/v1` prefix and exchange JSON.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Check that the backend is available. |
| `GET` | `/api/v1/events` | List events, ticket types, prices, and available quantities. |
| `GET` | `/api/v1/events/<event_id>` | Get one event and its ticket types. |
| `GET` | `/api/v1/payment-options` | Return the active payment mode and public Pochi number. |
| `POST` | `/api/v1/events/<event_id>/orders` | Create and hold a manual M-Pesa order with one or more ticket types. |
| `POST` | `/api/v1/payments/<payment_reference>/manual-receipt` | Attach a buyer-submitted M-Pesa code to its order. |
| `POST` | `/api/v1/admin/payments/<payment_reference>/review` | Approve or reject a submitted M-Pesa code (admin token required). |
| `POST` | `/api/v1/admin/payments/<payment_reference>/ticket-email` | Retry delivery of tickets for an approved order (admin token required). |
| `POST` | `/api/v1/events` | Create an event and its first ticket type (admin token required). |
| `POST` | `/api/v1/events/<event_id>/ticket-types` | Add a ticket category (admin token required). |
| `POST` | `/api/v1/ticket-types/<ticket_type_id>/reservations` | Hold tickets while the buyer completes payment. |
| `GET` | `/api/v1/payments/<payment_reference>` | Return a buyer-safe payment and reservation status. |
| `GET` | `/api/v1/payments/<payment_reference>/tickets/<ticket_number>.pdf` | Download one approved ticket as a PDF. |
| `POST` | `/api/v1/admin/tickets/check-in` | Validate a confirmed paid ticket and mark it checked in (admin token required). |
| `POST` | `/api/v1/payments/callbacks/<provider>/<callback_token>` | Receive a token-protected live provider callback over HTTPS. |
| `POST` | `/api/v1/sandbox/payments/<payment_reference>/result` | Simulate a payment result while sandbox mode is enabled (admin token required). |
| `POST` | `/api/v1/sandbox/callbacks/<provider>/<callback_token>` | Receive a token-protected mock callback through an HTTPS tunnel. |
| `GET` | `/api/v1/admin/events` | Show each event's ticket capacity, confirmed sales, active holds, and tickets left (admin token required). |
| `GET` | `/api/v1/admin/events/<event_id>/reservations` | Show buyers and their reservation details for one event (admin token required). |

### Admin access

The admin routes can show buyer names and email addresses, so the backend protects them with a secret token. Set the token before starting the app in PowerShell:

```powershell
$env:PLAY_TICKETS_ADMIN_TOKEN = "use-a-long-private-random-value"
python app.py
```

Keep the value private and do not put it in GitHub or public frontend code. Send it with each admin request in this HTTP header:

```text
Authorization: Bearer your-private-token
```

If no token is set, admin routes return an error instead of showing private data. The event-creation route also requires this token. A real public website will need proper admin accounts and sign-in rather than sharing one token.

### Create an event (admin only)

Send JSON like:

```json
{
  "name": "Opening Night",
  "venue": "Town Theatre",
  "starts_at": "2026-11-15T19:30:00",
  "description": "An evening of theatre.",
  "ticket_type": {
    "name": "General admission",
    "price": 1200,
    "capacity": 100
  }
}
```

The response is `201 Created` and contains the saved event, including its generated IDs.

To add another category such as VIP, use the returned event ID:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:5000/api/v1/events/1/ticket-types" `
  -Headers @{ Authorization = "Bearer $env:PLAY_TICKETS_ADMIN_TOKEN" } `
  -ContentType "application/json" `
  -Body '{"name":"VIP","price":500,"capacity":50}'
```

The branded partner page currently looks for an event named `Behind the Sins`; ticket names and prices are read from the database, not trusted from the page.

### Reserve tickets

Send JSON to the reservation endpoint:

```json
{
  "buyer_name": "Amina",
  "buyer_email": "amina@example.com",
  "quantity": 2,
  "payment_provider": "mpesa",
  "payment_phone": "0712345678"
}
```

A successful response is `201 Created` and includes a reservation ID, `status: "pending"`, an `expires_at` timestamp, and the payment state. In live mode the backend starts the provider request before responding. A pending reservation holds its tickets for 15 minutes. If inventory is insufficient, the API responds with `409 Conflict` and the remaining quantity. A reservation is not confirmed until the provider status is verified.

### Live M-Pesa and Airtel Money payments

Live payments require approved merchant accounts, production API credentials, a deployed backend reachable over HTTPS, and provider callback setup. Do not put credentials in the frontend or commit them to Git; `.env` is ignored. Configure secrets in the hosting provider's environment settings.

```powershell
$env:PLAY_TICKETS_PAYMENT_MODE = "live"
$env:PLAY_TICKETS_PUBLIC_URL = "https://api.example.com"
$env:PLAY_TICKETS_PAYMENT_CALLBACK_TOKEN = "<at-least-32-random-characters>"
$env:PLAY_TICKETS_CORS_ORIGINS = "https://tickets.example.com"

$env:MPESA_ENV = "production"
$env:MPESA_CONSUMER_KEY = "<Daraja consumer key>"
$env:MPESA_CONSUMER_SECRET = "<Daraja consumer secret>"
$env:MPESA_SHORTCODE = "<approved shortcode>"
$env:MPESA_PASSKEY = "<Daraja passkey>"
$env:MPESA_TRANSACTION_TYPE = "CustomerPayBillOnline"

$env:AIRTEL_ENV = "production"
$env:AIRTEL_CLIENT_ID = "<Airtel client ID>"
$env:AIRTEL_CLIENT_SECRET = "<Airtel client secret>"
```

Use a unique, unpredictable callback token of at least 32 characters. M-Pesa's STK Push callback URL is generated from `PLAY_TICKETS_PUBLIC_URL` and this token. Treat the full callback URL as a secret and redact it from access logs. Register the corresponding Airtel callback URL in the Airtel merchant portal:

```text
https://api.example.com/api/v1/payments/callbacks/airtel/<callback-token>
```

`MPESA_TRANSACTION_TYPE` defaults to `CustomerPayBillOnline`; set it to `CustomerBuyGoodsOnline` when the approved Safaricom account is a till.

For provider sandbox testing, set `MPESA_ENV` and/or `AIRTEL_ENV` to `sandbox` while keeping `PLAY_TICKETS_PAYMENT_MODE` set to `live`; requests then go to the provider's sandbox rather than the local simulator. Use only provider-issued sandbox credentials. The `PLAY_TICKETS_PAYMENT_MODE=sandbox` flow remains separate and never contacts a payment provider.

The backend verifies M-Pesa callback reference, phone, amount, and receipt against the reservation. Airtel callback notifications are checked against Airtel's server-side transaction-status API. Repeated callbacks do not create duplicate bookings. If money is confirmed after a reservation hold expires, the reservation is not revived; the status endpoint flags it for organizer support/refund review.

Only whole-number KES ticket amounts are payable. Confirmed payments issue unique QR entry tickets, and the check-in endpoint rejects invalid, unpaid, and already-used tickets. Camera scanning requires a browser that supports `BarcodeDetector` and camera access; a USB QR scanner or manual ticket-code entry is also supported. Late payments are not used to revive expired reservations and still require organizer support/refund review. Admin access currently uses a shared token, not individual staff accounts. Run the Flask development server only for local testing; production deployment must use a supported WSGI server behind HTTPS. Complete provider onboarding, configure callback URLs, and test with provider sandbox accounts before enabling production collection.

### Local payment sandbox

Set sandbox mode and an admin token before starting the backend in the first PowerShell window:

```powershell
$env:PLAY_TICKETS_PAYMENT_MODE = "sandbox"
$env:PLAY_TICKETS_ADMIN_TOKEN = "use-a-long-private-random-value"
python app.py
```

Reservation requests must include `payment_provider` (`mpesa` or `airtel`) and a Kenyan mobile number in `payment_phone`. Amounts are recorded in whole Kenyan shillings (KES). Create an event with the admin-only API before opening its checkout page.

To simulate a successful or failed provider result, send an admin-authenticated request:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:5000/api/v1/sandbox/payments/PT-REFERENCE/result" `
  -Headers @{ Authorization = "Bearer $env:PLAY_TICKETS_ADMIN_TOKEN" } `
  -ContentType "application/json" `
  -Body '{"result":"success"}'
```

The local simulator does not contact either provider or move money. Set a random `PLAY_TICKETS_SANDBOX_CALLBACK_TOKEN` to enable the mock tunnel callback route. Its JSON body must contain the reservation's `payment_reference` and `result` (`success` or `failure`). This mock callback is not the payload format used by Safaricom or Airtel.

An HTTPS tunnel URL is temporary and works only while both the local Flask app and tunnel process are running. A production callback needs a deployed HTTPS service and provider-specific callback verification.

## Current state and next work

- The backend uses **Python**, **Flask**, and **PostgreSQL** in production, with SQLite available for local development.
- Event, reservation, payment, and ticket records are stored in the configured database.
- The backend checks and holds tickets together, so two buyers cannot both reserve the very last ticket at the same time.
- The launch market is Kenya and prices use Kenyan shillings (KES). M-Pesa Daraja STK Push and Airtel Africa collection requests are supported when configured; live transactions require approved provider accounts and production credentials.
- Add sign-in before allowing organizers to create events on a public website.

See `app.py` for API route comments and `database.py` for how the database and its tables work.
