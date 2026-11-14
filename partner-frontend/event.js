"use strict";

const API_BASE = window.PLAY_TICKETS_API_BASE ||
  (window.location.protocol === "file:" ||
    window.location.port === "8000" ||
    window.location.port === "8001"
    ? "http://127.0.0.1:5000"
    : window.location.origin);
const PAYMENT_POLL_INTERVAL_MS = 8000;

const bookButton = document.getElementById("book-event");
const bookingForm = document.getElementById("booking-form");
const bookingFeedback = document.getElementById("booking-feedback");
const paymentFeedback = document.getElementById("payment-feedback");
const paymentResults = document.getElementById("payment-results");
const paymentResultTitle = document.getElementById("payment-result-title");
const paymentResultMessage = document.getElementById("payment-result-message");
const paymentResultReference = document.getElementById("payment-result-reference");
const issuedTickets = document.getElementById("issued-tickets");
const ticketPrices = document.getElementById("ticket-prices");
const eventDescription = document.getElementById("event-description");
const eventDate = document.getElementById("event-date");
const eventVenue = document.getElementById("event-venue");
const ticketTypeSelect = document.getElementById("ticket-type");
const quantitySelect = document.getElementById("ticket-quantity");
const submitButton = bookingForm.querySelector('button[type="submit"]');

let eventRecord;
let paymentReference;
let pollingTimer;
let pollingInProgress = false;

