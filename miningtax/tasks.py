import logging

from allianceauth.services.tasks import QueueOnce
from celery import shared_task

# Every task runs through Alliance Auth's QueueOnce: while one run is queued or
# running, a second one with the same keys is dropped quietly ('graceful')
# instead of running alongside it — a double click, a second schedule entry or
# a signal firing twice can no longer make two runs overlap. The keys say what
# counts as "the same run": the rebuild per month, a character sync per
# character, everything else once at a time. The long nightly and rebuild runs
# hold their lock for up to four hours instead of the default one.

from .services import (
    sync_all_characters, sync_all_corp_observers, update_market_prices,
    repair_unresolved_ledger_names,
)

logger = logging.getLogger(__name__)


# Daily sync: personal ledgers + corp observer + market prices + sovereignty
# + billing records + payment check
@shared_task(base=QueueOnce, once={'graceful': True, 'keys': [], 'timeout': 60 * 60 * 4})
def daily_mining_sync_task():
    from .billing import months_to_recalculate, save_billing_records_for_month
    from .payments import check_open_payments
    from .services import sync_sov_systems, sync_ore_categories

    # Refreshed first so any ore added to EVE is classified before the
    # ledgers that reference it are priced and taxed.
    ore_new, ore_updated = sync_ore_categories()

    # Corp observers first: the personal sync subtracts what they report in
    # order to derive belt and anomaly mining, so it needs their figures to
    # already be in place for the day being processed.
    synced_corps = sync_all_corp_observers()
    synced_chars = sync_all_characters()

    # Before pricing: a location left as "Unknown (id)" by a failed lookup stays
    # that way forever otherwise, and a tax-free moon whose structure name never
    # resolved cannot be matched — so its ore is taxed with nothing on screen to
    # explain it.
    repaired = repair_unresolved_ledger_names()

    priced = update_market_prices()
    sov_systems = sync_sov_systems()

    # The running month, plus the previous one until its payment code is
    # revealed — those runs are the month's last syncs, so its closing days are
    # billed too. From the reveal on the month is frozen and nothing touches it
    # again (see billing.is_month_frozen()).
    months = months_to_recalculate()
    billing_saved = 0
    for year, month in months:
        billing_saved += save_billing_records_for_month(year, month)

    # Join dates for the billing page's sort order — looks up only corps that
    # have none stored yet or changed alliance, normally none.
    from .billing import refresh_corp_join_dates
    join_dates = refresh_corp_join_dates()
    logger.debug(f'Alliance join dates looked up for {join_dates} corp(s) without a current stored date')

    # Every month whose payment code is out and that still has open invoices.
    payments_matched = check_open_payments()

    result = (
        f'{ore_new} new ore types, '
        f'{synced_chars} personal entries, '
        f'{repaired} names repaired, '
        f'{synced_corps} corp observer entries, '
        f'{priced} prices updated, '
        f'{sov_systems} sovereignty systems tracked, '
        f'{billing_saved} billing records saved ({", ".join(f"{m:02d}/{y}" for y, m in months)}), '
        f'{payments_matched} payments automatically detected'
    )
    logger.info(f'Daily sync complete: {result}')
    return result


# Triggered when a new character registers in Alliance Auth
@shared_task(base=QueueOnce, once={'graceful': True, 'keys': ['character_id']})
def sync_character_mining_task(character_id):
    """Syncs the mining ledger of a single character asynchronously."""
    try:
        from allianceauth.eveonline.models import EveCharacter
        from .services import sync_character_mining

        character = EveCharacter.objects.get(character_id=character_id)
        synced = sync_character_mining(character)
        logger.debug(f'Auto-sync for {character.character_name}: {synced} entries')
        return synced

    except Exception as e:
        logger.warning(f'Auto-sync failed for character {character_id}: {e}')
        return 0


