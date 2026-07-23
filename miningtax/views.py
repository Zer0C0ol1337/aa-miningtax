import logging
import traceback
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from decimal import Decimal, InvalidOperation

from .models import (
    MiningLedgerEntry, TaxRate, MoonRental, AllianceMoon, AllianceBillingRecord,
    TreasuryConfig, SovFilterConfig, JaniceConfig, TaxExemption, SovSystem,
)
from .billing import calculate_entry_tax, calculate_alliance_billing, mark_corp_paid
from .services import sync_character_mining, update_market_prices, sync_all_corp_observers
from .forms import (
    TaxRateForm, MoonRentalForm, AllianceMoonForm, TreasuryConfigForm,
    SovFilterConfigForm, JaniceConfigForm, TaxExemptionForm,
)

logger = logging.getLogger(__name__)


def has_basic_access(user):
    if user.is_superuser:
        return True
    if user.has_perm('miningtax.basic_access'):
        return True
    return has_officer_access(user)


def has_officer_access(user):
    """
    Access to billing views at all — either alliance-wide or, with
    corp_billing, limited to the holder's own corporation.
    """
    if user.is_superuser:
        return True
    return (
        user.has_perm('miningtax.mining_officer')
        or user.has_perm('miningtax.corp_billing')
    )


def own_corporation_id(user):
    """
    The corporation a corp_billing holder is limited to: the corporation of
    their main character, falling back to any registered character.

    Who deserves that access is a decision for whoever runs the Auth instance,
    expressed by assigning the permission — earlier versions detected CEOs from
    EveCorporationInfo.ceo_id and granted access automatically, which meant the
    plugin decided, the grant appeared nowhere in the permission UI, and any alt
    who happened to be CEO of some unrelated corp brought it along.
    """
    try:
        main = user.profile.main_character
        if main and main.corporation_id:
            return main.corporation_id
    except Exception:
        pass

    for co in user.character_ownerships.select_related('character').all():
        if co.character.corporation_id:
            return co.character.corporation_id
    return None


