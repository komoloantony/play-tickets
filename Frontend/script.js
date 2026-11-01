"use strict";

const API_BASE = window.PLAY_TICKETS_API_BASE ||
  (window.location.port === "8000" || window.location.protocol === "file:"
    ? "http://127.0.0.1:5000"
    : window.location.origin);

const featuredSection = document.querySelector("#featured");
const oldCard = featuredSection.querySelector(".event-card");
const oldDisclaimer = featuredSection.querySelector(".demo-disclaimer");
const eventList = document.createElement("div");
eventList.className = "event-list";
eventList.setAttribute("aria-live", "polite");
oldCard.replaceWith(eventList);
if (oldDisclaimer) oldDisclaimer.remove();

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function formatDate(value, options) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date to be announced";
  return new Intl.DateTimeFormat(undefined, options).format(date);
}

function createEventCard(event) {
  const card = makeElement("article", "event-card");
  const cover = makeElement("div", "event-cover");
  cover.append(makeElement("span", "cover-label", "THE SPOTLIGHT IS YOURS"));
  const coverTitle = makeElement("div", "cover-title");
  coverTitle.append(
    makeElement("span", "", "FIREBRAND"),
    makeElement("h3", "", event.name),
    makeElement("span", "cover-rule"),
    makeElement("p", "", "A STORY THAT MUST BE EXPERIENCED")
  );
  cover.append(coverTitle, makeElement("span", "cover-number", String(event.id).padStart(2, "0")));

  const information = makeElement("div", "event-information");
  const meta = makeElement("div", "event-meta");
  const ticketTypes = event.ticket_types || [];
  const available = ticketTypes.reduce((sum, type) => sum + Number(type.available || 0), 0);
  meta.append(
    makeElement("span", "event-tag", "LIVE EVENT"),
    makeElement("span", "availability", available > 0 ? `${available} tickets available` : "Sold out")
  );
  information.append(
    meta,
    makeElement("h3", "", event.name),
    makeElement("p", "event-description", event.description || "Event details will be announced by the organiser.")
  );

  const facts = makeElement("div", "event-facts");
  const dateFact = makeElement("div");
  dateFact.append(
    makeElement("span", "fact-icon", "◷"),
    makeElement("div")
  );
  dateFact.lastChild.append(
    makeElement("small", "", formatDate(event.starts_at, { dateStyle: "long" })),
    makeElement("p", "", formatDate(event.starts_at, { timeStyle: "short" }))
  );
  const venueFact = makeElement("div");
  venueFact.append(makeElement("span", "fact-icon", "⌖"), makeElement("div"));
  venueFact.lastChild.append(makeElement("small", "", "VENUE"), makeElement("p", "", event.venue));
  facts.append(dateFact, venueFact);

  const footer = makeElement("div", "event-card-footer");
  const priceInfo = makeElement("div");
  const firstTicket = ticketTypes[0];
  priceInfo.append(
    makeElement("small", "", "TICKET PRICE"),
    makeElement("p", "price", firstTicket ? `KSh ${firstTicket.price} per person` : "Unavailable")
  );
  const link = makeElement("a", "primary-button", "View event →");
  link.href = `event.html?id=${encodeURIComponent(event.id)}`;
  footer.append(priceInfo, link);
  information.append(facts, footer);
  card.append(cover, information);
  return card;
}

async function loadEvents() {
  try {
    const response = await fetch(`${API_BASE}/api/v1/events`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load events.");
    eventList.replaceChildren();
    if (!data.events || data.events.length === 0) {
      eventList.append(makeElement("p", "demo-disclaimer", "No events are available yet. Please check back soon."));
    } else {
      data.events.forEach((event) => eventList.append(createEventCard(event)));
    }
  } catch (error) {
    eventList.replaceChildren(makeElement(
      "p",
      "demo-disclaimer",
      `Couldn't reach the Play Tickets API (${error.message}). Start the backend and refresh this page.`
    ));
  }
}

loadEvents();
