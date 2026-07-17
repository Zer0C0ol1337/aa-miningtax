import logging

from .models import MiningLedgerEntry, OreCategory

logger = logging.getLogger(__name__)

STRUCTURE_ID_THRESHOLD = 100_000_000


# ─── CORPTOOLS INTEGRATION ────────────────────────────────────────────────────

def _get_corptools_entries(character):
    """
    Reads mining ledger entries from the Corptools DB for this character.
    Returns None if Corptools is not installed (→ ESI fallback).
    """
    try:
        from corptools.models import CharacterMiningLedger, CharacterAudit

        audit = CharacterAudit.objects.filter(
            character__character_id=character.character_id
        ).first()

        if not audit:
            return []

        entries = CharacterMiningLedger.objects.filter(
            character=audit
        ).select_related('type_name', 'system')

        result = []
        for e in entries:
            result.append({
                'date': e.date,
                'type_id': e.type_name.type_id,
                'type_name': e.type_name.name,
                'solar_system_id': e.system.solar_system_id,
                'solar_system_name': e.system.name,
                'quantity': e.quantity,
            })
        return result

    except ImportError:
        return None
    except Exception as e:
        logger.warning(f'Corptools read error for {character.character_name}: {e}')
        return None


def _has_structure_entry(character, date, type_id):
    """
    Checks whether a corp-observer-sourced entry (solar_system_id > STRUCTURE_ID_THRESHOLD)
    already exists for character/date/type_id. If so, the personal ledger sync should
    NOT overwrite it with less precise data.
    """
    existing = MiningLedgerEntry.objects.filter(
        character=character, date=date, type_id=type_id
    ).first()
    if existing and existing.solar_system_id and existing.solar_system_id > STRUCTURE_ID_THRESHOLD:
        return True
    return False


# ─── PERSONAL CHARACTER SYNC ──────────────────────────────────────────────────

def sync_character_mining(character):
    """
    Syncs personal mining data for a character.
    1. Corptools DB (no ESI call)
    2. Fallback: own ESI sync
    """
    corptools_data = _get_corptools_entries(character)

    if corptools_data is not None:
        return _sync_from_corptools(character, corptools_data)
    else:
        return _sync_from_esi(character)


def _sync_from_corptools(character, entries):
    """
    Saves Corptools data into our MiningLedgerEntry table.
    Does not overwrite an already-present, more precise corp observer entry.
    """
    saved = 0
    for entry in entries:
        if _has_structure_entry(character, entry['date'], entry['type_id']):
            saved += 1
            continue

        MiningLedgerEntry.objects.update_or_create(
            character=character,
            date=entry['date'],
            type_id=entry['type_id'],
            defaults={
                'type_name': entry['type_name'],
                'quantity': entry['quantity'],
                'solar_system_id': entry['solar_system_id'],
                'solar_system_name': entry['solar_system_name'],
            }
        )
        saved += 1
    return saved


