"""PDF báo cáo tháng cho Invoice & Billing."""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.pagesizes import landscape
import io
from datetime import datetime

from ..domain.models import Invoice


def generate_monthly_pdf(invoices, month: str, total_revenue: float, total_tax: float) -> bytes:
    """Generate A4 PDF báo cáo tháng từ danh sách invoices."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=15*mm, leftMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    story = []

    # Header
    story.append(Paragraph("Báo Cáo Hóa Đơn Tháng " + month, styles["Title"]))
    story.append(Paragraph(f"Ngày tạo: {datetime.now().strftime('%d/%m/%Y')}", styles["Normal"]))
    story.append(Spacer(1, 8))

    # Summary table
    summary_data = [
        ["Tổng doanh thu", f"{total_revenue:,.0f} VND"],
        ["Tổng thuế", f"{total_tax:,.0f} VND"],
        ["Số hóa đơn", str(len(invoices))],
    ]
    summary_table = Table(summary_data, colWidths=[60*mm, 50*mm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a73e8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 12))

    # Invoice table
    table_data = [["Mã HD", "Nhà cung cấp", "Ngày", "Tổng", "Thuế", "Trạng thái"]]
    for inv in invoices:
        table_data.append([
            inv.id[:8],
            (inv.vendor or "")[:20],
            (inv.issue_date or "")[:10] if inv.issue_date else "",
            f"{inv.total or 0:,.0f}",
            f"{inv.tax or 0:,.0f}",
            (inv.status or "").value if hasattr(inv.status, 'value') else (inv.status or ""),
        ])

    table = Table(table_data, colWidths=[25*mm, 55*mm, 25*mm, 25*mm, 25*mm, 30*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a73e8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
    ]))
    story.append(table)

    doc.build(story)
    return buf.getvalue()
