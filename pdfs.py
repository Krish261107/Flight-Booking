"""E-ticket and boarding-pass PDFs. Uses only fpdf2 calls that behave the same in old and new versions."""
from fpdf import FPDF


def _t(text):
    return str(text).encode("latin-1", "replace").decode("latin-1")  # built-in PDF fonts are Latin-1 only


def _line(pdf, text, h=6):
    pdf.cell(0, h, _t(text))
    pdf.ln(h)


def build_ticket_pdf(booking, segments, passengers):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_fill_color(0, 61, 165)
    pdf.rect(0, 0, 210, 25, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_xy(10, 7)
    pdf.cell(0, 10, "E-TICKET / ITINERARY RECEIPT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(10, 32)
    pdf.set_font("Helvetica", "B", 13)
    _line(pdf, "PNR: " + booking["pnr"], 8)
    pdf.set_font("Helvetica", "", 11)
    _line(pdf, "Status: %s   |   Trip: %s   |   Cabin: %s" % (booking["status"], booking["trip_type"], booking["cabin"]), 7)
    pdf.ln(3)
    for i, s in enumerate(segments, start=1):
        pdf.set_font("Helvetica", "B", 12)
        _line(pdf, "Flight %d: %s %s" % (i, s["airline_name"], s["flight_number"]), 8)
        pdf.set_font("Helvetica", "", 10)
        _line(pdf, "%s -> %s   Date: %s" % (s["origin"], s["destination"], s["depart_date"]))
        _line(pdf, "Departs %s -> Arrives %s (Duration %s min)" % (s["depart_time"], s["arrive_time"], s["duration_min"]))
        stops = "Non-stop" if s["stops"] == 0 else "%d stop via %s" % (s["stops"], s["layover_airport"])
        _line(pdf, "%s   Gate: %s" % (stops, s["gate"]))
        pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    _line(pdf, "Passengers", 8)
    pdf.set_font("Helvetica", "", 10)
    for p in passengers:
        _line(pdf, "- %s (%s, age %s)  Seat: %s  Meal: %s" % (p["full_name"], p["passenger_type"], p["age"], p["seat_no"] or "Not assigned", p["meal"]))
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    _line(pdf, "Fare Summary", 8)
    pdf.set_font("Helvetica", "", 10)
    _line(pdf, "Base fare: INR %.2f" % booking["base_fare"])
    _line(pdf, "Taxes & fees: INR %.2f" % booking["taxes"])
    _line(pdf, "Add-ons: INR %.2f" % booking["addon_total"])
    _line(pdf, "Discount: -INR %.2f" % booking["discount"])
    pdf.set_font("Helvetica", "B", 11)
    _line(pdf, "Total paid: INR %.2f (%s)" % (booking["total_amount"], booking["payment_method"]), 7)
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    _line(pdf, "Dummy ticket generated for demo purposes only. Not valid for travel.", 5)
    return bytes(pdf.output())


def build_boarding_pass_pdf(booking, flight, passenger):
    pdf = FPDF(orientation="L", format=(100, 210))
    pdf.add_page()
    pdf.set_fill_color(0, 61, 165)
    pdf.rect(0, 0, 210, 20, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_xy(5, 5)
    pdf.cell(0, 8, "BOARDING PASS")
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(5, 24)
    pdf.set_font("Helvetica", "B", 11)
    _line(pdf, passenger["full_name"])
    pdf.set_font("Helvetica", "", 10)
    for text in ("%s -> %s   %s" % (flight["origin"], flight["destination"], flight["flight_number"]),
                 "Date: %s   Dep: %s   Gate: %s" % (flight["depart_date"], flight["depart_time"], flight["gate"]),
                 "Seat: %s   PNR: %s" % (passenger["seat_no"] or "TBD", booking["pnr"])):
        pdf.set_x(5)
        _line(pdf, text)
    pdf.set_x(5)
    pdf.set_font("Helvetica", "I", 7)
    _line(pdf, "Demo boarding pass - not valid for actual travel.", 5)
    return bytes(pdf.output())
