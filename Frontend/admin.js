"use strict";

const ADMIN_API_BASE = window.PLAY_TICKETS_API_BASE ||
  (window.location.port === "8000" || window.location.port === "8001" ||
    window.location.protocol === "file:"
    ? "http://127.0.0.1:5000"
    : window.location.origin);

const accessForm = document.getElementById("admin-access");
const tokenInput = document.getElementById("admin-token");
const message = document.getElementById("admin-message");
const dashboard = document.getElementById("dashboard");
const eventPicker = document.getElementById("event-picker");
const ticketTypeForm = document.getElementById("ticket-type-form");
const ticketTypeEvent = document.getElementById("ticket-type-event");
const ticketTypeName = document.getElementById("ticket-type-name");
const ticketTypePrice = document.getElementById("ticket-type-price");
const ticketTypeCapacity = document.getElementById("ticket-type-capacity");
const addTicketTypeButton = document.getElementById("add-ticket-type");
const ticketTypeMessage = document.getElementById("ticket-type-message");
const metrics = document.getElementById("admin-metrics");
const rows = document.getElementById("reservation-rows");
const emptyReservations = document.getElementById("empty-reservations");
const reservationCount = document.getElementById("reservation-count");
const checkinForm = document.getElementById("ticket-checkin-form");
const ticketCodeInput = document.getElementById("ticket-code");
const checkinSubmit = document.getElementById("checkin-submit");
const checkinMessage = document.getElementById("checkin-message");
const cameraButton = document.getElementById("start-ticket-camera");
const cameraVideo = document.getElementById("ticket-scan-video");
let adminToken = "";
let eventData = [];
let cameraStream;
let cameraFrame;
let scanning = false;
let isStartingCamera = false;

function setMessage(text, kind = "") {
  message.textContent = text;
  message.className = `admin-message ${kind}`.trim();
}


async function adminRequest(path, options = {}) {
  const response = await fetch(`${ADMIN_API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${adminToken}`
    },
    cache: "no-store"
  });

  const responseText = await response.text();

  let data;
  try {
    data = JSON.parse(responseText);
  } catch {
    throw new Error(
      `API returned HTTP ${response.status}. Check the API URL: ${ADMIN_API_BASE}${path}`
    );
  }

  if (!response.ok) {
    throw new Error(data.error || `Request failed (${response.status}).`);
  }

  return data;
}



function setCheckinMessage(text, kind = "") {
  checkinMessage.textContent = text;
  checkinMessage.className = `checkin-message ${kind}`.trim();
}

function stopCameraScanner() {
  scanning = false;
  if (cameraFrame) window.cancelAnimationFrame(cameraFrame);
  if (cameraStream) {
    cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
  }
  cameraVideo.srcObject = null;
  cameraVideo.hidden = true;
  cameraButton.textContent = "Scan with camera";
}

async function scanWithCamera() {
  if (isStartingCamera) return;
  if (cameraStream) {
    stopCameraScanner();
    setCheckinMessage("Camera scanning stopped.");
    return;
  }
  if (!("BarcodeDetector" in window) || !navigator.mediaDevices?.getUserMedia) {
    setCheckinMessage(
      "Camera QR scanning is unavailable in this browser. Use a USB QR scanner or enter the printed ticket code.",
      "error"
    );
    return;
  }

  isStartingCamera = true;
  try {
    const formats = await window.BarcodeDetector.getSupportedFormats();
    if (!formats.includes("qr_code")) {
      setCheckinMessage(
        "This browser cannot read QR codes with its camera. Use a USB QR scanner or enter the printed ticket code.",
        "error"
      );
      return;
    }
    const detector = new window.BarcodeDetector({ formats: ["qr_code"] });
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false
    });
    cameraVideo.srcObject = cameraStream;
    cameraVideo.hidden = false;
    await cameraVideo.play();
    scanning = true;
    cameraButton.textContent = "Stop camera";
    setCheckinMessage("Point the camera at a ticket QR code.");

    const scanFrame = async () => {
      if (!scanning) return;
      try {
        const codes = await detector.detect(cameraVideo);
        if (codes.length > 0) {
          ticketCodeInput.value = codes[0].rawValue;
          stopCameraScanner();
          checkinForm.requestSubmit();
          return;
        }
      } catch (error) {
        stopCameraScanner();
        setCheckinMessage(`Camera scan failed: ${error.message}`, "error");
        return;
      }
      cameraFrame = window.requestAnimationFrame(scanFrame);
    };
    scanFrame();
  } catch (error) {
    stopCameraScanner();
    setCheckinMessage(`Could not start the camera: ${error.message}`, "error");
  } finally {
    isStartingCamera = false;
  }
}

function metric(label, value) {
  const card = document.createElement("article");
  card.className = "metric-card";
  const caption = document.createElement("span");
  caption.textContent = label;
  const total = document.createElement("strong");
  total.textContent = value;
  card.append(caption, total);
  return card;
}

