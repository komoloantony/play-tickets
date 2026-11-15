"""Generate printable SVG tickets with branded QR codes."""

from datetime import datetime
from xml.sax.saxutils import escape

import qrcode


BACKGROUND = "#090909"
SURFACE = "#121212"
GOLD = "#d8b45a"
GOLD_LIGHT = "#f3d98b"
MUTED = "#a7a39a"


def _escaped(value):
    return escape(str(value or ""))


def _qr_path(ticket_code):
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=4,
    )
    qr.add_data(ticket_code)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    path = "".join(
        f"M{column} {row}h1v1h-1z"
        for row, modules in enumerate(matrix)
        for column, is_dark in enumerate(modules)
        if is_dark
    )
    return len(matrix), path


def _format_event_time(value):
    if not value:
        return "See event details"
    try:
        event_time = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return event_time.strftime("%A, %B %d, %Y at %I:%M %p")


def generate_qr_svg(ticket_code):
    """Return a standalone SVG QR code containing the ticket's check-in code."""
    qr_size, path = _qr_path(ticket_code)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{qr_size * 8}" '
        f'height="{qr_size * 8}" viewBox="0 0 {qr_size} {qr_size}">'
        f'<rect width="{qr_size}" height="{qr_size}" fill="#ffffff"/>'
        f'<path d="{path}" fill="{BACKGROUND}" shape-rendering="crispEdges"/>'
        "</svg>"
    )


def generate_ticket_svg(
    *,
    event_name,
    ticket_type,
    ticket_number,
    ticket_code,
    starts_at,
    venue,
):
    """Build a print-ready, dark-and-gold ticket containing its unique QR code."""
    qr_size, qr_path = _qr_path(ticket_code)
    event_name = _escaped(event_name)
    ticket_type = _escaped(ticket_type)
    ticket_code = _escaped(ticket_code)
    venue = _escaped(venue)
    event_time = _escaped(_format_event_time(starts_at))
    qr_box_x = 674
    qr_box_y = 91
    qr_box_size = 232
    qr_padding = 16
    qr_inner_size = qr_box_size - qr_padding * 2

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="960" height="430" viewBox="0 0 960 430" role="img">
<title>{event_name} - entry ticket {ticket_number}</title>
<rect width="960" height="430" rx="24" fill="{BACKGROUND}"/>
<rect x="12" y="12" width="936" height="406" rx="20" fill="{SURFACE}" stroke="{GOLD}" stroke-width="2"/>
<rect x="38" y="40" width="8" height="62" rx="4" fill="{GOLD}"/>
<text x="66" y="67" fill="{GOLD}" font-family="Arial, sans-serif" font-size="17" font-weight="700" letter-spacing="3">FIREBRAND TICKETS</text>
<text x="66" y="137" fill="{GOLD_LIGHT}" font-family="Georgia, serif" font-size="39" font-weight="700">{event_name}</text>
<text x="66" y="174" fill="{MUTED}" font-family="Arial, sans-serif" font-size="18">{ticket_type} - Ticket {ticket_number}</text>
<text x="66" y="231" fill="{GOLD}" font-family="Arial, sans-serif" font-size="12" font-weight="700" letter-spacing="2">DATE AND TIME</text>
<text x="66" y="257" fill="#ffffff" font-family="Arial, sans-serif" font-size="17">{event_time}</text>
<text x="66" y="300" fill="{GOLD}" font-family="Arial, sans-serif" font-size="12" font-weight="700" letter-spacing="2">VENUE</text>
<text x="66" y="326" fill="#ffffff" font-family="Arial, sans-serif" font-size="17">{venue}</text>
<text x="66" y="379" fill="{MUTED}" font-family="Arial, sans-serif" font-size="13">TICKET CODE</text>
<text x="66" y="400" fill="#ffffff" font-family="monospace" font-size="15">{ticket_code}</text>
<path d="M642 40V390" stroke="{GOLD}" stroke-width="2" stroke-dasharray="7 9" opacity="0.7"/>
<rect x="{qr_box_x}" y="{qr_box_y}" width="{qr_box_size}" height="{qr_box_size}" rx="12" fill="#ffffff"/>
<svg x="{qr_box_x + qr_padding}" y="{qr_box_y + qr_padding}" width="{qr_inner_size}" height="{qr_inner_size}" viewBox="0 0 {qr_size} {qr_size}" preserveAspectRatio="xMidYMid meet">
<path d="{qr_path}" fill="{BACKGROUND}" shape-rendering="crispEdges"/>
</svg>
<text x="790" y="352" text-anchor="middle" fill="{GOLD_LIGHT}" font-family="Arial, sans-serif" font-size="14" font-weight="700" letter-spacing="1">SCAN AT THE DOOR</text>
</svg>"""