function showPaymentStatus(title, message, reference, shouldScroll = true) {
  paymentResultTitle.textContent = title;
  paymentResultMessage.textContent = message;
  paymentResultReference.textContent = reference
    ? `Reservation reference: ${reference}`
    : "";
  paymentResults.hidden = false;
  if (shouldScroll) {
    paymentResults.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderIssuedTickets(tickets, reference) {
  issuedTickets.replaceChildren();
  if (!Array.isArray(tickets) || tickets.length === 0) {
    const unavailable = document.createElement("p");
    unavailable.textContent =
      `Payment is confirmed, but entry tickets could not be loaded. Contact the organizer and provide reservation reference ${reference}.`;
    issuedTickets.append(unavailable);
    return;
  }

  for (const ticket of tickets) {
    const card = document.createElement("article");
    card.className = "ticket-card";
    const heading = document.createElement("h3");
    heading.textContent = `${ticket.ticket_type} · Ticket ${ticket.ticket_number}`;
    const eventName = document.createElement("p");
    eventName.textContent = ticket.event_name;
    const qrContainer = document.createElement("div");
    qrContainer.className = "ticket-qr";
    const qrImage = document.createElement("img");
    qrImage.alt = `Entry QR code for ticket ${ticket.ticket_number}`;
    qrImage.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(ticket.qr_svg)}`;
    qrContainer.append(qrImage);
    const code = document.createElement("p");
    code.textContent = `Ticket code: ${ticket.ticket_code}`;
    card.append(heading, eventName, qrContainer, code);
    issuedTickets.append(card);
  }
}

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
  if (maximum === 0 && selectedTicket) {
    paymentFeedback.textContent = "This ticket type is sold out.";
  } else {
    paymentFeedback.textContent = "";
  }
}

function renderTicketTypes() {
  ticketPrices.replaceChildren();
  ticketTypeSelect.replaceChildren();

  for (const ticket of eventRecord.ticket_types) {
    const priceCard = document.createElement("div");
    priceCard.className = "ticket-price-option";
    if (ticket.name.toLowerCase().includes("vip")) {
      priceCard.classList.add("ticket-price-vip");
    }

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
  if (firstAvailable) {
    ticketTypeSelect.value = String(firstAvailable.id);
  }
  updateQuantityOptions();
}

async function loadEvent() {
  try {
    const response = await fetch(`${API_BASE}/api/v1/events`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Ticket sales are unavailable.");
    }
    eventRecord = (data.events || []).find(
      (item) => item.name.trim().toLowerCase() === "behind the sins"
    );
    if (!eventRecord) {
      throw new Error("Ticket sales for this event are not set up yet.");
    }

    eventDescription.textContent =
      eventRecord.description || "Event details will be announced by the organizer.";
    const startsAt = new Date(eventRecord.starts_at);
    eventDate.textContent = Number.isNaN(startsAt.getTime())
      ? eventRecord.starts_at
      : new Intl.DateTimeFormat(undefined, {
        dateStyle: "full",
        timeStyle: "short"
      }).format(startsAt);
    eventVenue.textContent = eventRecord.venue;
    renderTicketTypes();
    if (eventRecord.ticket_types.length === 0) {
      throw new Error("Ticket types have not been set up for this event.");
    }
    bookButton.disabled = eventRecord.ticket_types.every(
      (ticket) => Number(ticket.available) < 1
    );
    bookingFeedback.textContent = bookButton.disabled
      ? "All tickets are sold out."
      : "";
  } catch (error) {
    bookingFeedback.textContent = window.location.protocol === "file:"
      ? "Event details and prices are shown above. To load live availability and buy tickets, start the Play Tickets servers and open http://127.0.0.1:8000/event.html."
      : error.message;
    bookButton.disabled = true;
    submitButton.disabled = true;
  }
}

async function getJsonResponse(response) {
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "The payment request could not be completed.");
  }
  return data;
}

async function checkPaymentStatus() {
  if (!paymentReference || pollingInProgress) return;
  pollingInProgress = true;
  try {
    const response = await fetch(
      `${API_BASE}/api/v1/payments/${encodeURIComponent(paymentReference)}`
    );
    const data = await getJsonResponse(response);
    if (data.manual_review_required) {
      showPaymentStatus(
        "Payment status uncertain — contact the organizer",
        "The request may have reached M-Pesa, but confirmation could not be retrieved. Do not retry until the organizer checks it.",
        paymentReference
      );
      window.clearInterval(pollingTimer);
      submitButton.disabled = true;
    } else if (data.payment_status === "succeeded" && data.reservation_status === "confirmed") {
      showPaymentStatus(
        "Payment confirmed",
        "Your payment is confirmed. Show each entry QR code at the door.",
        paymentReference
      );
      renderIssuedTickets(data.tickets, paymentReference);
      window.clearInterval(pollingTimer);
      submitButton.disabled = true;
    } else if (data.refund_or_support_required) {
      showPaymentStatus(
        "Payment received — contact the organizer",
        "The reservation hold expired before payment was confirmed. No tickets were reserved; contact the organizer about the payment.",
        paymentReference
      );
      window.clearInterval(pollingTimer);
      submitButton.disabled = true;
    } else if (data.payment_status === "failed") {
      showPaymentStatus(
        "Payment not completed",
        "The payment was declined or cancelled. The reservation hold has been released.",
        paymentReference
      );
      window.clearInterval(pollingTimer);
      submitButton.disabled = false;
      paymentReference = null;
    } else if (data.reservation_status === "expired") {
      showPaymentStatus(
        "Reservation expired",
        "The payment window expired before payment was confirmed. Please start a new reservation.",
        paymentReference
      );
      window.clearInterval(pollingTimer);
      submitButton.disabled = false;
      paymentReference = null;
    } else {
      showPaymentStatus(
        "Waiting for payment",
        "Approve the payment request on your phone. Keep this page open while we verify it.",
        paymentReference,
        false
      );
    }
  } catch (error) {
    showPaymentStatus(
      "Checking payment",
      "We could not refresh the payment status. We’ll keep trying; do not submit another payment yet.",
      paymentReference,
      false
    );
  } finally {
    pollingInProgress = false;
  }
}

bookButton.addEventListener("click", () => {
  bookingForm.hidden = false;
  bookingForm.scrollIntoView({ behavior: "smooth", block: "center" });
  bookingFeedback.textContent = "";
});

ticketTypeSelect.addEventListener("change", updateQuantityOptions);

bookingForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!bookingForm.reportValidity() || !eventRecord || paymentReference) return;

  submitButton.disabled = true;
  paymentFeedback.textContent = "Creating your reservation and sending the payment request…";
  try {
    const response = await fetch(
      `${API_BASE}/api/v1/ticket-types/${encodeURIComponent(ticketTypeSelect.value)}/reservations`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          buyer_name: document.getElementById("buyer-name").value.trim(),
          buyer_email: document.getElementById("buyer-email").value.trim(),
          quantity: Number(quantitySelect.value),
          payment_provider: document.getElementById("payment-provider").value,
          payment_phone: document.getElementById("payment-phone").value.trim()
        })
      }
    );
    const data = await response.json();
    if (!response.ok && response.status !== 202) {
      throw new Error(data.error || "The payment request could not be started.");
    }
    if (!data.reservation) {
      throw new Error(data.error || "The payment provider returned no reservation.");
    }

    paymentReference = data.reservation.payment_reference;
    issuedTickets.replaceChildren();
    paymentFeedback.textContent = data.message;
    showPaymentStatus(
      data.reservation.payment_status === "unknown"
        ? "Payment status uncertain"
        : "Waiting for payment",
      data.reservation.payment_status === "unknown"
        ? data.message
        : `Payment request sent for KSh ${Number(data.reservation.payment_amount).toLocaleString("en-KE")}. Approve it on your phone.`,
      paymentReference
    );
    submitButton.disabled = true;
    pollingTimer = window.setInterval(checkPaymentStatus, PAYMENT_POLL_INTERVAL_MS);
    await checkPaymentStatus();
  } catch (error) {
    paymentFeedback.textContent = error.message;
    submitButton.disabled = false;
  }
});

loadEvent();
