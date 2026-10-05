import logging
from datetime import timedelta

from django.db import models
from django.db.models import Q
from django.utils import timezone

from .amounts import whole_isk_nearest
from .models import MiningLedgerEntry, OreCategory

logger = logging.getLogger(__name__)

STRUCTURE_ID_THRESHOLD = 100_000_000

# How far back the personal ledger is re-read from Corptools on each sync.
# Mirrors the 30 days ESI itself returned, which is what every sync so far was
# actually built on. Corptools keeps a pilot's entire history, so without a
# window every nightly run would re-walk months that are long settled.
CORPTOOLS_LEDGER_WINDOW_DAYS = 30


# ─── CORPTOOLS INTEGRATION ────────────────────────────────────────────────────

def _get_corptools_entries(character):
    """
    Reads a character's personal (belt/anomaly) mining ledger from Corptools.

    The primary source; ESI is only asked for a character Corptools has no
    CharacterAudit for (this returns None then, and sync_character_mining()
    falls back). A list, possibly empty, means Corptools knows the character.

    Reads the foreign-key ids directly (type_name_id, system_id). Corptools 3.x
    points these at eve_sde, whose models call their primary key `id`; the
    previous version asked for `.type_id` and `.solar_system_id`, which do not
    exist there. Every read raised, was logged as a "Corptools read error" and
    quietly fell through to ESI — so until 0.10.18 this path had never
    actually been used.
    """
    try:
        from corptools.models import CharacterMiningLedger, CharacterAudit
    except ImportError:
        return None

    audit = CharacterAudit.objects.filter(
        character__character_id=character.character_id
    ).first()
    if not audit:
        return None

    since = timezone.now().date() - timedelta(days=CORPTOOLS_LEDGER_WINDOW_DAYS)
    entries = CharacterMiningLedger.objects.filter(
        character=audit, date__gte=since,
    ).select_related('type_name', 'system')

    return [
        {
            'date': e.date,
            'type_id': e.type_name_id,
            'type_name': e.type_name.name if e.type_name else f'Type {e.type_name_id}',
            'solar_system_id': e.system_id,
            'solar_system_name': e.system.name if e.system else '',
            'quantity': e.quantity,
        }
        for e in entries
    ]


def _structure_quantity(character, date, type_id):
    """
    How much of this ore the corp observers already report for that day at
    structures (moon drills).

    ESI reports the same moon mining twice — once in the character's own ledger
    against the solar system, and once per structure via the corp observer — so
    this figure is what has to be deducted from the personal total to avoid
    counting it twice.
    """
    total = MiningLedgerEntry.objects.filter(
        character=character,
        date=date,
        type_id=type_id,
        solar_system_id__gt=STRUCTURE_ID_THRESHOLD,
    ).aggregate(total=models.Sum('quantity'))['total']
    return total or 0


def _save_non_structure_entry(character, date, type_id, type_name,
                              personal_quantity, location_id, location_name):
    """
    Stores what a character mined *outside* structures on a given day: the
    personal ledger's total for that ore minus whatever the observers already
    account for.

    Belt and anomaly mining used to disappear whenever the same ore was also
    mined at a moon that day — the row was skipped so as not to overwrite the
    more precise structure entry, and with one row per character/date/type there
    was nowhere else to put it. Both now coexist, distinguished by location,
    and the subtraction keeps the total honest.
    """
    remainder = (personal_quantity or 0) - _structure_quantity(character, date, type_id)

    if remainder <= 0:
        # Everything that day came from structures. Any leftover row from an
        # earlier sync would now be double counting, so it goes.
        MiningLedgerEntry.objects.filter(
            character=character, date=date, type_id=type_id,
            solar_system_id=location_id,
        ).delete()
        return False

    MiningLedgerEntry.objects.update_or_create(
        character=character,
        date=date,
        type_id=type_id,
        solar_system_id=location_id,
        defaults={
            'type_name': type_name,
            'quantity': remainder,
            'solar_system_name': location_name,
        }
    )
    return True


# ─── PERSONAL CHARACTER SYNC ──────────────────────────────────────────────────

def sync_character_mining(character):
    """
    Syncs one character's personal mining: Corptools first, ESI only for a
    character Corptools doesn't audit.
    """
    entries = _get_corptools_entries(character)
    if entries is not None:
        return _sync_from_corptools(character, entries)
    return _sync_from_esi(character)


def _sync_from_corptools(character, entries):
    """
    Saves Corptools data into our MiningLedgerEntry table.
    Does not overwrite an already-present, more precise corp observer entry.
    """
    saved = 0
    for entry in entries:
        if _save_non_structure_entry(
            character, entry['date'], entry['type_id'], entry['type_name'],
            entry['quantity'], entry['solar_system_id'], entry['solar_system_name'],
        ):
            saved += 1
    return saved


def _sync_from_esi(character):
    """
    Fallback for a character Corptools doesn't audit.
    Does not overwrite an already-present, more precise corp observer entry.
    """
    try:
        from esi.models import Token
        from esi.exceptions import HTTPNotModified
    except ImportError:
        logger.warning('django-esi not available')
        return 0

    esi = _get_esi_client()

    token = Token.objects.filter(
        character_id=character.character_id
    ).require_scopes('esi-industry.read_character_mining.v1').require_valid().first()

    if not token:
        logger.debug(f'No valid mining token for {character.character_name}')
        return 0

    try:
        ledger = esi.client.Industry.GetCharactersCharacterIdMining(
            character_id=character.character_id,
            token=token
        ).results()
    except HTTPNotModified:
        existing = MiningLedgerEntry.objects.filter(character=character).count()
        logger.debug(f'{character.character_name}: no new ledger data (304) — {existing} existing entries still current')
        return existing

    saved = 0
    for entry in ledger:
        type_name = _get_type_name_db_first(entry.type_id, esi=esi)
        location_id = getattr(entry, 'solar_system_id', None)
        location_name = _get_location_name_db_first(location_id, token=token, esi=esi)

        if _save_non_structure_entry(
            character, entry.date, entry.type_id, type_name,
            entry.quantity, location_id, location_name,
        ):
            saved += 1

    return saved


