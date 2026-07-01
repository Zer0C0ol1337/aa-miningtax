import io
from decimal import Decimal
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

# EVE-Online-inspirierte Farben
COLOR_DARK      = colors.HexColor('#1a1a2e')
COLOR_ACCENT    = colors.HexColor('#0f3460')
COLOR_HIGHLIGHT = colors.HexColor('#e94560')
COLOR_LIGHT     = colors.HexColor('#f5f5f5')
COLOR_MUTED     = colors.HexColor('#888888')
COLOR_WHITE     = colors.white


def _format_isk(value):
    """Formatiert einen Decimal-Wert als ISK-String mit Tausenderpunkten."""
    try:
        return f"{float(value):,.2f} ISK"
    except Exception:
        return "0.00 ISK"


def generate_corp_invoice_pdf(corp_data, corp_name, month, year, moon_rentals=None):
    """
    Generiert eine Corp-Abrechnung als PDF und gibt ein BytesIO-Objekt zurück.

    corp_data: dict aus calculate_alliance_billing()['corps'][corp_id]
    corp_name: str
    month/year: int
    moon_rentals: QuerySet von MoonRental für diese Corp (optional)
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20*mm,
        rightMargin=20*mm,
        topMargin=20*mm,
        bottomMargin=20*mm,
    )

    styles = getSampleStyleSheet()
    story = []

    # ── Styles ────────────────────────────────────────────────────────────────
    style_title = ParagraphStyle(
        'InvoiceTitle',
        fontSize=22,
        textColor=COLOR_WHITE,
        backColor=COLOR_DARK,
        alignment=TA_CENTER,
        spaceAfter=4,
        spaceBefore=4,
        fontName='Helvetica-Bold',
        leading=28,
    )
    style_subtitle = ParagraphStyle(
        'InvoiceSubtitle',
        fontSize=11,
        textColor=COLOR_MUTED,
        alignment=TA_CENTER,
        spaceAfter=2,
        fontName='Helvetica',
    )
    style_section = ParagraphStyle(
        'SectionHeader',
        fontSize=12,
        textColor=COLOR_WHITE,
        backColor=COLOR_ACCENT,
        fontName='Helvetica-Bold',
        spaceBefore=10,
        spaceAfter=4,
        leftIndent=4,
        leading=18,
    )
    style_normal = styles['Normal']
    style_normal.fontName = 'Helvetica'
    style_normal.fontSize = 10

    style_total = ParagraphStyle(
        'TotalLine',
        fontSize=13,
        textColor=COLOR_HIGHLIGHT,
        fontName='Helvetica-Bold',
        alignment=TA_RIGHT,
        spaceBefore=6,
    )

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph(f"IGC Alliance — Mining Tax Invoice", style_title))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(f"{corp_name}  |  {month:02d}/{year}", style_subtitle))
    story.append(HRFlowable(width='100%', thickness=2, color=COLOR_HIGHLIGHT, spaceAfter=6))

    # ── Zusammenfassung ───────────────────────────────────────────────────────
    story.append(Paragraph("Zusammenfassung", style_section))

    rental_total = Decimal('0')
    if moon_rentals:
        for r in moon_rentals:
            if r.active:
                rental_total += r.monthly_fee

    total_due = corp_data['total_tax'] + rental_total

    summary_data = [
        ['Posten', 'Betrag'],
        ['Gesamt abgebaut (Wert)', _format_isk(corp_data['total_mined'])],
        ['Mining Tax gesamt', _format_isk(corp_data['total_tax'])],
    ]
    if rental_total > 0:
        summary_data.append(['Moon Rental gesamt', _format_isk(rental_total)])
    summary_data.append(['GESAMT FÄLLIG', _format_isk(total_due)])

    summary_table = Table(summary_data, colWidths=[110*mm, 60*mm])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0),  COLOR_ACCENT),
        ('TEXTCOLOR',    (0, 0), (-1, 0),  COLOR_WHITE),
        ('FONTNAME',     (0, 0), (-1, 0),  'Helvetica-Bold'),
        ('FONTSIZE',     (0, 0), (-1, 0),  10),
        ('BACKGROUND',   (0, -1), (-1, -1), COLOR_DARK),
        ('TEXTCOLOR',    (0, -1), (-1, -1), COLOR_HIGHLIGHT),
        ('FONTNAME',     (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE',     (0, -1), (-1, -1), 12),
        ('ALIGN',        (1, 0), (1, -1),  'RIGHT'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [COLOR_LIGHT, COLOR_WHITE]),
        ('GRID',         (0, 0), (-1, -1), 0.5, COLOR_MUTED),
        ('TOPPADDING',   (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
        ('LEFTPADDING',  (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 4*mm))

    # ── Steuer nach Kategorie ─────────────────────────────────────────────────
    story.append(Paragraph("Mining Tax nach Erz-Kategorie", style_section))

    cat_data = [['Kategorie', 'Steuersatz', 'Abgebaut (ISK)', 'Steuer (ISK)']]
    for cat, data in sorted(corp_data['categories'].items()):
        cat_data.append([
            cat,
            f"{data['rate']}%",
            _format_isk(data['value']),
            _format_isk(data['tax']),
        ])

    cat_table = Table(cat_data, colWidths=[40*mm, 30*mm, 60*mm, 40*mm])
    cat_table.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0),  COLOR_ACCENT),
        ('TEXTCOLOR',    (0, 0), (-1, 0),  COLOR_WHITE),
        ('FONTNAME',     (0, 0), (-1, 0),  'Helvetica-Bold'),
        ('FONTSIZE',     (0, 0), (-1, -1), 9),
        ('ALIGN',        (1, 0), (-1, -1), 'RIGHT'),
        ('ALIGN',        (0, 0), (0, -1),  'LEFT'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [COLOR_LIGHT, COLOR_WHITE]),
        ('GRID',         (0, 0), (-1, -1), 0.5, COLOR_MUTED),
        ('TOPPADDING',   (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
        ('LEFTPADDING',  (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(cat_table)
    story.append(Spacer(1, 4*mm))

    # ── Mitglieder ────────────────────────────────────────────────────────────
    story.append(Paragraph("Aufschlüsselung nach Mitglied", style_section))

    member_data = [['Spieler', 'Abgebaut (ISK)', 'Steuer (ISK)']]
    for name, data in sorted(corp_data['members'].items(), key=lambda x: x[1]['tax'], reverse=True):
        member_data.append([
            name,
            _format_isk(data['mined']),
            _format_isk(data['tax']),
        ])

    member_table = Table(member_data, colWidths=[80*mm, 60*mm, 30*mm])
    member_table.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0),  COLOR_ACCENT),
        ('TEXTCOLOR',    (0, 0), (-1, 0),  COLOR_WHITE),
        ('FONTNAME',     (0, 0), (-1, 0),  'Helvetica-Bold'),
        ('FONTSIZE',     (0, 0), (-1, -1), 9),
        ('ALIGN',        (1, 0), (-1, -1), 'RIGHT'),
        ('ALIGN',        (0, 0), (0, -1),  'LEFT'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [COLOR_LIGHT, COLOR_WHITE]),
        ('GRID',         (0, 0), (-1, -1), 0.5, COLOR_MUTED),
        ('TOPPADDING',   (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
        ('LEFTPADDING',  (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(member_table)

    # ── Moon Rentals ──────────────────────────────────────────────────────────
    if moon_rentals and rental_total > 0:
        story.append(Spacer(1, 4*mm))
        story.append(Paragraph("Moon Rentals", style_section))

        rental_data = [['Mond', 'Struktur', 'Monatliche Gebühr']]
        for r in moon_rentals:
            if r.active:
                rental_data.append([
                    r.moon_name,
                    r.structure_name or '—',
                    _format_isk(r.monthly_fee),
                ])

        rental_table = Table(rental_data, colWidths=[60*mm, 70*mm, 40*mm])
        rental_table.setStyle(TableStyle([
            ('BACKGROUND',   (0, 0), (-1, 0),  COLOR_ACCENT),
            ('TEXTCOLOR',    (0, 0), (-1, 0),  COLOR_WHITE),
            ('FONTNAME',     (0, 0), (-1, 0),  'Helvetica-Bold'),
            ('FONTSIZE',     (0, 0), (-1, -1), 9),
            ('ALIGN',        (2, 0), (2, -1),  'RIGHT'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [COLOR_LIGHT, COLOR_WHITE]),
            ('GRID',         (0, 0), (-1, -1), 0.5, COLOR_MUTED),
            ('TOPPADDING',   (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
            ('LEFTPADDING',  (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(rental_table)

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 8*mm))
    story.append(HRFlowable(width='100%', thickness=1, color=COLOR_MUTED))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        f"Generiert von IGC Alliance Mining Tax System  |  {month:02d}/{year}",
        ParagraphStyle('Footer', fontSize=8, textColor=COLOR_MUTED, alignment=TA_CENTER)
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer
