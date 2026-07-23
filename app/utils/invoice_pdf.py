import io
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER

NAVY = colors.HexColor("#0B2447")
ORANGE = colors.HexColor("#F97316")
GREY = colors.HexColor("#6B7280")
LIGHT_GREY = colors.HexColor("#F3F4F6")


def build_invoice_pdf(invoice, shop, customer, items, labour_lines=None):
    """Returns a BytesIO buffer containing the rendered GST invoice PDF.

    invoice: Invoice model instance
    shop: Shop model instance
    customer: Customer model instance
    items: list of InvoiceItem model instances
    labour_lines: optional list of (name, price) tuples for labour/service charges
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=15 * mm, bottomMargin=15 * mm,
        leftMargin=15 * mm, rightMargin=15 * mm,
    )
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle("TitleNavy", parent=styles["Title"], textColor=NAVY, fontSize=20)
    small_grey = ParagraphStyle("SmallGrey", parent=styles["Normal"], textColor=GREY, fontSize=9)
    right_small = ParagraphStyle("RightSmall", parent=small_grey, alignment=TA_RIGHT)
    section_header = ParagraphStyle("SectionHeader", parent=styles["Normal"], textColor=NAVY,
                                     fontSize=11, spaceAfter=4, fontName="Helvetica-Bold")

    # --- Header: Logo + Shop details | Invoice meta ---------------------------------
    header_data = [[
        Paragraph(f"<b>{shop.name}</b>", title_style),
        Paragraph(
            f"<b>TAX INVOICE</b><br/>Invoice #: {invoice.invoice_number}<br/>"
            f"Date: {invoice.created_at.strftime('%d-%m-%Y %H:%M')}",
            right_small
        ),
    ]]
    header_table = Table(header_data, colWidths=[100 * mm, 75 * mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(header_table)

    shop_meta = (
        f"{shop.address or ''}<br/>"
        f"Phone: {shop.phone or '-'} &nbsp;|&nbsp; GSTIN: {shop.gst_number or '-'}"
    )
    story.append(Paragraph(shop_meta, small_grey))
    story.append(Spacer(1, 6))
    story.append(Table([[""]], colWidths=[175 * mm], style=TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, ORANGE),
    ])))
    story.append(Spacer(1, 8))

    # --- Customer & vehicle details ---------------------------------------------------
    cust_data = [[
        Paragraph(
            f"<b>Bill To</b><br/>{customer.name}<br/>Mobile: {customer.mobile or '-'}",
            styles["Normal"]
        ),
        Paragraph(
            f"<b>Vehicle</b><br/>No: {invoice.vehicle_number or '-'}<br/>"
            f"Type: {(invoice.vehicle_type or '-').title()}",
            styles["Normal"]
        ),
        Paragraph(
            f"<b>Payment</b><br/>Method: {invoice.payment_method.replace('_', ' ').title()}<br/>"
            f"Status: {invoice.payment_status.title()}",
            styles["Normal"]
        ),
    ]]
    cust_table = Table(cust_data, colWidths=[58 * mm, 58 * mm, 59 * mm])
    cust_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREY),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(cust_table)
    story.append(Spacer(1, 12))

    # --- Parts table ---------------------------------------------------------------
    story.append(Paragraph("Parts", section_header))
    parts_header = ["#", "Part Name", "HSN", "Qty", "Rate", "GST %", "Amount"]
    parts_rows = [parts_header]
    for i, item in enumerate(items, start=1):
        gst_amt = float(item.line_total) * float(item.gst_percent) / (100 + float(item.gst_percent))
        parts_rows.append([
            str(i),
            item.part_name_snapshot or (item.part.name if item.part else "-"),
            item.part.hsn_code if item.part else "-",
            str(item.quantity),
            f"{float(item.unit_price):,.2f}",
            f"{float(item.gst_percent):.1f}%",
            f"{float(item.line_total):,.2f}",
        ])
    parts_table = Table(parts_rows, colWidths=[8*mm, 62*mm, 20*mm, 12*mm, 24*mm, 18*mm, 26*mm])
    parts_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E5E7EB")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_GREY]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(parts_table)
    story.append(Spacer(1, 10))

    # --- Labour charges table --------------------------------------------------------
    if labour_lines:
        story.append(Paragraph("Labour / Service Charges", section_header))
        labour_rows = [["Description", "Amount"]] + [
            [name, f"{float(price):,.2f}"] for name, price in labour_lines
        ]
        labour_table = Table(labour_rows, colWidths=[145 * mm, 30 * mm])
        labour_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), ORANGE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E5E7EB")),
        ]))
        story.append(labour_table)
        story.append(Spacer(1, 10))

    # --- Totals ------------------------------------------------------------------
    totals_rows = [
        ["Subtotal", f"Rs. {float(invoice.subtotal):,.2f}"],
        ["Labour Charges", f"Rs. {float(invoice.labour_total):,.2f}"],
        ["Discount", f"- Rs. {float(invoice.discount):,.2f}"],
        ["GST", f"Rs. {float(invoice.gst_total):,.2f}"],
        ["Grand Total", f"Rs. {float(invoice.grand_total):,.2f}"],
        ["Amount Paid", f"Rs. {float(invoice.amount_paid):,.2f}"],
        ["Balance Due", f"Rs. {invoice.balance_due():,.2f}"],
    ]
    totals_table = Table(totals_rows, colWidths=[145 * mm, 30 * mm])
    totals_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, 4), (-1, 4), 0.8, NAVY),
        ("FONTNAME", (0, 4), (-1, 4), "Helvetica-Bold"),
        ("FONTNAME", (0, 6), (-1, 6), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 6), (-1, 6), ORANGE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 16))

    footer_style = ParagraphStyle("Footer", parent=small_grey, alignment=TA_CENTER)
    story.append(Paragraph(
        "This is a system-generated invoice from PartFlow ERP. Thank you for your business.",
        footer_style
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer
