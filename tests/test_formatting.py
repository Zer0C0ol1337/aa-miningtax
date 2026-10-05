"""How amounts and rates are shown on pages, in the PDF and in the CSV."""
from decimal import Decimal

from django.template import engines

from miningtax.templatetags.miningtax_tags import format_rate, isk, isk_plain, whole_isk
from tests.helpers import load


def test_amounts_with_dots_and_no_decimals():
    assert isk(45141) == '45.141'
    assert isk(Decimal('2023066246')) == '2.023.066.246'


def test_copy_value_is_bare_digits():
    assert isk_plain(2187500000) == '2187500000'


def test_breakdown_lines_never_exceed_the_total():
    members = [Decimal('6896.54'), Decimal('38245.13')]
    assert sum(whole_isk(m) for m in members) <= whole_isk(Decimal('45141.67'))


def test_rates_lose_trailing_zeros_but_are_not_rounded():
    assert [format_rate(Decimal(v)) for v in ('7.00', '7.50', '10.00', '0.00')] == ['7', '7.5', '10', '0']


def test_payment_box_shows_dots_but_copies_digits():
    html = engines['django'].from_string(
        '{% load miningtax_tags %}<code data-copy="{{ due|isk_plain }}">{{ due|isk }}</code>'
    ).render({'due': 2187500000})
    assert 'data-copy="2187500000"' in html and '>2.187.500.000<' in html


def test_csv_writes_plain_whole_numbers():
    ns = load('csv_views.py', {'_fmt', '_fmt_rate'}, {'whole_isk': whole_isk, 'format_rate': format_rate})
    assert ns['_fmt'](Decimal('45141.67')) == 45141
    assert ns['_fmt_rate'](Decimal('7.00')) == '7'


def test_pdf_total_matches_the_page():
    from pypdf import PdfReader
    from miningtax.pdf_export import generate_corp_invoice_pdf
    corp = {'corp_name': "Your Ex's", 'total_mined': 2023066246, 'total_tax': 45141,
            'categories': {'Ore': {'rate': Decimal('7.00'), 'value': Decimal('644881'), 'tax': Decimal('45141.67')}},
            'members': {}}
    pdf = generate_corp_invoice_pdf(corp, "Your Ex's", 9, 2026, moon_rentals=[], rental_total=0)
    text = ' '.join(page.extract_text() for page in PdfReader(pdf).pages)
    assert '45.141' in text and '7%' in text and '.00' not in text


def test_templates_parse():
    from tests.helpers import PKG
    for name in ('alliance_overview', 'dashboard', 'pilot_detail', 'settings'):
        engines['django'].from_string((PKG / 'templates' / 'miningtax' / f'{name}.html').read_text(encoding='utf-8'))
