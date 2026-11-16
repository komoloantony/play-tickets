import unittest
from xml.etree import ElementTree

from ticket_service import generate_qr_svg, generate_ticket_svg


class TicketServiceTests(unittest.TestCase):
    def test_ticket_svg_contains_buyer_and_escapes_text(self):
        svg = generate_ticket_svg(
            event_name="Behind the Sins <Finale>",
            ticket_type="VIP & guest",
            buyer_name="Amina <Test>",
            ticket_number=1,
            ticket_code="PTK-" + "A" * 32,
            starts_at="2026-12-13T15:00:00",
            venue="Jesus Is Alive Ministries",
        )

        root = ElementTree.fromstring(svg)
        text = "".join(root.itertext())
        self.assertIn("Behind the Sins <Finale>", text)
        self.assertIn("VIP & guest", text)
        self.assertIn("Amina <Test>", text)

    def test_long_ticket_fields_fit_the_printable_layout(self):
        long_event_name = "A very long event name " * 5
        svg = generate_ticket_svg(
            event_name=long_event_name,
            ticket_type="General admission",
            buyer_name="Amina",
            ticket_number=1,
            ticket_code="PTK-" + "A" * 32,
            starts_at="2026-12-13T15:00:00",
            venue="A very long venue name " * 8,
        )

        root = ElementTree.fromstring(svg)
        texts = root.findall("{http://www.w3.org/2000/svg}text")
        fitted_texts = [text for text in texts if "textLength" in text.attrib]
        self.assertGreaterEqual(len(fitted_texts), 2)

    def test_qr_svg_is_valid_xml_with_ticket_code(self):
        svg = generate_qr_svg("PTK-" + "A" * 32)

        root = ElementTree.fromstring(svg)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        path = root.find("{http://www.w3.org/2000/svg}path")
        self.assertIsNotNone(path)
        self.assertTrue(path.attrib["d"])


if __name__ == "__main__":
    unittest.main()
