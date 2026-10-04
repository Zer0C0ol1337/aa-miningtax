import logging
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from .models import (
    OreCategory, TaxRate, TaxRateHistory, FleetSession, AllianceMoon, MoonRental,
    AllianceBillingRecord, TaxExemption, OreCategoryRule, TaxableScope,
)
from .services import STRUCTURE_ID_THRESHOLD


# Hardcoded fallback used only if no "Default" TaxRate row exists in the DB
# at all (e.g. right after install, before populate_ore_categories creates
# one). Once a "Default" row exists, get_tax_rate() falls back to it and
# it's fully editable by officers in the Settings UI — no code change
# needed to adjust the rate for unrecognized ore categories.
logger = logging.getLogger(__name__)

DEFAULT_TAX_RATE = Decimal('10.00')


def category_from_rules(type_name='', group_name=''):
    """
    First pass of classification: alliance-defined name rules.

    These exist because EVE's grouping doesn't always match how ore should be
    taxed — abyssal ore and Prismaticite sit in ordinary asteroid groups but
    warrant their own rate. Rules are checked before the group is consulted, so
    they override the automatic result, and they apply to ore that doesn't exist
    yet as long as its name matches.
    """
    haystacks = {
        'type_name': (type_name or '').lower(),
        'group_name': (group_name or '').lower(),
    }
    for rule in OreCategoryRule.objects.filter(active=True):
        needle = (rule.contains or '').strip().lower()
        if not needle:
            continue
        if needle in haystacks.get(rule.match_field, ''):
            return rule.category
    return None


def classify_group_name(group_name):
    """
    Maps an EVE market group name to one of our tax categories.

    Kept separate so both the on-demand lookup and the full ore-table import use
    exactly the same rules — two implementations would inevitably drift apart.

    Order matters: "Uncommon Moon Asteroids" contains "Common Moon", and a naive
    substring test would misfile R16 as R8, so the most specific match wins.
    """
    group = (group_name or '').lower()
    if 'exceptional moon' in group:
        return 'R64'
    if 'rare moon' in group:
        return 'R32'
    if 'uncommon moon' in group:
        return 'R16'
    if 'common moon' in group:
        return 'R8'
    if 'ubiquitous moon' in group:
        return 'R4'
    if 'mercoxit' in group:
        return 'Mercoxit'
    if 'ice' in group:
        return 'Ice'
    if 'cloud' in group or 'gas' in group:
        return 'Gas'
    if 'asteroid' in group or 'ore' in group:
        return 'Ore'
    return None


def _type_and_group_from_sde(type_id):
    """
    A type's own name and its group name, from eve_sde (CCP's static data
    export, which Corptools already loads).

    Replaces the old eveuniverse-then-ESI chain: eve_sde holds every type in
    the game, including ore added by the latest expansion as soon as the SDE
    is refreshed, so there is nothing left for ESI to answer that this can't.
    Returns ('', '') for an ID eve_sde doesn't know.
    """
    try:
        from eve_sde.models import ItemType
    except ImportError:
        return '', ''

    eve_type = ItemType.objects.filter(id=type_id).select_related('group').first()
    if not eve_type:
        return '', ''
    return eve_type.name or '', (eve_type.group.name if eve_type.group else '') or ''


def _type_and_group_from_eveuniverse(type_id):
    """
    A type's name and group name from eveuniverse — the second local source
    behind eve_sde. Returns ('', '') when eveuniverse isn't installed or
    doesn't know the type.
    """
    try:
        from eveuniverse.models import EveType
    except ImportError:
        return '', ''

    eve_type = EveType.objects.filter(id=type_id).select_related('eve_group').first()
    if not eve_type:
        return '', ''
    return eve_type.name or '', (eve_type.eve_group.name if eve_type.eve_group else '') or ''


def _type_and_group_from_esi(type_id):
    """
    A type's own name and its group name, straight from ESI.

    The fallback behind eve_sde, for ore an expansion added before eve_sde
    was refreshed. Without it such ore sits at the Default rate until the
    SDE is reloaded, which is not a thing anyone thinks to check.
    """
    from .services import _get_esi_client
    from esi.exceptions import HTTPNotModified

    esi = _get_esi_client()

    def _fetch(op, **kwargs):
        try:
            return op(**kwargs).results()
        except HTTPNotModified:
            return op(**kwargs).results(force_refresh=True)

    try:
        types = _fetch(esi.client.Universe.GetUniverseTypesTypeId, type_id=type_id)
        if not types:
            return '', ''
        eve_type = types[0]
        name = getattr(eve_type, 'name', '') or ''
        group_id = getattr(eve_type, 'group_id', None)
        if not group_id:
            return name, ''

        groups = _fetch(esi.client.Universe.GetUniverseGroupsGroupId, group_id=group_id)
        group_name = getattr(groups[0], 'name', '') if groups else ''
        return name, group_name
    except Exception as e:
        logger.debug(f'Could not classify type {type_id} via ESI: {e}')
        return '', ''


def forget_unclassifiable_types():
    """
    Drops the record of which types could not be classified.

    Called whenever the rules change or the ore list is reimported. Without it a
    newly written rule would sit idle for up to a day on exactly the ore it was
    written for, which is the one case where someone is watching for it to work.
    """
    from .models import MiningLedgerEntry

    type_ids = MiningLedgerEntry.objects.values_list('type_id', flat=True).distinct()
    cache.delete_many([f'miningtax:unclassifiable:{t}' for t in type_ids])