# ─── CORP OBSERVER SYNC ───────────────────────────────────────────────────────

def sync_corp_observer(corp_id, corp_name, token):
    """
    Fetches all mining observers (moons/structures) for a corp and saves the
    ledger entries into MiningLedgerEntry. The corp observer always takes
    precedence — it overwrites any less precise entries from the personal
    ledger (unique_together is character/date/type_id, so no duplicates possible).

    Per-observer detail is logged at DEBUG level to avoid flooding the log
    for corps with many structures/members — only the per-corp summary and
    any errors are logged at INFO/WARNING.
    """
    from allianceauth.eveonline.models import EveCharacter
    from esi.exceptions import HTTPNotModified

    esi = _get_esi_client()
    saved = 0
    new_characters = 0

    try:
        observers = esi.client.Industry.GetCorporationCorporationIdMiningObservers(
            corporation_id=corp_id,
            token=token
        ).results()
        logger.debug(f'Corp {corp_name}: {len(observers)} observers (structures) found')
    except HTTPNotModified:
        existing = MiningLedgerEntry.objects.filter(
            character__corporation_id=corp_id
        ).count()
        logger.info(f'Corp {corp_name}: observer list not modified (304) — {existing} existing entries still current')
        return existing
    except Exception as e:
        logger.warning(f'Corp {corp_name} ({corp_id}): observer list request failed: {e}')
        return 0

    for observer in observers:
        observer_id = observer.observer_id
        # The token is passed along so a structure Corptools doesn't know yet can
        # still be named — the one place a name lookup may reach ESI, because this
        # sync is an ESI call regardless and already holds the corp token.
        structure_name = _get_location_name_db_first(observer_id, token=token, esi=esi)

        try:
            entries = esi.client.Industry.GetCorporationCorporationIdMiningObserversObserverId(
                corporation_id=corp_id,
                observer_id=observer_id,
                token=token
            ).results()
            logger.debug(f'Corp {corp_name}: observer {observer_id} ({structure_name}) → {len(entries)} entries')
        except HTTPNotModified:
            existing = MiningLedgerEntry.objects.filter(
                solar_system_id=observer_id
            ).count()
            logger.debug(
                f'Corp {corp_name}: observer {observer_id} ({structure_name}) not modified (304) '
                f'— {existing} existing entries still current'
            )
            saved += existing
            continue
        except Exception as e:
            logger.warning(f'Corp {corp_name}: observer {observer_id} failed: {e}')
            continue

        for entry in entries:
            try:
                character = EveCharacter.objects.get(character_id=entry.character_id)
            except EveCharacter.DoesNotExist:
                try:
                    character = EveCharacter.objects.create_character(character_id=entry.character_id)
                    new_characters += 1
                except Exception as e:
                    logger.warning(f'Could not create character {entry.character_id}: {e}')
                    continue

            type_name = _get_type_name_db_first(entry.type_id)

            # The structure has to be part of the lookup, not just the payload:
            # keyed on character/date/ore alone this would match a belt entry
            # for the same ore that day and rewrite it into a structure entry,
            # which is precisely the data loss this is meant to avoid.
            MiningLedgerEntry.objects.update_or_create(
                character=character,
                date=entry.last_updated,
                type_id=entry.type_id,
                solar_system_id=observer_id,
                defaults={
                    'type_name': type_name,
                    'quantity': entry.quantity,
                    'solar_system_name': structure_name,
                }
            )
            saved += 1

    if new_characters:
        logger.info(f'Corp {corp_name}: {new_characters} previously unknown character(s) auto-registered in AA')

    return saved


def sync_all_corp_observers():
    """
    Iterates over all characters with an esi-industry.read_corporation_mining.v1
    token and syncs the corp observer data for their respective corporation.
    Each corp is synced only once (even if multiple director tokens exist).
    Respects ESI ETags — no cache clear, 304 Not Modified is handled correctly.
    """
    from esi.models import Token
    from allianceauth.eveonline.models import EveCharacter

    tokens = Token.objects.filter(
        scopes__name='esi-industry.read_corporation_mining.v1'
    ).require_valid()

    token_count = tokens.count()

    if token_count == 0:
        logger.warning(
            'No token with esi-industry.read_corporation_mining.v1 found. '
            'A director character must log in via Alliance Auth SSO '
            'and authorize the corp mining scope.'
        )
        return 0

    seen_corps = set()
    total_synced = 0
    corps_synced = 0

    for token in tokens:
        try:
            character = EveCharacter.objects.get(character_id=token.character_id)
            corp_id = character.corporation_id
            corp_name = character.corporation_name

            if corp_id in seen_corps:
                continue
            seen_corps.add(corp_id)

            synced = sync_corp_observer(corp_id, corp_name, token)
            total_synced += synced
            corps_synced += 1

        except EveCharacter.DoesNotExist:
            logger.warning(
                f'Token {token.character_id} has no matching EveCharacter in AA. '
                f'The character must register in Alliance Auth first.'
            )
        except Exception as e:
            logger.warning(f'Corp observer sync failed for token {token.character_id}: {e}')

    logger.info(f'Corp observer sync complete — {corps_synced} corp(s), {total_synced} entries total')
    return total_synced


# ─── SYNC ALL CHARACTERS ──────────────────────────────────────────────────────

