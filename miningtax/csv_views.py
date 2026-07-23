"""
CSV exports, alongside the existing PDF invoices.

PDFs are the document you send a corp; CSV is what you open in a spreadsheet to
check a figure or build your own summary. Both read the same billing functions,
so a CSV can never disagree with the invoice generated from the same month.

Every view reuses the access rules of the page it exports, rather than defining
its own — an export must not become a way around a permission check.
"""
import csv
from datetime import date
from decimal import Decimal

from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from allianceauth.eveonline.models import EveCharacter

from .billing import calculate_alliance_billing, calculate_entry_tax
from .models import MiningLedgerEntry
from .views import (
    check_access, has_basic_access, has_officer_access,
    get_ceo_corp_id, is_ceo_only, _corp_for_entry,
)


def _csv_response(filename, rows):
    """
    Writes rows to a downloadable CSV.

    Uses a BOM and semicolons because these files are opened in Excel far more
    often than parsed: without the BOM Excel mangles ore names with non-ASCII
    characters, and in locales where the comma is the decimal separator a
    comma-delimited file lands entirely in column A.
    """
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('\ufeff')

    writer = csv.writer(response, delimiter=';')
    for row in rows:
        writer.writerow(row)
    return response


def _fmt(value):
    """Plain decimal string — no thousands separators, so it stays a number."""
    if isinstance(value, Decimal):
        return f'{value:.2f}'
    return value


def _ledger_rows(entries):
    """Ledger entries as CSV rows, with tax resolved per entry."""
    yield [
        'Date', 'Character', 'Corporation', 'Location', 'Ore', 'Category',
        'Quantity', 'Value (ISK)', 'Tax Rate (%)', 'Tax (ISK)', 'Excluded',
    ]

    for entry in entries:
        info = calculate_entry_tax(entry, corporation=_corp_for_entry(entry))
        yield [
            entry.date.isoformat(),
            entry.character.character_name,
            entry.character.corporation_name or '',
            entry.solar_system_name or '',
            entry.type_name,
            info['category'],
            entry.quantity,
            _fmt(entry.total_value),
            _fmt(info['tax_rate']),
            _fmt(info['tax_amount']),
            'yes' if info['excluded'] else 'no',
        ]


@check_access(has_basic_access)
def export_my_ledger(request):
    """The requesting user's own mining for a month, across all their characters."""
    today = date.today()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))

    character_ids = request.user.character_ownerships.all().values_list(
        'character_id', flat=True
    )
    entries = MiningLedgerEntry.objects.filter(
        character__in=EveCharacter.objects.filter(pk__in=character_ids),
        date__year=year,
        date__month=month,
    ).select_related('character').order_by('date', 'character__character_name')

    filename = f'my_mining_{year}_{month:02d}.csv'
    return _csv_response(filename, _ledger_rows(entries))


@check_access(has_basic_access)
def export_pilot_ledger(request, character_id):
    """
    One player's mining for a month, covering their whole account.

    Mirrors the pilot detail page exactly, including its access rules: your own
    characters always, anyone's as an officer, own corporation only as a CEO.
    """
    today = date.today()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))

    main = get_object_or_404(EveCharacter, character_id=character_id)

    own_character_pks = set(
        request.user.character_ownerships.all().values_list('character_id', flat=True)
    )
    if main.pk not in own_character_pks:
        if not has_officer_access(request.user):
            return HttpResponse('Not permitted.', status=403)
        restricted = get_ceo_corp_id(request.user) if is_ceo_only(request.user) else None
        if restricted and main.corporation_id != restricted:
            return HttpResponse('Not permitted.', status=403)

    try:
        user = main.character_ownership.user
        character_ids = list(
            user.character_ownerships.all().values_list('character_id', flat=True)
        )
        characters = EveCharacter.objects.filter(pk__in=character_ids)
    except Exception:
        characters = EveCharacter.objects.filter(pk=main.pk)

    entries = MiningLedgerEntry.objects.filter(
        character__in=characters, date__year=year, date__month=month,
    ).select_related('character').order_by('date', 'character__character_name')

    safe_name = main.character_name.replace(' ', '_')
    filename = f'mining_{safe_name}_{year}_{month:02d}.csv'
    return _csv_response(filename, _ledger_rows(entries))


@check_access(has_officer_access)
def export_alliance_billing(request):
    """
    The billing summary for a month: one section per corp with its category
    breakdown and member totals, then an alliance total.

    Shaped for reading rather than for parsing, since it mirrors a page that is
    itself a summary. Anyone wanting per-entry data is better served by the
    ledger export, which is one flat table.
    """
    today = date.today()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))

    data = calculate_alliance_billing(year, month)

    restricted = get_ceo_corp_id(request.user) if is_ceo_only(request.user) else None

    def rows():
        yield [f'Mining Tax — {year}-{month:02d}']
        yield []

        for corp_id, corp in data['corps'].items():
            if restricted and corp_id != restricted:
                continue

            yield ['Corporation', corp['corp_name']]
            yield ['Total mined (ISK)', _fmt(corp['total_mined'])]
            yield ['Total tax (ISK)', _fmt(corp['total_tax'])]
            yield []

            yield ['Category', 'Rate (%)', 'Value (ISK)', 'Tax (ISK)']
            for category, values in corp['categories'].items():
                yield [
                    category,
                    _fmt(values['rate']),
                    _fmt(values['value']),
                    _fmt(values['tax']),
                ]
            yield []

            yield ['Member', 'Mined (ISK)', 'Tax (ISK)']
            for name, values in corp['members'].items():
                yield [name, _fmt(values['mined']), _fmt(values['tax'])]
            yield []
            yield []

        # Only meaningful across the whole alliance, so it is left out when the
        # view is restricted to a single corporation.
        if not restricted:
            yield ['Alliance total mined (ISK)', _fmt(data['totals']['mined'])]
            yield ['Alliance total tax (ISK)', _fmt(data['totals']['tax'])]

    filename = f'alliance_billing_{year}_{month:02d}.csv'
    return _csv_response(filename, rows())