def is_corp_scoped(user):
    """
    True when the user may see billing, but only for their own corporation.
    """
    if user.is_superuser or user.has_perm('miningtax.mining_officer'):
        return False
    return user.has_perm('miningtax.corp_billing')


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

    # Characters without a personal mining token. Their belt and anomaly mining
    # is invisible here — moon mining still shows up, because that arrives via
    # the corp observer sync and needs no token of the pilot's own. The result
    # is a ledger that looks like the player only ever mined moons, with nothing
    # on screen to explain the gap, so it is named explicitly.
    characters_without_token = _characters_missing_mining_token(request.user)

    prev_year, prev_month = _prev_month(year, month)
    next_year, next_month = _next_month(year, month)

    context = {
        'rows': rows,
        'characters_without_token': characters_without_token,
        'add_token_url': _add_character_url(),
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


@check_access(has_full_officer_access)
def sync_now(request):
    # Refreshing every corporation's observers is an alliance-wide operation,
    # so it sits with the other alliance-wide actions behind the real
    # permission rather than the CEO bypass — a CEO reads their own corp, they
    # do not spend rate limit on everyone's behalf.
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
    totals = data['totals']

    if is_corp_scoped(request.user):
        restricted_to_corp = own_corporation_id(request.user)
        corps_with_status = {
            cid: cdata for cid, cdata in corps_with_status.items()
            if cid == restricted_to_corp
        }
        # The corp list was already filtered, but the totals were not — a CEO
        # could read the alliance's entire mined value and tax income off the
        # summary cards while seeing only their own corp below. Recomputed from
        # what they are actually allowed to see.
        totals = {
            'mined': sum((c['total_mined'] for c in corps_with_status.values()), Decimal('0')),
            'tax': sum((c['total_tax'] for c in corps_with_status.values()), Decimal('0')),
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
        'totals': totals,
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

    if is_corp_scoped(request.user) and own_corporation_id(request.user) != corp_id:
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

    if is_corp_scoped(request.user) and own_corporation_id(request.user) != corp_id:
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

    # Solar systems offered in the moon dropdowns. Sourced from the sovereignty
    # cache so officers only ever see their own space instead of all ~8000 EVE
    # systems. Empty until the sov sync has run at least once.
    # Ore-list health, shown on the tax rates tab. "Uncategorized" counts types
    # that actually appear in mining data but have no category — those are the
    # ones silently taxed at the Default rate, so they're worth surfacing.
    from .models import OreCategory
    ore_type_count = OreCategory.objects.count()
    mined_type_ids = set(
        MiningLedgerEntry.objects.values_list('type_id', flat=True).distinct()
    )
    known_type_ids = set(OreCategory.objects.values_list('type_id', flat=True))
    ore_uncategorized = len(mined_type_ids - known_type_ids)

    # Categories that ore can end up in but which have no rate yet — those are
    # silently billed at the Default rate, so they're surfaced for one-click
    # creation instead of requiring a trip to the Django admin.
    from .models import OreCategoryRule
    used_categories = set(
        OreCategory.objects.values_list('category', flat=True).distinct()
    ) | set(
        OreCategoryRule.objects.filter(active=True).values_list('category', flat=True)
    )
    rated_categories = set(TaxRate.objects.values_list('ore_category', flat=True))
    categories_without_rate = sorted(c for c in used_categories - rated_categories if c)

    sov_systems = SovSystem.objects.order_by('system_name')

    # Corporations offered in the structure-corp picker on the moon forms. Scoped
    # to the officer's own alliance(s) by default so the list stays short, and
    # each carries its real EVE alliance_id so the alliance filter can narrow it
    # client-side just like the other corp dropdowns.
    from allianceauth.eveonline.models import EveCorporationInfo
    structure_corps = []
    for corp in EveCorporationInfo.objects.select_related('alliance').order_by('corporation_name'):
        if alliance_ids and (not corp.alliance_id or corp.alliance.alliance_id not in alliance_ids):
            continue
        structure_corps.append({
            'corporation_id': corp.corporation_id,
            'corporation_name': corp.corporation_name,
            'alliance_eve_id': corp.alliance.alliance_id if corp.alliance_id else '',
        })
    # Shown in the Sovereignty tab so officers can tell at a glance whether the
    # sov cache is populated — without needing shell access on a live server.
    sov_last_sync = SovSystem.objects.order_by('-updated_at').values_list(
        'updated_at', flat=True
    ).first()

    tax_exemptions = TaxExemption.objects.select_related(
        'character', 'corporation'
    ).order_by('-active', 'corporation__corporation_name', 'character__character_name')

    context = {
        'tax_forms': tax_forms,
        'ore_type_count': ore_type_count,
        'categories_without_rate': categories_without_rate,
        'ore_uncategorized': ore_uncategorized,
        'sov_systems': sov_systems,
        'structure_corps': structure_corps,
        'sov_last_sync': sov_last_sync,
        'tax_exemptions': tax_exemptions,
        'exemption_form': TaxExemptionForm(alliance_ids=alliance_ids),
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
    # Explicit officer action, so the daily ETag-recovery limit is lifted here.
    count = sync_sov_systems(force_recovery=True)
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


@check_access(has_full_officer_access)
def settings_add_exemption(request):
    """Adds a tax exemption for either one character or a whole corporation."""
    if request.method == 'POST':
        alliance_ids = _own_alliance_ids(request.user)
        form = TaxExemptionForm(request.POST, alliance_ids=alliance_ids)
        if form.is_valid():
            exemption = form.save()
            logger.info(f'{request.user.username}: tax exemption added → {exemption}')
            messages.success(request, f'✅ Exemption added: {exemption}')
        else:
            logger.warning(f'{request.user.username}: TaxExemption form invalid: {form.errors}')
            error_text = '; '.join(
                str(e) for errors in form.errors.values() for e in errors
            )
            messages.error(request, f'❌ {error_text}')
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_delete_exemption(request, pk):
    """Removes an exemption entirely — use toggle if it should only pause."""
    exemption = get_object_or_404(TaxExemption, pk=pk)
    if request.method == 'POST':
        label = str(exemption)
        exemption.delete()
        logger.info(f'{request.user.username}: tax exemption deleted → {label}')
        messages.success(request, f'🗑️ Exemption removed: {label}')
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_toggle_exemption(request, pk):
    """
    Pauses or resumes an exemption without losing it. Handy for temporary
    arrangements (events, trial periods) that come back later.
    """
    exemption = get_object_or_404(TaxExemption, pk=pk)
    if request.method == 'POST':
        exemption.active = not exemption.active
        exemption.save(update_fields=['active'])
        state = 'activated' if exemption.active else 'paused'
        logger.info(f'{request.user.username}: tax exemption {state} → {exemption}')
        messages.success(request, f'✅ Exemption {state}: {exemption}')
    return redirect('miningtax:settings')


@check_access(has_basic_access)
def pilot_detail(request, character_id):
    """
    Every ledger entry of a single player for one month — the main plus all
    alts owned by the same Auth account, since tax is assessed per player.

    Members reach this from their own dashboard and may only open their own
    characters; officers reach it from the alliance billing member list and may
    open anyone, with CEOs limited to their own corporation as elsewhere.
    """
    from calendar import month_name
    from allianceauth.eveonline.models import EveCharacter

    today = date.today()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))

    main = get_object_or_404(EveCharacter, character_id=character_id)

    # Any character on the requesting user's own account is fair game — the page
    # shows that account either way, so there is nothing to withhold.
    own_character_pks = set(
        request.user.character_ownerships.all().values_list('character_id', flat=True)
    )
    is_own_character = main.pk in own_character_pks
    is_officer = has_officer_access(request.user)

    if not is_own_character:
        if not is_officer:
            messages.error(request, '❌ You can only view your own characters.')
            return redirect('miningtax:dashboard')

        restricted_to_corp = own_corporation_id(request.user) if is_corp_scoped(request.user) else None
        if restricted_to_corp and main.corporation_id != restricted_to_corp:
            messages.error(request, '❌ You can only view pilots of your own corporation.')
            return redirect('miningtax:alliance_overview')

    # All characters of the owning account. Falls back to the single character
    # when it isn't registered in Auth, so an unlinked pilot still shows its own
    # mining rather than an empty page.
    try:
        user = main.character_ownership.user
        character_ids = list(
            user.character_ownerships.all().values_list('character_id', flat=True)
        )
        characters = EveCharacter.objects.filter(pk__in=character_ids).order_by('character_name')
    except Exception:
        characters = EveCharacter.objects.filter(pk=main.pk)

    entries = MiningLedgerEntry.objects.filter(
        character__in=characters,
        date__year=year,
        date__month=month,
    ).select_related('character').order_by('-date', 'character__character_name')

    rows = []
    totals = {'mined': Decimal('0'), 'tax': Decimal('0')}
    per_character = {}
    per_category = {}

    for entry in entries:
        corp = _corp_for_entry(entry)
        tax_info = calculate_entry_tax(entry, corporation=corp)

        rows.append({
            'entry': entry,
            'category': tax_info['category'],
            'tax_rate': tax_info['tax_rate'],
            'tax_amount': tax_info['tax_amount'],
            'excluded': tax_info['excluded'],
        })

        totals['mined'] += entry.total_value
        totals['tax'] += tax_info['tax_amount']

        name = entry.character.character_name
        bucket = per_character.setdefault(name, {'mined': Decimal('0'), 'tax': Decimal('0')})
        bucket['mined'] += entry.total_value
        bucket['tax'] += tax_info['tax_amount']

        cat = tax_info['category']
        cat_bucket = per_category.setdefault(
            cat, {'value': Decimal('0'), 'tax': Decimal('0'), 'rate': tax_info['tax_rate']}
        )
        cat_bucket['value'] += entry.total_value
        cat_bucket['tax'] += tax_info['tax_amount']

    # Assemble one row per character up front rather than looking values up in
    # the template — every character appears, including those with no mining
    # this month, which is exactly how an alt that never synced becomes visible.
    character_rows = []
    for character in characters:
        stats = per_character.get(character.character_name)
        character_rows.append({
            'name': character.character_name,
            'is_main': character.character_id == main.character_id,
            'has_data': stats is not None,
            'mined': stats['mined'] if stats else Decimal('0'),
            'tax': stats['tax'] if stats else Decimal('0'),
        })

    prev_year, prev_month = _prev_month(year, month)
    next_year, next_month = _next_month(year, month)

    context = {
        'main': main,
        'is_officer': is_officer,
        # Drives the back link. Deliberately keyed on whose account is being
        # viewed rather than on the viewer's role: an officer looking at their
        # own characters got here from their dashboard, not from billing.
        'viewing_own': is_own_character,
        'characters': characters,
        'character_rows': character_rows,
        'rows': rows,
        'totals': totals,
        'per_character': per_character,
        'per_category': per_category,
        'month': f'{month_name[month]} {year}',
        'year': year,
        'month_num': month,
        'prev_year': prev_year,
        'prev_month': prev_month,
        'next_year': next_year,
        'next_month': next_month,
    }
    return render(request, 'miningtax/pilot_detail.html', context)


def _corp_for_entry(entry):
    """Corporation object for a ledger entry, needed for moon-rental checks."""
    from allianceauth.eveonline.models import EveCorporationInfo
    try:
        return EveCorporationInfo.objects.get(corporation_id=entry.character.corporation_id)
    except EveCorporationInfo.DoesNotExist:
        return None


@check_access(has_full_officer_access)
def settings_sync_ore_categories(request):
    """
    Imports every mineable type from ESI and classifies it. Exposed as a button
    so an officer can repair the ore table from the browser — the alternative
    would be shell access on a live server.
    """
    if request.method != 'POST':
        return redirect('miningtax:settings')

    from .services import sync_ore_categories

    imported, updated = sync_ore_categories()
    if imported or updated:
        messages.success(
            request,
            f'✅ Ore list refreshed — {imported} new, {updated} updated.'
        )
    else:
        messages.warning(
            request,
            '⚠️ No ore types imported. Check the server log for the ESI error.'
        )
    return redirect('miningtax:settings')


@check_access(has_full_officer_access)
def settings_add_taxrate(request):
    """
    Creates a rate for a category that has none yet. Without this the only way
    to give a new category its own rate was the Django admin, which rather
    defeats a settings page.
    """
    if request.method != 'POST':
        return redirect('miningtax:settings')

    category = (request.POST.get('ore_category') or '').strip()
    rate = (request.POST.get('tax_rate') or '').strip()
    description = (request.POST.get('description') or '').strip()

    if not category:
        messages.error(request, '❌ Pick a category.')
        return redirect('miningtax:settings')

    try:
        rate_value = Decimal(rate or '0')
    except (InvalidOperation, TypeError):
        messages.error(request, '❌ That tax rate is not a number.')
        return redirect('miningtax:settings')

    if rate_value < 0 or rate_value > 100:
        messages.error(request, '❌ Tax rate must be between 0 and 100.')
        return redirect('miningtax:settings')

    obj, created = TaxRate.objects.get_or_create(
        ore_category=category,
        defaults={'tax_rate': rate_value, 'description': description},
    )
    if created:
        logger.info(f'{request.user.username}: tax rate for "{category}" created at {rate_value}%')
        messages.success(request, f'✅ Rate for {category} created at {rate_value}%.')
    else:
        messages.warning(request, f'⚠️ {category} already has a rate.')
    return redirect('miningtax:settings')


PERSONAL_MINING_SCOPE = 'esi-industry.read_character_mining.v1'


def _characters_missing_mining_token(user):
    """
    Characters of this user that have no valid personal mining token, as a list
    of names. Empty — the normal case — costs one query.
    """
    try:
        from esi.models import Token
    except ImportError:
        return []

    from allianceauth.eveonline.models import EveCharacter

    owned = EveCharacter.objects.filter(
        pk__in=user.character_ownerships.all().values_list('character_id', flat=True)
    ).order_by('character_name')
    if not owned:
        return []

    eve_ids = [c.character_id for c in owned]
    with_token = set(
        Token.objects
        .filter(character_id__in=eve_ids)
        .require_scopes(PERSONAL_MINING_SCOPE)
        .require_valid()
        .values_list('character_id', flat=True)
    )
    return [c.character_name for c in owned if c.character_id not in with_token]


def _add_character_url():
    """
    Alliance Auth's "add character" flow, so the warning can link straight to
    the fix. Resolved rather than hardcoded, and None if this Auth version names
    it differently — the warning then simply carries no link instead of raising
    NoReverseMatch on every dashboard load.
    """
    from django.urls import reverse, NoReverseMatch
    for name in ('authentication:add_character', 'add_character'):
        try:
            return reverse(name)
        except NoReverseMatch:
            continue
    return None