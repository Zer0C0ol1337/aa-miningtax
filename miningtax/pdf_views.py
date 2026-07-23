import zipfile
import io
from datetime import date

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required

from allianceauth.eveonline.models import EveCorporationInfo

from .billing import calculate_alliance_billing
from .models import MoonRental
from .pdf_export import generate_corp_invoice_pdf
from .views import check_access, has_officer_access, own_corporation_id, is_corp_scoped


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

    data = calculate_alliance_billing(year, month)

    if corp_id not in data['corps']:
        return HttpResponse('Keine Daten für diese Corp in diesem Monat.', status=404)

    corp_data = data['corps'][corp_id]
    corp_name = corp_data['corp_name']

    # Moon Rentals für diese Corp holen
    try:
        corp_obj = EveCorporationInfo.objects.get(corporation_id=corp_id)
        moon_rentals = MoonRental.objects.filter(corporation=corp_obj, active=True)
    except EveCorporationInfo.DoesNotExist:
        moon_rentals = None

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

    data = calculate_alliance_billing(year, month)

    # Same reasoning as the single invoice: a CEO gets a ZIP of their own corp
    # rather than of every corp in the alliance.
    if is_corp_scoped(request.user):
        own_corp = own_corporation_id(request.user)
        data['corps'] = {
            cid: cdata for cid, cdata in data['corps'].items() if cid == own_corp
        }

    if not data['corps']:
        return HttpResponse('Keine Daten für diesen Monat.', status=404)

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for corp_id, corp_data in data['corps'].items():
            corp_name = corp_data['corp_name']

            try:
                corp_obj = EveCorporationInfo.objects.get(corporation_id=corp_id)
                moon_rentals = MoonRental.objects.filter(corporation=corp_obj, active=True)
            except EveCorporationInfo.DoesNotExist:
                moon_rentals = None

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