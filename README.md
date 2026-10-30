# Play Tickets Backend

This folder contains the Python backend and the partner-built static frontend for Play Tickets. The backend stores events and reservations. The API is the messenger between the frontend and backend, and JSON is the simple text format they use to send information to each other.

## What each file is for

- **`app.py`** — The Python app. It contains API routes for events, ticket reservations, and protected admin data.
- **`requirements.txt`** — The list of Python tools the app needs. Right now, that is Flask.
- **`database.py`** — The database helper file. It creates and connects to SQLite, builds the tables, and counts or expires ticket reservations.
- **`.gitignore`** — A list of local files Git should leave out when sending the project to GitHub, such as the local database and private settings.
- **`README.md`** — This guide. It explains how to start the app and how the frontend can talk to the API.
- **`Frontend/`** — The partner's standalone HTML, CSS, and JavaScript website, including the event listing and event detail pages.
- **`play_tickets.db`** — The local database where the app saves events, ticket types, and reservations. The app creates this file when it starts; it is not included in GitHub.

## Run locally

1. Create and activate a Python virtual environment.
2. Install dependencies: `python -m pip install -r requirements.txt`.
3. Start the backend: `python app.py`.
4. Check that it is responding at `http://127.0.0.1:5000/api/v1/health`.

SQLite creates `play_tickets.db` automatically. It is a small local database that stores events and reservations. Git does not upload the local database or private settings. Admin routes and event creation require a shared secret token for development; this is not a full sign-in system, so the API is not ready to open publicly as-is.

## Frontend

The partner frontend lives in `Frontend/`: `index.html` loads events from the API and `event.html` creates ticket reservations. From the project root, start the frontend's local web server in a second PowerShell window:

```powershell
Set-Location Frontend
python -m http.server 8000
```

Then open `http://127.0.0.1:8000/` in a browser. The backend allows browser requests from `localhost:8000` and `127.0.0.1:8000` by default. Set `PLAY_TICKETS_CORS_ORIGINS` to a comma-separated list to use other trusted frontend origins. Create an event with the admin API before booking it.

Checkout behavior is in `Frontend/checkout.js`; `Frontend/event.js` loads and displays event details. Checkout creates a 15-minute reservation and shows its payment reference and amount. In sandbox mode, the page offers success and failure buttons; each asks for the local admin token and calls the protected simulation endpoint. Sandbox payments do not contact M-Pesa or Airtel, collect money, or issue entry tickets or QR codes.

## API for the frontend

All API routes use the `/api/v1` prefix and exchange JSON.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Check that the backend is available. |
| `GET` | `/api/v1/events` | List events, ticket types, prices, and available quantities. |
| `GET` | `/api/v1/events/<event_id>` | Get one event and its ticket types. |
| `POST` | `/api/v1/events` | Create an event and its first ticket type (admin token required). |
| `POST` | `/api/v1/ticket-types/<ticket_type_id>/reservations` | Hold tickets while the buyer completes payment. |
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

A successful response is `201 Created` and includes a reservation ID, `status: "pending"`, and an `expires_at` timestamp. A pending reservation holds its tickets for 15 minutes. If inventory is insufficient, the API responds with `409 Conflict` and the remaining quantity. A reservation is not a confirmed payment or an entry-ready ticket.

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

The local simulator does not contact either provider or move money. Set a random `PLAY_TICKETS_SANDBOX_CALLBACK_TOKEN` to enable the tunnel callback route. Its JSON body must contain the reservation's `payment_reference` and `result` (`success` or `failure`). This mock callback is not the payload format used by Safaricom or Airtel.

An HTTPS tunnel URL is temporary and works only while both the local Flask app and tunnel process are running. A production callback needs a deployed HTTPS service and provider-specific callback verification.

## Current state and next work

- The backend uses **Python**, **Flask**, and **SQLite**.
- Event and ticket records are stored locally in SQLite.
- The backend checks and holds tickets together, so two buyers cannot both reserve the very last ticket at the same time.
- The launch market is Kenya and prices use Kenyan shillings (KES). The local sandbox can simulate M-Pesa and Airtel payment results, but it does not contact the providers or move money. Provider payment requests and verified callbacks still need implementation.
- Add sign-in before allowing organizers to create events on a public website.

See `app.py` for API route comments and `database.py` for how the database and its tables work.