def sync_all_characters():
    """
    Syncs all characters known either through Corptools' audits or through an
    ESI mining token.

    The union of both, not "Corptools' list if installed, else the ESI list"
    — a character can hold a valid ESI mining token before Corptools has ever
    audited them (a new alt, or Corptools simply hasn't run for them yet).
    With only the Corptools list, such a character was invisible to this
    function entirely: never attempted, so sync_character_mining()'s own
    None-vs-[] fallback (see _get_corptools_entries) never even got a chance
    to run for them.
    """
    from allianceauth.eveonline.models import EveCharacter

    character_ids = set()

    try:
        from corptools.models import CharacterAudit
        character_ids.update(
            CharacterAudit.objects.values_list(
                'character__character_id', flat=True
            ).distinct()
        )
    except ImportError:
        pass

    from esi.models import Token
    character_ids.update(
        Token.objects.filter(
            scopes__name='esi-industry.read_character_mining.v1'
        ).values_list('character_id', flat=True).distinct()
    )

    total_synced = 0
    errors = 0
    for char_id in character_ids:
        try:
            character = EveCharacter.objects.get(character_id=char_id)
            total_synced += sync_character_mining(character)
        except Exception as e:
            errors += 1
            logger.warning(f'Sync error for character {char_id}: {e}')

    logger.info(f'Personal ledger sync complete — {len(character_ids)} character(s), {total_synced} entries, {errors} error(s)')
    return total_synced


# ─── SOVEREIGNTY SYNC ──────────────────────────────────────────────────────────

def _repair_unresolved_system_names():
    """
    Re-resolves SovSystem rows whose name is still a placeholder, from eve_sde.
    Returns how many were fixed. Cheap in the normal case: nothing matches.
    """
    from .models import SovSystem

    repaired = 0
    for row in SovSystem.objects.filter(system_name__startswith='Unknown ('):
        name = _get_location_name_db_first(row.system_id)
        if name and not name.startswith('Unknown ('):
            row.system_name = name
            row.save(update_fields=['system_name'])
            repaired += 1
    return repaired


def _sov_matches_from_esi(target_corp_ids, target_alliance_ids):
    """
    Fallback for sync_sov_systems() when Corptools tracks no hub for the
    configured corps: the systems they hold per ESI's public sovereignty map.
    Returns [(system_id, corp_id), ...], or None if ESI couldn't be read.
    """
    from esi.exceptions import HTTPNotModified

    esi = _get_esi_client()

    def _fetch(force=False):
        # Single, unpaginated payload — hence result() rather than results().
        return esi.client.Sovereignty.GetSovereigntySystems().result(force_refresh=force)

    try:
        try:
            sov_map = _fetch()
        except HTTPNotModified:
            # Only reached when Corptools had nothing, so the data is not in
            # hand — honouring the ETag would leave the list without systems.
            sov_map = _fetch(force=True)
    except Exception as e:
        logger.warning(f'Sovereignty map request failed: {e}')
        return None

    matched = []
    for entry in getattr(sov_map, 'solar_systems', None) or []:
        claim = getattr(entry, 'claim', None)
        # aiopenapi3 renders the claim union as a RootModel, so the variant
        # (alliance / faction / unclaimed) sits one level down under .root.
        variant = getattr(claim, 'root', claim)
        alliance_claim = getattr(variant, 'alliance', None)
        if not alliance_claim:
            continue
        alliance_id = getattr(alliance_claim, 'alliance_id', None)
        corp_id = getattr(alliance_claim, 'corporation_id', None)
        if alliance_id in target_alliance_ids or corp_id in target_corp_ids:
            matched.append((entry.solar_system_id, corp_id))
    return matched


def sync_sov_systems():
    """
    Refreshes the SovSystem list: the sovereignty hubs Corptools already
    tracks first, ESI's public sovereignty map only if Corptools has none.

    A system counts when its holder is one of the configured reference corps
    or belongs to the alliance of one — sovereignty inside an alliance is
    normally held by a single holding corp, which need not be the one
    configured here.

    If neither source reports a system, the existing list is kept and a
    warning logged rather than emptying every system dropdown.

    Feeds the solar-system dropdowns on the Alliance Moons tab only — it has
    no effect on taxation.
    """
    from .models import SovFilterConfig, SovSystem

    configs = SovFilterConfig.objects.select_related('corporation', 'corporation__alliance')
    if not configs.exists():
        return 0

    target_corp_ids = set()
    target_alliance_ids = set()
    for config in configs:
        corp = config.corporation
        target_corp_ids.add(corp.corporation_id)
        if corp.alliance_id:
            target_alliance_ids.add(corp.alliance.alliance_id)

    matched = []
    source = 'Corptools'
    try:
        from corptools.models.sovereignty import SovereigntyHub
        matched = list(
            SovereigntyHub.objects.filter(
                Q(corporation__corporation__corporation_id__in=target_corp_ids)
                | Q(corporation__corporation__alliance__alliance_id__in=target_alliance_ids)
            ).values_list('solar_system_id', 'corporation__corporation__corporation_id').distinct()
        )
    except ImportError:
        pass

    if not matched:
        source = 'ESI'
        matched = _sov_matches_from_esi(target_corp_ids, target_alliance_ids)
        if matched is None:
            return SovSystem.objects.count()

    if not matched:
        repaired = _repair_unresolved_system_names()
        logger.warning(
            f'Neither Corptools nor ESI reports a sovereignty system for corp(s) '
            f'{sorted(target_corp_ids)} or alliance(s) {sorted(target_alliance_ids)} — '
            f'keeping the existing {SovSystem.objects.count()} system(s).'
            + (f' {repaired} name(s) repaired.' if repaired else '')
        )
        return SovSystem.objects.count()

    seen_ids = set()
    for system_id, corp_id in matched:
        seen_ids.add(system_id)
        SovSystem.objects.update_or_create(
            system_id=system_id,
            defaults={
                'system_name': _get_location_name_db_first(system_id),
                'corporation_id': corp_id or next(iter(target_corp_ids), 0),
            }
        )

    removed, _ = SovSystem.objects.exclude(system_id__in=seen_ids).delete()
    logger.info(
        f'Sovereignty sync complete ({source}) — {len(seen_ids)} system(s) tracked, '
        f'{removed} stale entrie(s) removed'
    )
    return len(seen_ids)


