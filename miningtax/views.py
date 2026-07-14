import logging
import traceback
from datetime import date

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from decimal import Decimal

from .models import MiningLedgerEntry, TaxRate, MoonRental, AllianceMoon, AllianceBillingRecord, TreasuryConfig
from .billing import calculate_entry_tax, calculate_alliance_billing, mark_corp_paid
from .services import sync_character_mining, update_market_prices, sync_all_corp_observers
from .forms import TaxRateForm, MoonRentalForm, AllianceMoonForm, TreasuryConfigForm

logger = logging.getLogger(__name__)


def has_basic_access(user):
    if user.is_superuser:
        return True
    return (
        user.has_perm('miningtax.basic_access') or
        user.has_perm('miningtax.mining_officer')
    )


def has_officer_access(user):
    if user.is_superuser:
        return True
    return user.has_perm('miningtax.mining_officer')


def check_access(test_func):
    def decorator(view_func):
        @login_required
        def wrapped(request, *args, **kwargs):
            if not test_func(request.user):
                raise PermissionDenied
            try:
                return view_func(request, *args, **kwargs)
            except Exception as e:
                logger.error(
                    f'Unexpected error in {view_func.__name__} '
                    f'(User: {request.user.username}, Args: {args}, Kwargs: {kwargs}): {e}\n'
                    f'{traceback.format_exc()}'
                )
                messages.error(request, f'❌ An unexpected error occurred: {e}')
                return redirect('miningtax:dashboard')
        return wrapped
    return decorator


def _prev_month(year, month):
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _next_month(year, month):
    if month == 12:
        return year + 1, 1
    return year, month + 1


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


@check_access(has_basic_access)
def sync_now(request):
    logger.info(f'Manual sync triggered by {request.user.username}')
    user_characters = [co.character for co in request.user.character_ownerships.all()]

    total_synced = 0
    for character in user_characters:
        try:
            total_synced += sync_character_mining(character)
        except Exception as e:
            logger.warning(f'Sync failed for {character.character_name}: {e}\n{traceback.format_exc()}')
            messages.warning(request, f'Sync failed for {character.character_name}: {e}')

    corp_synced = sync_all_corp_observers()
    priced = update_market_prices()
    logger.info(f'Manual sync completed: {total_synced} personal + {corp_synced} corp entries, {priced} prices updated')
    messages.success(
        request,
        f'✅ {total_synced} personal + {corp_synced} corp entries synced, {priced} prices updated'
    )

    return redirect('miningtax:dashboard')


@check_access(has_officer_access)
def alliance_overview(request):
    today = date.today()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))

    data = calculate_alliance_billing(year, month)

    paid_records = {
        r.corporation.corporation_id: r
        for r in AllianceBillingRecord.objects.filter(
            month=month, year=year
        ).select_related('corporation')
    }

    # Active moon rental total per corp — shown as its own line item on each corp card
    rental_totals = {}
    for rental in MoonRental.objects.filter(active=True).select_related('corporation'):
        corp_id = rental.corporation.corporation_id
        rental_totals[corp_id] = rental_totals.get(corp_id, Decimal('0')) + rental.monthly_fee

    corps_with_status = {}
    for corp_id, corp_data in data['corps'].items():
        record = paid_records.get(corp_id)
        rental_fee = rental_totals.get(corp_id, Decimal('0'))
        # Live total: tax + rental, always freshly calculated.
        # Once a record is marked paid, its stored total_due reflects what was
        # actually due at payment time and is used instead so paid invoices
        # don't change retroactively if rentals are edited afterwards.
        live_total_due = corp_data['total_tax'] + rental_fee
        total_due = record.total_due if (record and record.paid) else live_total_due

        corps_with_status[corp_id] = {
            **corp_data,
            'paid': record.paid if record else False,
            'paid_at': record.paid_at if record else None,
            'auto_verified': record.auto_verified if record else False,
            'moon_rental_total': rental_fee,
            'total_due': total_due,
        }

    # Also include corps that have an active moon rental but no mining activity this month —
    # they still owe the rental fee and should appear on the overview
    for corp_id, rental_fee in rental_totals.items():
        if corp_id not in corps_with_status:
            from allianceauth.eveonline.models import EveCorporationInfo
            try:
                corp_obj = EveCorporationInfo.objects.get(corporation_id=corp_id)
            except EveCorporationInfo.DoesNotExist:
                continue
            record = paid_records.get(corp_id)
            total_due = record.total_due if (record and record.paid) else rental_fee
            corps_with_status[corp_id] = {
                'corp_name': corp_obj.corporation_name,
                'total_mined': Decimal('0'),
                'total_tax': Decimal('0'),
                'members': {},
                'categories': {},
                'paid': record.paid if record else False,
                'paid_at': record.paid_at if record else None,
                'auto_verified': record.auto_verified if record else False,
                'moon_rental_total': rental_fee,
                'total_due': total_due,
            }

    prev_year, prev_month = _prev_month(year, month)
    next_year, next_month = _next_month(year, month)

    context = {
        'corps': corps_with_status,
        'totals': data['totals'],
        'year': year,
        'month': month,
        'prev_year': prev_year,
        'prev_month': prev_month,
        'next_year': next_year,
        'next_month': next_month,
    }
    return render(request, 'miningtax/alliance_overview.html', context)


