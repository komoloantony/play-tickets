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
const providerPaymentFields = document.getElementById("provider-payment-fields");
const manualPaymentFields = document.getElementById("manual-payment-fields");
const manualTicketQuantities = document.getElementById("manual-ticket-quantities");
const manualOrderTotal = document.getElementById("manual-order-total");
const manualPaymentPanel = document.getElementById("manual-payment-panel");
const manualPaymentInstructions = document.getElementById("manual-payment-instructions");
const mpesaPaymentNumber = document.getElementById("mpesa-payment-number");
const copyPaymentNumberButton = document.getElementById("copy-payment-number");
const copyPaymentFeedback = document.getElementById("copy-payment-feedback");
const manualOrderExpiry = document.getElementById("manual-order-expiry");
const manualReceiptForm = document.getElementById("manual-receipt-form");
const manualReceiptFeedback = document.getElementById("manual-receipt-feedback");
const manualReceiptSubmit = document.getElementById("submit-manual-receipt");
const manualTransactionCode = document.getElementById("manual-transaction-code");

let eventRecord;
let paymentOptions;
let manualCheckoutEnabled = false;
let paymentReference;
let pollingTimer;
let pollingInProgress = false;

async function parseJsonResponse(response) {
  const body = await response.text();
  try {
    return JSON.parse(body);
  } catch (error) {
    if (!(error instanceof SyntaxError)) {
      throw error;
    }
    throw new Error(
      `The ticket API returned HTTP ${response.status} ${response.statusText} instead of JSON. ` +
      "Set window.PLAY_TICKETS_API_BASE to the deployed Flask API URL."
    );
  }
}

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
    const buyerName = document.createElement("p");
    buyerName.textContent = `Ticket holder: ${ticket.buyer_name}`;
    const qrContainer = document.createElement("div");
    qrContainer.className = "ticket-qr";
    const qrImage = document.createElement("img");
    qrImage.alt = `Entry QR code for ticket ${ticket.ticket_number}`;
    qrImage.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(ticket.qr_svg)}`;
    qrContainer.append(qrImage);
    const code = document.createElement("p");
    code.textContent = `Ticket code: ${ticket.ticket_code}`;
    const download = document.createElement("a");
    download.className = "ticket-download";
    download.href = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(ticket.ticket_svg)}`;
    download.download = `behind-the-sins-ticket-${ticket.ticket_number}.svg`;
    download.textContent = "Download printable ticket";
    card.append(heading, eventName, buyerName, qrContainer, code, download);
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

function updateManualOrderTotal() {
  const inputs = manualTicketQuantities.querySelectorAll("input[data-ticket-type-id]");
  let quantity = 0;
  let total = 0;
  const itemSummaries = [];
  for (const input of inputs) {
    const selectedQuantity = Number(input.value);
    if (!Number.isInteger(selectedQuantity) || selectedQuantity < 1) continue;
    const ticket = eventRecord.ticket_types.find(
      (item) => String(item.id) === input.dataset.ticketTypeId
    );
    if (!ticket) continue;
    quantity += selectedQuantity;
    const subtotal = selectedQuantity * Number(ticket.price);
    total += subtotal;
    itemSummaries.push(
      `${selectedQuantity} × ${ticket.name}: KSh ${subtotal.toLocaleString("en-KE")}`
    );
  }
  manualOrderTotal.textContent = quantity
    ? `${itemSummaries.join(" + ")} · Total: KSh ${total.toLocaleString("en-KE")}`
    : "Select at least one ticket.";
  submitButton.disabled = quantity < 1 || quantity > 10;
}

function renderManualTicketQuantities() {
  manualTicketQuantities.replaceChildren();
  for (const ticket of eventRecord.ticket_types) {
    const row = document.createElement("label");
    row.className = "manual-ticket-row";
    const description = document.createElement("span");
    description.textContent =
      `${ticket.name} · KSh ${Number(ticket.price).toLocaleString("en-KE")} · ${ticket.available} available`;
    const quantity = document.createElement("input");
    quantity.type = "number";
    quantity.min = "0";
    quantity.max = String(Math.min(10, Number(ticket.available)));
    quantity.step = "1";
    quantity.value = "0";
    quantity.dataset.ticketTypeId = String(ticket.id);
    quantity.disabled = Number(ticket.available) < 1;
    quantity.addEventListener("input", updateManualOrderTotal);
    row.append(description, quantity);
    manualTicketQuantities.append(row);
  }
  updateManualOrderTotal();
}

function configureManualCheckout() {
  providerPaymentFields.hidden = manualCheckoutEnabled;
  for (const control of providerPaymentFields.querySelectorAll("select, input")) {
    control.disabled = manualCheckoutEnabled;
    control.required = !manualCheckoutEnabled;
  }
  manualPaymentFields.hidden = !manualCheckoutEnabled;
  if (manualCheckoutEnabled) {
    renderManualTicketQuantities();
    document.getElementById("checkout-instructions").textContent =
      "Select Regular, VIP, or both. Your ticket quantities will be held for 15 minutes while you pay.";
  }
}

