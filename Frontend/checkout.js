"use strict";

const CHECKOUT_API_BASE = window.PLAY_TICKETS_API_BASE ||
  (window.location.port === "8000" || window.location.port === "8001" ||
    window.location.protocol === "file:"
    ? "http://127.0.0.1:5000"
    : window.location.origin);

const checkoutForm = document.getElementById("booking-form");
const checkoutResults = document.getElementById("ticket-results");
const providerPaymentFields = document.getElementById("provider-payment-fields");
const manualPaymentPanel = document.getElementById("manual-payment-panel");
const manualPaymentInstructions = document.getElementById("manual-payment-instructions");
const mpesaPaymentNumber = document.getElementById("mpesa-payment-number");
const copyPaymentNumberButton = document.getElementById("copy-payment-number");
const copyPaymentFeedback = document.getElementById("copy-payment-feedback");
const manualReceiptForm = document.getElementById("manual-receipt-form");
const manualTransactionCode = document.getElementById("manual-transaction-code");
const manualReceiptSubmit = document.getElementById("submit-manual-receipt");
const manualReceiptFeedback = document.getElementById("manual-receipt-feedback");
let checkoutEvent;
let pendingReservation;
let checkoutListenerAttached = false;
let paymentPollingTimer;
let paymentPollingInProgress = false;
let manualCheckoutEnabled = false;
let paymentOptions;