def _sync_from_esi(character):
    """
    Fallback ESI sync when Corptools is not available.
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
        if _has_structure_entry(character, entry.date, entry.type_id):
            saved += 1
            continue

        type_name = _get_type_name_db_first(entry.type_id, esi)
        location_id = getattr(entry, 'solar_system_id', None)
        location_name = _get_location_name_db_first(location_id, token, esi)

        MiningLedgerEntry.objects.update_or_create(
            character=character,
            date=entry.date,
            type_id=entry.type_id,
            defaults={
                'type_name': type_name,
                'quantity': entry.quantity,
                'solar_system_id': location_id,
                'solar_system_name': location_name,
            }
        )
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
        structure_name = _get_location_name_db_first(observer_id, token, esi)

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

            type_name = _get_type_name_db_first(entry.type_id, esi)

            MiningLedgerEntry.objects.update_or_create(
                character=character,
                date=entry.last_updated,
                type_id=entry.type_id,
                defaults={
                    'type_name': type_name,
                    'quantity': entry.quantity,
                    'solar_system_id': observer_id,
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
    """Syncs all characters from Corptools DB or the ESI token table."""
    from allianceauth.eveonline.models import EveCharacter

    try:
        from corptools.models import CharacterAudit
        character_ids = CharacterAudit.objects.values_list(
            'character__character_id', flat=True
        ).distinct()
    except ImportError:
        from esi.models import Token
        character_ids = Token.objects.filter(
            scopes__name='esi-industry.read_character_mining.v1'
        ).values_list('character_id', flat=True).distinct()

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

def sync_sov_systems():
    """
    Refreshes the SovSystem cache from ESI's public sovereignty map
    (/sovereignty/map/, no token needed) for every active SovFilterConfig.
    Rebuilds the table fully each run so it always reflects current
    sovereignty — no manual system list to maintain.
    """
    from .models import SovFilterConfig, SovSystem

    configs = SovFilterConfig.objects.filter(active=True).select_related('corporation')
    if not configs.exists():
        return 0

    esi = _get_esi_client()

    try:
        sov_map = esi.client.Sovereignty.GetSovereigntyMap().results()
    except Exception as e:
        logger.warning(f'Sovereignty map request failed: {e}')
        return 0

    target_corp_ids = {c.corporation.corporation_id for c in configs}
    matching_systems = [s for s in sov_map if getattr(s, 'corporation_id', None) in target_corp_ids]

    updated = 0
    seen_ids = set()
    for sys_entry in matching_systems:
        system_id = sys_entry.system_id
        corp_id = sys_entry.corporation_id
        seen_ids.add(system_id)

        system_name = _get_location_name_db_first(system_id, None, esi)

        SovSystem.objects.update_or_create(
            system_id=system_id,
            defaults={'system_name': system_name, 'corporation_id': corp_id}
        )
        updated += 1

    # Remove systems no longer held by any tracked corp
    removed, _ = SovSystem.objects.exclude(system_id__in=seen_ids).delete()

    logger.info(f'Sovereignty sync complete — {updated} system(s) tracked, {removed} stale entrie(s) removed')
    return updated


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
            tags=['Industry', 'Universe', 'Market', 'Wallet', 'Alliance', 'Sovereignty'],
        )
    return _esi_client


def _get_type_name_db_first(type_id, esi):
    """Ore type name, cheapest reliable source first.

    eveuniverse (when installed) is authoritative — its names come straight from
    ESI — so it's checked before the local OreCategory table. This matters
    because a stale or mis-seeded OreCategory row would otherwise bake a wrong
    name into every synced ledger entry. Order: eveuniverse → OreCategory →
    existing ledger rows → live ESI.
    """
    try:
        from eveuniverse.models import EveType
        et = EveType.objects.filter(id=type_id).first()
        if et and et.name:
            return et.name
    except ImportError:
        pass

    try:
        return OreCategory.objects.get(type_id=type_id).type_name
    except OreCategory.DoesNotExist:
        pass

    existing = MiningLedgerEntry.objects.filter(
        type_id=type_id
    ).exclude(type_name='').values_list('type_name', flat=True).first()
    if existing:
        return existing

    try:
        result = esi.client.Universe.GetUniverseTypesTypeId(type_id=type_id).results()
        return result[0].name if result else f'Type {type_id}'
    except Exception:
        return f'Type {type_id}'


def _get_location_name_db_first(location_id, token, esi):
    """Structure/system name: check DB first, then ESI."""
    if location_id is None:
        return ''

    existing = MiningLedgerEntry.objects.filter(
        solar_system_id=location_id
    ).exclude(solar_system_name='').values_list('solar_system_name', flat=True).first()
    if existing:
        return existing

    name = f'Unknown ({location_id})'
    if location_id > STRUCTURE_ID_THRESHOLD:
        try:
            structure = esi.client.Universe.GetUniverseStructuresStructureId(
                structure_id=location_id, token=token
            ).results()
            name = structure[0].name if structure else f'Structure ({location_id})'
        except Exception:
            name = f'Structure ({location_id})'
    else:
        try:
            system = esi.client.Universe.GetUniverseSystemsSystemId(
                system_id=location_id
            ).results()
            name = system[0].name if system else name
        except Exception:
            pass
    return name


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
    if not price_map:
        return 0

    # Portion sizes + which types are priced by refined value (recipe present).
    portion_map, refined_type_ids = _portion_info(type_ids)

    updated = 0
    for entry in entries:
        price = price_map.get(entry.type_id, 0)
        if price <= 0:
            continue

        billable_qty = entry.quantity
        # For refined ores, only whole reprocessing portions are billable.
        if entry.type_id in refined_type_ids:
            portion = portion_map.get(entry.type_id, 1) or 1
            billable_qty = (entry.quantity // portion) * portion

        entry.price_per_unit = price
        entry.total_value = price * billable_qty
        entry.save(update_fields=['price_per_unit', 'total_value'])
        updated += 1

    if updated:
        logger.info(f'Market prices updated for {updated} entries')

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


def _fetch_bulk_prices():
    """Single ESI call for all EVE market prices via /markets/prices/.

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

        logger.debug(f'Market prices not updated: {e}')
        return {}