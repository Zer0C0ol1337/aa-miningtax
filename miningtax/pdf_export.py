import io
from decimal import Decimal
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from django.utils.translation import gettext as _

# EVE-Online-inspirierte Farben
COLOR_DARK      = colors.HexColor('#1a1a2e')
COLOR_ACCENT    = colors.HexColor('#0f3460')
COLOR_HIGHLIGHT = colors.HexColor('#e94560')
COLOR_LIGHT     = colors.HexColor('#f5f5f5')
COLOR_MUTED     = colors.HexColor('#888888')
COLOR_WHITE     = colors.white


from .templatetags.miningtax_tags import format_rate, whole_isk

def _format_isk(value, suffix=False):
    """
    An ISK amount exactly as the billing page writes it: whole ISK, thousands
    grouped with dots — "45.141" — so the invoice shows the same figure as the
    page (see the isk template filter). Amounts are whole ISK, so a dot here is
    never a decimal point.

    " ISK" is not appended by default: the column header already says it,
    and those four characters decided whether billion-sized values still fit
    their cell.
    """
    text = f"{whole_isk(value):,}".replace(',', '.')
    return f"{text} ISK" if suffix else text


def generate_corp_invoice_pdf(corp_data, corp_name, month, year, moon_rentals=None, rental_total=None):
    """
    Builds a corp invoice PDF and returns it as a BytesIO.

    corp_data: dict shaped like calculate_alliance_billing()['corps'][corp_id]
    corp_name: str
    month/year: int
    moon_rentals: the corp's MoonRental rows, used to list the individual moons
    rental_total: the rental this month was billed with (from its snapshot).
        When given it is the amount shown and added to the total; the moons are
        only itemised if today's rentals still add up to it. None falls back to
        summing moon_rentals, the behaviour before 0.10.21.
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

    # Fuer Textspalten: ein reiner String kann in einer Tabellenzelle nicht
    # umbrechen und laeuft bei langen Namen ueber den Rand hinaus. Ein
    # Paragraph bricht stattdessen um und macht die Zeile hoeher.
    style_cell = ParagraphStyle(
        'Cell',
        fontName='Helvetica',
        fontSize=9,
        leading=11,
    )

    style_total = ParagraphStyle(
        'TotalLine',
        fontSize=13,
        textColor=COLOR_HIGHLIGHT,
        fontName='Helvetica-Bold',
        alignment=TA_RIGHT,
        spaceBefore=6,
    )

    # ── Header ────────────────────────────────────────────────────────────────
    story.append(Paragraph(_("IGC Alliance — Mining Tax Invoice"), style_title))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(f"{corp_name}  |  {month:02d}/{year}", style_subtitle))
    story.append(HRFlowable(width='100%', thickness=2, color=COLOR_HIGHLIGHT, spaceAfter=6))

    # ── Summary ───────────────────────────────────────────────────────────────
    story.append(Paragraph(_("Summary"), style_section))

    active_rentals = [r for r in (moon_rentals or []) if r.active]
    active_sum = sum((r.monthly_fee for r in active_rentals), Decimal('0'))
    if rental_total is None:
        rental_total = active_sum
    # A rental added or removed after the month was billed would make an
    # itemised list disagree with the billed amount, so the moons are listed
    # only while they still add up to it — otherwise a single line shows the
    # amount as billed.
    itemise_rentals = bool(active_rentals) and active_sum == rental_total

    total_due = corp_data['total_tax'] + rental_total

    summary_data = [
        [_("Item"), _("Amount")],
        [_("Total mined (value)"), _format_isk(corp_data['total_mined'])],
        [_("Total mining tax"), _format_isk(corp_data['total_tax'])],
    ]
    if rental_total > 0:
        summary_data.append([_("Total moon rental"), _format_isk(rental_total)])
    summary_data.append([_("TOTAL DUE"), _format_isk(total_due)])

    # 100/70 statt 110/60: die Schlusszeile setzt den Betrag in 12pt, wo eine
    # Milliardensumme die alten 60 mm gesprengt hat.
    summary_table = Table(summary_data, colWidths=[100*mm, 70*mm])
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

    # ── Tax by ore category ───────────────────────────────────────────────────
    story.append(Paragraph(_("Mining Tax by Ore Category"), style_section))

    cat_data = [[_("Category"), _("Tax Rate"), _("Mined (ISK)"), _("Tax (ISK)")]]
    for cat, data in sorted(corp_data['categories'].items()):
        cat_data.append([
            Paragraph(cat, style_cell),
            f"{format_rate(data['rate'])}%",
            _format_isk(data['value']),
            _format_isk(data['tax']),
        ])

    # Die Steuerspalte hatte 40 mm und lief bei grossen Betraegen ueber; beide
    # Zahlenspalten bekommen jetzt 55 mm, bezahlt aus Kategorie und Steuersatz,
    # die mit kurzen Werten auskommen.
    cat_table = Table(cat_data, colWidths=[35*mm, 25*mm, 55*mm, 55*mm])
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
        # Nach der allgemeinen Regel, sonst wuerde diese sie wieder ueberschreiben:
        # in den Zahlenspalten ist jeder Punkt Innenabstand Platz, der den
        # Ziffern fehlt.
        ('LEFTPADDING',  (2, 0), (-1, -1), 4),
        ('RIGHTPADDING', (2, 0), (-1, -1), 4),
    ]))
    story.append(cat_table)
    story.append(Spacer(1, 4*mm))

    # ── Member breakdown ──────────────────────────────────────────────────────
    story.append(Paragraph(_("Breakdown by Member"), style_section))

    member_data = [[_("Player"), _("Mined (ISK)"), _("Tax (ISK)")]]
    # Alphabetical (case-insensitive), same order the Alliance Billing page and
    # pdf_views._record_to_corp_data() already use — previously sorted by tax
    # descending here only, which meant the PDF and the page it came from could
    # list members in two different orders.
    for name in sorted(corp_data['members'], key=str.lower):
        data = corp_data['members'][name]
        member_data.append([
            Paragraph(name, style_cell),
            _format_isk(data['mined']),
            _format_isk(data['tax']),
        ])

    # Die Steuerspalte hatte 30 mm — davon nach Innenabstand rund 24 mm nutzbar,
    # waehrend eine Milliardensumme etwa 26 mm braucht. Genau hier ist der Wert
    # ueber den Rand hinausgelaufen.
    member_table = Table(member_data, colWidths=[80*mm, 45*mm, 45*mm])
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
        # Nach der allgemeinen Regel, sonst wuerde diese sie wieder ueberschreiben:
        # in den Zahlenspalten ist jeder Punkt Innenabstand Platz, der den
        # Ziffern fehlt.
        ('LEFTPADDING',  (1, 0), (-1, -1), 4),
        ('RIGHTPADDING', (1, 0), (-1, -1), 4),
    ]))
    story.append(member_table)

    # ── Moon Rentals ──────────────────────────────────────────────────────────
    if rental_total > 0:
        story.append(Spacer(1, 4*mm))
        story.append(Paragraph(_("Moon Rentals"), style_section))

        rental_data = [[_("Moon"), _("Structure"), _("Monthly Fee")]]
        if itemise_rentals:
            for r in active_rentals:
                rental_data.append([
                    Paragraph(r.moon_name, style_cell),
                    Paragraph(r.structure_name or '—', style_cell),
                    _format_isk(r.monthly_fee),
                ])
        else:
            rental_data.append([
                Paragraph(_("Moon rental as billed for this month"), style_cell),
                Paragraph('—', style_cell),
                _format_isk(rental_total),
            ])

        rental_table = Table(rental_data, colWidths=[55*mm, 65*mm, 50*mm])
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
        _("Generated by IGC Alliance Mining Tax System") + f"  |  {month:02d}/{year}",
        ParagraphStyle('Footer', fontSize=8, textColor=COLOR_MUTED, alignment=TA_CENTER)
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer