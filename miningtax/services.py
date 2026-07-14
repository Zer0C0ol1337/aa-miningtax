import logging

from .models import MiningLedgerEntry, OreCategory

logger = logging.getLogger(__name__)

# Struktur-IDs (Observer, z.B. Refineries) sind sehr große Zahlen (>100_000_000),
# echte Solar-System-IDs liegen im niedrigen Millionenbereich.
STRUCTURE_ID_THRESHOLD = 100_000_000


# ─── CORPTOOLS INTEGRATION ────────────────────────────────────────────────────

def _get_corptools_entries(character):
    """
    Liest Mining-Ledger-Einträge aus der Corptools-DB für diesen Character.
    Gibt None zurück wenn Corptools nicht installiert ist (→ ESI Fallback).
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
        logger.warning(f'Corptools-Lesefehler für {character.character_name}: {e}')
        return None


def _has_structure_entry(character, date, type_id):
    """
    Prüft ob für character/date/type_id bereits ein Eintrag vom Corp Observer
    existiert (solar_system_id > STRUCTURE_ID_THRESHOLD). Wenn ja, soll der
    persönliche Ledger-Sync diesen NICHT mit weniger genauen Daten überschreiben.
    """
    existing = MiningLedgerEntry.objects.filter(
        character=character, date=date, type_id=type_id
    ).first()
    if existing and existing.solar_system_id and existing.solar_system_id > STRUCTURE_ID_THRESHOLD:
        return True
    return False


# ─── PERSÖNLICHER CHARACTER SYNC ─────────────────────────────────────────────

def sync_character_mining(character):
    """
    Synct persönliche Mining-Daten für einen Character.
    1. Corptools-DB (kein ESI-Call)
    2. Fallback: eigener ESI-Sync
    """
    corptools_data = _get_corptools_entries(character)

    if corptools_data is not None:
        return _sync_from_corptools(character, corptools_data)
    else:
        return _sync_from_esi(character)


def _sync_from_corptools(character, entries):
    """
    Speichert Corptools-Daten in unsere MiningLedgerEntry-Tabelle.
    Überschreibt keinen bereits vorhandenen, genaueren Corp-Observer-Eintrag.
    """
    saved = 0
    for entry in entries:
        if _has_structure_entry(character, entry['date'], entry['type_id']):
            # Corp Observer hat schon einen genaueren Eintrag — überspringen
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
    Fallback ESI-Sync wenn Corptools nicht verfügbar ist.
    Überschreibt keinen bereits vorhandenen, genaueren Corp-Observer-Eintrag.
    """
    try:
        from esi.models import Token
        from esi.exceptions import HTTPNotModified
    except ImportError:
        logger.warning('django-esi nicht verfügbar')
        return 0

    esi = _get_esi_client()

    token = Token.objects.filter(
        character_id=character.character_id
    ).require_scopes('esi-industry.read_character_mining.v1').require_valid().first()

    if not token:
        logger.warning(f'Kein gültiges Mining-Token für {character.character_name}')
        return 0

    try:
        ledger = esi.client.Industry.GetCharactersCharacterIdMining(
            character_id=character.character_id,
            token=token
        ).results()
    except HTTPNotModified:
        existing = MiningLedgerEntry.objects.filter(character=character).count()
        logger.debug(f'{character.character_name}: Kein neuer Ledger (304) — {existing} bestehende Einträge aktuell')
        return existing

    saved = 0
    for entry in ledger:
        if _has_structure_entry(character, entry.date, entry.type_id):
            # Corp Observer hat schon einen genaueren Eintrag — überspringen
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
    Holt alle Mining-Observer (Monde/Strukturen) einer Corp und speichert
    die Ledger-Einträge in MiningLedgerEntry. Der Corp Observer hat immer
    Vorrang — er überschreibt ggf. vorhandene, weniger genaue Einträge
    aus dem persönlichen Ledger (unique_together ist character/date/type_id,
    also automatisch kein Duplikat möglich).
    """
    from allianceauth.eveonline.models import EveCharacter
    from esi.exceptions import HTTPNotModified

    esi = _get_esi_client()
    saved = 0

    try:
        observers = esi.client.Industry.GetCorporationCorporationIdMiningObservers(
            corporation_id=corp_id,
            token=token
        ).results()
        logger.info(f'Corp {corp_name}: {len(observers)} Observer (Strukturen) gefunden')
    except HTTPNotModified:
        existing = MiningLedgerEntry.objects.filter(
            character__corporation_id=corp_id
        ).count()
        logger.info(f'Corp {corp_name}: Observer-Liste nicht geändert (304) — {existing} bestehende Einträge aktuell')
        return existing
    except Exception as e:
        logger.warning(f'Corp {corp_name} ({corp_id}): Observer-Liste fehlgeschlagen: {e}')
        return 0

    for observer in observers:
        observer_id = observer.observer_id
        structure_name = _get_location_name_db_first(observer_id, token, esi)
        logger.info(f'Corp {corp_name}: Verarbeite Observer {observer_id} ({structure_name})')

        try:
            entries = esi.client.Industry.GetCorporationCorporationIdMiningObserversObserverId(
                corporation_id=corp_id,
                observer_id=observer_id,
                token=token
            ).results()
            logger.info(f'Corp {corp_name}: Observer {observer_id} → {len(entries)} Einträge')
        except HTTPNotModified:
            existing = MiningLedgerEntry.objects.filter(
                solar_system_id=observer_id
            ).count()
            logger.info(
                f'Corp {corp_name}: Observer {observer_id} ({structure_name}) nicht geändert (304) '
                f'— {existing} bestehende Einträge aktuell'
            )
            saved += existing
            continue
        except Exception as e:
            logger.warning(f'Corp {corp_name}: Observer {observer_id} fehlgeschlagen: {e}')
            continue

        for entry in entries:
            try:
                character = EveCharacter.objects.get(character_id=entry.character_id)
            except EveCharacter.DoesNotExist:
                try:
                    character = EveCharacter.objects.create_character(character_id=entry.character_id)
                    logger.info(f'Character {character.character_name} automatisch in AA angelegt')
                except Exception as e:
                    logger.warning(f'Character {entry.character_id} konnte nicht angelegt werden: {e}')
                    continue

            type_name = _get_type_name_db_first(entry.type_id, esi)

            # unique_together ist (character, date, type_id) — Corp Observer
            # überschreibt automatisch jeden vorhandenen (weniger genauen) Eintrag
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

    return saved


def sync_all_corp_observers():
    """
    Iteriert über alle Characters mit esi-industry.read_corporation_mining.v1 Token
    und synct die Corp-Observer-Daten für ihre jeweilige Corp.
    Jede Corp wird nur einmal gesynct (auch wenn mehrere Director-Tokens vorhanden).
    Respektiert ESI ETags — kein Cache-Clear, 304 Not Modified wird korrekt behandelt.
    """
    from esi.models import Token
    from allianceauth.eveonline.models import EveCharacter

    tokens = Token.objects.filter(
        scopes__name='esi-industry.read_corporation_mining.v1'
    ).require_valid()

    token_count = tokens.count()
    logger.info(f'Corp Observer Sync gestartet — {token_count} Token(s) mit Corp-Mining-Scope gefunden')

    if token_count == 0:
        logger.warning(
            'Kein Token mit esi-industry.read_corporation_mining.v1 gefunden. '
            'Ein Director-Character muss sich in Alliance Auth per SSO einloggen '
            'und den Corp-Mining-Scope authorisieren.'
        )
        return 0

    seen_corps = set()
    total_synced = 0

    for token in tokens:
        try:
            character = EveCharacter.objects.get(character_id=token.character_id)
            corp_id = character.corporation_id
            corp_name = character.corporation_name

            if corp_id in seen_corps:
                logger.debug(f'Corp {corp_name} bereits gesynct — überspringe duplizierten Token')
                continue
            seen_corps.add(corp_id)

            logger.info(f'Starte Corp Observer Sync für {corp_name} via {character.character_name}')
            synced = sync_corp_observer(corp_id, corp_name, token)
            total_synced += synced
            logger.info(f'Corp {corp_name}: Sync abgeschlossen — {synced} Einträge')

        except EveCharacter.DoesNotExist:
            logger.warning(
                f'Token {token.character_id} hat keinen zugehörigen EveCharacter in AA. '
                f'Character muss sich erst in Alliance Auth registrieren.'
            )
        except Exception as e:
            logger.warning(f'Corp Observer Sync fehlgeschlagen für Token {token.character_id}: {e}')

    logger.info(f'Corp Observer Sync abgeschlossen — gesamt {total_synced} Einträge')
    return total_synced


# ─── ALLE CHARACTERS SYNCT ───────────────────────────────────────────────────

def sync_all_characters():
    """Synct alle Characters aus Corptools-DB oder ESI-Token-Tabelle."""
    from allianceauth.eveonline.models import EveCharacter

    try:
        from corptools.models import CharacterAudit
        character_ids = CharacterAudit.objects.values_list(
            'character__character_id', flat=True
        ).distinct()
        logger.info(f'Corptools verfügbar — {character_ids.count()} Characters gefunden')
    except ImportError:
        from esi.models import Token
        character_ids = Token.objects.filter(
            scopes__name='esi-industry.read_character_mining.v1'
        ).values_list('character_id', flat=True).distinct()
        logger.info(f'Corptools nicht installiert — ESI Fallback, {character_ids.count()} Tokens gefunden')

    total_synced = 0
    for char_id in character_ids:
        try:
            character = EveCharacter.objects.get(character_id=char_id)
            total_synced += sync_character_mining(character)
        except Exception as e:
            logger.warning(f'Sync Fehler für Character {char_id}: {e}')

    return total_synced


# ─── ESI CLIENT ──────────────────────────────────────────────────────────────

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
    """Type-Name: erst DB prüfen, dann ESI."""
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
    """Struktur-/System-Name: erst DB prüfen, dann ESI."""
    if location_id is None:
        return ''

    existing = MiningLedgerEntry.objects.filter(
        solar_system_id=location_id
    ).exclude(solar_system_name='').values_list('solar_system_name', flat=True).first()
    if existing:
        return existing

    name = f'Unbekannt ({location_id})'
    if location_id > STRUCTURE_ID_THRESHOLD:
        try:
            structure = esi.client.Universe.GetUniverseStructuresStructureId(
                structure_id=location_id, token=token
            ).results()
            name = structure[0].name if structure else f'Mond-Struktur ({location_id})'
        except Exception:
            name = f'Mond-Struktur ({location_id})'
    else:
        try:
            system = esi.client.Universe.GetUniverseSystemsSystemId(
                system_id=location_id
            ).results()
            name = system[0].name if system else name
        except Exception:
            pass
    return name


# ─── MARKTPREISE (Bulk-Endpoint) ─────────────────────────────────────────────

def update_market_prices():
    """
    Aktualisiert Preise für alle Einträge ohne Preis.
    Ein einziger ESI Bulk-Call für alle Preise.
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

    return updated


def _fetch_bulk_prices():
    """Ein ESI-Call für alle EVE-Marktpreise via /markets/prices/."""
    try:
        esi = _get_esi_client()
        results = esi.client.Market.GetMarketsPrices().results()
        return {
            item.type_id: float(item.adjusted_price or item.average_price or 0)
            for item in results
            if item.type_id is not None
        }
    except Exception as e:
        logger.info(f'Marktpreise nicht aktualisiert: {e}')
        return {}