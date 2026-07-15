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
            tags=['Industry', 'Universe', 'Market', 'Wallet'],
        )
    return _esi_client


def _get_type_name_db_first(type_id, esi):
    """Ore type name: check DB first, then ESI."""
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


# ─── MARKET PRICES (bulk endpoint) ────────────────────────────────────────────

def update_market_prices():
    """
    Updates prices for all entries without a price.
    Uses a single ESI bulk call for all prices.
    """
    entries = MiningLedgerEntry.objects.filter(price_per_unit=0)
    if not entries.exists():
        return 0

    bulk_prices = _fetch_bulk_prices()
    if not bulk_prices:
        return 0

    updated = 0
    for entry in entries:
        price = bulk_prices.get(entry.type_id, 0)
        if price <= 0:
            continue
        entry.price_per_unit = price
        entry.total_value = price * entry.quantity
        entry.save(update_fields=['price_per_unit', 'total_value'])
        updated += 1

    if updated:
        logger.info(f'Market prices updated for {updated} entries')

    return updated


def _fetch_bulk_prices():
    """Single ESI call for all EVE market prices via /markets/prices/."""
    try:
        esi = _get_esi_client()
        results = esi.client.Market.GetMarketsPrices().results()
        return {
            item.type_id: float(item.adjusted_price or item.average_price or 0)
            for item in results
            if item.type_id is not None
        }
    except Exception as e:
        logger.debug(f'Market prices not updated: {e}')
        return {}