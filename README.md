# Play Tickets

Play Tickets is a planned event ticketing system for selling tickets online. Buyers will be able to open an event page from a shareable link or QR code, select a ticket, and pay using mobile money. Event admins will be able to manage ticket availability, track sales, and check attendees in at the door.

## Planned features

- Create events with a name, date, venue, ticket types, prices, and ticket limits.
- Share an event purchase page through a link or QR code.
- Let buyers select tickets and provide their contact details.
- Support M-Pesa and Airtel Money payments, subject to payment provider or gateway availability in the launch country.
- Issue a unique ticket with a QR code after payment is confirmed by the payment provider.
- Give admins a dashboard showing tickets sold, tickets remaining, payment status, and attendee information.
- Scan ticket QR codes at the entrance and prevent a ticket from being checked in more than once.

## Purchase flow

1. The buyer opens an event link or scans its QR code.
2. The buyer selects a ticket and enters their details.
3. The system starts a mobile money payment request.
4. The payment provider confirms the transaction through a server callback.
5. The system issues the ticket and makes its QR code available to the buyer.
6. Event staff scan the QR code at entry to validate and check in the attendee.

Tickets should only be issued after a verified payment confirmation. A buyer's claim that they have paid is not sufficient confirmation.

## Project status

This repository currently contains the project overview and initial feature plan. The application, payment integrations, and ticket-scanning tools have not been implemented yet.

## Initial scope

The proposed first release focuses on one launch country, event creation, online ticket purchases, mobile money payment confirmation, an admin sales dashboard, and QR check-in. The country, technology stack, and payment provider or gateway still need to be selected.

## Future considerations

- Email or SMS ticket delivery.
- Refund and cancellation handling.
- Multiple organizer accounts and staff roles.
- Sales reports and payment reconciliation.
- Additional payment methods and countries.