# ─── LOOKUP CACHES ────────────────────────────────────────────────────────────
#
# Tax is worked out per ledger entry, and each entry used to re-read the same
# handful of tiny tables — ore categories, rates, exemptions, fleet sessions,
# moons, rentals. Eleven queries an entry is unnoticeable for one pilot's month
# and ruinous for an alliance's: a monthly bill across ten thousand entries ran
# to six figures of queries against tables that mostly hold single-digit row
# counts.
#
# They are read once and held for a minute instead. Anything that edits them
# calls invalidate_billing_caches(), so an officer never has to wonder whether
# the figure in front of them predates the change they just made.

BILLING_CACHE_TTL = 60

# Defined here rather than beside the function that reads it: _CACHE_KEYS
# below needs it, and module code runs top to bottom — the name has to exist
# before the tuple is built, not merely before it is used.
SCOPE_CACHE_KEY = 'miningtax:taxable_scope'

_CACHE_KEYS = (
    'miningtax:lookup:ore_categories',
    'miningtax:lookup:tax_rates',
    'miningtax:lookup:tax_rate_history',
    'miningtax:lookup:exemptions',
    'miningtax:lookup:fleet_sessions',
    'miningtax:lookup:tax_free_moons',
    'miningtax:lookup:moon_rentals',
    'miningtax:lookup:corp_alliances',
    SCOPE_CACHE_KEY,
)


def invalidate_billing_caches():
    """Drops every cached lookup. Called whenever one of them is edited."""
    cache.delete_many(list(_CACHE_KEYS))


def _cached(key, build):
    value = cache.get(key)
    if value is None:
        value = build()
        cache.set(key, value, BILLING_CACHE_TTL)
    return value


def _ore_categories():
    return _cached(
        'miningtax:lookup:ore_categories',
        lambda: dict(OreCategory.objects.values_list('type_id', 'category')),
    )


def _tax_rates():
    """
    Current TaxRate.tax_rate per category — used only where "today's rate"
    genuinely is what's wanted (Settings display, the "categories without a
    rate" health check), never for taxing a specific ledger entry. Billing
    itself goes through _tax_rate_history() so a rate change never reaches
    into the past.
    """
    return _cached(
        'miningtax:lookup:tax_rates',
        lambda: {
            c: r for c, r in TaxRate.objects.values_list('ore_category', 'tax_rate')
        },
    )


def _tax_rate_history():
    """
    {category: [(effective_from, rate), ...]} for every category that has ever
    had a rate change, each list sorted newest-first.

    Loaded whole and cached rather than queried per lookup: the table stays
    small (one row per rate change, not per ledger entry), and get_tax_rate()
    is called once per entry — querying it per entry would reopen exactly the
    N+1 problem the lookup-cache layer above exists to avoid.
    """
    def build():
        history = {}
        rows = TaxRateHistory.objects.order_by(
            'ore_category', '-effective_from'
        ).values_list('ore_category', 'effective_from', 'tax_rate')
        for category, effective_from, rate in rows:
            history.setdefault(category, []).append((effective_from, rate))
        return history

    return _cached('miningtax:lookup:tax_rate_history', build)


def _exemptions():
    def build():
        rows = TaxExemption.objects.filter(active=True).values_list(
            'character_id', 'corporation__corporation_id'
        )
        chars, corps = set(), set()
        for char_pk, corp_id in rows:
            if char_pk:
                chars.add(char_pk)
            if corp_id:
                corps.add(corp_id)
        return {'chars': chars, 'corps': corps}

    return _cached('miningtax:lookup:exemptions', build)


def _fleet_sessions():
    return _cached(
        'miningtax:lookup:fleet_sessions',
        lambda: list(
            FleetSession.objects.filter(exclude_from_billing=True).values(
                'start_time', 'end_time', 'ore_type_id', 'ore_category'
            )
        ),
    )


def _tax_free_moons():
    return _cached(
        'miningtax:lookup:tax_free_moons',
        lambda: list(
            AllianceMoon.objects.filter(is_tax_free=True).values(
                'structure_name', 'solar_system_name'
            )
        ),
    )


def _corp_alliances():
    """
    {corporation_id: alliance_id} from Alliance Auth's corporation records.

    Preferred over the alliance stored on each character: there is one record
    per corporation rather than one per pilot, so it is both cheaper to keep
    current and far less likely to be carrying a value from before someone
    changed corp. A character record that still names the old alliance would
    otherwise keep billing a pilot who left months ago.
    """
    def build():
        from allianceauth.eveonline.models import EveCorporationInfo
        return {
            corp_id: alliance_id
            for corp_id, alliance_id in EveCorporationInfo.objects
            .values_list('corporation_id', 'alliance__alliance_id')
        }

    return _cached('miningtax:lookup:corp_alliances', build)


def _moon_rentals():
    def build():
        rentals = {}
        rows = MoonRental.objects.filter(active=True).values_list(
            'corporation__corporation_id', 'structure_name'
        )
        for corp_id, structure in rows:
            if corp_id and structure:
                rentals.setdefault(corp_id, set()).add(structure.strip().lower())
        return rentals

    return _cached('miningtax:lookup:moon_rentals', build)


