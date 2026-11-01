
"use strict";

// Booking form elements
const bookButton = document.getElementById("book-event");
const bookingForm = document.getElementById("booking-form");
const bookingFeedback = document.getElementById("booking-feedback");
const paymentFeedback = document.getElementById("payment-feedback");

// Purchaser details
const nameInput = document.getElementById("attendee-name");
const emailInput = document.getElementById("attendee-email");
const quantityInput = document.getElementById("ticket-quantity");
const phoneInput = document.getElementById("mpesa-phone");

// Ticket results
const ticketResults = document.getElementById("ticket-results");
const ticketList = document.getElementById("ticket-list");

// Open booking form
bookButton.addEventListener("click", () => {
  bookingForm.hidden = false;

  bookingForm.scrollIntoView({
    behavior: "smooth",
    block: "center"
  });

  bookingFeedback.textContent = "";
  paymentFeedback.textContent = "";
});

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

// Generate individual downloadable ticket
function downloadTicket(ticket) {
  const canvas = ticket.qrContainer.querySelector("canvas");
  const image = ticket.qrContainer.querySelector("img");

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
<p><strong>Purchaser:</strong> ${escapeHtml(ticket.name)}</p>
<p><strong>Email:</strong> ${escapeHtml(ticket.email)}</p>
<p><strong>Ticket ID:</strong> ${ticket.id}</p>
<img src="${qrData}" alt="Ticket QR code">
<p class="warning">DEMO TICKET — NOT VALID FOR ENTRY</p>
<p>Event date, venue and price to be confirmed.</p>
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

// Handle booking
bookingForm.addEventListener("submit", event => {
  event.preventDefault();

  if (!bookingForm.reportValidity()) return;

  const purchaserName = nameInput.value.trim();
  const purchaserEmail = emailInput.value.trim();
  const quantity = Number(quantityInput.value);
  const phone = phoneInput.value.trim();

  if (!/^(07|01)\d{8}$/.test(phone)) {
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

  // Clear previous tickets
  ticketList.replaceChildren();
  ticketResults.hidden = false;

  // Generate one unique ticket for each quantity
  for (let i = 0; i < quantity; i++) {

    const ticketId = "FB-" + crypto.randomUUID().toUpperCase();

    const ticket = {
      id: ticketId,
      name: purchaserName,
      email: purchaserEmail
    };

    const card = document.createElement("article");
    card.className = "ticket-card";

    const title = document.createElement("h3");
    title.textContent = "FIREBRAND PLAY";

    const warning = document.createElement("p");
    warning.className = "demo-warning";
    warning.textContent = "DEMO TICKET — NOT VALID FOR ENTRY";

    const idLine = document.createElement("p");
    idLine.textContent = "Ticket ID: " + ticketId;

    const nameLine = document.createElement("p");
    nameLine.textContent = "Purchaser: " + purchaserName;

    const emailLine = document.createElement("p");
    emailLine.textContent = "Email: " + purchaserEmail;

    const qrContainer = document.createElement("div");
    qrContainer.className = "ticket-qr";

    new QRCode(qrContainer, {
      text: ticketId,
      width: 180,
      height: 180,
      correctLevel: QRCode.CorrectLevel.M
    });

    ticket.qrContainer = qrContainer;

    const downloadButton = document.createElement("button");
    downloadButton.className = "ticket-download";
    downloadButton.type = "button";
    downloadButton.textContent = "Download ticket";

    downloadButton.addEventListener("click", () => {
      downloadTicket(ticket);
    });

    card.append(
      title,
      warning,
      idLine,
      nameLine,
      emailLine,
      qrContainer,
      downloadButton
    );

    ticketList.appendChild(card);
  }

  paymentFeedback.textContent =
    "Demo tickets created. Payment has not been requested or verified.";

  ticketResults.scrollIntoView({
    behavior: "smooth",
    block: "start"
  });
});