@check_access(has_officer_access)
def mark_paid(request, corp_id):
    if request.method != 'POST':
        return redirect('miningtax:alliance_overview')

    year = int(request.POST.get('year', date.today().year))
    month = int(request.POST.get('month', date.today().month))

    data = calculate_alliance_billing(year, month)

    if corp_id not in data['corps']:
        logger.warning(f'{request.user.username}: mark_paid failed — no data for corp {corp_id} in {month}/{year}')
        messages.error(request, '❌ No data found for this corp this month.')
        return redirect(f"{reverse('miningtax:alliance_overview')}?year={year}&month={month}")

    corp_data = data['corps'][corp_id]
    record = mark_corp_paid(corp_id, corp_data, year, month)

    if record:
        logger.info(
            f'{request.user.username}: {corp_data["corp_name"]} for {month:02d}/{year} '
            f'MANUALLY marked as paid ({record.total_due} ISK)'
        )
        messages.success(
            request,
            f'✅ {corp_data["corp_name"]} marked as paid for {month:02d}/{year}.'
        )
    else:
        logger.warning(f'{request.user.username}: mark_paid failed — corp {corp_id} not found')
        messages.error(request, '❌ Corporation not found.')

    return redirect(f"{reverse('miningtax:alliance_overview')}?year={year}&month={month}")


@check_access(has_officer_access)
def mark_unpaid(request, corp_id):
    if request.method != 'POST':
        return redirect('miningtax:alliance_overview')

    year = int(request.POST.get('year', date.today().year))
    month = int(request.POST.get('month', date.today().month))

    from allianceauth.eveonline.models import EveCorporationInfo
    try:
        corp = EveCorporationInfo.objects.get(corporation_id=corp_id)
        record = AllianceBillingRecord.objects.get(corporation=corp, year=year, month=month)
        was_auto_verified = record.auto_verified
        record.paid = False
        record.paid_at = None
        record.auto_verified = False
        record.save(update_fields=['paid', 'paid_at', 'auto_verified'])

        logger.info(
            f'{request.user.username}: {corp.corporation_name} for {month:02d}/{year} '
            f'RESET to unpaid (was previously {"auto-verified" if was_auto_verified else "manually confirmed"})'
        )
        messages.success(request, f'↩️ {corp.corporation_name} reset to unpaid for {month:02d}/{year}.')
    except (EveCorporationInfo.DoesNotExist, AllianceBillingRecord.DoesNotExist) as e:
        logger.warning(f'{request.user.username}: mark_unpaid failed — {type(e).__name__}: corp {corp_id} / {month}/{year}')
        messages.error(request, '❌ No billing record found for this corp/month.')

    return redirect(f"{reverse('miningtax:alliance_overview')}?year={year}&month={month}")


@check_access(has_officer_access)
def check_payments_now(request):
    from .payments import check_corp_payments

    year = int(request.GET.get('year', date.today().year))
    month = int(request.GET.get('month', date.today().month))

    logger.info(f'{request.user.username}: manual payment check triggered for {month:02d}/{year}')
    matched = check_corp_payments(year, month)

    if matched > 0:
        messages.success(request, f'✅ {matched} payment(s) automatically detected and marked as paid.')
    else:
        messages.info(request, 'ℹ️ No new payments found. See log for details.')

    return redirect(f"{reverse('miningtax:alliance_overview')}?year={year}&month={month}")


# ─── SETTINGS-VIEWS ───────────────────────────────────────────────────────────