def get_ore_category(type_id):
    """
    Category for an ore type. The OreCategory table wins, so anything an officer
    corrected by hand stays corrected; unknown types are classified from their
    group and written back, which both fixes the current calculation and makes
    the result visible and editable in the admin afterwards.
    """
    known = _ore_categories()
    if type_id in known:
        return known[type_id]

    # The negative answer is remembered. This runs once per ledger entry while
    # a page renders, so without it an unclassifiable type would cost two ESI
    # calls on every view — the ore is not going to change group in the
    # meantime, and a day is soon enough to notice a new rule.
    miss_key = f'miningtax:unclassifiable:{type_id}'
    if cache.get(miss_key):
        return 'Default'

    name, group_name = _type_and_group_from_sde(type_id)
    if not group_name:
        name, group_name = _type_and_group_from_eveuniverse(type_id)
    if not group_name:
        name, group_name = _type_and_group_from_esi(type_id)

    derived = category_from_rules(name, group_name) or classify_group_name(group_name)

    if not derived and group_name:
        # Every type reaching this point came from a mining ledger entry, so
        # it is ore by definition — this function is never called with a
        # type_id from anywhere else. EVE names many ordinary ore groups
        # after the ore itself ("Bistot", "Arkonor") rather than "Asteroid"
        # or similar, which classify_group_name has no pattern for.
        #
        # The bulk import (services.sync_ore_categories) already falls back
        # to 'Ore' for exactly this reason. Doing the same here keeps this
        # on-demand path from disagreeing with the bulk import about ore
        # mined between two scheduled imports — without this, a type stayed
        # at Default until the next full "Import ore list" run happened to
        # sweep it up, even though the bulk import would have classified it
        # correctly from the start.
        derived = 'Ore'

    if not derived:
        # group_name is empty here — neither eve_sde, eveuniverse nor ESI knows the type,
        # not merely an unrecognised group.
        logger.info(
            f'Type {type_id} ("{name or "unknown"}") could not be resolved '
            f'via eve_sde, eveuniverse or ESI, taxed at the Default rate'
        )
        cache.set(f'miningtax:unclassifiable:{type_id}', True, 60 * 60 * 24)
        return 'Default'

    OreCategory.objects.update_or_create(
        type_id=type_id,
        defaults={'type_name': name or f'Type {type_id}', 'category': derived},
    )
    cache.delete('miningtax:lookup:ore_categories')
    logger.info(f'Classified unseeded type {type_id} ({name or "unknown"}) as {derived}')
    return derived


def get_tax_rate(category, entry_date=None):
    """
    The rate for a category as it stood on entry_date — not today's rate.

    A rate change must never reach into the past: raising R64 from 10% to 15%
    today should tax today's ore at 15% while everything mined before today
    keeps the 10% it was actually taxed under, even when that earlier month
    gets recalculated later (a "Rebuild Snapshot" click, the daily sync
    re-running). Without entry_date, every historical recalculation would
    silently apply whatever rate happens to be current right now.

    entry_date is optional only for call sites that never touch a ledger entry
    (e.g. displaying "today's rate" somewhere) — every caller that is pricing
    an actual MiningLedgerEntry must pass its date. Falls back to today when
    omitted, and falls back further to the live TaxRate table when a category
    has no history at all yet: a category can exist without a single recorded
    change either because it predates TaxRateHistory (the migration seeds one
    row per existing TaxRate, so this should be rare) or because it was just
    created and never explicitly re-rated.
    """
    if entry_date is None:
        entry_date = timezone.now().date()

    history = _tax_rate_history()

    for cat in (category, 'Default'):
        for effective_from, rate in history.get(cat, []):
            if effective_from <= entry_date:
                return rate

    # No history row applies (none exists yet, or all of them postdate
    # entry_date, which would only happen for ore mined before the plugin's
    # own tax-rate history began). Live TaxRate is the honest last resort.
    rates = _tax_rates()
    if category in rates:
        return rates[category]
    return rates.get('Default', DEFAULT_TAX_RATE)


def set_tax_rate(category, new_rate, effective_from=None):
    """
    The one correct way to change a tax rate. Updates TaxRate (so Settings/
    admin keep showing "today's rate" the way they always have) AND records
    the change in TaxRateHistory with the date it takes effect — this second
    part is what makes the change non-retroactive; skipping it and writing
    TaxRate.tax_rate directly would silently re-tax every past ledger entry
    the next time that month's billing is recalculated.

    effective_from defaults to today: a rate changed right now applies from
    today onward, not from whenever someone next clicks "Rebuild Snapshot".
    An officer can backdate or postdate it by passing an explicit date instead
    (the Settings form exposes today as the default with the field editable).
    """
    if effective_from is None:
        effective_from = timezone.now().date()

    TaxRate.objects.update_or_create(
        ore_category=category,
        defaults={'tax_rate': new_rate},
    )

    # update_or_create rather than create: setting the same category's rate
    # twice on the same day (e.g. correcting a typo minutes later) replaces
    # that day's entry instead of leaving two rows both claiming to be what
    # applied — TaxRateHistory's unique_together enforces this at the DB level
    # too, this just makes fixing a same-day mistake not require a delete first.
    TaxRateHistory.objects.update_or_create(
        ore_category=category,
        effective_from=effective_from,
        defaults={'tax_rate': new_rate},
    )

    cache.delete('miningtax:lookup:tax_rates')
    cache.delete('miningtax:lookup:tax_rate_history')


def is_excluded_by_fleet_session(entry, ore_category):
    entry_datetime = timezone.make_aware(
        timezone.datetime.combine(entry.date, timezone.datetime.min.time())
    )
    for session in _fleet_sessions():
        if not (session['start_time'] <= entry_datetime <= session['end_time']):
            continue
        if session['ore_type_id'] and session['ore_type_id'] == entry.type_id:
            return True
        if session['ore_category'] and session['ore_category'] == ore_category:
            return True
        if not session['ore_type_id'] and not session['ore_category']:
            return True
    return False


def is_excluded_by_alliance_moon(entry):
    # A tax-free alliance moon exempts ONLY ore mined at that moon's structure,
    # not everything in the whole solar system. Moon mining always comes from a
    # corp mining observer, so such entries carry the structure ID in
    # solar_system_id (> STRUCTURE_ID_THRESHOLD) and the structure name in
    # solar_system_name. Belt and anomaly mining (incl. Mercoxit) comes from the
    # personal ledger with a real system id below the threshold — so gating on
    # the threshold guarantees belts/anomalies are never wrongly exempted, even
    # if they share a system with a tax-free moon.
    if not entry.solar_system_name:
        return False
    if not entry.solar_system_id or entry.solar_system_id <= STRUCTURE_ID_THRESHOLD:
        return False

    entry_structure = entry.solar_system_name.strip().lower()
    for moon in _tax_free_moons():
        structure = (moon['structure_name'] or '').strip().lower()
        if structure:
            # Precise per-structure match — required when several moon structures
            # share one system, so only the named structure is exempted.
            if structure == entry_structure:
                return True
            continue

        # Backward-compatible fallback for moons configured before the
        # structure_name field existed (substring match on the system field).
        system = (moon['solar_system_name'] or '').lower()
        if system and system in entry_structure:
            return True
    return False


def is_excluded_by_moon_rental(entry, corporation):
    if not entry.solar_system_name or not corporation:
        return False
    rented = _moon_rentals().get(corporation.corporation_id)
    return bool(rented) and entry.solar_system_name.strip().lower() in rented


# One day: a corp's alliance join date changes only on an actual alliance
# switch, which is rare and noticed immediately by an officer if it matters —
# unlike the 60-second billing caches above, which exist to survive a burst of
# page views, not to track something that moves this slowly.
CORP_JOIN_DATE_CACHE_TTL = 60 * 60 * 24


def get_corp_join_date(corporation_id):
    """
    The date the corporation joined its CURRENT alliance, or None if that
    can't be determined (not in an alliance, ESI unreachable, or the corp has
    never been in one).

    Reads /corporations/{id}/alliancehistory/ — public, no token needed. ESI
    has changed this endpoint's shape between versions: v1 nests alliance_id
    under an "alliance" sub-object, v2 has it at the top level. Both are
    handled here rather than pinning a version, since django-esi resolves
    "latest" for public endpoints and which one comes back isn't something
    this code controls.

    The current alliance's row is the one with the highest record_id — ESI
    documents record_id specifically as the field to use when dates might be
    ambiguous, rather than trusting start_date/is_deleted ordering.

    Returns None on any failure. A join date this function can't determine is
    treated as "not applicable" by the caller, which means the mining is
    taxed rather than exempted — the same reasoning is_corp_outside_taxable_
    scope() applies to an unconfirmable corp: of the two ways to be wrong,
    quietly not taxing a corp that should be taxed is the one people notice
    and resent, so an unknown answer defaults to taxing.
    """
    cache_key = f'miningtax:corp_join_date:{corporation_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        # cache.get can't distinguish "not cached" from "cached as None", so a
        # sentinel string stands in for the negative result.
        return None if cached == 'none' else cached

    from .services import _get_esi_client
    from esi.exceptions import HTTPNotModified

    esi = _get_esi_client()

    def _fetch(force=False):
        return esi.client.Corporation.GetCorporationsCorporationIdAlliancehistory(
            corporation_id=corporation_id
        ).results(force_refresh=force)

    try:
        try:
            history = _fetch()
        except HTTPNotModified:
            history = _fetch(force=True)
    except Exception as e:
        # Cached the same as a genuine "no history" result (not a short retry
        # window): a broken or missing ESI operation does not fix itself
        # between one ledger entry and the next, so without this every entry
        # for the corp re-attempted the same failing call — one alliance with
        # a few thousand entries in a month turned one bad endpoint into a
        # few thousand near-identical warnings inside minutes.
        logger.warning(f'Could not fetch alliance history for corp {corporation_id}: {e}')
        cache.set(cache_key, 'none', CORP_JOIN_DATE_CACHE_TTL)
        return None

    if not history:
        cache.set(cache_key, 'none', CORP_JOIN_DATE_CACHE_TTL)
        return None

    def _row_alliance_id(row):
        # v1 nests it under .alliance.alliance_id, v2 puts it directly on the
        # row — try both rather than assuming which one django-esi returned.
        nested = getattr(row, 'alliance', None)
        if nested is not None:
            return getattr(nested, 'alliance_id', None)
        return getattr(row, 'alliance_id', None)

    current = max(
        (row for row in history if _row_alliance_id(row)),
        key=lambda row: getattr(row, 'record_id', 0),
        default=None,
    )

    if current is None:
        # Every row in the history is a departure with no alliance (is_deleted
        # rows, or gaps between alliances) — the corp is not currently in one.
        cache.set(cache_key, 'none', CORP_JOIN_DATE_CACHE_TTL)
        return None

    start_date = getattr(current, 'start_date', None)
    if start_date is None:
        cache.set(cache_key, 'none', CORP_JOIN_DATE_CACHE_TTL)
        return None

    join_date = start_date.date() if hasattr(start_date, 'date') else start_date
    cache.set(cache_key, join_date, CORP_JOIN_DATE_CACHE_TTL)
    return join_date


def cached_corp_join_date(corporation_id):
    """
    A corp's alliance join date from the cache only, or None if it isn't
    cached yet (or is known to be unknown). Never calls ESI — it serves the
    billing page's sort order, and a page must not wait on ESI. The nightly
    sync and Rebuild Snapshot keep it filled (see save_billing_records_for_month()).
    """
    cached = cache.get(f'miningtax:corp_join_date:{corporation_id}')
    if cached is None or cached == 'none':
        return None
    return cached


