"use strict";

const ADMIN_API_BASE = window.PLAY_TICKETS_API_BASE ||
  (window.location.port === "8000" || window.location.protocol === "file:"
    ? "http://127.0.0.1:5000"
    : window.location.origin);

const accessForm = document.getElementById("admin-access");
const tokenInput = document.getElementById("admin-token");
const message = document.getElementById("admin-message");
const dashboard = document.getElementById("dashboard");
const eventPicker = document.getElementById("event-picker");
const metrics = document.getElementById("admin-metrics");
const rows = document.getElementById("reservation-rows");
const emptyReservations = document.getElementById("empty-reservations");
const reservationCount = document.getElementById("reservation-count");
let adminToken = "";
let eventData = [];

function setMessage(text, kind = "") {
  message.textContent = text;
  message.className = `admin-message ${kind}`.trim();
}

async function adminRequest(path) {
  const response = await fetch(`${ADMIN_API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${adminToken}` },
    cache: "no-store"
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function metric(label, value) {
  const card = document.createElement("article");
  card.className = "metric-card";
  const caption = document.createElement("span");
  caption.textContent = label;
  const total = document.createElement("strong");
  total.textContent = value;
  card.append(caption, total);
  return card;
}

function renderMetrics() {
  const ticketTypes = eventData.flatMap((event) => event.ticket_types || []);
  const sold = ticketTypes.reduce((sum, type) => sum + Number(type.sold || 0), 0);
  const pending = ticketTypes.reduce((sum, type) => sum + Number(type.pending || 0), 0);
  const available = ticketTypes.reduce((sum, type) => sum + Number(type.available || 0), 0);
  const revenue = ticketTypes.reduce((sum, type) => sum + Number(type.sold || 0) * Number(type.price || 0), 0);
  metrics.replaceChildren(
    metric("Events", String(eventData.length)),
    metric("Tickets sold", String(sold)),
    metric("Pending holds", String(pending)),
    metric("Confirmed sales · KSh", revenue.toLocaleString())
  );
  return available;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

async function loadReservations() {
  const eventId = eventPicker.value;
  if (!eventId) {
    rows.replaceChildren();
    emptyReservations.hidden = false;
    reservationCount.textContent = "";
    return;
  }
  const data = await adminRequest(`/api/v1/admin/events/${encodeURIComponent(eventId)}/reservations?limit=100`);
  rows.replaceChildren();
  emptyReservations.hidden = data.reservations.length > 0;
  data.reservations.forEach((reservation) => {
    const row = document.createElement("tr");
    const buyer = document.createElement("td");
    buyer.textContent = reservation.buyer_name;
    const buyerEmail = document.createElement("small");
    buyerEmail.textContent = reservation.buyer_email;
    buyer.append(buyerEmail);
    const ticket = document.createElement("td");
    ticket.textContent = `${reservation.quantity} × ${reservation.ticket_type}`;
    const payment = document.createElement("td");
    payment.textContent = `${reservation.payment_provider || "—"} · ${reservation.payment_phone || "—"}`;
    const amount = document.createElement("td");
    amount.textContent = `${reservation.payment_currency || "KES"} ${Number(reservation.payment_amount || 0).toLocaleString()}`;
    const status = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `status-pill ${reservation.status}`;
    badge.textContent = reservation.status;
    status.append(badge);
    const created = document.createElement("td");
    created.textContent = formatDate(reservation.created_at);
    row.append(buyer, ticket, payment, amount, status, created);
    rows.append(row);
  });
  reservationCount.textContent = `${data.reservations.length} of ${data.pagination.total} reservations · Showing the most recent 100`;
}

async function loadDashboard() {
  setMessage("Loading private event data…");
  try {
    const data = await adminRequest("/api/v1/admin/events");
    eventData = data.events || [];
    const selected = eventPicker.value;
    eventPicker.replaceChildren();
    eventData.forEach((event) => {
      const option = new Option(event.name, String(event.id));
      eventPicker.add(option);
    });
    if (eventData.some((event) => String(event.id) === selected)) eventPicker.value = selected;
    const available = renderMetrics();
    dashboard.hidden = false;
    await loadReservations();
    setMessage(`Dashboard updated. ${available} tickets available across all events.`, "success");
  } catch (error) {
    if (error.message.includes("admin token")) {
      adminToken = "";
      dashboard.hidden = true;
    }
    setMessage(error.message, "error");
  }
}

accessForm.addEventListener("submit", (event) => {
  event.preventDefault();
  adminToken = tokenInput.value.trim();
  tokenInput.value = "";
  if (adminToken) loadDashboard();
});
eventPicker.addEventListener("change", () => loadReservations().catch((error) => setMessage(error.message, "error")));
document.getElementById("refresh-dashboard").addEventListener("click", loadDashboard);
