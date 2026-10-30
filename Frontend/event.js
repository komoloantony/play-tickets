"use strict";

const API_BASE = window.PLAY_TICKETS_API_BASE ||
  (window.location.port === "8000" || window.location.protocol === "file:"
    ? "http://127.0.0.1:5000"
    : window.location.origin);

const params = new URLSearchParams(window.location.search);
const requestedEventId = params.get("id");
const bookButton = document.getElementById("book-event");
const bookingForm = document.getElementById("booking-form");
const bookingFeedback = document.getElementById("booking-feedback");
const attendeeHeading = document.querySelector(".event-detail-content h2");
const intro = document.querySelector(".event-detail-intro");
const detailRows = [...document.querySelectorAll(".detail-list > div")];
const detailNote = document.querySelector(".event-detail-note");

let selectedEvent;

function makeLabel(text, controlId) {
  const label = document.createElement("label");
  label.htmlFor = controlId;
  label.textContent = text;
  return label;
}

function makeInput(id, type, placeholder, autocomplete) {
  const input = document.createElement("input");
  input.id = id;
  input.name = id;
  input.type = type;
  input.placeholder = placeholder;
  input.required = true;
  if (autocomplete) input.autocomplete = autocomplete;
  return input;
}

function buildBookingForm() {
  bookingForm.replaceChildren();
  const title = document.createElement("h3");
  title.textContent = "Reserve your tickets";
  const description = document.createElement("p");
  description.textContent = "Choose your ticket and payment method. No real payment will be taken in sandbox mode.";

  const buyerName = makeInput("buyer-name", "text", "Enter your full name", "name");
  const buyerEmail = makeInput("buyer-email", "email", "you@example.com", "email");
  bookingForm.append(
    title,
    description,
    makeLabel("Full name", buyerName.id),
    buyerName,
    makeLabel("Email address", buyerEmail.id),
    buyerEmail
  );

  const ticketType = document.createElement("select");
  ticketType.id = "ticket-type";
  ticketType.required = true;
  bookingForm.append(makeLabel("Ticket type", ticketType.id), ticketType);

  const quantity = document.createElement("select");
  quantity.id = "ticket-quantity";
  quantity.required = true;
  bookingForm.append(makeLabel("Number of tickets", quantity.id), quantity);

  const provider = document.createElement("select");
  provider.id = "payment-provider";
  provider.required = true;
  provider.append(new Option("M-Pesa", "mpesa"), new Option("Airtel Money", "airtel"));
  bookingForm.append(makeLabel("Payment method", provider.id), provider);

  const phone = makeInput("payment-phone", "tel", "e.g. 0712345678", "tel-national");
  phone.pattern = "(?:07|01)[0-9]{8}";
  phone.maxLength = 10;
  bookingForm.append(makeLabel("Kenyan mobile number", phone.id), phone);

  const submit = document.createElement("button");
  submit.className = "primary-button";
  submit.type = "submit";
  submit.textContent = "Create reservation";
  bookingForm.append(submit);

  const feedback = document.createElement("p");
  feedback.id = "payment-feedback";
  feedback.className = "booking-feedback";
  feedback.setAttribute("role", "status");
  bookingForm.append(feedback);

  ticketType.addEventListener("change", populateQuantities);
}

function formatDate(value, options) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "To be announced";
  return new Intl.DateTimeFormat(undefined, options).format(date);
}

function populateQuantities() {
  const ticketTypes = selectedEvent?.ticket_types || [];
  const type = ticketTypes.find(
    (ticket) => String(ticket.id) === document.getElementById("ticket-type").value
  );
  const quantity = document.getElementById("ticket-quantity");
  quantity.replaceChildren();
  const available = Math.max(0, Math.min(10, Number(type?.available || 0)));
  for (let count = 1; count <= available; count += 1) {
    quantity.add(new Option(`${count} ticket${count === 1 ? "" : "s"}`, String(count)));
  }
  document.getElementById("payment-feedback").textContent = available
    ? `KSh ${type.price} each · ${type.available} available`
    : "This ticket type is sold out.";
  bookingForm.querySelector('button[type="submit"]').disabled = available === 0;
}

function renderEvent(event) {
  selectedEvent = event;
  attendeeHeading.textContent = event.name;
  intro.textContent = event.description || "Event details will be announced by the organiser.";
  if (detailRows[1]) detailRows[1].querySelector("strong").textContent = formatDate(event.starts_at, { dateStyle: "full" });
  if (detailRows[2]) detailRows[2].querySelector("strong").textContent = formatDate(event.starts_at, { timeStyle: "short" });
  if (detailRows[3]) detailRows[3].querySelector("strong").textContent = event.venue;
  const firstType = event.ticket_types?.[0];
  if (detailRows[4]) detailRows[4].querySelector("strong").textContent = firstType ? `KSh ${firstType.price} per person` : "Unavailable";
  detailNote.textContent = "A reservation holds tickets for 15 minutes. Sandbox checkout does not collect real money or issue entry tickets.";

  const typeSelect = document.getElementById("ticket-type");
  typeSelect.replaceChildren();
  (event.ticket_types || []).forEach((type) => {
    const option = new Option(`${type.name} — KSh ${type.price} (${type.available} available)`, String(type.id));
    option.disabled = Number(type.available) < 1;
    typeSelect.add(option);
  });
  const firstAvailable = (event.ticket_types || []).findIndex((type) => Number(type.available) > 0);
  if (firstAvailable >= 0) typeSelect.selectedIndex = firstAvailable;
  populateQuantities();
  bookButton.disabled = firstAvailable < 0;
  if (bookButton.disabled) bookingFeedback.textContent = "Tickets are currently unavailable.";
  window.setupPlayTicketsCheckout(event);
}

async function loadEvent() {
  try {
    let event;
    if (requestedEventId) {
      const response = await fetch(`${API_BASE}/api/v1/events/${encodeURIComponent(requestedEventId)}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Event not found.");
      event = data.event;
    } else {
      const response = await fetch(`${API_BASE}/api/v1/events`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not load events.");
      event = data.events?.[0];
    }
    if (!event) throw new Error("There are no events available to book yet.");
    renderEvent(event);
  } catch (error) {
    bookButton.disabled = true;
    bookingFeedback.textContent = `Couldn't load this event (${error.message}). Return to the event list or start the backend.`;
  }
}

buildBookingForm();
bookButton.disabled = true;
bookButton.addEventListener("click", () => {
  bookingForm.hidden = false;
  bookingForm.scrollIntoView({ behavior: "smooth", block: "center" });
  bookingFeedback.textContent = "";
});

loadEvent();