def is_before_corp_join_date(entry, corporation):
    """
    True when the entry's date predates the corporation's current alliance
    membership — mining that happened before the corp (and so the alliance)
    had any claim on it, e.g. a pilot's history synced from before their corp
    joined, or a corp whose sync ran before an officer noticed it had just
    joined.

    Judged on the corporation of the character who mined it, same as every
    other exclusion here — not on the individual character's own join date,
    since only the corp's alliance membership was asked for.
    """
    if not corporation:
        return False
    join_date = get_corp_join_date(corporation.corporation_id)
    if join_date is None:
        return False
    return entry.date < join_date


CHARACTER_JOIN_DATE_CACHE_TTL = 60 * 60 * 24


def _character_join_date_from_corptools(character):
    """
    Reads the character's corp-history from Corptools' own database
    (corptools.models.interactions.CorporationHistory) instead of ESI.

    Corptools already syncs this on its own schedule for every character it
    audits — the alliance runs Corptools regardless of this plugin, so the
    data is normally already sitting locally with no extra ESI cost to us at
    all, not even the one-call-per-day this function used to make itself.

    Returns the join date, or None if Corptools isn't installed, doesn't have
    an audit for this character, or its history doesn't (yet) cover the
    character's current corp — callers fall back to ESI in any of those
    cases, exactly like _get_corptools_entries() already does for the mining
    ledger.
    """
    try:
        from corptools.models import CharacterAudit
        from corptools.models.interactions import CorporationHistory
    except ImportError:
        return None

    audit = CharacterAudit.objects.filter(
        character__character_id=character.character_id
    ).first()
    if not audit:
        return None

    # Same selection rule as the ESI path: the row for the CURRENT corp with
    # the highest record_id, not just "the last row" or "the row without
    # is_deleted" — record_id is what ESI itself documents as authoritative
    # when dates might be ambiguous, and Corptools' table mirrors ESI's shape
    # exactly (it's populated directly from the same endpoint).
    current = CorporationHistory.objects.filter(
        character=audit, corporation_id=character.corporation_id
    ).order_by('-record_id').first()

    if current is None:
        return None

    return current.start_date.date()


def get_character_join_date(character):
    """
    The date this character joined its CURRENT corporation, or None if that
    can't be determined.

    A different question from get_corp_join_date(): a corp can have been in
    the alliance for years while THIS character only joined the corp last
    week — their mining ledger has no record of which corp they were in when
    each entry was mined, only their corp today, so history from before they
    personally joined would otherwise be swept into the new corp's bill the
    moment they show up in it.

    Corptools first (see _character_join_date_from_corptools — it already
    audits this data locally for every character it tracks), then ESI as a
    fallback for characters Corptools doesn't audit. The ESI path reads
    /characters/{id}/corporationhistory/ — public, no token needed, same flat
    {corporation_id, record_id, start_date, is_deleted} shape Corptools itself
    stores. That endpoint carries its own ESI rate limit (300/minute per IP,
    documented separately from the usual error-limit system) rather than the
    usual generous ceiling, which is why this result is cached per character
    for a day regardless of which source answered — an alliance with many
    active pilots would burn through that limit quickly if every billing
    recalculation re-fetched it for every character in every entry.
    """
    cache_key = f'miningtax:char_join_date:{character.character_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return None if cached == 'none' else cached

    from_corptools = _character_join_date_from_corptools(character)
    if from_corptools is not None:
        cache.set(cache_key, from_corptools, CHARACTER_JOIN_DATE_CACHE_TTL)
        return from_corptools

    from .services import _get_esi_client
    from esi.exceptions import HTTPNotModified

    esi = _get_esi_client()

    def _fetch(force=False):
        return esi.client.Character.GetCharactersCharacterIdCorporationhistory(
            character_id=character.character_id
        ).results(force_refresh=force)

    try:
        try:
            history = _fetch()
        except HTTPNotModified:
            history = _fetch(force=True)
    except Exception as e:
        # Same reasoning as the corp-level check: a broken or missing ESI
        # operation does not fix itself between one ledger entry and the
        # next, so failing to cache it here meant every entry for this
        # character re-attempted — and failed at — the same call, turning one
        # bad endpoint into one warning per entry rather than one per day.
        logger.warning(
            f'Could not fetch corporation history for character '
            f'{character.character_id}: {e}'
        )
        cache.set(cache_key, 'none', CHARACTER_JOIN_DATE_CACHE_TTL)
        return None

    if not history:
        cache.set(cache_key, 'none', CHARACTER_JOIN_DATE_CACHE_TTL)
        return None

    # The row for the character's CURRENT corp is the one with the highest
    # record_id, same selection rule as get_corp_join_date() — matched against
    # the character's own corporation_id rather than assumed to be the last
    # entry, since a stale character record could in principle disagree with
    # what ESI's history considers current.
    current_corp_id = character.corporation_id
    matching = [
        row for row in history
        if getattr(row, 'corporation_id', None) == current_corp_id
    ]
    current = max(matching, key=lambda row: getattr(row, 'record_id', 0), default=None)

    if current is None:
        # History exists but none of it matches the character's current corp —
        # can happen right after a corp move, before ESI's own history catches
        # up. Treated as unknown rather than guessed at.
        cache.set(cache_key, 'none', CHARACTER_JOIN_DATE_CACHE_TTL)
        return None

    start_date = getattr(current, 'start_date', None)
    if start_date is None:
        cache.set(cache_key, 'none', CHARACTER_JOIN_DATE_CACHE_TTL)
        return None

    join_date = start_date.date() if hasattr(start_date, 'date') else start_date
    cache.set(cache_key, join_date, CHARACTER_JOIN_DATE_CACHE_TTL)
    return join_date


