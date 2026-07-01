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
from .views import check_access, has_officer_access


# Generiert die PDF-Abrechnung für eine einzelne Corp und liefert sie als Download.
@login_required
@check_access(has_officer_access)
def download_corp_pdf(request, corp_id):
    year  = int(request.GET.get('year',  date.today().year))
    month = int(request.GET.get('month', date.today().month))

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
