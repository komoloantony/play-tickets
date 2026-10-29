# Play Tickets Backend

This folder contains the Python backend for Play Tickets. The frontend is the part people see and click; a teammate is building that separately. The backend stores events and reservations. The API is the messenger between the frontend and backend, and JSON is the simple text format they use to send information to each other.

## What each file is for

- **`app.py`** — The Python app. It contains API routes for events, ticket reservations, and protected admin data.
- **`requirements.txt`** — The list of Python tools the app needs. Right now, that is Flask.
- **`database.py`** — The database helper file. It creates and connects to SQLite, builds the tables, and counts or expires ticket reservations.
- **`.gitignore`** — A list of local files Git should leave out when sending the project to GitHub, such as the local database and private settings.
- **`README.md`** — This guide. It explains how to start the app and how the frontend can talk to the API.
- **`play_tickets.db`** — The local database where the app saves events, ticket types, and reservations. The app creates this file when it starts; it is not included in GitHub.

## Run locally

1. Create and activate a Python virtual environment.
2. Install dependencies: `python -m pip install -r requirements.txt`.
3. Start the backend: `python app.py`.
4. Check that it is responding at `http://127.0.0.1:5000/api/v1/health`.

SQLite creates `play_tickets.db` automatically. It is a small local database that stores events and reservations. Git does not upload the local database or private settings. Admin routes and event creation require a shared secret token for development; this is not a full sign-in system, so the API is not ready to open publicly as-is.

## API for the frontend

All API routes use the `/api/v1` prefix and exchange JSON.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Check that the backend is available. |
| `GET` | `/api/v1/events` | List events, ticket types, prices, and available quantities. |
| `GET` | `/api/v1/events/<event_id>` | Get one event and its ticket types. |
| `POST` | `/api/v1/events` | Create an event and its first ticket type (admin token required). |
| `POST` | `/api/v1/ticket-types/<ticket_type_id>/reservations` | Hold tickets while the buyer completes payment. |
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
  "quantity": 2
}
```

A successful response is `201 Created` and includes a reservation ID, `status: "pending"`, and an `expires_at` timestamp. A pending reservation holds its tickets for 15 minutes. If inventory is insufficient, the API responds with `409 Conflict` and the remaining quantity. A reservation is not a confirmed payment or an entry-ready ticket.

## Current state and next work

- The backend uses **Python**, **Flask**, and **SQLite**.
- Event and ticket records are stored locally in SQLite.
- The backend checks and holds tickets together, so two buyers cannot both reserve the very last ticket at the same time.
- M-Pesa and Airtel Money are not connected yet. The launch country and payment provider setup must be chosen before implementing provider-specific payment requests and verified callbacks.
- Add sign-in before allowing organizers to create events on a public website.

See `app.py` for API route comments and `database.py` for how the database and its tables work.