def is_before_character_join_date(entry):
    """
    True when the entry's date predates this character's own join date into
    their current corporation — mining they did while still somewhere else,
    which their current corp (and alliance) has no more claim on than it does
    on ore mined by a character who was never a member.

    Independent of is_before_corp_join_date(): that one asks when the CORP
    joined the ALLIANCE, this one asks when the CHARACTER joined the CORP.
    Both apply — a character can clear this check by having joined their corp
    long ago, and still be excluded by the other if the corp itself is new to
    the alliance, or the reverse.
    """
    join_date = get_character_join_date(entry.character)
    if join_date is None:
        return False
    return entry.date < join_date


def _get_main_character(character):
    """
    The main character behind a given character, via
    CharacterOwnership -> User -> UserProfile.main_character.
    Returns None if the character isn't registered in Auth, has no owning
    user, or that user never set a main.
    """
    try:
        return character.character_ownership.user.profile.main_character
    except Exception:
        return None


def _taxable_scope():
    """
    The alliances and corporations that are taxed, as {alliances, corps} of EVE
    IDs, cached briefly since billing consults it once per ledger entry.

    'active' is False when nothing is configured, which taxes everything — the
    behaviour every install had before scopes existed, so upgrading changes
    nothing until someone sets one.
    """
    data = cache.get(SCOPE_CACHE_KEY)
    if data is not None:
        return data

    rows = TaxableScope.objects.select_related('alliance', 'corporation').all()
    alliances = {r.alliance.alliance_id for r in rows if r.alliance_id}
    corps = {r.corporation.corporation_id for r in rows if r.corporation_id}

    data = {
        'active': bool(alliances or corps),
        'alliances': alliances,
        'corps': corps,
    }
    cache.set(SCOPE_CACHE_KEY, data, 300)
    return data


def is_corp_outside_taxable_scope(corp_id, corp_name=''):
    """
    True when a corporation itself is somewhere the alliance does not tax —
    the corp-level core of is_outside_taxable_scope() below, usable directly
    when only a corp_id is at hand (e.g. deciding whether to show a corp on
    the Alliance Billing overview) rather than a MiningLedgerEntry.

    Judged on the corporation's *current* alliance, not where it was when the
    ore was mined — the same reasoning is_outside_taxable_scope() documents in
    more detail: history isn't stamped with a corporation's alliance at the
    time, so present membership is the only thing available.
    """
    scope = _taxable_scope()
    if not scope['active']:
        return False

    if corp_id in scope['corps']:
        return False

    known_alliances = _corp_alliances()
    if corp_id in known_alliances:
        alliance_id = known_alliances[corp_id]
        return not (alliance_id and alliance_id in scope['alliances'])

    # Alliance Auth has no record of that corporation, so its membership cannot
    # be confirmed. Setting a scope says "tax these and no others", and of the
    # two ways to be wrong here, billing an outsider is the one people notice
    # and resent — so an unconfirmable corporation is left alone and logged,
    # rather than taxed on the strength of a guess.
    logger.info(
        f'Corporation {corp_id} ({corp_name or "unknown"}) is not registered '
        f'in Alliance Auth, so its alliance cannot be confirmed — left out of '
        f'billing while a scope is set'
    )
    return True


def is_outside_taxable_scope(entry):
    """
    True when the character who mined this is somewhere the alliance does not
    tax — a high-sec alt, a trade character, a corp outside the alliance.

    Judged on the character's *current* corporation, not where they were at the
    time. Mining history is not stamped with a corporation, so present
    membership is the only thing available; someone who leaves the alliance
    therefore takes their unpaid billing with them, which is the same outcome as
    leaving without paying.

    Thin wrapper around is_corp_outside_taxable_scope() — kept as its own
    function because every existing caller passes a MiningLedgerEntry, and
    changing all of them to pass a bare corp_id instead would be a much larger,
    riskier diff for no behavioural difference.
    """
    character = entry.character
    return is_corp_outside_taxable_scope(
        character.corporation_id, character.corporation_name
    )


def is_tax_exempt(entry):
    # Exemptions are granted per MAIN character (or per corporation), never per
    # alt: exempting a main automatically covers every alt that main owns in
    # Auth, so an officer doesn't have to tick 50 alts by hand. The direct
    # character check stays as a fallback for pilots whose main can't be
    # resolved (not registered in Auth, or no main set on the profile).
    # Evaluated before every other exclusion, so an exemption always wins over
    # ore category, fleet sessions and moon configuration.
    exempt = _exemptions()
    if not exempt['chars'] and not exempt['corps']:
        return False

    character = entry.character

    if character.pk in exempt['chars']:
        return True

    if exempt['chars']:
        main = _get_main_character(character)
        if main and main.pk != character.pk and main.pk in exempt['chars']:
            return True

    return bool(character.corporation_id) and character.corporation_id in exempt['corps']


