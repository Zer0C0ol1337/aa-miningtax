from datetime import date

from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render, redirect, get_object_or_404
from django.core.exceptions import PermissionDenied
from django.contrib import messages

from .models import MiningLedgerEntry, TaxRate, MoonRental, AllianceMoon, AllianceBillingRecord
from .billing import calculate_entry_tax, calculate_alliance_billing, mark_corp_paid
from .services import sync_character_mining, update_market_prices
from .forms import TaxRateForm, MoonRentalForm, AllianceMoonForm


def has_basic_access(user):
    return (
        user.has_perm('miningtax.basic_access') or
        user.has_perm('miningtax.mining_officer') or
        user.has_perm('miningtax.admin_access')
    )


def has_officer_access(user):
    return (
        user.has_perm('miningtax.mining_officer') or
        user.has_perm('miningtax.admin_access')
    )


def check_access(test_func):
    def decorator(view_func):
        @login_required
        def wrapped(request, *args, **kwargs):
            if not test_func(request.user):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)
        return wrapped
    return decorator


# Persönliches Mining-Dashboard
@check_access(has_basic_access)
def dashboard(request):
    user_character_ids = request.user.character_ownerships.all().values_list('character_id', flat=True)

    today = date.today()
    entries = MiningLedgerEntry.objects.filter(
        character_id__in=user_character_ids,
        date__year=today.year,
        date__month=today.month,
    ).select_related('character').order_by('-date')

    rows = []
    total_mined_value = 0
    total_tax = 0

    for entry in entries:
        tax_info = calculate_entry_tax(entry)
        rows.append({
            'entry': entry,
            'category': tax_info['category'],
            'tax_rate': tax_info['tax_rate'],
            'tax_amount': tax_info['tax_amount'],
            'excluded': tax_info['excluded'],
        })
        total_mined_value += entry.total_value
        total_tax += tax_info['tax_amount']

    context = {
        'rows': rows,
        'total_mined_value': total_mined_value,
        'total_tax': total_tax,
        'month': today.strftime('%B %Y'),
        'is_officer': has_officer_access(request.user),
    }
    return render(request, 'miningtax/dashboard.html', context)


# Manueller Sync
@check_access(has_basic_access)
def sync_now(request):
    user_characters = [co.character for co in request.user.character_ownerships.all()]

    total_synced = 0
    for character in user_characters:
        try:
            total_synced += sync_character_mining(character)
        except Exception as e:
            messages.warning(request, f'Sync für {character.character_name} fehlgeschlagen: {e}')

    priced = update_market_prices()
    messages.success(request, f'✅ {total_synced} Einträge synchronisiert, {priced} Preise aktualisiert')

    return redirect('miningtax:dashboard')


# Alliance-weite Abrechnungsübersicht
@check_access(has_officer_access)
def alliance_overview(request):
    today = date.today()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))

    data = calculate_alliance_billing(year, month)

    # Bezahlstatus für alle Corps dieses Monats laden
    paid_records = {
        r.corporation.corporation_id: r
        for r in AllianceBillingRecord.objects.filter(
            month=month, year=year
        ).select_related('corporation')
    }

    # Corps-Daten mit Bezahlstatus anreichern
    corps_with_status = {}
    for corp_id, corp_data in data['corps'].items():
        record = paid_records.get(corp_id)
        corps_with_status[corp_id] = {
            **corp_data,
            'paid': record.paid if record else False,
            'paid_at': record.paid_at if record else None,
        }

    context = {
        'corps': corps_with_status,
        'totals': data['totals'],
        'year': year,
        'month': month,
    }
    return render(request, 'miningtax/alliance_overview.html', context)


# Corp als bezahlt markieren
@check_access(has_officer_access)
def mark_paid(request, corp_id):
    if request.method != 'POST':
        return redirect('miningtax:alliance_overview')

    year = int(request.POST.get('year', date.today().year))
    month = int(request.POST.get('month', date.today().month))

    # Aktuelle Abrechnung berechnen und als bezahlt speichern
    data = calculate_alliance_billing(year, month)

    if corp_id not in data['corps']:
        messages.error(request, '❌ Keine Daten für diese Corp in diesem Monat.')
        return redirect(f"{request.path}?year={year}&month={month}")

    corp_data = data['corps'][corp_id]
    record = mark_corp_paid(corp_id, corp_data, year, month)

    if record:
        messages.success(
            request,
            f'✅ {corp_data["corp_name"]} für {month:02d}/{year} als bezahlt markiert.'
        )
    else:
        messages.error(request, '❌ Corp nicht gefunden.')

    return redirect(f"{'/'.join(request.build_absolute_uri().split('/')[:3])}/miningtax/alliance/?year={year}&month={month}")


# ─── SETTINGS-VIEWS ───────────────────────────────────────────────────────────

@check_access(has_officer_access)
def settings_view(request):
    tax_rates = TaxRate.objects.all().order_by('ore_category')
    moon_rentals = MoonRental.objects.select_related('corporation').order_by('corporation__corporation_name')
    alliance_moons = AllianceMoon.objects.all().order_by('solar_system_name', 'name')

    tax_forms = [(tr, TaxRateForm(instance=tr, prefix=f'tax_{tr.pk}')) for tr in tax_rates]

    context = {
        'tax_forms': tax_forms,
        'moon_rentals': moon_rentals,
        'alliance_moons': alliance_moons,
        'rental_form': MoonRentalForm(),
        'moon_form': AllianceMoonForm(),
    }
    return render(request, 'miningtax/settings.html', context)


@check_access(has_officer_access)
def settings_save_taxrate(request, pk):
    tax_rate = get_object_or_404(TaxRate, pk=pk)
    if request.method == 'POST':
        form = TaxRateForm(request.POST, instance=tax_rate, prefix=f'tax_{pk}')
        if form.is_valid():
            form.save()
            messages.success(request, f'✅ Steuersatz für {tax_rate.ore_category} gespeichert.')
        else:
            messages.error(request, f'❌ Fehler beim Speichern: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_add_rental(request):
    if request.method == 'POST':
        form = MoonRentalForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, '✅ Moon Rental angelegt.')
        else:
            messages.error(request, f'❌ Fehler: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_delete_rental(request, pk):
    rental = get_object_or_404(MoonRental, pk=pk)
    if request.method == 'POST':
        corp_name = rental.corporation.corporation_name
        rental.delete()
        messages.success(request, f'🗑️ Rental für {corp_name} gelöscht.')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_add_moon(request):
    if request.method == 'POST':
        form = AllianceMoonForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, '✅ Mond angelegt.')
        else:
            messages.error(request, f'❌ Fehler: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_edit_moon(request, pk):
    moon = get_object_or_404(AllianceMoon, pk=pk)
    if request.method == 'POST':
        form = AllianceMoonForm(request.POST, instance=moon)
        if form.is_valid():
            form.save()
            messages.success(request, f'✅ Mond "{moon.name}" aktualisiert.')
        else:
            messages.error(request, f'❌ Fehler: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_delete_moon(request, pk):
    moon = get_object_or_404(AllianceMoon, pk=pk)
    if request.method == 'POST':
        name = moon.name
        moon.delete()
        messages.success(request, f'🗑️ Mond "{name}" gelöscht.')
    return redirect('miningtax:settings')
