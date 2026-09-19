import zipfile
import io
from decimal import Decimal
from datetime import date

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required

from allianceauth.eveonline.models import EveCorporationInfo

from .models import MoonRental, AllianceBillingRecord
from .pdf_export import generate_corp_invoice_pdf
from .views import check_access, has_officer_access, own_corporation_id, is_corp_scoped


def _record_to_corp_data(record):
    """
    Converts an AllianceBillingRecord into the same dict shape
    calculate_alliance_billing()['corps'][corp_id] produces — the shape
    generate_corp_invoice_pdf() has always expected.

    Introduced because the PDF export used to call calculate_alliance_billing()
    live while the Alliance Billing overview page reads the daily snapshot
    (since 0.10.10). Two different calculation paths for the same figure will
    disagree the moment any mining data lands between the last snapshot refresh
    and the PDF download — which is exactly what officers were seeing. Reading
    from the same record both pages already share removes the second path
    entirely: the PDF can no longer show a different number than the page it
    was downloaded from, because there's only one number to show.

    category_snapshot/member_snapshot store Decimal values as str() (JSON has
    no Decimal type) — converted back here the same way _deserialise_members()
    does in views.py.
    """
    categories = {}
    for cat, data in (record.category_snapshot or {}).items():
        categories[cat] = {
            'value': Decimal(data.get('value', '0')),
            'tax': Decimal(data.get('tax', '0')),
            'rate': Decimal(data.get('rate', '0')),
        }

    # Sorted alphabetically (case-insensitively), same as _deserialise_members()
    # in views.py: a plain dict keeps insertion order, and the invoice table
    # renders it as-is, so pre-sorting here is what makes the PDF list match
    # what the Alliance Billing page shows instead of the two disagreeing on
    # member order.
    snapshot = record.member_snapshot or {}
    members = {
        name: {
            'mined': Decimal(snapshot[name].get('mined', '0')),
            'tax': Decimal(snapshot[name].get('tax', '0')),
            'character_id': snapshot[name].get('character_id'),
        }
        for name in sorted(snapshot, key=str.lower)
    }

    return {
        'corp_name': record.corporation.corporation_name,
        'total_mined': record.total_mined_value,
        'total_tax': record.mining_tax_amount,
        'categories': categories,
        'members': members,
    }


def _get_or_build_record(corp_obj, year, month):
    """
    Fetches the billing record for a corp/month, building the whole month's
    snapshot first if none exists yet — same fallback alliance_overview() uses,
    so a PDF requested before the first daily sync of a fresh month still works
    rather than returning nothing.
    """
    record = AllianceBillingRecord.objects.filter(
        corporation=corp_obj, year=year, month=month
    ).first()

    if record is None:
        from .billing import save_billing_records_for_month
        save_billing_records_for_month(year, month)
        record = AllianceBillingRecord.objects.filter(
            corporation=corp_obj, year=year, month=month
        ).first()

    return record


# Generiert die PDF-Abrechnung für eine einzelne Corp und liefert sie als Download.
@login_required
@check_access(has_officer_access)
def download_corp_pdf(request, corp_id):
    year  = int(request.GET.get('year',  date.today().year))
    month = int(request.GET.get('month', date.today().month))

    # CEOs reach officer views through the automatic bypass rather than a
    # granted permission, so their scope has to be enforced here as well —
    # otherwise the invoice of any corp is one edited URL away.
    if is_corp_scoped(request.user) and own_corporation_id(request.user) != corp_id:
        return HttpResponse('Not permitted.', status=403)

    try:
        corp_obj = EveCorporationInfo.objects.get(corporation_id=corp_id)
    except EveCorporationInfo.DoesNotExist:
        return HttpResponse('Corporation not found.', status=404)

    record = _get_or_build_record(corp_obj, year, month)
    if record is None:
        return HttpResponse('Keine Daten für diese Corp in diesem Monat.', status=404)

    corp_data = _record_to_corp_data(record)
    corp_name = corp_data['corp_name']

    # Moon Rentals für diese Corp holen
    moon_rentals = MoonRental.objects.filter(corporation=corp_obj, active=True)

    pdf_buffer = generate_corp_invoice_pdf(
        corp_data=corp_data,
        corp_name=corp_name,
        month=month,
        year=year,
        moon_rentals=moon_rentals,
    )

    filename = f"mining_invoice_{corp_name.replace(' ', '_')}_{year}_{month:02d}.pdf"
    response = HttpResponse(pdf_buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# Generiert PDFs für alle Corps des Monats und packt sie in ein ZIP zum Download.
@login_required
@check_access(has_officer_access)
def download_all_corps_zip(request):
    year  = int(request.GET.get('year',  date.today().year))
    month = int(request.GET.get('month', date.today().month))

    records = AllianceBillingRecord.objects.filter(
        year=year, month=month
    ).select_related('corporation')

    if not records.exists():
        from .billing import save_billing_records_for_month
        save_billing_records_for_month(year, month)
        records = AllianceBillingRecord.objects.filter(
            year=year, month=month
        ).select_related('corporation')

    # Same reasoning as the single invoice: a CEO gets a ZIP of their own corp
    # rather than of every corp in the alliance.
    if is_corp_scoped(request.user):
        own_corp = own_corporation_id(request.user)
        records = records.filter(corporation__corporation_id=own_corp)

    if not records.exists():
        return HttpResponse('Keine Daten für diesen Monat.', status=404)

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for record in records:
            corp_data = _record_to_corp_data(record)
            corp_name = corp_data['corp_name']

            moon_rentals = MoonRental.objects.filter(
                corporation=record.corporation, active=True
            )

            pdf_buffer = generate_corp_invoice_pdf(
                corp_data=corp_data,
                corp_name=corp_name,
                month=month,
                year=year,
                moon_rentals=moon_rentals,
            )

            filename = f"mining_invoice_{corp_name.replace(' ', '_')}_{year}_{month:02d}.pdf"
            zf.writestr(filename, pdf_buffer.read())

    zip_buffer.seek(0)
    zip_filename = f"mining_invoices_{year}_{month:02d}.zip"
    response = HttpResponse(zip_buffer, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
    return response