def calculate_entry_tax(entry, corporation=None):
    # Some callers (the dashboard, the daily-summary widget) only ever price
    # the requesting user's own entries and never had a reason to look up the
    # corp object before — but is_excluded_by_moon_rental() and, since this
    # release, is_before_corp_join_date() both need one. Filled in here rather
    # than requiring every caller to pass it, so a check added to this
    # function's exclusion chain doesn't silently miss whichever callers
    # weren't updated to supply it — which is exactly the gap this fixes for
    # is_before_corp_join_date() on the dashboard views.
    if corporation is None:
        corporation = _get_corp_info(entry.character.corporation_id)

    category = get_ore_category(entry.type_id)

    # Gas cloud materials (Cytoserocin, Mykoserocin, Fullerite, Tricarboxyl
    # Vapor) aren't in the static OreCategory table with verified type_ids,
    # so they're recognized here by name instead — safer than guessing
    # type_ids, and adapts automatically to whatever ESI reports.
    if category == 'Default' and entry.type_name:
        name_lower = entry.type_name.lower()
        if any(k in name_lower for k in ('cytoserocin', 'mykoserocin', 'fullerite', 'tricarboxyl')):
            category = 'Gas'

    excluded = (
        # Scope first: mining outside the alliance's reach is not a question of
        # ore category or exemptions, it simply isn't ours to tax.
        is_outside_taxable_scope(entry)
        # Two separate "not yet ours" questions about WHEN: did the CORP
        # belong to the alliance yet, and did this CHARACTER belong to the
        # corp yet. Either being false at the entry's date means the mining
        # predates any claim on it — a corp new to the alliance and a
        # character new to an established corp are both covered.
        or is_before_corp_join_date(entry, corporation)
        or is_before_character_join_date(entry)
        or is_tax_exempt(entry)
        or is_excluded_by_fleet_session(entry, category)
        or is_excluded_by_alliance_moon(entry)
        or is_excluded_by_moon_rental(entry, corporation)
    )

    if excluded:
        return {
            'category': category,
            'tax_rate': Decimal('0.00'),
            'tax_amount': Decimal('0.00'),
            'excluded': True,
        }

    tax_rate = get_tax_rate(category, entry.date)
    tax_amount = entry.total_value * (tax_rate / Decimal('100'))

    return {
        'category': category,
        'tax_rate': tax_rate,
        'tax_amount': tax_amount,
        'excluded': False,
    }


def _get_main_character_name(character):
    """
    Resolves the main character name for a given character via
    CharacterOwnership -> User -> UserProfile.main_character.
    Falls back to the character's own name if it's not registered,
    has no owning user, or no main character is set.
    """
    try:
        ownership = character.character_ownership
        user = ownership.user
        main_char = user.profile.main_character
        if main_char:
            return main_char.character_name
    except Exception:
        pass
    return character.character_name


def calculate_alliance_billing(year, month):
    from .models import MiningLedgerEntry

    entries = MiningLedgerEntry.objects.filter(
        date__year=year, date__month=month
    ).select_related(
        'character',
        'character__character_ownership__user__profile__main_character',
    )

    corps_data = {}
    alliance_totals = {'mined': Decimal('0'), 'tax': Decimal('0')}

    for entry in entries:
        # Out-of-scope mining is left out of the alliance's books entirely, not
        # merely zero-rated. A corporation that is not ours has no place on a
        # billing page — listing it with a mined value and no tax reads like an
        # oversight and invites the question every time someone scrolls past.
        #
        # Exemptions are the opposite case and stay visible: those corps are
        # members, and that they owe nothing is a decision worth seeing.
        if is_outside_taxable_scope(entry):
            continue

        corp = entry.character.corporation_id
        corp_name = entry.character.corporation_name or 'Unknown'

        tax_info = calculate_entry_tax(entry, corporation=_get_corp_info(corp))

        if corp not in corps_data:
            corps_data[corp] = {
                'corp_name': corp_name,
                'total_mined': Decimal('0'),
                'total_tax': Decimal('0'),
                'members': {},
                'categories': {},
            }

        corp_entry = corps_data[corp]
        corp_entry['total_mined'] += entry.total_value
        corp_entry['total_tax'] += tax_info['tax_amount']

        # Resolve to the main so a player's alts roll up into one row, and keep
        # the main's character_id alongside it so the overview can link straight
        # to that pilot's detail page.
        main = _get_main_character(entry.character) or entry.character
        member_name = main.character_name
        if member_name not in corp_entry['members']:
            corp_entry['members'][member_name] = {
                'mined': Decimal('0'),
                'tax': Decimal('0'),
                'character_id': main.character_id,
            }
        corp_entry['members'][member_name]['mined'] += entry.total_value
        corp_entry['members'][member_name]['tax'] += tax_info['tax_amount']

        cat = tax_info['category']
        if cat not in corp_entry['categories']:
            corp_entry['categories'][cat] = {'value': Decimal('0'), 'tax': Decimal('0'), 'rate': tax_info['tax_rate']}
        corp_entry['categories'][cat]['value'] += entry.total_value
        corp_entry['categories'][cat]['tax'] += tax_info['tax_amount']

        alliance_totals['mined'] += entry.total_value
        alliance_totals['tax'] += tax_info['tax_amount']

    return {'corps': corps_data, 'totals': alliance_totals}


def _serialise_members(members):
    """
    Members dict -> JSON-safe dict, same treatment category_snapshot already
    gets: Decimal isn't JSON-serialisable, so 'mined'/'tax' go through str().
    character_id is already a plain int and passes through unchanged.
    """
    return {
        name: {
            'mined': str(data['mined']),
            'tax': str(data['tax']),
            'character_id': data.get('character_id'),
        }
        for name, data in members.items()
    }


def previous_month(year, month):
    """(year, month) of the month before the given one."""
    return (year, month - 1) if month > 1 else (year - 1, 12)


