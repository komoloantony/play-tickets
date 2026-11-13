"use strict";

const EVENT_API_BASE = window.PLAY_TICKETS_API_BASE ||
  (window.location.protocol === "file:" ||
    window.location.port === "8000" ||
    window.location.port === "8001"
    ? "http://127.0.0.1:5000"
    : window.location.origin);

const eventName = document.getElementById("event-name");
const eventDescription = document.getElementById("event-description");
const eventDate = document.getElementById("event-date");
const eventVenue = document.getElementById("event-venue");
const ticketPrices = document.getElementById("ticket-prices");
const ticketTypeSelect = document.getElementById("ticket-type");
const quantitySelect = document.getElementById("ticket-quantity");
const bookButton = document.getElementById("book-event");
const bookingForm = document.getElementById("booking-form");
const bookingFeedback = document.getElementById("booking-feedback");
const submitButton = bookingForm.querySelector('button[type="submit"]');

let eventRecord;

function updateQuantityOptions() {
  const selectedTicket = eventRecord.ticket_types.find(
    (ticket) => String(ticket.id) === ticketTypeSelect.value
  );
  const maximum = selectedTicket
    ? Math.min(10, Number(selectedTicket.available))
    : 0;
  quantitySelect.replaceChildren();
  for (let quantity = 1; quantity <= maximum; quantity += 1) {
    quantitySelect.add(new Option(
      `${quantity} ${quantity === 1 ? "ticket" : "tickets"}`,
      String(quantity)
    ));
  }
  quantitySelect.disabled = maximum === 0;
  submitButton.disabled = maximum === 0;
}

function renderTicketTypes() {
  ticketPrices.replaceChildren();
  ticketTypeSelect.replaceChildren();
  for (const ticket of eventRecord.ticket_types) {
    const priceCard = document.createElement("div");
    priceCard.className = "ticket-price-option";
    const name = document.createElement("span");
    name.textContent = `${ticket.name} · ${ticket.available} available`;
    const price = document.createElement("strong");
    price.textContent = `KSh ${Number(ticket.price).toLocaleString("en-KE")}`;
    priceCard.append(name, price);
    ticketPrices.appendChild(priceCard);

    const option = new Option(
      `${ticket.name} — KSh ${Number(ticket.price).toLocaleString("en-KE")}`,
      String(ticket.id)
    );
    option.disabled = Number(ticket.available) < 1;
    ticketTypeSelect.add(option);
  }

  ticketTypeSelect.disabled = eventRecord.ticket_types.length === 0;
  const firstAvailable = eventRecord.ticket_types.find(
    (ticket) => Number(ticket.available) > 0
  );
  if (firstAvailable) ticketTypeSelect.value = String(firstAvailable.id);
  updateQuantityOptions();
}

async function loadEvent() {
  const eventId = new URLSearchParams(window.location.search).get("id");
  if (!eventId || !/^\d+$/.test(eventId)) {
    bookingFeedback.textContent = "Choose an event from the events page.";
    return;
  }

  try {
    const response = await fetch(
      `${EVENT_API_BASE}/api/v1/events/${encodeURIComponent(eventId)}`
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load this event.");
    eventRecord = data.event;
    eventName.textContent = eventRecord.name;
    document.title = `${eventRecord.name} | Firebrand Tickets`;
    document.getElementById("poster-title").textContent = eventRecord.name;
    eventDescription.textContent =
      eventRecord.description || "Event details will be announced by the organiser.";
    const startsAt = new Date(eventRecord.starts_at);
    eventDate.textContent = Number.isNaN(startsAt.getTime())
      ? eventRecord.starts_at
      : new Intl.DateTimeFormat(undefined, {
        dateStyle: "full",
        timeStyle: "short"
      }).format(startsAt);
    eventVenue.textContent = eventRecord.venue;
    renderTicketTypes();
    bookButton.disabled = eventRecord.ticket_types.every(
      (ticket) => Number(ticket.available) < 1
    );
    if (bookButton.disabled) bookingFeedback.textContent = "All tickets are sold out.";
    window.setupPlayTicketsCheckout(eventRecord);
  } catch (error) {
    bookingFeedback.textContent = error.message;
    bookButton.disabled = true;
    submitButton.disabled = true;
    ticketPrices.textContent = error.message;
  }
}

bookButton.addEventListener("click", () => {
  bookingForm.hidden = false;
  bookingForm.scrollIntoView({ behavior: "smooth", block: "center" });
  bookingFeedback.textContent = "";
});

ticketTypeSelect.addEventListener("change", updateQuantityOptions);
loadEvent();