function showCheckoutResult(title, message, reference) {
  checkoutResults.replaceChildren();
  const heading = document.createElement("h2");
  heading.textContent = title;
  const copy = document.createElement("p");
  copy.textContent = message;
  checkoutResults.append(heading, copy);
  if (reference) {
    const referenceLine = document.createElement("p");
    referenceLine.textContent = `Payment reference: ${reference}`;
    checkoutResults.append(referenceLine);
  }
  checkoutResults.hidden = false;
  checkoutResults.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderIssuedTickets(tickets, reference) {
  const ticketList = document.createElement("div");
  ticketList.className = "ticket-list";
  if (!Array.isArray(tickets) || tickets.length === 0) {
    const unavailable = document.createElement("p");
    unavailable.textContent =
      `Payment is confirmed, but entry tickets could not be loaded. Contact the organizer and provide reservation reference ${reference}.`;
    ticketList.append(unavailable);
    checkoutResults.append(ticketList);
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
    ticketList.append(card);
  }
  checkoutResults.append(ticketList);
}

function addSandboxControls(reservation) {
  const note = document.createElement("p");
  note.textContent = "For local testing, choose a simulated payment result. The backend admin token is required.";
  const successButton = document.createElement("button");
  successButton.className = "primary-button";
  successButton.type = "button";
  successButton.textContent = "Simulate successful payment";
  successButton.addEventListener("click", () => simulatePayment("success"));
  const failureButton = document.createElement("button");
  failureButton.className = "text-button sandbox-failure-button";
  failureButton.type = "button";
  failureButton.textContent = "Simulate failed payment";
  failureButton.addEventListener("click", () => simulatePayment("failure"));
  checkoutResults.append(note, successButton, failureButton);
}

function startLivePaymentPolling(reservation) {
  window.clearInterval(paymentPollingTimer);
  const checkStatus = async () => {
    if (paymentPollingInProgress) return;
    paymentPollingInProgress = true;
    try {
      const response = await fetch(
        `${CHECKOUT_API_BASE}/api/v1/payments/${encodeURIComponent(reservation.payment_reference)}`
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not check the payment status.");

      if (data.manual_review_required) {
        reservation.payment_status = data.payment_status;
        showCheckoutResult(
          "Payment status uncertain — contact the organizer",
          "The request may have reached M-Pesa, but confirmation could not be retrieved. Do not retry until the organizer checks it.",
          reservation.payment_reference
        );
        window.clearInterval(paymentPollingTimer);
      } else if (data.payment_status === "succeeded" && data.reservation_status === "confirmed") {
        reservation.payment_status = data.payment_status;
        reservation.status = data.reservation_status;
        manualPaymentPanel.hidden = true;
        showCheckoutResult(
          "Payment confirmed",
          "Your payment is confirmed. Show each entry QR code at the door.",
          reservation.payment_reference
        );
        renderIssuedTickets(data.tickets, reservation.payment_reference);
        window.clearInterval(paymentPollingTimer);
      } else if (data.refund_or_support_required) {
        reservation.payment_status = data.payment_status;
        reservation.status = data.reservation_status;
        showCheckoutResult(
          "Payment received — contact the organizer",
          "The reservation hold expired before payment was confirmed. No tickets were reserved; contact the organizer about the payment.",
          reservation.payment_reference
        );
        window.clearInterval(paymentPollingTimer);
      } else if (data.payment_status === "failed") {
        reservation.payment_status = data.payment_status;
        reservation.status = data.reservation_status;
        showCheckoutResult(
          "Payment not completed",
          "The payment was declined or cancelled. The reservation hold has been released.",
          reservation.payment_reference
        );
        window.clearInterval(paymentPollingTimer);
        manualPaymentPanel.hidden = true;
        pendingReservation = null;
        checkoutForm.querySelector('button[type="submit"]').disabled = false;
      } else if (data.payment_status === "rejected") {
        showCheckoutResult(
          "Payment not approved",
          "The organizer rejected this payment submission and released the ticket hold. Contact the organizer if you believe this is a mistake.",
          reservation.payment_reference
        );
        window.clearInterval(paymentPollingTimer);
        manualPaymentPanel.hidden = true;
        checkoutForm.hidden = false;
        manualTransactionCode.disabled = false;
        pendingReservation = null;
        checkoutForm.querySelector('button[type="submit"]').disabled = false;
      } else if (data.reservation_status === "expired") {
        reservation.payment_status = data.payment_status;
        reservation.status = data.reservation_status;
        showCheckoutResult(
          "Reservation expired",
          "The payment window expired before payment was confirmed. Please create a new reservation.",
          reservation.payment_reference
        );
        window.clearInterval(paymentPollingTimer);
        manualPaymentPanel.hidden = true;
        checkoutForm.hidden = false;
        manualTransactionCode.disabled = false;
        pendingReservation = null;
        checkoutForm.querySelector('button[type="submit"]').disabled = false;
      } else if (manualCheckoutEnabled) {
        showCheckoutResult(
          data.payment_status === "submitted"
            ? "Payment code submitted — awaiting verification"
            : "Order reserved — awaiting payment",
          data.payment_status === "submitted"
            ? "The organizer is checking your M-Pesa transaction. Keep this page open; tickets appear here after approval."
            : "Pay using the Pochi la Biashara number above, then submit your M-Pesa transaction code.",
          reservation.payment_reference
        );
      }
    } catch (error) {
      showCheckoutResult(
        "Checking payment",
        "We could not refresh the payment status. Keep this page open; we’ll try again.",
        reservation.payment_reference
      );
    } finally {
      paymentPollingInProgress = false;
    }
  };

  checkStatus();
  paymentPollingTimer = window.setInterval(checkStatus, 8000);
}

async function createManualOrder() {
  const ticketTypeId = Number(document.getElementById("ticket-type").value);
  const quantity = Number(document.getElementById("ticket-quantity").value);
  const response = await fetch(
    `${CHECKOUT_API_BASE}/api/v1/events/${encodeURIComponent(checkoutEvent.id)}/orders`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        buyer_name: document.getElementById("buyer-name").value.trim(),
        buyer_email: document.getElementById("buyer-email").value.trim(),
        ticket_items: [{ ticket_type_id: ticketTypeId, quantity }]
      })
    }
  );
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Could not create the order.");

  pendingReservation = {
    ...data.order,
    payment_mode: "manual",
    payment_status: "pending",
    status: "pending"
  };
  const itemSummary = data.order.ticket_items
    .map((item) =>
      `${item.quantity} × ${item.ticket_type} (KSh ${Number(item.subtotal).toLocaleString("en-KE")})`
    )
    .join(" + ");
  manualPaymentInstructions.textContent =
    `${itemSummary}. Total: KSh ${Number(data.order.payment_amount).toLocaleString("en-KE")}. ` +
    `Pay the exact amount to ${paymentOptions.mpesa.method}. ` +
    `Enter your M-Pesa transaction code below. Keep reference ${data.order.payment_reference}.`;
  mpesaPaymentNumber.textContent = paymentOptions.mpesa.till_number;
  copyPaymentFeedback.textContent = "";
  manualReceiptForm.reset();
  manualTransactionCode.disabled = false;
  manualReceiptSubmit.disabled = false;
  manualReceiptFeedback.textContent = "";
  manualPaymentPanel.hidden = false;
  checkoutForm.hidden = true;
  const expiry = new Date(data.order.expires_at).toLocaleTimeString();
  showCheckoutResult(
    "Order reserved",
    `Your tickets are held until ${expiry}. Pay the total above and submit your M-Pesa transaction code.`,
    data.order.payment_reference
  );
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
    copyPaymentFeedback.textContent = `Copied ${number}.`;
  } catch {
    copyPaymentFeedback.textContent = "Could not copy. Select and copy the number above.";
  }
});