async function loadEvent() {
  try {
    const [response, optionsResponse] = await Promise.all([
      fetch(`${API_BASE}/api/v1/events`),
      fetch(`${API_BASE}/api/v1/payment-options`)
    ]);
    const data = await getJsonResponse(response);
    const optionsData = await getJsonResponse(optionsResponse);
    paymentOptions = optionsData.payment_options;
    manualCheckoutEnabled = paymentOptions.mode === "manual";
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
    configureManualCheckout();
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
  const data = await parseJsonResponse(response);
  if (!response.ok) {
    throw new Error(data.error || "The payment request could not be completed.");
  }
  return data;
}

async function createManualOrder() {
  const ticketItems = [...manualTicketQuantities.querySelectorAll("input[data-ticket-type-id]")]
    .map((input) => ({
      ticket_type_id: Number(input.dataset.ticketTypeId),
      quantity: Number(input.value)
    }))
    .filter((item) => item.quantity > 0);
  if (!ticketItems.length) {
    throw new Error("Choose at least one ticket.");
  }

  const response = await fetch(`${API_BASE}/api/v1/events/${eventRecord.id}/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      buyer_name: document.getElementById("buyer-name").value.trim(),
      buyer_email: document.getElementById("buyer-email").value.trim(),
      ticket_items: ticketItems
    })
  });
  const data = await getJsonResponse(response);
  paymentReference = data.order.payment_reference;
  issuedTickets.replaceChildren();
  manualReceiptForm.reset();
  manualTransactionCode.disabled = false;
  manualReceiptForm.hidden = false;
  manualReceiptSubmit.disabled = false;
  manualReceiptFeedback.textContent = "";
  copyPaymentFeedback.textContent = "";
  const itemSummary = data.order.ticket_items
    .map((item) =>
      `${item.quantity} × ${item.ticket_type} (KSh ${Number(item.subtotal).toLocaleString("en-KE")})`
    )
    .join(" + ");
  manualPaymentInstructions.textContent =
    `${itemSummary}. Total: KSh ${Number(data.order.payment_amount).toLocaleString("en-KE")}. ` +
    `Pay the exact amount to ${paymentOptions.mpesa.method}. ` +
    `Enter your M-Pesa transaction code below. Keep reference ${paymentReference}.`;
  mpesaPaymentNumber.textContent = paymentOptions.mpesa.till_number;
  manualOrderExpiry.textContent =
    `Your tickets are held until ${new Date(data.order.expires_at).toLocaleTimeString()}.`;
  manualPaymentPanel.hidden = false;
  bookingForm.hidden = true;
  showPaymentStatus(
    "Order reserved",
    "Complete the M-Pesa payment, then submit the transaction code below. Tickets are issued only after the organizer verifies payment.",
    paymentReference
  );
  submitButton.disabled = true;
  pollingTimer = window.setInterval(checkPaymentStatus, PAYMENT_POLL_INTERVAL_MS);
  await checkPaymentStatus();
}

copyPaymentNumberButton.addEventListener("click", async () => {
  const number = mpesaPaymentNumber.textContent.trim();
  if (!number) {
    copyPaymentFeedback.textContent = "The payment number is unavailable. Contact the organizer.";
    return;
  }
  if (!navigator.clipboard || !navigator.clipboard.writeText) {
    copyPaymentFeedback.textContent = "Copy is unavailable here. Select and copy the number above.";
    return;
  }
  try {
    await navigator.clipboard.writeText(number);
    copyPaymentFeedback.textContent = "Copied to clipboard.";
  } catch {
    copyPaymentFeedback.textContent = "Could not copy. Select and copy the number above.";
  }
});

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
      if (manualCheckoutEnabled) manualReceiptForm.hidden = true;
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
    } else if (data.payment_status === "rejected") {
      showPaymentStatus(
        "Payment not approved",
        "The organizer rejected this payment submission and released the ticket hold. Contact the organizer if you believe this is a mistake.",
        paymentReference
      );
      window.clearInterval(pollingTimer);
      manualPaymentPanel.hidden = true;
      bookingForm.hidden = false;
      manualTransactionCode.disabled = false;
      submitButton.disabled = false;
      paymentReference = null;
    } else if (data.reservation_status === "expired") {
      showPaymentStatus(
        "Reservation expired",
        "The payment window expired before payment was confirmed. Please start a new reservation.",
        paymentReference
      );
      window.clearInterval(pollingTimer);
      manualPaymentPanel.hidden = true;
      bookingForm.hidden = false;
      manualTransactionCode.disabled = false;
      submitButton.disabled = false;
      paymentReference = null;
    } else if (manualCheckoutEnabled) {
      showPaymentStatus(
        data.payment_status === "submitted"
          ? "Payment code submitted — awaiting verification"
          : "Order reserved — awaiting payment",
        data.payment_status === "submitted"
          ? "The organizer is checking your M-Pesa transaction. Keep this page open; tickets appear here after approval."
          : "Pay using the Pochi la Biashara number above, then submit your M-Pesa transaction code.",
        paymentReference,
        false
      );
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
  if (manualCheckoutEnabled) {
    try {
      await createManualOrder();
    } catch (error) {
      paymentFeedback.textContent = error.message;
      submitButton.disabled = false;
    }
    return;
  }

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
    const data = await parseJsonResponse(response);
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

manualReceiptForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!manualReceiptForm.reportValidity() || !paymentReference) return;
  manualReceiptSubmit.disabled = true;
  manualReceiptFeedback.textContent = "Submitting transaction code…";
  try {
    const response = await fetch(
      `${API_BASE}/api/v1/payments/${encodeURIComponent(paymentReference)}/manual-receipt`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transaction_code: manualTransactionCode.value.trim()
        })
      }
    );
    const data = await getJsonResponse(response);
    manualReceiptFeedback.textContent =
      `Code received for ${data.payment_reference}. Waiting for organizer verification.`;
    manualTransactionCode.disabled = true;
    await checkPaymentStatus();
  } catch (error) {
    manualReceiptFeedback.textContent = error.message;
    manualReceiptSubmit.disabled = false;
  }
});

loadEvent();
