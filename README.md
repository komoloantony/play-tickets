# Play Tickets Backend

This folder contains the Python backend for Play Tickets. The frontend is being developed separately and should call this app through its JSON API.

## Run locally

1. Create and activate a Python virtual environment.
2. Install dependencies: `python -m pip install -r requirements.txt`.
3. Start the backend: `python app.py`.
4. Check that it is responding at `http://127.0.0.1:5000/api/v1/health`.

SQLite creates `play_tickets.db` automatically. The database and local secrets are excluded from Git. This is a development setup; the API does not have user authentication yet.

## API for the frontend

All API routes use the `/api/v1` prefix and exchange JSON.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Check that the backend is available. |
| `GET` | `/api/v1/events` | List events, ticket types, prices, and available quantities. |
| `GET` | `/api/v1/events/<event_id>` | Get one event and its ticket types. |
| `POST` | `/api/v1/events` | Create an event and its first ticket type. |
| `POST` | `/api/v1/ticket-types/<ticket_type_id>/reservations` | Hold tickets while the buyer completes payment. |

### Create an event

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
- Availability is checked inside a database write transaction to prevent two simultaneous reservations from overselling the same tickets.
- M-Pesa and Airtel Money are not connected yet. The launch country and payment provider setup must be chosen before implementing provider-specific payment requests and verified callbacks.
- Add authentication before exposing event creation or other organizer operations publicly.

See `app.py` for route-level comments and the reservation flow.