async function submitManualReceipt(event) {
  event.preventDefault();
  if (!pendingReservation || !manualReceiptForm.reportValidity()) return;

  manualReceiptSubmit.disabled = true;
  manualReceiptFeedback.textContent = "Submitting your payment code…";
  try {
    const response = await fetch(
      `${CHECKOUT_API_BASE}/api/v1/payments/${encodeURIComponent(pendingReservation.payment_reference)}/manual-receipt`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transaction_code: manualTransactionCode.value.trim() })
      }
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not submit the payment code.");

    pendingReservation.payment_status = data.payment_status;
    manualTransactionCode.disabled = true;
    manualReceiptFeedback.textContent = "Payment code submitted. The organizer will verify your payment.";
    startLivePaymentPolling(pendingReservation);
  } catch (error) {
    manualReceiptFeedback.textContent = error.message;
    manualReceiptSubmit.disabled = false;
  }
}

async function loadPaymentOptions() {
  const response = await fetch(`${CHECKOUT_API_BASE}/api/v1/payment-options`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Could not load payment options.");
  paymentOptions = data.payment_options;
  manualCheckoutEnabled = paymentOptions.mode === "manual";
  providerPaymentFields.hidden = manualCheckoutEnabled;
  for (const control of providerPaymentFields.querySelectorAll("select, input")) {
    control.disabled = manualCheckoutEnabled;
    control.required = !manualCheckoutEnabled;
  }
  if (manualCheckoutEnabled) {
    document.getElementById("checkout-instructions").textContent =
      "Select your ticket and quantity. Pay the displayed total to the Pochi la Biashara number, then submit your M-Pesa code.";
  }
  checkoutForm.querySelector('button[type="submit"]').disabled = false;
}

async function submitReservation(event) {
  event.preventDefault();
  if (!checkoutForm.reportValidity() || !checkoutEvent) return;

  const feedback = document.getElementById("payment-feedback");
  const submit = checkoutForm.querySelector('button[type="submit"]');
  const ticketTypeId = document.getElementById("ticket-type").value;
  const ticketType = checkoutEvent.ticket_types.find((ticket) => String(ticket.id) === ticketTypeId);
  if (!ticketType) {
    feedback.textContent = "Choose an available ticket type.";
    return;
  }

  submit.disabled = true;
  if (manualCheckoutEnabled) {
    feedback.textContent = "Creating your order…";
    try {
      await createManualOrder();
    } catch (error) {
      feedback.textContent = error.message;
      submit.disabled = false;
    }
    return;
  }
  feedback.textContent = "Creating your 15-minute reservation…";
  try {
    const response = await fetch(
      `${CHECKOUT_API_BASE}/api/v1/ticket-types/${ticketType.id}/reservations`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          buyer_name: document.getElementById("buyer-name").value.trim(),
          buyer_email: document.getElementById("buyer-email").value.trim(),
          quantity: Number(document.getElementById("ticket-quantity").value),
          payment_provider: document.getElementById("payment-provider").value,
          payment_phone: document.getElementById("payment-phone").value.trim()
        })
      }
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Reservation could not be created.");

    pendingReservation = data.reservation;
    if (pendingReservation.payment_mode === "live") {
      showCheckoutResult(
        pendingReservation.payment_status === "unknown"
          ? "Payment status uncertain"
          : "Waiting for payment",
        pendingReservation.payment_status === "unknown"
          ? data.message
          : `Payment request sent for KSh ${pendingReservation.payment_amount}. Approve it on your phone to confirm the reservation.`,
        pendingReservation.payment_reference
      );
      startLivePaymentPolling(pendingReservation);
      feedback.textContent = "Approve the payment request on your phone.";
    } else {
      const expiry = new Date(pendingReservation.expires_at).toLocaleTimeString();
      showCheckoutResult(
        "Reservation created",
        `${pendingReservation.quantity} ticket(s) are held until ${expiry}. Total: KSh ${pendingReservation.payment_amount}. Sandbox mode does not contact a payment provider or collect money.`,
        pendingReservation.payment_reference
      );
      addSandboxControls(pendingReservation);
      feedback.textContent = "Reservation is pending. Complete a payment to confirm it.";
    }
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    submit.disabled = Boolean(
      pendingReservation && pendingReservation.payment_mode === "live"
    );
  }
}

