import logging
import traceback
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from decimal import Decimal

from .models import (
    MiningLedgerEntry, TaxRate, MoonRental, AllianceMoon, AllianceBillingRecord,
    TreasuryConfig, SovFilterConfig, JaniceConfig,
)
from .billing import calculate_entry_tax, calculate_alliance_billing, mark_corp_paid
from .services import sync_character_mining, update_market_prices, sync_all_corp_observers
from .forms import (
    TaxRateForm, MoonRentalForm, AllianceMoonForm, TreasuryConfigForm,
    SovFilterConfigForm, JaniceConfigForm,
)

logger = logging.getLogger(__name__)


def has_basic_access(user):
    if user.is_superuser:
        return True
    if user.has_perm('miningtax.basic_access'):
        return True
    return has_officer_access(user)


def has_officer_access(user):
    if user.is_superuser:
        return True
    if user.has_perm('miningtax.mining_officer'):
        return True
    return get_ceo_corp_id(user) is not None


def get_ceo_corp_id(user):
    """
    Returns the corporation_id if the user is the CEO of a corp (via any of
    their registered characters), otherwise None. Requires
    EveCorporationInfo.ceo_id to be populated (set by Alliance Auth's
    periodic corp update task).
    """
    from allianceauth.eveonline.models import EveCorporationInfo
    for co in user.character_ownerships.select_related('character').all():
        char = co.character
        corp = EveCorporationInfo.objects.filter(corporation_id=char.corporation_id).first()
        if corp and corp.ceo_id and corp.ceo_id == char.character_id:
            return corp.corporation_id
    return None


def is_ceo_only(user):
    """
    True if the user only has officer access because they're a CEO
    (auto-detected), not because of the mining_officer permission or
    superuser status.
    """
    if user.is_superuser or user.has_perm('miningtax.mining_officer'):
        return False
    return get_ceo_corp_id(user) is not None


def has_full_officer_access(user):
    """Real officer access — permission or superuser only, not the CEO
    auto-bypass. Used for Settings and alliance-wide actions."""
    return user.is_superuser or user.has_perm('miningtax.mining_officer')


