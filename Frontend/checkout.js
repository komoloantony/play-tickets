"use strict";

const CHECKOUT_API_BASE = window.PLAY_TICKETS_API_BASE ||
  (window.location.port === "8000" || window.location.port === "8001" ||
    window.location.protocol === "file:"
    ? "http://127.0.0.1:5000"
    : window.location.origin);

const checkoutForm = document.getElementById("booking-form");
const checkoutResults = document.getElementById("ticket-results");
let checkoutEvent;
let pendingReservation;
let checkoutListenerAttached = false;
let paymentPollingTimer;
let paymentPollingInProgress = false;

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
        pendingReservation = null;
        checkoutForm.querySelector('button[type="submit"]').disabled = false;
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
    checkoutListenerAttached = true;
  }
};
