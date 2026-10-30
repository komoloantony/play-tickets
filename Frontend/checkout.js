"use strict";

const CHECKOUT_API_BASE = window.PLAY_TICKETS_API_BASE ||
  (window.location.port === "8000" || window.location.protocol === "file:"
    ? "http://127.0.0.1:5000"
    : window.location.origin);

const checkoutForm = document.getElementById("booking-form");
const checkoutResults = document.getElementById("ticket-results");
let checkoutEvent;
let pendingReservation;
let checkoutListenerAttached = false;

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
    const expiry = new Date(pendingReservation.expires_at).toLocaleTimeString();
    showCheckoutResult(
      "Reservation created",
      `${pendingReservation.quantity} ticket(s) are held until ${expiry}. Total: KSh ${pendingReservation.payment_amount}. Sandbox mode does not contact a payment provider or collect money.`,
      pendingReservation.payment_reference
    );
    if (pendingReservation.payment_mode === "sandbox") addSandboxControls(pendingReservation);
    feedback.textContent = "Reservation is pending. Complete a payment to confirm it.";
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    submit.disabled = false;
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
        ? "The reservation is confirmed in the local sandbox. This prototype does not issue entry tickets or QR codes."
        : "The payment failed. The reservation remains pending until its 15-minute hold expires.",
      data.payment_reference
    );
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