# Triggered by the "Sync Now" button — runs in the background so the request
# doesn't time out on large datasets.
@shared_task(base=QueueOnce, once={'graceful': True, 'keys': ['user_id']})
def manual_sync_task(user_id):
    from django.contrib.auth.models import User

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning(f'manual_sync_task: user {user_id} not found')
        return 'user not found'

    from .services import sync_character_mining

    # Corp observers first, same reasoning as the daily task: the personal sync
    # derives belt and anomaly mining by subtracting what the observers report,
    # so running it first would credit structure mining twice.
    corp_synced = sync_all_corp_observers()

    user_characters = [co.character for co in user.character_ownerships.all()]
    total_synced = 0
    for character in user_characters:
        try:
            total_synced += sync_character_mining(character)
        except Exception as e:
            logger.warning(f'Sync failed for {character.character_name}: {e}')
    priced = update_market_prices()

    result = (
        f'{total_synced} personal + {corp_synced} corp entries synced, '
        f'{priced} prices updated'
    )
    logger.info(f'Manual sync by {user.username} complete: {result}')
    return result


# Triggered by the "Check Payments Now" button — runs in the background.
@shared_task(base=QueueOnce, once={'graceful': True, 'keys': []})
def check_payments_task(year=None, month=None, requested_by=None):
    """
    Backs "Check Payments Now": checks every month whose payment code is out
    and that still has open invoices, the same set the nightly check uses.
    year/month are accepted only so a check queued by the previous version
    still runs; they no longer narrow it down.
    """
    from .payments import check_open_payments

    matched = check_open_payments()

    logger.info(
        'Manual payment check' +
        (f' by {requested_by}' if requested_by else '') +
        f' complete: {matched} corp(s) marked as paid'
    )
    return matched


# ─── MANUALLY TRIGGERED MAINTENANCE ───────────────────────────────────────────
#
# These back the buttons in Settings. They are Celery tasks rather than direct
# calls for two reasons: the work is slow enough to time out a web request —
# the ore import alone makes one ESI call per group — and a task that runs
# inside the web process appears nowhere in Alliance Auth's task monitor, so
# there is no record of it having run, by whom, or whether it finished.


@shared_task(base=QueueOnce, once={'graceful': True, 'keys': []})
def sync_sov_systems_task(requested_by=None):
    """Refreshes the known systems list. Backs the Systems tab button."""
    from .services import sync_sov_systems

    count = sync_sov_systems()
    result = f'{count} system(s) tracked'
    logger.info(f'Sovereignty sync by {requested_by or "unknown"}: {result}')
    return result


@shared_task(base=QueueOnce, once={'graceful': True, 'keys': []})
def sync_ore_categories_task(requested_by=None):
    """Imports the ore list from ESI. Backs the Tax Rates tab button."""
    from .services import sync_ore_categories

    imported, updated = sync_ore_categories()
    result = f'{imported} new, {updated} updated'
    logger.info(f'Ore import by {requested_by or "unknown"}: {result}')
    return result


@shared_task(base=QueueOnce, once={'graceful': True, 'keys': []})
def repair_location_names_task(requested_by=None):
    """Re-resolves placeholder locations. Backs the Systems tab button."""
    from .services import repair_unresolved_ledger_names

    repaired = repair_unresolved_ledger_names()
    result = f'{repaired} name(s) resolved'
    logger.info(f'Location repair by {requested_by or "unknown"}: {result}')
    return result


@shared_task(base=QueueOnce, once={'graceful': True, 'keys': []})
def update_prices_task(requested_by=None):
    """Prices entries that have none. Backs the Pricing tab button."""
    from .services import update_market_prices

    updated = update_market_prices()
    result = f'{updated} entrie(s) priced'
    logger.info(f'Price update by {requested_by or "unknown"}: {result}')
    return result


@shared_task(base=QueueOnce, once={'graceful': True, 'keys': ['corporation_id']})
def register_corporation_task(corporation_id, requested_by=None):
    """
    Registers one corporation with Alliance Auth. Backs the Settings button.

    A task rather than a direct call for the same reason as the rest: work that
    talks to ESI belongs off the web request, and an action nobody can see
    afterwards is an action nobody can troubleshoot.
    """
    from allianceauth.eveonline.models import EveCorporationInfo

    if EveCorporationInfo.objects.filter(corporation_id=corporation_id).exists():
        return 'already registered'

    try:
        corp = EveCorporationInfo.objects.create_corporation(corporation_id=corporation_id)
    except Exception as e:
        logger.warning(f'Could not register corporation {corporation_id}: {e}')
        return f'failed: {e}'

    logger.info(f'Corporation {corp.corporation_name} ({corporation_id}) registered by {requested_by or "unknown"}')
    return f'registered {corp.corporation_name}'


