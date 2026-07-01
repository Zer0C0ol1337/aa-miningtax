from decimal import Decimal

from .models import MiningLedgerEntry, OreCategory


# ─── CORPTOOLS INTEGRATION ────────────────────────────────────────────────────
# Corptools (CharacterMiningLedger) speichert Mining-Daten bereits in der DB.
# Wir lesen direkt daraus statt eigene ESI-Calls zu machen.
# Felder: character (→ CharacterAudit), date, type_name (FK→ItemType),
#         system (FK→SolarSystem), quantity

def _get_corptools_entries(character):
    """
    Liest Mining-Ledger-Einträge aus der Corptools-DB für diesen Character.
    Gibt eine Liste von Dicts zurück die unserem MiningLedgerEntry-Format entsprechen.
    Fällt auf leere Liste zurück wenn Corptools nicht installiert ist.
    """
    try:
        from corptools.models import CharacterMiningLedger, CharacterAudit

        # CharacterAudit ist der Corptools-Wrapper um EveCharacter
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
        # Corptools nicht installiert — Fallback auf eigenen ESI-Sync
        return None
    except Exception as e:
        print(f'⚠️ Corptools-Lesefehler für {character.character_name}: {e}')
        return None


def sync_character_mining(character):
    """
    Synct Mining-Daten für einen Character.
    Strategie:
    1. Versuche Corptools-DB zu lesen (kein ESI-Call)
    2. Falls Corptools nicht verfügbar: eigener ESI-Sync als Fallback
    """
    corptools_data = _get_corptools_entries(character)

    if corptools_data is not None:
        # Corptools verfügbar — Daten direkt aus DB übernehmen
        return _sync_from_corptools(character, corptools_data)
    else:
        # Fallback: eigener ESI-Sync
        return _sync_from_esi(character)


def _sync_from_corptools(character, entries):
    """Speichert Corptools-Daten in unsere MiningLedgerEntry-Tabelle."""
    saved = 0
    for entry in entries:
        MiningLedgerEntry.objects.update_or_create(
            character=character,
            date=entry['date'],
            type_id=entry['type_id'],
            solar_system_id=entry['solar_system_id'],
            defaults={
                'type_name': entry['type_name'],
                'quantity': entry['quantity'],
                'solar_system_name': entry['solar_system_name'],
            }
        )
        saved += 1
    return saved


def _sync_from_esi(character):
    """
    Fallback ESI-Sync wenn Corptools nicht verfügbar ist.
    Nutzt DB-first Caching um ESI-Calls zu minimieren.
    """
    try:
        from esi.openapi_clients import ESIClientProvider
        from esi.models import Token
        from esi.exceptions import HTTPNotModified
    except ImportError:
        print('⚠️ django-esi nicht verfügbar')
        return 0

    esi = _get_esi_client()

    token = Token.objects.filter(
        character_id=character.character_id
    ).require_scopes('esi-industry.read_character_mining.v1').require_valid().first()

    if not token:
        raise Exception(f'Kein gültiges Mining-Token für {character.character_name}')

    try:
        ledger = esi.client.Industry.GetCharactersCharacterIdMining(
            character_id=character.character_id,
            token=token
        ).results()
    except HTTPNotModified:
        return 0

    saved = 0
    for entry in ledger:
        type_name = _get_type_name_db_first(entry.type_id, esi)
        location_id = getattr(entry, 'solar_system_id', None)
        location_name = _get_location_name_db_first(location_id, token, esi)

        MiningLedgerEntry.objects.update_or_create(
            character=character,
            date=entry.date,
            type_id=entry.type_id,
            solar_system_id=location_id,
            defaults={
                'type_name': type_name,
                'quantity': entry.quantity,
                'solar_system_name': location_name,
            }
        )
        saved += 1

    return saved


# ESI-Client wird nur bei Bedarf (Fallback) initialisiert
_esi_client = None


def _get_esi_client():
    global _esi_client
    if _esi_client is None:
        from esi.openapi_clients import ESIClientProvider
        _esi_client = ESIClientProvider(
            compatibility_date="2026-06-09",
            ua_appname="EVE Mining Manager Plugin",
            ua_version="1.0",
            tags=['Industry', 'Universe', 'Market'],
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
    """System-Name: erst DB prüfen, dann ESI."""
    if location_id is None:
        return ''

    existing = MiningLedgerEntry.objects.filter(
        solar_system_id=location_id
    ).exclude(solar_system_name='').values_list('solar_system_name', flat=True).first()
    if existing:
        return existing

    name = f'Unbekannt ({location_id})'
    if location_id > 100_000_000:
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


# ─── ALLE CHARACTERS SYNCT ───────────────────────────────────────────────────

def sync_all_characters():
    """Synct alle Characters mit gültigem Mining-Token."""
    from allianceauth.eveonline.models import EveCharacter

    # Versuche zuerst über Corptools alle Characters zu holen
    try:
        from corptools.models import CharacterAudit
        character_ids = CharacterAudit.objects.values_list(
            'character__character_id', flat=True
        ).distinct()
    except ImportError:
        # Fallback: Characters über ESI-Token-Tabelle
        from esi.models import Token
        character_ids = Token.objects.filter(
            scopes__name='esi-industry.read_character_mining.v1'
        ).values_list('character_id', flat=True).distinct()

    total_synced = 0
    for char_id in character_ids:
        try:
            character = EveCharacter.objects.get(character_id=char_id)
            total_synced += sync_character_mining(character)
        except Exception as e:
            print(f'⚠️ Sync Fehler für Character {char_id}: {e}')

    return total_synced


# ─── MARKTPREISE (Bulk-Endpoint) ─────────────────────────────────────────────

def update_market_prices():
    """
    Aktualisiert Preise für alle Einträge ohne Preis.
    Nutzt einen einzigen ESI Bulk-Call statt einzelner Market-Order-Anfragen.
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
    """
    Ein einziger ESI-Call für alle EVE-Marktpreise via /markets/prices/.
    Kein Token nötig, liefert alle Typen auf einmal.
    """
    try:
        esi = _get_esi_client()
        results = esi.client.Market.GetMarketsPrices().results()
        return {
            item.type_id: float(item.adjusted_price or item.average_price or 0)
            for item in results
            if item.type_id is not None
        }
    except Exception as e:
        print(f'⚠️ Bulk-Preisabruf fehlgeschlagen: {e}')
        return {}