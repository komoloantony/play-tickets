"use strict";

const bookButton = document.getElementById("book-event");
const bookingForm = document.getElementById("booking-form");
const bookingFeedback = document.getElementById("booking-feedback");
const paymentFeedback = document.getElementById("payment-feedback");

const quantityInput = document.getElementById("ticket-quantity");
const phoneInput = document.getElementById("mpesa-phone");

const attendeeFields = document.getElementById("attendee-fields");
const ticketResults = document.getElementById("ticket-results");
const ticketList = document.getElementById("ticket-list");

function createAttendeeFields() {
  const quantity = Number(quantityInput.value);

  attendeeFields.replaceChildren();

  for (let i = 0; i < quantity; i++) {
    const section = document.createElement("div");
    section.className = "attendee-field-group";

    const heading = document.createElement("h4");
    heading.textContent = `Attendee ${i + 1}`;

    const nameLabel = document.createElement("label");
    nameLabel.textContent = "Full name";
    nameLabel.htmlFor = `attendee-name-${i}`;

    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.id = `attendee-name-${i}`;
    nameInput.name = `attendeeName${i}`;
    nameInput.placeholder = "Enter full name";
    nameInput.autocomplete = "name";
    nameInput.required = true;

    const emailLabel = document.createElement("label");
    emailLabel.textContent = "Email address";
    emailLabel.htmlFor = `attendee-email-${i}`;

    const emailInput = document.createElement("input");
    emailInput.type = "email";
    emailInput.id = `attendee-email-${i}`;
    emailInput.name = `attendeeEmail${i}`;
    emailInput.placeholder = "person@example.com";
    emailInput.autocomplete = "email";
    emailInput.required = true;

    section.append(
      heading,
      nameLabel,
      nameInput,
      emailLabel,
      emailInput
    );

    attendeeFields.appendChild(section);
  }
}

bookButton.addEventListener("click", () => {
  bookingForm.hidden = false;
  createAttendeeFields();

  bookingForm.scrollIntoView({
    behavior: "smooth",
    block: "center"
  });

  bookingFeedback.textContent = "";
});

quantityInput.addEventListener("change", createAttendeeFields);

bookingForm.addEventListener("submit", (event) => {
  event.preventDefault();

  if (!bookingForm.reportValidity()) return;

  const quantity = Number(quantityInput.value);
  const phone = phoneInput.value.trim();

  if (!/^(07|01)\d{8}$/.test(phone)) {
    paymentFeedback.textContent =
      "Enter a valid Kenyan phone number, e.g. 0712345678.";
    return;
  }

  if (typeof QRCode === "undefined") {
    paymentFeedback.textContent =
      "QR library not loaded. Check your internet connection and refresh.";
    return;
  }

  const attendees = [];

  for (let i = 0; i < quantity; i++) {
    const name = document.getElementById(`attendee-name-${i}`).value.trim();
    const email = document.getElementById(`attendee-email-${i}`).value.trim();

    attendees.push({ name, email });
  }

  ticketList.replaceChildren();
  ticketResults.hidden = false;

  attendees.forEach((attendee) => {
    const ticketId = "FB-" + crypto.randomUUID().toUpperCase();

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
    nameLine.textContent = "Attendee: " + attendee.name;

    const emailLine = document.createElement("p");
    emailLine.textContent = "Email: " + attendee.email;

    const qrContainer = document.createElement("div");
    qrContainer.className = "ticket-qr";

    new QRCode(qrContainer, {
      text: ticketId,
      width: 180,
      height: 180,
      correctLevel: QRCode.CorrectLevel.M
    });

    const downloadButton = document.createElement("button");
    downloadButton.className = "ticket-download";
    downloadButton.type = "button";
    downloadButton.textContent = "Download ticket";

    downloadButton.addEventListener("click", () => {
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

      const escapeHtml = (value) =>
        value.replace(/[&<>"']/g, (char) => ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;"
        })[char]);

      const ticketHtml = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Firebrand Play Ticket</title>
<style>
body{font-family:Arial,sans-serif;background:#090909;color:#fff;padding:24px}
.ticket{max-width:420px;margin:20px auto;padding:24px;border:2px solid #d8b45a;text-align:center}
h1{color:#f3d98b}
img{width:200px;height:200px;background:#fff;padding:8px}
p{overflow-wrap:anywhere}
.warning{color:#e5c97e;font-weight:bold;font-size:12px}
@media print{body{padding:0}.ticket{margin:auto}}
</style>
</head>
<body>
<div class="ticket">
<h1>FIREBRAND PLAY</h1>
<p>FIREBRAND TICKETS</p>
<p><strong>Attendee:</strong> ${escapeHtml(attendee.name)}</p>
<p><strong>Email:</strong> ${escapeHtml(attendee.email)}</p>
<p><strong>Ticket ID:</strong> ${ticketId}</p>
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
      link.download = ticketId + ".html";

      document.body.appendChild(link);
      link.click();
      link.remove();

      setTimeout(() => URL.revokeObjectURL(fileUrl), 1000);
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
  });

  paymentFeedback.textContent =
    "Demo tickets created. Payment has not been requested or verified.";

  ticketResults.scrollIntoView({
    behavior: "smooth",
    block: "start"
  });
});

createAttendeeFields();
