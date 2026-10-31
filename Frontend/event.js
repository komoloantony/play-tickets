
"use strict";

// Booking form elements
const bookButton = document.getElementById("book-event");
const bookingForm = document.getElementById("booking-form");
const bookingFeedback = document.getElementById("booking-feedback");
const paymentFeedback = document.getElementById("payment-feedback");

const nameInput = document.getElementById("attendee-name");
const emailInput = document.getElementById("attendee-email");
const quantityInput = document.getElementById("ticket-quantity");
const phoneInput = document.getElementById("mpesa-phone");

const ticketResults = document.getElementById("ticket-results");
const ticketList = document.getElementById("ticket-list");

const TICKET_PRICE = 100;

// Open booking form
bookButton.addEventListener("click", () => {
  bookingForm.hidden = false;

  bookingForm.scrollIntoView({
    behavior: "smooth",
    block: "center"
  });

  bookingFeedback.textContent = "";
});

// Generate unique IDs
function generateTicketId() {
  return "FB-" + crypto.randomUUID().toUpperCase();
}

// Protect ticket text from HTML injection
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[char]);
}

// Generate one downloadable ticket
function downloadTicket(ticket) {
  const qrContainer = ticket.qrContainer;

  const canvas = qrContainer.querySelector("canvas");
  const image = qrContainer.querySelector("img");

  let qrData;

  if (canvas) {
    qrData = canvas.toDataURL("image/png");
  } else if (image) {
    qrData = image.src;
  } else {
    alert("QR code is not ready. Please try again.");
    return;
  }

  const ticketHtml = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Firebrand Play Ticket</title>

<style>
body {
  font-family: Arial, sans-serif;
  background: #090909;
  color: #fff;
  padding: 24px;
}

.ticket {
  max-width: 420px;
  margin: 20px auto;
  padding: 24px;
  border: 2px solid #d8b45a;
  text-align: center;
}

h1 {
  color: #f3d98b;
}

img {
  width: 200px;
  height: 200px;
  background: #fff;
  padding: 8px;
}

p {
  overflow-wrap: anywhere;
}

.warning {
  color: #e5c97e;
  font-weight: bold;
  font-size: 12px;
}

@media print {
  body { padding: 0; }
  .ticket { margin: auto; }
}
</style>
</head>

<body>
<div class="ticket">

  <h1>FIREBRAND PLAY</h1>
  <p>FIREBRAND TICKETS</p>

  <p><strong>Purchaser:</strong>
  ${escapeHtml(ticket.purchaserName)}</p>

  <p><strong>Ticket ID:</strong>
  ${escapeHtml(ticket.id)}</p>

  <p><strong>Date:</strong> Sunday, 13 December 2026</p>
  <p><strong>Time:</strong> 3:00 PM</p>
  <p><strong>Venue:</strong> Jesus Is Alive Ministries</p>
  <p><strong>Price:</strong> KSh ${TICKET_PRICE}</p>

  <img src="${qrData}" alt="Unique ticket QR code">

  <p class="warning">
    DEMO TICKET — NOT VALID FOR ENTRY
  </p>

</div>
</body>
</html>`;

  const file = new Blob([ticketHtml], {
    type: "text/html;charset=utf-8"
  });

  const fileUrl = URL.createObjectURL(file);
  const link = document.createElement("a");

  link.href = fileUrl;
  link.download = ticket.id + ".html";

  document.body.appendChild(link);
  link.click();
  link.remove();

  setTimeout(() => URL.revokeObjectURL(fileUrl), 1000);
}

// Create individual tickets for one purchaser
function generateTickets(purchaser, quantity) {
  ticketList.replaceChildren();

  for (let i = 0; i < quantity; i++) {

    const ticket = {
      id: generateTicketId(),
      purchaserName: purchaser.name,
      qrContainer: null
    };

    const card = document.createElement("article");
    card.className = "ticket-card";

    const title = document.createElement("h3");
    title.textContent = "FIREBRAND PLAY";

    const warning = document.createElement("p");
    warning.className = "demo-warning";
    warning.textContent = "DEMO TICKET — NOT VALID FOR ENTRY";

    const numberLine = document.createElement("p");
    numberLine.textContent = "Ticket " + (i + 1) + " of " + quantity;

    const idLine = document.createElement("p");
    idLine.textContent = "Ticket ID: " + ticket.id;

    const purchaserLine = document.createElement("p");
    purchaserLine.textContent =
      "Purchased by: " + purchaser.name;

    const qrContainer = document.createElement("div");
    qrContainer.className = "ticket-qr";

    ticket.qrContainer = qrContainer;

    new QRCode(qrContainer, {
      text: ticket.id,
      width: 180,
      height: 180,
      correctLevel: QRCode.CorrectLevel.M
    });

    const downloadButton = document.createElement("button");
    downloadButton.className = "ticket-download";
    downloadButton.type = "button";
    downloadButton.textContent = "Download Ticket";

    downloadButton.addEventListener("click", () => {
      downloadTicket(ticket);
    });

    card.append(
      title,
      warning,
      numberLine,
      idLine,
      purchaserLine,
      qrContainer,
      downloadButton
    );

    ticketList.appendChild(card);
  }

  ticketResults.hidden = false;

  ticketResults.scrollIntoView({
    behavior: "smooth",
    block: "start"
  });
}

// Process booking form
bookingForm.addEventListener("submit", event => {
  event.preventDefault();

  if (!bookingForm.reportValidity()) return;

  const purchaser = {
    name: nameInput.value.trim(),
    email: emailInput.value.trim(),
    phone: phoneInput.value.trim()
  };

  const quantity = Number(quantityInput.value);

  if (!/^(07|01)\d{8}$/.test(purchaser.phone)) {
    paymentFeedback.textContent =
      "Enter a valid Kenyan phone number, e.g. 0712345678.";
    return;
  }

  if (!Number.isInteger(quantity) || quantity < 1 || quantity > 5) {
    paymentFeedback.textContent =
      "Please select between 1 and 5 tickets.";
    return;
  }

  if (typeof QRCode === "undefined") {
    paymentFeedback.textContent =
      "QR library not loaded. Check your internet connection and refresh.";
    return;
  }

  const total = quantity * TICKET_PRICE;

  paymentFeedback.textContent =
    `Booking total: KSh ${total}. Generating ${quantity} individual demo tickets...`;

  generateTickets(purchaser, quantity);

  paymentFeedback.textContent =
    `Demo booking prepared for ${quantity} ticket(s). Total: KSh ${total}. Payment has NOT been requested or verified.`;
});