function renderMetrics() {
  const ticketTypes = eventData.flatMap((event) => event.ticket_types || []);
  const sold = ticketTypes.reduce((sum, type) => sum + Number(type.sold || 0), 0);
  const pending = ticketTypes.reduce((sum, type) => sum + Number(type.pending || 0), 0);
  const available = ticketTypes.reduce((sum, type) => sum + Number(type.available || 0), 0);
  const revenue = ticketTypes.reduce((sum, type) => sum + Number(type.sold || 0) * Number(type.price || 0), 0);
  metrics.replaceChildren(
    metric("Events", String(eventData.length)),
    metric("Tickets sold", String(sold)),
    metric("Pending holds", String(pending)),
    metric("Confirmed sales · KSh", revenue.toLocaleString())
  );
  return available;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

async function loadReservations() {
  const eventId = eventPicker.value;
  if (!eventId) {
    rows.replaceChildren();
    emptyReservations.hidden = false;
    reservationCount.textContent = "";
    return;
  }
  const data = await adminRequest(`/api/v1/admin/events/${encodeURIComponent(eventId)}/reservations?limit=100`);
  rows.replaceChildren();
  emptyReservations.hidden = data.reservations.length > 0;
  data.reservations.forEach((reservation) => {
    const row = document.createElement("tr");
    const buyer = document.createElement("td");
    buyer.textContent = reservation.buyer_name;
    const buyerEmail = document.createElement("small");
    buyerEmail.textContent = reservation.buyer_email;
    buyer.append(buyerEmail);
    const ticket = document.createElement("td");
    ticket.textContent = `${reservation.quantity} × ${reservation.ticket_type}`;
    const payment = document.createElement("td");
    payment.textContent = `${reservation.payment_provider || "—"} · ${reservation.payment_phone || "—"}`;
    const amount = document.createElement("td");
    amount.textContent = `${reservation.payment_currency || "KES"} ${Number(reservation.payment_amount || 0).toLocaleString()}`;
    const status = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `status-pill ${reservation.status}`;
    badge.textContent = reservation.status;
    status.append(badge);
    const created = document.createElement("td");
    created.textContent = formatDate(reservation.created_at);
    row.append(buyer, ticket, payment, amount, status, created);
    rows.append(row);
  });
  reservationCount.textContent = `${data.reservations.length} of ${data.pagination.total} reservations · Showing the most recent 100`;
}

async function loadDashboard() {
  setMessage("Loading private event data…");
  try {
    const data = await adminRequest("/api/v1/admin/events");
    eventData = data.events || [];
    const selected = eventPicker.value;
    const selectedTicketEvent = ticketTypeEvent.value;
    eventPicker.replaceChildren();
    ticketTypeEvent.replaceChildren();
    eventData.forEach((event) => {
      const option = new Option(event.name, String(event.id));
      eventPicker.add(option);
      ticketTypeEvent.add(new Option(event.name, String(event.id)));
    });
    if (eventData.some((event) => String(event.id) === selected)) eventPicker.value = selected;
    if (eventData.some((event) => String(event.id) === selectedTicketEvent)) {
      ticketTypeEvent.value = selectedTicketEvent;
    } else {
      ticketTypeEvent.value = eventPicker.value;
    }
    ticketTypeEvent.disabled = eventData.length === 0;
    addTicketTypeButton.disabled = eventData.length === 0;
    const available = renderMetrics();
    dashboard.hidden = false;
    await loadReservations();
    setMessage(`Dashboard updated. ${available} tickets available across all events.`, "success");
  } catch (error) {
    if (error.message.includes("admin token")) {
      adminToken = "";
      dashboard.hidden = true;
    }
    setMessage(error.message, "error");
  }
}

accessForm.addEventListener("submit", (event) => {
  event.preventDefault();
  adminToken = tokenInput.value.trim();
  tokenInput.value = "";
  if (adminToken) loadDashboard();
});
eventPicker.addEventListener("change", () => loadReservations().catch((error) => setMessage(error.message, "error")));
ticketTypeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!ticketTypeForm.reportValidity() || !ticketTypeEvent.value) return;

  addTicketTypeButton.disabled = true;
  ticketTypeMessage.textContent = "Adding ticket category…";
  ticketTypeMessage.className = "ticket-type-message";
  try {
    const eventId = ticketTypeEvent.value;
    const eventName = eventData.find((item) => String(item.id) === eventId)?.name;
    const data = await adminRequest(
      `/api/v1/events/${encodeURIComponent(eventId)}/ticket-types`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: ticketTypeName.value.trim(),
          price: Number(ticketTypePrice.value),
          capacity: Number(ticketTypeCapacity.value)
        })
      }
    );
    ticketTypeMessage.textContent =
      `Added ${data.ticket_type.name} to ${eventName}. It will appear on the event page.`;
    ticketTypeMessage.className = "ticket-type-message success";
    await loadDashboard();
  } catch (error) {
    ticketTypeMessage.textContent = error.message;
    ticketTypeMessage.className = "ticket-type-message error";
  } finally {
    addTicketTypeButton.disabled = eventData.length === 0;
  }
});
document.getElementById("refresh-dashboard").addEventListener("click", loadDashboard);
checkinForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const ticketCode = ticketCodeInput.value.trim().toUpperCase();
  checkinSubmit.disabled = true;
  setCheckinMessage("Validating ticket…");
  try {
    const data = await adminRequest("/api/v1/admin/tickets/check-in", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticket_code: ticketCode })
    });
    setCheckinMessage(
      `Checked in: ${data.ticket.buyer_name} · ${data.ticket.ticket_type} · ${data.ticket.event_name}.`,
      "success"
    );
    ticketCodeInput.value = "";
    ticketCodeInput.focus();
  } catch (error) {
    setCheckinMessage(error.message, "error");
    ticketCodeInput.select();
  } finally {
    checkinSubmit.disabled = false;
  }
});
cameraButton.addEventListener("click", scanWithCamera);
window.addEventListener("pagehide", stopCameraScanner);
