"use strict";

const API_BASE = window.PLAY_TICKETS_API_BASE ||
  (window.location.port === "8000" || window.location.port === "8001" ||
    window.location.protocol === "file:"
    ? "http://127.0.0.1:5000"
    : window.location.origin);

const availabilityLabel = document.getElementById("featured-availability");
const eventName = document.getElementById("featured-event-name");
const eventDescription = document.getElementById("featured-event-description");
const eventDateLabel = document.getElementById("featured-event-date-label");
const eventDate = document.getElementById("featured-event-date");
const eventVenue = document.getElementById("featured-event-venue");
const ticketPrices = document.getElementById("featured-ticket-prices");
const eventLink = document.getElementById("featured-event-link");
const eventMessage = document.getElementById("featured-event-message");

function formatEventDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "Date to be announced";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "long",
    timeStyle: "short"
  }).format(date);
}

function renderTicketPrices(ticketTypes) {
  ticketPrices.replaceChildren();
  const heading = document.createElement("small");
  heading.textContent = "LIVE TICKET OPTIONS";
  ticketPrices.append(heading);

  for (const ticket of ticketTypes) {
    const option = document.createElement("p");
    const available = Number(ticket.available);
    option.textContent = `${ticket.name}: KSh ${Number(ticket.price).toLocaleString("en-KE")} · ${
      available > 0
        ? `${available} of ${ticket.capacity} available`
        : "Sold out"
    }`;
    ticketPrices.append(option);
  }
}

async function loadFeaturedEvent() {
  try {
    const response = await fetch(`${API_BASE}/api/v1/events`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load live event details.");

    const event = (data.events || []).find(
      (item) => item.name.trim().toLowerCase() === "behind the sins"
    );
    if (!event) throw new Error("Ticket sales for this event are not set up yet.");

    const ticketTypes = event.ticket_types || [];
    const available = ticketTypes.reduce(
      (total, ticket) => total + Number(ticket.available || 0),
      0
    );
    availabilityLabel.textContent = available > 0
      ? `${available} tickets available`
      : "Sold out";
    eventName.textContent = event.name;
    eventDescription.textContent =
      event.description || "Event details will be announced by the organizer.";
    eventDateLabel.textContent = "DATE AND TIME";
    eventDate.textContent = formatEventDate(event.starts_at);
    eventVenue.textContent = event.venue || "Venue to be announced";
    renderTicketPrices(ticketTypes);
    eventLink.href = `event.html?id=${encodeURIComponent(event.id)}`;
    eventMessage.textContent = "";
  } catch (error) {
    eventMessage.textContent =
      `Live prices and availability could not be loaded: ${error.message}`;
    ticketPrices.replaceChildren();
    const heading = document.createElement("small");
    const message = document.createElement("p");
    heading.textContent = "LIVE TICKET OPTIONS";
    message.textContent = "Open the event page to retry loading current ticket availability.";
    ticketPrices.append(heading, message);
  }
}

loadFeaturedEvent();