def _own_alliance_ids(user):
    """
    The alliance ID(s) of the user's own registered characters — used to
    scope corporation dropdowns to the officer's own alliance by default,
    instead of every alliance/corp Alliance Auth has ever resolved via ESI.
    """
    return {
        co.character.alliance_id
        for co in user.character_ownerships.select_related('character').all()
        if co.character.alliance_id
    }


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
    from calendar import month_name

    today = date.today()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))

    user_character_ids = request.user.character_ownerships.all().values_list('character_id', flat=True)

    entries = MiningLedgerEntry.objects.filter(
        character_id__in=user_character_ids,
        date__year=year,
        date__month=month,
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

    prev_year, prev_month = _prev_month(year, month)
    next_year, next_month = _next_month(year, month)

    context = {
        'rows': rows,
        'total_mined_value': total_mined_value,
        'total_tax': total_tax,
        'month': f'{month_name[month]} {year}',
        'year': year,
        'month_num': month,
        'prev_year': prev_year,
        'prev_month': prev_month,
        'next_year': next_year,
        'next_month': next_month,
        'is_officer': has_officer_access(request.user),
        'is_full_officer': has_full_officer_access(request.user),
    }
    return render(request, 'miningtax/dashboard.html', context)


@check_access(has_basic_access)
def sync_now(request):
    from .tasks import manual_sync_task

    logger.info(f'Manual sync queued by {request.user.username}')
    manual_sync_task.delay(request.user.id)

    messages.success(
        request,
        '✅ Sync started in the background. This may take a few minutes for large corps — '
        'check the log or refresh this page shortly.'
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

    rental_totals = {}
    for rental in MoonRental.objects.filter(active=True).select_related('corporation'):
        corp_id = rental.corporation.corporation_id
        rental_totals[corp_id] = rental_totals.get(corp_id, Decimal('0')) + rental.monthly_fee

    corps_with_status = {}
    for corp_id, corp_data in data['corps'].items():
        record = paid_records.get(corp_id)
        rental_fee = rental_totals.get(corp_id, Decimal('0'))
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

    restricted_to_corp = None
    if is_ceo_only(request.user):
        restricted_to_corp = get_ceo_corp_id(request.user)
        corps_with_status = {
            cid: cdata for cid, cdata in corps_with_status.items()
            if cid == restricted_to_corp
        }

    from .payments import payment_code_for
    next_year, next_month = _next_month(year, month)
    reveal_date = date(next_year, next_month, 1) + timedelta(days=1)
    code_revealed = today >= reveal_date
    for cid, cdata in corps_with_status.items():
        cdata['payment_code'] = payment_code_for(cid, month, year) if code_revealed else None

    prev_year, prev_month = _prev_month(year, month)

    context = {
        'corps': corps_with_status,
        'totals': data['totals'],
        'year': year,
        'month': month,
        'prev_year': prev_year,
        'prev_month': prev_month,
        'next_year': next_year,
        'next_month': next_month,
        'restricted_to_corp': restricted_to_corp,
        'is_full_officer': has_full_officer_access(request.user),
    }
    return render(request, 'miningtax/alliance_overview.html', context)


@check_access(has_officer_access)
def mark_paid(request, corp_id):
    if request.method != 'POST':
        return redirect('miningtax:alliance_overview')

    if is_ceo_only(request.user) and get_ceo_corp_id(request.user) != corp_id:
        logger.warning(f'{request.user.username}: attempted mark_paid on corp {corp_id} outside their own corp — denied')
        messages.error(request, '❌ You can only manage billing for your own corporation.')
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

    if is_ceo_only(request.user) and get_ceo_corp_id(request.user) != corp_id:
        logger.warning(f'{request.user.username}: attempted mark_unpaid on corp {corp_id} outside their own corp — denied')
        messages.error(request, '❌ You can only manage billing for your own corporation.')
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


@check_access(has_full_officer_access)
def check_payments_now(request):
    from .tasks import check_payments_task

    year = int(request.GET.get('year', date.today().year))
    month = int(request.GET.get('month', date.today().month))

    logger.info(f'{request.user.username}: payment check queued for {month:02d}/{year}')
    check_payments_task.delay(year, month, requested_by=request.user.username)

    messages.success(
        request,
        '✅ Payment check started in the background — check the log or refresh '
        'this page shortly for results.'
    )
    return redirect(f"{reverse('miningtax:alliance_overview')}?year={year}&month={month}")


# ─── SETTINGS-VIEWS ───────────────────────────────────────────────────────────

@check_access(has_full_officer_access)
def settings_view(request):
    _STANDARD_CATEGORIES = {
        'Default': 10.00,
        'Mercoxit': 10.00,
        'Ore': 10.00,
        'Ice': 10.00,
        'Gas': 10.00,
        'R4': 0.00,
        'R8': 0.00,
        'R16': 0.00,
        'R32': 20.00,
        'R64': 30.00,
    }
    for category, default_rate in _STANDARD_CATEGORIES.items():
        TaxRate.objects.get_or_create(
            ore_category=category,
            defaults={'tax_rate': default_rate}
        )

    tax_rates = TaxRate.objects.all().order_by('ore_category')
    moon_rentals = MoonRental.objects.select_related('corporation').order_by('corporation__corporation_name')
    alliance_moons = AllianceMoon.objects.all().order_by('solar_system_name', 'name')
    treasury_configs = TreasuryConfig.objects.select_related('corporation').all()
    sov_filter_configs = SovFilterConfig.objects.select_related('corporation').all()
    janice_config = JaniceConfig.get_solo()

    from allianceauth.eveonline.models import EveAllianceInfo
    alliance_ids = _own_alliance_ids(request.user)
    if alliance_ids:
        alliances = EveAllianceInfo.objects.filter(alliance_id__in=alliance_ids).order_by('alliance_name')
    else:
        alliances = EveAllianceInfo.objects.filter(
            evecorporationinfo__isnull=False
        ).distinct().order_by('alliance_name')

    # Known structure names already seen in the ledger, for the Moon Rental
    # "structure name" field's autocomplete — officers pick from what's
    # actually been mined instead of typing it by hand.
    known_structures = list(
        MiningLedgerEntry.objects.exclude(solar_system_name='')
        .values_list('solar_system_name', flat=True).distinct().order_by('solar_system_name')
    )

    tax_forms = [(tr, TaxRateForm(instance=tr, prefix=f'tax_{tr.pk}')) for tr in tax_rates]

    context = {
        'tax_forms': tax_forms,
        'moon_rentals': moon_rentals,
        'alliance_moons': alliance_moons,
        'treasury_configs': treasury_configs,
        'sov_filter_configs': sov_filter_configs,
        'alliances': alliances,
        'known_structures': known_structures,
        'rental_form': MoonRentalForm(alliance_ids=alliance_ids),
        'moon_form': AllianceMoonForm(),
        'treasury_form': TreasuryConfigForm(alliance_ids=alliance_ids),
        'sov_filter_form': SovFilterConfigForm(alliance_ids=alliance_ids),
        'janice_config': janice_config,
        'janice_form': JaniceConfigForm(instance=janice_config),
    }
    return render(request, 'miningtax/settings.html', context)


@check_access(has_full_officer_access)
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


@check_access(has_full_officer_access)
def settings_add_rental(request):
    if request.method == 'POST':
        form = MoonRentalForm(request.POST, alliance_ids=_own_alliance_ids(request.user))
        if form.is_valid():
            form.save()
            logger.info(f'{request.user.username}: moon rental added for {form.instance.corporation}')
            messages.success(request, '✅ Moon rental added.')
        else:
            logger.warning(f'{request.user.username}: MoonRental form invalid: {form.errors}')
            messages.error(request, f'❌ Error: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_delete_rental(request, pk):
    rental = get_object_or_404(MoonRental, pk=pk)
    if request.method == 'POST':
        corp_name = rental.corporation.corporation_name
        rental.delete()
        logger.info(f'{request.user.username}: moon rental for {corp_name} deleted')
        messages.success(request, f'🗑️ Rental for {corp_name} deleted.')
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
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


@check_access(has_full_officer_access)
def settings_edit_moon(request, pk):
    moon = get_object_or_404(AllianceMoon, pk=pk)
    if request.method == 'POST':
        form = AllianceMoonForm(request.POST, instance=moon)
        if form.is_valid():
            form.save()
            logger.info(f'{request.user.username}: moon "{moon.name}" updated → category={moon.ore_category}, tax_free={moon.is_tax_free}')
            messages.success(request, f'✅ Moon "{moon.name}" updated.')
        else:
            logger.warning(f'{request.user.username}: AllianceMoon edit form invalid: {form.errors}')
            messages.error(request, f'❌ Error updating moon: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_delete_moon(request, pk):
    moon = get_object_or_404(AllianceMoon, pk=pk)
    if request.method == 'POST':
        name = moon.name
        moon.delete()
        logger.info(f'{request.user.username}: moon "{name}" deleted')
        messages.success(request, f'🗑️ Moon "{name}" deleted.')
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_add_treasury(request):
    if request.method == 'POST':
        form = TreasuryConfigForm(request.POST, alliance_ids=_own_alliance_ids(request.user))
        if form.is_valid():
            form.save()
            logger.info(f'{request.user.username}: treasury config added for {form.instance.corporation}')
            messages.success(request, '✅ Treasury configuration added.')
        else:
            logger.warning(f'{request.user.username}: TreasuryConfig form invalid: {form.errors}')
            messages.error(request, f'❌ Error: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_delete_treasury(request, pk):
    config = get_object_or_404(TreasuryConfig, pk=pk)
    if request.method == 'POST':
        corp_name = config.corporation.corporation_name
        config.delete()
        logger.info(f'{request.user.username}: treasury config for {corp_name} deleted')
        messages.success(request, f'🗑️ Treasury configuration for {corp_name} deleted.')
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_register_corp(request):
    if request.method == 'POST':
        from allianceauth.eveonline.models import EveCorporationInfo

        raw_id = request.POST.get('corporation_id', '').strip()
        try:
            corp_id = int(raw_id)
        except ValueError:
            messages.error(request, f'❌ "{raw_id}" is not a valid corporation ID.')
            return redirect('miningtax:settings')

        if EveCorporationInfo.objects.filter(corporation_id=corp_id).exists():
            messages.info(request, 'ℹ️ This corporation is already registered.')
            return redirect('miningtax:settings')

        try:
            corp = EveCorporationInfo.objects.create_corporation(corporation_id=corp_id)
            logger.info(f'{request.user.username}: registered corporation {corp.corporation_name} ({corp_id}) via ESI')
            messages.success(request, f'✅ {corp.corporation_name} registered and now available in the dropdowns.')
        except Exception as e:
            logger.warning(f'{request.user.username}: failed to register corporation {corp_id}: {e}')
            messages.error(request, f'❌ Could not fetch corporation {corp_id}: {e}')

    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_register_alliance_corps(request):
    if request.method == 'POST':
        from allianceauth.eveonline.models import EveCorporationInfo
        from .services import _get_esi_client

        raw_id = request.POST.get('alliance_id', '').strip()
        try:
            alliance_id = int(raw_id)
        except ValueError:
            messages.error(request, f'❌ "{raw_id}" is not a valid alliance ID.')
            return redirect('miningtax:settings')

        try:
            esi = _get_esi_client()
            corp_ids = esi.client.Alliance.GetAlliancesAllianceIdCorporations(
                alliance_id=alliance_id
            ).results()
        except Exception as e:
            logger.warning(f'{request.user.username}: failed to fetch corp list for alliance {alliance_id}: {e}')
            messages.error(request, f'❌ Could not fetch corporations for this alliance: {e}')
            return redirect('miningtax:settings')

        registered = 0
        already_present = 0
        failed = 0

        for corp_id in corp_ids:
            if EveCorporationInfo.objects.filter(corporation_id=corp_id).exists():
                already_present += 1
                continue
            try:
                EveCorporationInfo.objects.create_corporation(corporation_id=corp_id)
                registered += 1
            except Exception as e:
                logger.warning(f'{request.user.username}: failed to register corp {corp_id} from alliance {alliance_id}: {e}')
                failed += 1

        logger.info(
            f'{request.user.username}: registered alliance {alliance_id} corps — '
            f'{registered} new, {already_present} already present, {failed} failed'
        )
        messages.success(
            request,
            f'✅ {registered} new corporation(s) registered, {already_present} already present'
            + (f', {failed} failed' if failed else '') + '.'
        )

    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_add_sov_filter(request):
    if request.method == 'POST':
        form = SovFilterConfigForm(request.POST, alliance_ids=_own_alliance_ids(request.user))
        if form.is_valid():
            form.save()
            logger.info(f'{request.user.username}: sov filter added for {form.instance.corporation}')
            messages.success(request, '✅ Sovereignty filter added. Run "Sync Sovereignty Now" or wait for the daily sync to populate its systems.')
        else:
            logger.warning(f'{request.user.username}: SovFilterConfig form invalid: {form.errors}')
            messages.error(request, f'❌ Error: {form.errors}')
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_delete_sov_filter(request, pk):
    config = get_object_or_404(SovFilterConfig, pk=pk)
    if request.method == 'POST':
        corp_name = config.corporation.corporation_name
        config.delete()
        logger.info(f'{request.user.username}: sov filter for {corp_name} deleted')
        messages.success(request, f'🗑️ Sovereignty filter for {corp_name} deleted.')
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_sync_sov_now(request):
    from .services import sync_sov_systems

    logger.info(f'{request.user.username}: manual sovereignty sync triggered')
    count = sync_sov_systems()
    messages.success(request, f'✅ Sovereignty sync complete — {count} system(s) tracked.')
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_save_janice(request):
    if request.method == 'POST':
        config = JaniceConfig.get_solo()
        form = JaniceConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            logger.info(f'{request.user.username}: Janice config saved (enabled={form.instance.enabled})')
            messages.success(request, '✅ Janice configuration saved.')
        else:
            logger.warning(f'{request.user.username}: JaniceConfig form invalid: {form.errors}')
            messages.error(request, f'❌ Error: {form.errors}')
    return redirect('miningtax:settings')