# ─── ESI CLIENT ────────────────────────────────────────────────────────────────

_esi_client = None


def _get_esi_client():
    global _esi_client
    if _esi_client is None:
        from esi.openapi_clients import ESIClientProvider
        _esi_client = ESIClientProvider(
            compatibility_date="2026-06-09",
            ua_appname="EVE Mining Manager Plugin",
            ua_version="1.0",
            # Every tag a fallback may still need. Corptools and eve_sde are
            # asked first everywhere; these are for what they don't have.
            # 'Alliance' is gone — alliance corp lists go through Alliance
            # Auth's own populate_alliance() since 0.10.18.
            tags=['Industry', 'Universe', 'Market', 'Wallet', 'Sovereignty', 'Corporation', 'Search', 'Character'],
        )
    return _esi_client


def _get_type_name_db_first(type_id, esi=None):
    """
    Ore type name, local sources first: eve_sde (CCP's static data export,
    loaded for Corptools), then eveuniverse, then OreCategory, then any
    existing ledger row. ESI only if none of them knows the type — which in
    practice means ore added by an expansion before either was refreshed.
    """
    try:
        from eve_sde.models import ItemType
        name = ItemType.objects.filter(id=type_id).values_list('name', flat=True).first()
        if name:
            return name
    except ImportError:
        pass

    try:
        from eveuniverse.models import EveType
        name = EveType.objects.filter(id=type_id).values_list('name', flat=True).first()
        if name:
            return name
    except ImportError:
        pass

    name = OreCategory.objects.filter(type_id=type_id).values_list('type_name', flat=True).first()
    if name:
        return name

    existing = MiningLedgerEntry.objects.filter(
        type_id=type_id
    ).exclude(type_name='').values_list('type_name', flat=True).first()
    if existing:
        return existing

    try:
        esi = esi or _get_esi_client()
        result = esi.client.Universe.GetUniverseTypesTypeId(type_id=type_id).results()
        return result[0].name if result else f'Type {type_id}'
    except Exception:
        return f'Type {type_id}'


def _resolve_location_name_local(location_id):
    """
    A location name from local sources only, ignoring what the ledger holds.

    Solar systems come from eve_sde, then eveuniverse. Player structures come
    from Corptools —
    its structure list (structures of corps it audits) first, then its general
    location cache (anything it has seen through assets or similar). Returns
    None when neither knows the location.
    """
    if location_id > STRUCTURE_ID_THRESHOLD:
        try:
            from corptools.models import EveLocation
            from corptools.models.structures import Structure
        except ImportError:
            return None
        name = Structure.objects.filter(structure_id=location_id).values_list('name', flat=True).first()
        if name:
            return name
        return EveLocation.objects.filter(location_id=location_id).values_list('location_name', flat=True).first()

    try:
        from eve_sde.models import SolarSystem
        name = SolarSystem.objects.filter(id=location_id).values_list('name', flat=True).first()
        if name:
            return name
    except ImportError:
        pass

    try:
        from eveuniverse.models import EveSolarSystem
    except ImportError:
        return None
    return EveSolarSystem.objects.filter(id=location_id).values_list('name', flat=True).first()


def _system_name_from_esi(system_id):
    """A solar system's name from ESI (public), or None. Fallback behind eve_sde."""
    from esi.exceptions import HTTPNotModified

    esi = _get_esi_client()

    def _fetch(force=False):
        return esi.client.Universe.GetUniverseSystemsSystemId(
            system_id=system_id
        ).results(force_refresh=force)

    try:
        try:
            system = _fetch()
        except HTTPNotModified:
            system = _fetch(force=True)
        return system[0].name if system else None
    except Exception:
        return None


def _get_location_name_db_first(location_id, token=None, esi=None):
    """
    Structure or system name: the ledger first, then local data (eve_sde for
    systems, Corptools for structures), ESI last.

    A system unknown to eve_sde is asked from ESI's public endpoint. A
    structure needs a token that can see it, so ESI is only tried when the
    caller passes one — the corp observer sync and the personal ESI fallback
    do. That keeps tax-free moons working for a drill Corptools hasn't picked
    up: the exemption matches on the structure's name, and a placeholder
    matches nothing.
    """
    if location_id is None:
        return ''

    existing = MiningLedgerEntry.objects.filter(
        solar_system_id=location_id
    ).exclude(solar_system_name='').values_list('solar_system_name', flat=True).first()
    if existing:
        return existing

    name = _resolve_location_name_local(location_id)
    if name:
        return name

    if location_id <= STRUCTURE_ID_THRESHOLD:
        return _system_name_from_esi(location_id) or f'Unknown ({location_id})'

    if token is not None and esi is not None:
        from esi.exceptions import HTTPNotModified

        def _fetch(force=False):
            return esi.client.Universe.GetUniverseStructuresStructureId(
                structure_id=location_id, token=token
            ).results(force_refresh=force)

        try:
            try:
                structure = _fetch()
            except HTTPNotModified:
                structure = _fetch(force=True)
            if structure:
                return structure[0].name
        except Exception:
            pass

    return f'Structure ({location_id})'


# ─── MARKET PRICES ────────────────────────────────────────────────────────────

# Reprocessing efficiency factors (Janice defaults). Applied to the raw
# reprocessing yields to get the actual materials received. Ore/moon ore use
# the ore factor; gas clouds use the gas factor.
REPROCESS_EFF_ORE = 0.9063
REPROCESS_EFF_GAS = 0.9500


