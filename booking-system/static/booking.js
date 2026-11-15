
const bookingForm = document.getElementById("bookingForm");
const quantityInput = document.getElementById("quantity");
const totalAmount = document.getElementById("totalAmount");
const result = document.getElementById("result");
const submitButton = document.getElementById("submitButton");

const ticketPrice = 200;

// Update the total whenever the customer changes quantity.
quantityInput.addEventListener("change", () => {
    const quantity = Number(quantityInput.value);
    totalAmount.textContent = `KES ${ticketPrice * quantity}`;
});

// Submit the booking.
bookingForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    submitButton.disabled = true;
    submitButton.textContent = "Processing booking...";
    result.textContent = "";

    const bookingData = {
        customer_name: document.getElementById("customer_name").value.trim(),
        customer_email: document.getElementById("customer_email").value.trim(),
        quantity: Number(quantityInput.value)
    };

    try {
        const response = await fetch("/api/bookings", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(bookingData)
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Booking could not be completed.");
        }

        const booking = data.booking;

        result.innerHTML = `
            <h3>Booking successfully recorded!</h3>
            <p><strong>Booking reference:</strong> ${booking.reference}</p>
            <p><strong>Event:</strong> ${booking.event_name}</p>
            <p><strong>Tickets:</strong> ${booking.quantity}</p>
            <p><strong>Total amount:</strong> KES ${booking.total_amount}</p>
            <p><strong>Payment:</strong> Pending</p>
            <p>Your booking is recorded. This is not a paid entry ticket.
            Your QR ticket will be issued after payment is verified.</p>
        `;

        bookingForm.reset();
        totalAmount.textContent = "KES 200";

    } catch (error) {
        result.textContent = error.message;
    } finally {
        submitButton.disabled = false;
        submitButton.textContent = "Reserve Tickets";
    }
});
