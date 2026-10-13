"""Create four fictional, text-selectable PDFs for the local upload demo.

Run with Python plus reportlab: python scripts/generate_demo_pdfs.py
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo_docs"

DOCUMENTS = {
    "customs_declaration": (
        "CUSTOMS DECLARATION",
        [
            ("DECLARATION NO", "SYN2026000303"),
            ("DOMESTIC CONSIGNOR", "EAST HARBOR IMPORTS LLC"),
            ("OVERSEAS CONSIGNEE", "PACIFIC CARGO LTD"),
            ("TRADING COUNTRY", "CN"),
            ("PORT OF DISPATCH", "SHANGHAI"),
            ("PORT OF DESTINATION", "LOS ANGELES"),
            ("TRANSPORT MODE", "SEA"),
            ("TRANSACTION MODE", "FOB"),
            ("HS CODE", "8517620000"),
            ("GOODS NAME", "BLUETOOTH SPEAKER"),
            ("QUANTITY", "2400 PCS"),
            ("TOTAL VALUE", "USD 52800.00"),
            ("GROSS WEIGHT", "9600 KG"),
            ("NET WEIGHT", "8640 KG"),
            ("PACKAGES", "240"),
            ("DECLARATION DATE", "2026-04-12"),
            ("B/L NO", "BL20260303"),
        ],
    ),
    "bill_of_lading": (
        "BILL OF LADING",
        [
            ("B/L NO", "BL20260303"),
            ("SHIPPER", "PACIFIC CARGO LTD"),
            ("CONSIGNEE", "TO ORDER"),
            ("NOTIFY PARTY", "EAST HARBOR IMPORTS LLC"),
            ("VESSEL", "NORDIC VOYAGER"),
            ("VOYAGE", "0FM3W1"),
            ("PORT OF LOADING", "SHANGHAI"),
            ("PORT OF DISCHARGE", "LOS ANGELES"),
            ("PACKAGES", "240"),
            ("GROSS WEIGHT", "9600 KG"),
            ("GOODS DESCRIPTION", "BLUETOOTH SPEAKER; HS 8517620000; Container SYNU3344556"),
        ],
    ),
    "commercial_invoice": (
        "COMMERCIAL INVOICE",
        [
            ("INVOICE NO", "INV-2026-0303"),
            ("INVOICE DATE", "2026-04-02"),
            ("SELLER", "PACIFIC CARGO LTD"),
            ("BUYER", "EAST HARBOR IMPORTS LLC"),
            ("ORDER NO", "PO-FAKE-0303"),
            ("GOODS NAME", "BLUETOOTH SPEAKER"),
            ("QUANTITY", "2400 PCS"),
            ("UNIT PRICE", "USD 22.00"),
            ("TOTAL VALUE", "USD 52800.00"),
            ("PACKAGES", "240"),
            ("GROSS WEIGHT", "11000 KG"),
            ("NET WEIGHT", "9900 KG"),
            ("MARKS", "N/M"),
            ("CONTAINER NO", "SYNU3344556"),
            ("COUNTRY OF ORIGIN", "CN"),
            ("PORT OF LOADING", "SHANGHAI"),
            ("PORT OF DISCHARGE", "LOS ANGELES"),
        ],
    ),
    "packing_list": (
        "PACKING LIST",
        [
            ("PACKING LIST NO", "PL-2026-0303"),
            ("INVOICE NO", "INV-2026-0303"),
            ("PACKING DATE", "2026-04-03"),
            ("SELLER", "PACIFIC CARGO LTD"),
            ("BUYER", "EAST HARBOR IMPORTS LLC"),
            ("GOODS NAME", "BLUETOOTH SPEAKER"),
            ("QUANTITY", "2400 PCS"),
            ("PACKAGES", "240"),
            ("KIND OF PACKAGES", "CARTONS"),
            ("GROSS WEIGHT", "9600 KG"),
            ("NET WEIGHT", "8640 KG"),
            ("MEASUREMENT", "28.6 CBM"),
            ("MARKS", "N/M"),
            ("CONTAINER NO", "SYNU3344556"),
            ("PORT OF LOADING", "SHANGHAI"),
            ("PORT OF DISCHARGE", "LOS ANGELES"),
        ],
    ),
}


def create_pdf(path: Path, title: str, rows: list[tuple[str, str]]) -> None:
    page_width, page_height = A4
    pdf = canvas.Canvas(str(path), pagesize=A4, pageCompression=0)
    pdf.setTitle(title + " - Synthetic demo")
    pdf.setAuthor("Shulian local demo")

    pdf.setFillColor(colors.HexColor("#10233e"))
    pdf.rect(0, page_height - 120, page_width, 120, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 19)
    pdf.drawString(48, page_height - 70, title)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(48, page_height - 91, "SYNTHETIC DOCUMENT  |  LOCAL DEMONSTRATION ONLY")

    pdf.setFillColor(colors.HexColor("#31557a"))
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(48, page_height - 154, "DOCUMENT DETAILS")
    pdf.setStrokeColor(colors.HexColor("#dce4ed"))
    pdf.line(48, page_height - 165, page_width - 48, page_height - 165)

    y = page_height - 190
    for index, (label, value) in enumerate(rows):
        if index % 2 == 0:
            pdf.setFillColor(colors.HexColor("#f5f8fc"))
            pdf.roundRect(43, y - 7, page_width - 86, 25, 4, fill=1, stroke=0)
        pdf.setFillColor(colors.HexColor("#18283d"))
        pdf.setFont("Helvetica", 10)
        pdf.drawString(50, y, f"{label}: {value}")
        y -= 30

    pdf.setStrokeColor(colors.HexColor("#dce4ed"))
    pdf.line(48, 52, page_width - 48, 52)
    pdf.setFillColor(colors.HexColor("#6c7d91"))
    pdf.setFont("Helvetica", 8)
    pdf.drawString(48, 37, "All names, numbers and shipment details on this page are fictional.")
    pdf.save()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for doc_type, (title, rows) in DOCUMENTS.items():
        path = OUT / f"{doc_type}.pdf"
        create_pdf(path, title, rows)
        print(path)


if __name__ == "__main__":
    main()