async function simulatePayment(result) {
  if (!pendingReservation) return;
  const adminToken = window.prompt("Enter the local sandbox admin token:");
  if (!adminToken) return;

  try {
    const response = await fetch(
      `${CHECKOUT_API_BASE}/api/v1/sandbox/payments/${encodeURIComponent(pendingReservation.payment_reference)}/result`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${adminToken}`
        },
        body: JSON.stringify({ result })
      }
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not simulate the payment.");
    showCheckoutResult(
      data.reservation_status === "confirmed" ? "Sandbox payment confirmed" : "Sandbox payment failed",
      data.reservation_status === "confirmed"
        ? "The reservation is confirmed in the local sandbox. Show each entry QR code at the door."
        : "The payment failed. The reservation remains pending until its 15-minute hold expires.",
      data.payment_reference
    );
    if (data.reservation_status === "confirmed") {
      const statusResponse = await fetch(
        `${CHECKOUT_API_BASE}/api/v1/payments/${encodeURIComponent(pendingReservation.payment_reference)}`
      );
      const statusData = await statusResponse.json();
      if (!statusResponse.ok) {
        throw new Error(statusData.error || "Could not load the issued tickets.");
      }
      renderIssuedTickets(statusData.tickets, pendingReservation.payment_reference);
    }
  } catch (error) {
    showCheckoutResult("Payment simulation error", error.message, pendingReservation.payment_reference);
  }
}

window.setupPlayTicketsCheckout = function setupPlayTicketsCheckout(event) {
  checkoutEvent = event;
  if (!checkoutListenerAttached) {
    checkoutForm.addEventListener("submit", submitReservation);
    manualReceiptForm.addEventListener("submit", submitManualReceipt);
    checkoutListenerAttached = true;
  }
  checkoutForm.querySelector('button[type="submit"]').disabled = true;
  loadPaymentOptions().catch((error) => {
    document.getElementById("booking-feedback").textContent = error.message;
    document.getElementById("book-event").disabled = true;
  });
};
