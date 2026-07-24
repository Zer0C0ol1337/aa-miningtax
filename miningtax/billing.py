import logging
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from .models import (
    OreCategory, TaxRate, FleetSession, AllianceMoon, MoonRental,
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


def _category_from_eveuniverse(type_id):
    """
    Derives an ore category from the type's group in eveuniverse, e.g.
    "Exceptional Moon Asteroids" -> R64, "Harvestable Cloud" -> Gas.

    This is what keeps newly introduced or simply unseeded ore from silently
    falling through to the Default rate: the group is authoritative SDE data,
    so nothing has to be maintained by hand. Returns None when eveuniverse
    isn't installed or doesn't know the type.
    """
    try:
        from eveuniverse.models import EveType
    except ImportError:
        return None

    eve_type = EveType.objects.filter(id=type_id).select_related('eve_group').first()
    if not eve_type or not eve_type.eve_group:
        return None

    return classify_group_name(eve_type.eve_group.name)


def _type_and_group_from_esi(type_id):
    """
    A type's own name and its group name, straight from ESI.

    The last resort when eveuniverse cannot answer — either because it is not
    installed, or because its type data predates the ore in question. Without
    this, ore introduced by an expansion sits at the Default rate until someone
    notices and reloads eveuniverse, which is not a thing anyone thinks to check.
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
    return _cached(
        'miningtax:lookup:tax_rates',
        lambda: {
            c: r for c, r in TaxRate.objects.values_list('ore_category', 'tax_rate')
        },
    )


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

    name = ''
    group_name = ''
    try:
        from eveuniverse.models import EveType
        eve_type = EveType.objects.filter(id=type_id).select_related('eve_group').first()
        if eve_type:
            name = eve_type.name or ''
            group_name = eve_type.eve_group.name if eve_type.eve_group else ''
    except ImportError:
        pass

    if not group_name:
        # eveuniverse either isn't installed or doesn't know this type. Asking
        # ESI covers ore outside the Asteroid category the bulk import walks,
        # and ore added after eveuniverse was last loaded.
        #
        # The negative answer is remembered as well. This runs once per ledger
        # entry while a page renders, so without it every unclassifiable type
        # cost two ESI calls on every single view — the ore is not going to
        # change group in the meantime, and a day is soon enough to notice a
        # new rule.
        miss_key = f'miningtax:unclassifiable:{type_id}'
        if cache.get(miss_key):
            return 'Default'

        name, group_name = _type_and_group_from_esi(type_id)

    derived = category_from_rules(name, group_name) or classify_group_name(group_name)
    if not derived:
        logger.info(
            f'Type {type_id} ("{name or "unknown"}", group "{group_name or "unknown"}") '
            f'matches no category rule, taxed at the Default rate'
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


def get_tax_rate(category):
    rates = _tax_rates()
    if category in rates:
        return rates[category]
    return rates.get('Default', DEFAULT_TAX_RATE)


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


def is_outside_taxable_scope(entry):
    """
    True when the character who mined this is somewhere the alliance does not
    tax — a high-sec alt, a trade character, a corp outside the alliance.

    Judged on the character's *current* corporation, not where they were at the
    time. Mining history is not stamped with a corporation, so present
    membership is the only thing available; someone who leaves the alliance
    therefore takes their unpaid billing with them, which is the same outcome as
    leaving without paying.
    """
    scope = _taxable_scope()
    if not scope['active']:
        return False

    character = entry.character
    corp_id = character.corporation_id

    if corp_id in scope['corps']:
        return False

    # The corporation's own record decides, not the copy held on the character.
    # Both are real EVE alliance IDs, but there is one corporation record per
    # corporation and one character record per pilot — so the character's copy
    # is the one that goes stale, and a pilot who changed corp months ago would
    # keep being billed on the strength of it.
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
        f'Corporation {corp_id} ({entry.character.corporation_name or "unknown"}) '
        f'is not registered in Alliance Auth, so its alliance cannot be '
        f'confirmed — left out of billing while a scope is set'
    )
    return True


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

    tax_rate = get_tax_rate(category)
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


def save_billing_records_for_month(year, month):
    """
    Saves an AllianceBillingRecord for all corps for a given month.
    Called daily after the sync to keep billing records up to date.
    """
    data = calculate_alliance_billing(year, month)
    saved = 0
    for corp_id, corp_data in data['corps'].items():
        record = save_billing_record(corp_id, corp_data, year, month)
        if record:
            saved += 1
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

    record, created = AllianceBillingRecord.objects.get_or_create(
        corporation=corp_obj,
        month=month,
        year=year,
        defaults={
            'total_mined_value': corp_data['total_mined'],
            'mining_tax_amount': corp_data['total_tax'],
            'moon_rental_total': rental_total,
            'total_due': total_due,
            'category_snapshot': {
                cat: {
                    'value': str(data['value']),
                    'tax': str(data['tax']),
                    'rate': str(data['rate']),
                }
                for cat, data in corp_data['categories'].items()
            },
        }
    )

    if not created and not record.paid:
        record.total_mined_value = corp_data['total_mined']
        record.mining_tax_amount = corp_data['total_tax']
        record.moon_rental_total = rental_total
        record.total_due = total_due
        record.category_snapshot = {
            cat: {
                'value': str(data['value']),
                'tax': str(data['tax']),
                'rate': str(data['rate']),
            }
            for cat, data in corp_data['categories'].items()
        }
        record.save()

    return record


def mark_corp_paid(corp_id, corp_data, year, month):
    """Saves the billing record and marks it as paid."""
    record = save_billing_record(corp_id, corp_data, year, month)
    if record:
        record.paid = True
        record.paid_at = timezone.now()
        record.save(update_fields=['paid', 'paid_at'])
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