def update_market_prices():
    """
    Updates prices for all mining ledger entries that don't have a price yet.

    Pricing strategy (best value first, always with a safe fallback):
      1. Refined value via Janice — the ore's reprocessed minerals valued at
         Janice's Jita split price. Preferred because raw ore market prices are
         thin and easy to manipulate, especially for moon ore (R32/R64), where
         the mineral value is far above the raw ore price.
      2. Janice raw split price of the item itself — for anything that can't be
         reprocessed (e.g. gas) but that Janice still prices.
      3. ESI adjusted_price — CCP's smoothed reference price, used whenever
         Janice is disabled, unreachable, or doesn't know the item.

    For refined ores the taxable quantity is rounded DOWN to whole reprocessing
    portions: ore only yields minerals in full batches (e.g. 100 units), so a
    non-divisible remainder can't actually be reprocessed. Taxing that remainder
    would rely on the thin, manipulable raw ore price — exactly what refined
    value avoids — so the remainder is left untaxed.
    """
    entries = MiningLedgerEntry.objects.filter(price_per_unit=0)
    if not entries.exists():
        return 0

    type_ids = set(entries.values_list('type_id', flat=True))

    # Per-unit price for each ore type, resolved once for the whole batch.
    price_map = _build_price_map(type_ids)
    if not price_map or not any(p > 0 for p in price_map.values()):
        logger.warning(
            f'No price found for any of {len(type_ids)} ore type(s); '
            f'{entries.count()} ledger entrie(s) stay at zero value'
        )
        return 0

    # Portion sizes + which types are priced by refined value (recipe present).
    portion_map, refined_type_ids = _portion_info(type_ids)

    updated = 0
    unpriced_types = set()
    for entry in entries:
        price = price_map.get(entry.type_id, 0)
        if price <= 0:
            # Neither Janice nor ESI knows this type — common for ore added in a
            # recent expansion, where CCP has not computed an adjusted price yet.
            unpriced_types.add(entry.type_id)
            continue

        billable_qty = entry.quantity
        # For refined ores, only whole reprocessing portions are billable.
        if entry.type_id in refined_type_ids:
            portion = portion_map.get(entry.type_id, 1) or 1
            billable_qty = (entry.quantity // portion) * portion

        entry.price_per_unit = price
        entry.total_value = whole_isk_nearest(price * billable_qty)
        entry.save(update_fields=['price_per_unit', 'total_value'])
        updated += 1

    if updated:
        logger.info(f'Market prices updated for {updated} entries')

    if unpriced_types:
        # Named rather than counted: knowing which types are unpriced is what
        # lets an officer decide whether it matters, and a zero-valued entry
        # yields zero tax no matter how much was mined.
        names = list(
            MiningLedgerEntry.objects.filter(type_id__in=unpriced_types)
            .values_list('type_name', flat=True).distinct()[:15]
        )
        logger.warning(
            f'No price for {len(unpriced_types)} ore type(s), their entries stay '
            f'at zero value: {", ".join(n for n in names if n)}'
        )

    return updated


def _portion_info(type_ids):
    """
    Returns (portion_map, refined_type_ids):
      - portion_map: {type_id: portion_size} for ore types that have a
        reprocessing recipe.
      - refined_type_ids: the set of type_ids that are priced by refined value
        (i.e. have a recipe), so callers know for which ores the whole-portion
        rounding applies.
    """
    recipes = _get_reprocessing_recipes(type_ids)
    portion_map = {tid: r['portion_size'] for tid, r in recipes.items()}
    return portion_map, set(recipes.keys())


def _build_price_map(type_ids):
    """
    Resolves a per-unit price for each ore type_id using the strategy described
    in update_market_prices(). Returns {type_id: price_per_unit}.
    """
    from .models import JaniceConfig

    esi_prices = _fetch_bulk_prices()  # always fetched as the universal fallback
    config = JaniceConfig.get_solo()

    # If Janice is disabled or unconfigured, everything falls back to ESI.
    if not config.enabled or not config.api_key:
        return {tid: esi_prices.get(tid, 0) for tid in type_ids}

    # Reprocessing recipes from eveuniverse (may be unavailable if not installed).
    recipes = _get_reprocessing_recipes(type_ids)

    # Fetch mineral/material prices SEPARATELY from raw ore prices. Requesting an
    # ore together with its own minerals in one Janice call can cause the ore to
    # crowd out the mineral entries in the response, which would collapse refined
    # value to the raw fallback. Two clean calls avoid that entirely.
    material_ids = set()
    for recipe in recipes.values():
        material_ids.update(int(m) for m in recipe['materials'].keys())

    ore_ids = {int(t) for t in type_ids}

    mineral_prices = _fetch_janice_split_prices(material_ids, config.api_key)
    raw_ore_prices = _fetch_janice_split_prices(ore_ids - material_ids, config.api_key)

    # Combined lookup: minerals win over ore for any overlapping id (an id that is
    # both a mined ore and a reprocessing output — rare, but minerals are what the
    # refined calc needs).
    janice_prices = {**raw_ore_prices, **mineral_prices}

    price_map = {}
    for tid in type_ids:
        price = 0.0
        recipe = recipes.get(tid)

        if recipe:
            # Refined value: sum(material qty × efficiency × janice split price)
            # divided by the ore's portion size to get per-unit value.
            portion = recipe['portion_size'] or 1
            eff = REPROCESS_EFF_GAS if _is_gas(tid) else REPROCESS_EFF_ORE
            refined = 0.0
            complete = True
            for mat_id, qty in recipe['materials'].items():
                mp = janice_prices.get(int(mat_id))
                if mp is None or mp <= 0:
                    complete = False
                    break
                refined += qty * eff * mp
            if complete and refined > 0:
                price = refined / portion
            else:
                # Recipe exists but a mineral price was missing. Do NOT fall back
                # to the raw ore price here — for moon ore the raw market price is
                # thin and often manipulated (the very reason we use refined value).
                # Use ESI's smoothed adjusted_price instead, which is safe.
                price = esi_prices.get(tid, 0)
        else:
            # No reprocessing recipe (e.g. gas): raw Janice split, then ESI.
            jp = janice_prices.get(int(tid))
            if jp and jp > 0:
                price = jp
            else:
                price = esi_prices.get(tid, 0)

        price_map[tid] = price

    return price_map


def _is_gas(type_id):
    """True if the ore type is categorised as Gas (uses gas reprocess efficiency)."""
    try:
        return OreCategory.objects.get(type_id=type_id).category == 'Gas'
    except OreCategory.DoesNotExist:
        return False


def _get_reprocessing_recipes(type_ids):
    """
    Returns reprocessing recipes for the given ore type_ids from eveuniverse:
        {type_id: {'portion_size': int, 'materials': {material_type_id: qty}}}

    Only ore types that actually have material data are included. If eveuniverse
    isn't installed the result is empty and callers fall back to raw prices.
    """
    try:
        from eveuniverse.models import EveType, EveTypeMaterial
    except ImportError:
        logger.debug('eveuniverse not installed — refined value unavailable, using raw prices')
        return {}

    recipes = {}
    materials = EveTypeMaterial.objects.filter(
        eve_type_id__in=type_ids
    ).values('eve_type_id', 'material_eve_type_id', 'quantity')

    portion_sizes = dict(
        EveType.objects.filter(id__in=type_ids).values_list('id', 'portion_size')
    )

    for m in materials:
        tid = m['eve_type_id']
        if tid not in recipes:
            recipes[tid] = {
                'portion_size': portion_sizes.get(tid, 1),
                'materials': {},
            }
        recipes[tid]['materials'][m['material_eve_type_id']] = m['quantity']

    return recipes


def _fetch_janice_split_prices(type_ids, api_key):
    """
    Fetches the Jita split price for the given type_ids from Janice's v2 pricer
    endpoint. Returns {type_id: split_price}.

    The request is split into chunks: Janice can silently drop items from very
    large batches, and a missing mineral price would wrongly collapse an ore's
    refined value back to its (often manipulated) raw price. Chunking keeps
    every requested price present.

    On any error for a chunk that chunk is skipped (its ores then fall back to
    ESI) — Janice being unreachable must never block billing.
    """
    if not type_ids:
        return {}

    ids = list(type_ids)
    chunk_size = 100
    prices = {}
    for start in range(0, len(ids), chunk_size):
        chunk = ids[start:start + chunk_size]
        prices.update(_fetch_janice_chunk(chunk, api_key))
    return prices


def _fetch_janice_chunk(type_ids, api_key):
    """Single Janice pricer call for up to ~100 type_ids."""
    import urllib.request
    import urllib.error
    import json

    url = 'https://janice.e-351.com/api/rest/v2/pricer?market=2'
    body = '\n'.join(str(tid) for tid in type_ids).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('X-ApiKey', api_key)
    req.add_header('Content-Type', 'text/plain')
    req.add_header('accept', 'application/json')
    # Janice sits behind Cloudflare, which blocks requests with a default
    # urllib user-agent (Error 1010). A normal UA string gets through.
    req.add_header('User-Agent', 'aa-miningtax/1.0 (Alliance Auth Mining Tax plugin)')

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as e:
        logger.warning(f'Janice price request failed ({e}) — those ores fall back to ESI prices')
        return {}

    prices = {}
    for item in data:
        try:
            eid = item['itemType']['eid']
            split = item['immediatePrices']['splitPrice']
            if eid is not None and split:
                prices[int(eid)] = float(split)
        except (KeyError, TypeError, ValueError):
            continue
    return prices


# eveuniverse's market prices are only trusted if its own price task ran this
# recently. Older than that means the task isn't scheduled on this install,
# and pricing from a stale table would under- or over-charge silently.
EVEUNIVERSE_PRICE_MAX_AGE_HOURS = 24


def _prices_from_eveuniverse():
    """
    All market prices from eveuniverse's EveMarketPrice table, or None when
    eveuniverse isn't installed, has no prices, or its newest price is older
    than EVEUNIVERSE_PRICE_MAX_AGE_HOURS.

    The same data ESI's /markets/prices/ returns — eveuniverse stores exactly
    that response — so the price basis, and therefore every bill, is
    unchanged whichever of the two answers.
    """
    from datetime import timedelta
    from django.utils import timezone

    try:
        from eveuniverse.models import EveMarketPrice
    except ImportError:
        return None

    newest = EveMarketPrice.objects.order_by('-updated_at').values_list('updated_at', flat=True).first()
    if newest is None or newest < timezone.now() - timedelta(hours=EVEUNIVERSE_PRICE_MAX_AGE_HOURS):
        return None

    return {
        type_id: float(adjusted or average or 0)
        for type_id, adjusted, average in
        EveMarketPrice.objects.values_list('eve_type_id', 'adjusted_price', 'average_price')
    }


def _fetch_bulk_prices():
    """All EVE market prices: eveuniverse first when its prices are fresh,
    otherwise a single ESI call via /markets/prices/.

    ETags are respected: the ESI client sends the stored ETag, and when ESI
    replies 304 Not Modified it raises HTTPNotModified rather than returning
    data. Prices only change a few times a day, so on a 304 we serve the last
    successful price list from the Django cache (Redis) — no wasted transfer,
    no empty result. The full list is only re-parsed when ESI actually reports
    a change.
    """
    from django.core.cache import cache

    CACHE_KEY = 'miningtax:bulk_prices'
    CACHE_TTL = 60 * 60 * 6  # 6h; refreshed whenever ESI reports a change

    local = _prices_from_eveuniverse()
    if local:
        logger.debug(f'Market prices from eveuniverse ({len(local)} types)')
        return local

    try:
        from esi.exceptions import HTTPNotModified
    except ImportError:
        HTTPNotModified = None

    try:
        esi = _get_esi_client()
        results = esi.client.Market.GetMarketsPrices().results()
        prices = {
            item.type_id: float(item.adjusted_price or item.average_price or 0)
            for item in results
            if item.type_id is not None
        }
        # Store the fresh list so a later 304 can be served from cache.
        cache.set(CACHE_KEY, prices, CACHE_TTL)
        return prices

    except Exception as e:
        # 304 Not Modified: nothing changed → reuse the cached price list.
        if HTTPNotModified is not None and isinstance(e, HTTPNotModified):
            cached = cache.get(CACHE_KEY)
            if cached:
                logger.debug('Market prices not modified (304) — using cached price list')
                return cached
            # No cache yet (e.g. first run after a restart). Fetch once while
            # ignoring the stored ETag so we get a full list to cache.
            logger.debug('Market prices 304 but cache empty — fetching fresh once')
            try:
                results = esi.client.Market.GetMarketsPrices().results(
                    use_etag=False, use_cache=False
                )
                prices = {
                    item.type_id: float(item.adjusted_price or item.average_price or 0)
                    for item in results
                    if item.type_id is not None
                }
                cache.set(CACHE_KEY, prices, CACHE_TTL)
                return prices
            except Exception as e2:
                logger.warning(f'Market price refresh after 304 failed: {e2}')
                return {}

        # Not a 304, so prices genuinely could not be fetched. Every entry then
        # stays at zero value and therefore zero tax — too consequential to
        # leave at debug level, where nobody would ever see it.
        logger.warning(f'Could not fetch market prices, entries stay unpriced: {e}')
        return {}


# Market category 25 ("Asteroid") holds every mineable type in EVE: ordinary
# ore, ice, moon ores and harvestable gas clouds alike.
ASTEROID_CATEGORY_ID = 25


def _ore_groups_from_sde():
    """
    Every published ore group of category 25 with its published types, from
    eve_sde: [(group_name, [(type_id, type_name), ...]), ...]. None when eve_sde
    isn't installed or holds no ore groups (not loaded), so the caller can
    fall back to ESI.
    """
    try:
        from eve_sde.models import ItemGroup, ItemType
    except ImportError:
        return None

    groups = list(ItemGroup.objects.filter(category_id=ASTEROID_CATEGORY_ID, published=True))
    if not groups:
        return None

    return [
        (
            group.name or '',
            [(tid, name or f'Type {tid}') for tid, name in
             ItemType.objects.filter(group=group, published=True).values_list('id', 'name')],
        )
        for group in groups
    ]


def _ore_groups_from_eveuniverse():
    """
    Same shape as _ore_groups_from_sde(), from eveuniverse — the second local
    source, for installs where eve_sde isn't loaded but eveuniverse holds
    category 25 (e.g. after eveuniverse_load_types). None when eveuniverse
    isn't installed or holds no ore groups.
    """
    try:
        from eveuniverse.models import EveGroup, EveType
    except ImportError:
        return None

    groups = list(EveGroup.objects.filter(eve_category_id=ASTEROID_CATEGORY_ID, published=True))
    if not groups:
        return None

    return [
        (
            group.name or '',
            [(tid, name or f'Type {tid}') for tid, name in
             EveType.objects.filter(eve_group=group, published=True).values_list('id', 'name')],
        )
        for group in groups
    ]


def _ore_groups_from_esi():
    """
    Same shape as _ore_groups_from_sde(), walked from ESI — one call per ore
    group. Fallback when eve_sde isn't available. None if ESI can't be read.
    """
    from esi.exceptions import HTTPNotModified

    esi = _get_esi_client()

    def _call(op, **kwargs):
        # Every one of these is a static lookup, so a 304 means "you already
        # have it" while we in fact have nothing in hand — hence the refetch.
        try:
            return op(**kwargs).results()
        except HTTPNotModified:
            return op(**kwargs).results(force_refresh=True)

    try:
        categories = _call(
            esi.client.Universe.GetUniverseCategoriesCategoryId,
            category_id=ASTEROID_CATEGORY_ID,
        )
    except Exception as e:
        logger.warning(f'Could not load ore category list from ESI: {e}')
        return None
    if not categories:
        return None

    result = []
    for group_id in (getattr(categories[0], 'groups', None) or []):
        try:
            groups = _call(esi.client.Universe.GetUniverseGroupsGroupId, group_id=group_id)
        except Exception as e:
            logger.warning(f'Could not load group {group_id}: {e}')
            continue
        if not groups:
            continue
        group = groups[0]
        types = [
            (tid, _get_type_name_db_first(tid, esi=esi))
            for tid in (getattr(group, 'types', None) or [])
        ]
        result.append((getattr(group, 'name', '') or '', types))
    return result


def sync_ore_categories():
    """
    Imports every mineable type into OreCategory and classifies it by its
    group. Walks category 25 -> groups -> types, so the result is complete by
    construction rather than depending on someone remembering to add an ore.

    Read from eve_sde first (CCP's static data export, loaded for Corptools —
    no ESI call at all), then eveuniverse, ESI only when neither has it. Only published
    groups and types are taken from eve_sde, matching what ESI returns.

    Existing rows are updated, which repairs a wrong category from an earlier
    seed. Returns (imported, updated).
    """
    from .billing import classify_group_name, category_from_rules
    from .models import OreCategory

    ore_groups = _ore_groups_from_sde()
    source = 'eve_sde'
    if ore_groups is None:
        ore_groups = _ore_groups_from_eveuniverse()
        source = 'eveuniverse'
    if ore_groups is None:
        ore_groups = _ore_groups_from_esi()
        source = 'ESI'
    if not ore_groups:
        logger.warning('Ore import: neither eve_sde nor ESI returned any ore group')
        return 0, 0

    logger.info(f'Ore import ({source}): {len(ore_groups)} group(s) in category {ASTEROID_CATEGORY_ID}')

    imported = 0
    updated = 0
    skipped = 0
    fallback_groups = set()

    for group_name, types in ore_groups:
        group_category = classify_group_name(group_name)

        if not group_category:
            # Everything in this category is mineable by definition, so an
            # unrecognised group is still ore — it just isn't ice, gas, moon
            # ore or Mercoxit. Ordinary ore groups are named after the ore
            # itself ("Veldspar", "Arkonor"), which matches none of the rules,
            # and skipping them left those types at the Default rate with
            # nothing to indicate why.
            group_category = 'Ore'
            fallback_groups.add(group_name)

        for type_id, name in types:
            # Alliance rules win over EVE's own grouping, and are evaluated per
            # type so a single ore can be pulled out of an otherwise ordinary
            # group — which is the whole point for things like Prismaticite.
            category = category_from_rules(name, group_name) or group_category

            existing = OreCategory.objects.filter(type_id=type_id).first()
            if existing and existing.locked:
                # Deliberately categorised by hand — the import refreshes the
                # name but leaves the category alone, otherwise a 0% ore would
                # quietly revert to a taxed category overnight.
                if existing.type_name != name:
                    existing.type_name = name
                    existing.save(update_fields=['type_name'])
                skipped += 1
                continue

            _, created = OreCategory.objects.update_or_create(
                type_id=type_id,
                defaults={'type_name': name, 'category': category},
            )
            if created:
                imported += 1
            else:
                updated += 1

    if fallback_groups:
        # Named rather than counted: if a group that should have its own
        # category lands here, that is a missing rule, and only the name shows
        # which one.
        logger.info(
            f'Filed as plain Ore for want of a more specific rule: '
            f'{", ".join(sorted(fallback_groups))}'
        )

    # A type that could not be classified before may well be classifiable now,
    # so the record of past failures is dropped rather than left to expire.
    from .billing import forget_unclassifiable_types
    forget_unclassifiable_types()

    logger.info(
        f'Ore import complete — {imported} new, {updated} updated, '
        f'{skipped} locked and left unchanged'
    )
    return imported, updated


def repair_unresolved_ledger_names():
    """
    Re-resolves ledger entries whose location is still a placeholder: local
    sources first (eve_sde for systems, Corptools for structures), ESI for
    whatever they don't know.

    A lookup that fails once is otherwise permanent: the name is written as
    "Unknown (id)" or "Structure (id)" and nothing ever revisits it, because
    _get_location_name_db_first prefers an existing name and finds that one.
    Beyond looking wrong, it silently breaks tax-free moons — the exemption
    matches on the structure name, and a placeholder matches nothing, so ore
    from an exempt moon gets taxed with no indication why.

    Returns the number of entries repaired.
    """
    from .models import MiningLedgerEntry

    # 'Unbekannt' and 'Mond-Struktur' are placeholders written by versions
    # before the messages were translated. They are matched here too, or the
    # oldest broken entries — the ones most likely to be un-exempting a
    # tax-free moon — would be the only ones the repair could never reach.
    broken = MiningLedgerEntry.objects.filter(
        Q(solar_system_name__startswith='Unknown (')
        | Q(solar_system_name__startswith='Structure (')
        | Q(solar_system_name__startswith='Unbekannt (')
        | Q(solar_system_name__startswith='Mond-Struktur (')
        | Q(solar_system_name='')
    ).exclude(solar_system_id__isnull=True)

    location_ids = list(broken.values_list('solar_system_id', flat=True).distinct())
    if not location_ids:
        return 0

    logger.info(f'Repairing {len(location_ids)} unresolved location name(s)')

    esi = None
    repaired = 0
    for location_id in location_ids:
        name = _resolve_location_name_local(location_id)
        if not name:
            esi = esi or _get_esi_client()
            name = _resolve_location_name_esi(location_id, esi)
        if not name:
            continue
        repaired += MiningLedgerEntry.objects.filter(
            solar_system_id=location_id
        ).exclude(solar_system_name=name).update(solar_system_name=name)

    logger.info(f'Repaired {repaired} ledger entrie(s)')
    return repaired


def _any_corp_mining_token():
    """Any valid corp-mining token, used to read structure names from ESI."""
    from esi.models import Token
    return (
        Token.objects
        .require_scopes('esi-industry.read_corporation_mining.v1')
        .require_valid()
        .first()
    )


def _resolve_location_name_esi(location_id, esi):
    """
    Asks ESI for a location name — the fallback behind the local sources in
    repair_unresolved_ledger_names(). Structures need a token that can see
    them; any corp mining token will do, since that is the same access the
    observer sync already relies on. Systems are public. Returns None if ESI
    has no answer.
    """
    if location_id <= STRUCTURE_ID_THRESHOLD:
        return _system_name_from_esi(location_id)

    token = _any_corp_mining_token()
    if not token:
        return None

    from esi.exceptions import HTTPNotModified

    def _fetch(force=False):
        return esi.client.Universe.GetUniverseStructuresStructureId(
            structure_id=location_id, token=token
        ).results(force_refresh=force)

    try:
        try:
            res = _fetch()
        except HTTPNotModified:
            res = _fetch(force=True)
        return res[0].name if res else None
    except Exception as e:
        logger.debug(f'Could not resolve location {location_id}: {e}')
        return None