@check_access(has_officer_access)
def settings_view(request):
    tax_rates = TaxRate.objects.all().order_by('ore_category')
    moon_rentals = MoonRental.objects.select_related('corporation').order_by('corporation__corporation_name')
    alliance_moons = AllianceMoon.objects.all().order_by('solar_system_name', 'name')
    treasury_configs = TreasuryConfig.objects.select_related('corporation').all()

    tax_forms = [(tr, TaxRateForm(instance=tr, prefix=f'tax_{tr.pk}')) for tr in tax_rates]

    context = {
        'tax_forms': tax_forms,
        'moon_rentals': moon_rentals,
        'alliance_moons': alliance_moons,
        'treasury_configs': treasury_configs,
        'rental_form': MoonRentalForm(),
        'moon_form': AllianceMoonForm(),
        'treasury_form': TreasuryConfigForm(),
    }
    return render(request, 'miningtax/settings.html', context)


@check_access(has_officer_access)
def settings_save_taxrate(request, pk):
    tax_rate = get_object_or_404(TaxRate, pk=pk)
    if request.method == 'POST':
        form = TaxRateForm(request.POST, instance=tax_rate, prefix=f'tax_{pk}')
        if form.is_valid():
            form.save()
            logger.info(f'{request.user.username}: tax rate {tax_rate.ore_category} → {form.instance.tax_rate}%')
            messages.success(request, f'✅ Tax rate for {tax_rate.ore_category} saved.')
        else:
            logger.warning(f'{request.user.username}: TaxRate form invalid: {form.errors}')
            messages.error(request, f'❌ Error saving: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_add_rental(request):
    if request.method == 'POST':
        form = MoonRentalForm(request.POST)
        if form.is_valid():
            form.save()
            logger.info(f'{request.user.username}: moon rental added for {form.instance.corporation}')
            messages.success(request, '✅ Moon rental added.')
        else:
            logger.warning(f'{request.user.username}: MoonRental form invalid: {form.errors}')
            messages.error(request, f'❌ Error: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_delete_rental(request, pk):
    rental = get_object_or_404(MoonRental, pk=pk)
    if request.method == 'POST':
        corp_name = rental.corporation.corporation_name
        rental.delete()
        logger.info(f'{request.user.username}: moon rental for {corp_name} deleted')
        messages.success(request, f'🗑️ Rental for {corp_name} deleted.')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_add_moon(request):
    if request.method == 'POST':
        form = AllianceMoonForm(request.POST)
        if form.is_valid():
            form.save()
            logger.info(f'{request.user.username}: moon "{form.instance.name}" added')
            messages.success(request, '✅ Moon added.')
        else:
            logger.warning(f'{request.user.username}: AllianceMoon form invalid: {form.errors}')
            messages.error(request, f'❌ Error: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_edit_moon(request, pk):
    moon = get_object_or_404(AllianceMoon, pk=pk)
    if request.method == 'POST':
        form = AllianceMoonForm(request.POST, instance=moon)
        if form.is_valid():
            form.save()
            logger.info(f'{request.user.username}: moon "{moon.name}" updated')
            messages.success(request, f'✅ Moon "{moon.name}" updated.')
        else:
            logger.warning(f'{request.user.username}: AllianceMoon edit form invalid: {form.errors}')
            messages.error(request, f'❌ Error: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_delete_moon(request, pk):
    moon = get_object_or_404(AllianceMoon, pk=pk)
    if request.method == 'POST':
        name = moon.name
        moon.delete()
        logger.info(f'{request.user.username}: moon "{name}" deleted')
        messages.success(request, f'🗑️ Moon "{name}" deleted.')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_add_treasury(request):
    if request.method == 'POST':
        form = TreasuryConfigForm(request.POST)
        if form.is_valid():
            form.save()
            logger.info(f'{request.user.username}: treasury config added for {form.instance.corporation}')
            messages.success(request, '✅ Treasury configuration added.')
        else:
            logger.warning(f'{request.user.username}: TreasuryConfig form invalid: {form.errors}')
            messages.error(request, f'❌ Error: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_officer_access)
def settings_delete_treasury(request, pk):
    config = get_object_or_404(TreasuryConfig, pk=pk)
    if request.method == 'POST':
        corp_name = config.corporation.corporation_name
        config.delete()
        logger.info(f'{request.user.username}: treasury config for {corp_name} deleted')
        messages.success(request, f'🗑️ Treasury configuration for {corp_name} deleted.')
    return redirect('miningtax:settings')