@shared_task(base=QueueOnce, once={'graceful': True, 'keys': ['alliance_id']})
def register_alliance_corps_task(alliance_id, requested_by=None):
    """
    Registers every corporation of an alliance through Alliance Auth's own
    EveAllianceInfo.populate_alliance(), rather than this plugin asking ESI
    for the corp list itself.

    populate_alliance() still reaches ESI internally — nothing in the stack
    holds an alliance's member list — but it is Alliance Auth's standard path,
    and it also sets every member corp's alliance assignment, which the
    taxable-scope check relies on. Runs as a task because an alliance of fifty
    corps is still fifty-one requests on Alliance Auth's side.
    """
    from allianceauth.eveonline.models import EveAllianceInfo, EveCorporationInfo

    before = EveCorporationInfo.objects.count()
    try:
        alliance = EveAllianceInfo.objects.filter(alliance_id=alliance_id).first()
        if alliance is None:
            alliance = EveAllianceInfo.objects.create_alliance(alliance_id)
        alliance.populate_alliance()
    except Exception as e:
        logger.warning(f'Could not register corps of alliance {alliance_id}: {e}')
        return f'failed: {e}'

    registered = EveCorporationInfo.objects.count() - before
    member_count = EveCorporationInfo.objects.filter(alliance=alliance).count()
    result = f'{registered} new, {member_count} member corp(s) now assigned to {alliance.alliance_name}'
    logger.info(f'Alliance {alliance_id} corps registered by {requested_by or "unknown"}: {result}')
    return result


@shared_task(base=QueueOnce, once={'graceful': True, 'keys': ['year', 'month'], 'timeout': 60 * 60 * 4})
def rebuild_billing_snapshot_task(year, month, requested_by=None):
    """
    Rebuilds the AllianceBillingRecord snapshot (totals, category breakdown,
    per-member figures) for one specific month.

    Backs the "Rebuild Snapshot" button on the Alliance Billing page. Needed
    for any month other than the current one: the daily sync only ever
    recalculates today's month, so a closed month whose snapshot predates a
    schema or logic change (e.g. member_snapshot being added) has no other
    way to pick that up short of the next time that same month number rolls
    around a year later.

    Deletes and recreates rather than updating in place, mirroring what an
    officer running the equivalent shell command by hand would have done —
    the difference is this happens through a tracked, visible task instead.
    Already-paid records for the month are left untouched: save_billing_record()
    skips a corp once paid=True is set, so a finalised invoice can't be
    silently rewritten by a rebuild.
    """
    from .models import AllianceBillingRecord
    from .billing import is_month_frozen, save_billing_records_for_month

    # Checked here as well as in the view: a queued rebuild must not slip past
    # the moment its month became final.
    if is_month_frozen(year, month):
        logger.warning(f'Billing snapshot rebuild for {month:02d}/{year} refused — month is final')
        return f'refused: {month:02d}/{year} is final (payment code already released)'

    existing = AllianceBillingRecord.objects.filter(year=year, month=month)
    paid_count = existing.filter(paid=True).count()
    unpaid_deleted, _ = existing.filter(paid=False).delete()

    saved = save_billing_records_for_month(year, month)

    # Fill the join dates right away too, so the new sort order is complete
    # without waiting for the nightly run.
    from .billing import refresh_corp_join_dates
    refresh_corp_join_dates()

    result = (
        f'{saved} record(s) rebuilt for {month:02d}/{year} '
        f'({unpaid_deleted} unpaid record(s) recreated, '
        f'{paid_count} paid record(s) left untouched)'
    )
    logger.info(
        f'Billing snapshot rebuild for {month:02d}/{year}' +
        (f' by {requested_by}' if requested_by else '') +
        f' complete: {result}'
    )
    return result