def is_month_frozen(year, month, now=None):
    """
    True once a month's invoices are final — from the moment its payment code
    is revealed (the day and UTC hour set under Payment Code Timing) onwards.

    From then on nothing may change the month: not the nightly sync, not
    Rebuild Snapshot. Corps can only pay once they have the code, so a frozen
    amount is exactly what they transfer — recalculating it afterwards could
    change the total under a payment already on its way, and the payment
    check would then no longer recognise it. The nightly syncs before the
    reveal are the month's last ones; they still pick up its closing days.
    """
    from .auth_hooks import last_issued_month
    return (year, month) <= last_issued_month(now)


def months_to_recalculate(now=None):
    """
    The months the nightly sync recalculates: the running month, plus the
    previous month for as long as it isn't frozen. Without the second, a
    month's last snapshot was taken on its final night, before its closing
    day's mining (and whatever ESI reports late) had landed — and that day was
    never billed.
    """
    from django.utils import timezone

    now = now or timezone.now()
    months = [(now.year, now.month)]
    prev = previous_month(now.year, now.month)
    if not is_month_frozen(*prev, now=now):
        months.append(prev)
    return months


def save_billing_records_for_month(year, month):
    """
    Saves an AllianceBillingRecord for all corps for a given month.

    Only ever called for a month that isn't frozen (see is_month_frozen()):
    by the nightly sync for the months months_to_recalculate() returns, and by
    Rebuild Snapshot, which refuses a frozen month. A paid record is never
    overwritten either way (save_billing_record() skips it).

    Includes corps that mined nothing this month but still owe a moon rental:
    calculate_alliance_billing() only ever discovers a corp through its ledger
    entries, so a corp with an active MoonRental and zero mining would
    otherwise never get a record at all — no record for save_billing_record()
    to skip once paid, and nothing for a PDF/CSV export to find.

    Called from exactly two places: the Rebuild Snapshot task, and the nightly
    sync for the running month. Nothing else may calculate a month — pages,
    exports and Mark as Paid only ever read the stored invoices.
    """
    data = calculate_alliance_billing(year, month)
    saved = 0
    for corp_id, corp_data in data['corps'].items():
        record = save_billing_record(corp_id, corp_data, year, month)
        if record:
            saved += 1

    from allianceauth.eveonline.models import EveCorporationInfo

    rental_only_corps = MoonRental.objects.filter(
        active=True
    ).exclude(
        corporation__corporation_id__in=data['corps'].keys()
    ).values_list('corporation_id', flat=True).distinct()

    for corp_pk in rental_only_corps:
        try:
            corp_obj = EveCorporationInfo.objects.get(pk=corp_pk)
        except EveCorporationInfo.DoesNotExist:
            continue

        if is_corp_outside_taxable_scope(corp_obj.corporation_id, corp_obj.corporation_name):
            continue

        rental_total = MoonRental.objects.filter(
            corporation=corp_obj, active=True
        ).aggregate(total=Sum('monthly_fee'))['total'] or Decimal('0')
        if rental_total <= 0:
            continue

        empty_corp_data = {
            'corp_name': corp_obj.corporation_name,
            'total_mined': Decimal('0'),
            'total_tax': Decimal('0'),
            'members': {},
            'categories': {},
        }
        record = save_billing_record(corp_obj.corporation_id, empty_corp_data, year, month)
        if record:
            saved += 1

    # Keeps every billed corp's alliance join date cached for the billing
    # page's sort order. Corps that only pay rent are otherwise never looked
    # up, because only mining triggers the join-date check. At most one ESI
    # call per corp per day (the result is cached), and only here in the
    # task — never while a page loads.
    for corp_id in AllianceBillingRecord.objects.filter(
        year=year, month=month
    ).values_list('corporation__corporation_id', flat=True):
        get_corp_join_date(corp_id)

    return saved


def save_billing_record(corp_id, corp_data, year, month):
    """
    Creates or updates an AllianceBillingRecord for a corp.
    Only updates existing records that are not yet paid.
    """
    corp_obj = _get_corp_info(corp_id)
    if not corp_obj:
        return None

    rental_total = MoonRental.objects.filter(
        corporation=corp_obj, active=True
    ).aggregate(total=Sum('monthly_fee'))['total'] or Decimal('0')

    total_due = corp_data['total_tax'] + rental_total

    category_snapshot = {
        cat: {
            'value': str(data['value']),
            'tax': str(data['tax']),
            'rate': str(data['rate']),
        }
        for cat, data in corp_data['categories'].items()
    }
    member_snapshot = _serialise_members(corp_data['members'])

    record, created = AllianceBillingRecord.objects.get_or_create(
        corporation=corp_obj,
        month=month,
        year=year,
        defaults={
            'total_mined_value': corp_data['total_mined'],
            'mining_tax_amount': corp_data['total_tax'],
            'moon_rental_total': rental_total,
            'total_due': total_due,
            'category_snapshot': category_snapshot,
            'member_snapshot': member_snapshot,
        }
    )

    if not created and not record.paid:
        record.total_mined_value = corp_data['total_mined']
        record.mining_tax_amount = corp_data['total_tax']
        record.moon_rental_total = rental_total
        record.total_due = total_due
        record.category_snapshot = category_snapshot
        record.member_snapshot = member_snapshot
        record.save()

    return record


_corp_cache = {}


def _get_corp_info(corp_id):
    from allianceauth.eveonline.models import EveCorporationInfo
    if corp_id in _corp_cache:
        return _corp_cache[corp_id]
    try:
        corp = EveCorporationInfo.objects.get(corporation_id=corp_id)
    except EveCorporationInfo.DoesNotExist:
        corp = None
    _corp_cache[corp_id] = corp
    return corp