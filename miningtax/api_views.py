"""
Small JSON endpoints used by the Settings UI to fill dependent dropdowns.

Kept in its own module so views.py stays focused on page rendering. Nothing
here renders a template — these are called by JS from settings.html only.

Local data first since 0.10.18: moons come from eve_sde (CCP's static data
export, loaded for Corptools), structures from Corptools' own structure and
location tables plus names already stored in the mining ledger. ESI is only
asked when those have nothing.
"""
import logging

from django.core.cache import cache
from django.http import JsonResponse

from esi.exceptions import HTTPNotModified
from esi.models import Token

from .services import STRUCTURE_ID_THRESHOLD, _get_esi_client

logger = logging.getLogger(__name__)

# settings_view's stale-moon warning reads the structure-list cache key to
# learn which structures a system has, so it is filled on every lookup.
STRUCTURES_CACHE_TIMEOUT = 60 * 60 * 6  # 6 hours


def _resolve_moon_names(esi, moon_ids):
    """
    Resolves moon IDs to their in-game names (e.g. "M-PGT0 II - Moon 4").

    Tries the bulk /universe/names/ endpoint first, since it needs a single
    request for the whole system. Not every django-esi build exposes that
    operation under the same name, so it falls back to resolving each moon
    individually — slower, but guaranteed to work.
    """
    names = {}

    bulk_op = getattr(esi.client.Universe, 'PostUniverseNames', None)
    if bulk_op is not None:
        try:
            try:
                result = bulk_op(ids=list(moon_ids)).results()
            except HTTPNotModified:
                result = bulk_op(ids=list(moon_ids)).results(force_refresh=True)
            for item in result or []:
                item_id = getattr(item, 'id', None)
                item_name = getattr(item, 'name', None)
                if item_id and item_name:
                    names[item_id] = item_name
            if names:
                return names
        except Exception as e:
            logger.debug(f'Bulk name resolve unavailable, falling back per moon: {e}')

    for moon_id in moon_ids:
        try:
            try:
                res = esi.client.Universe.GetUniverseMoonsMoonId(moon_id=moon_id).results()
            except HTTPNotModified:
                res = esi.client.Universe.GetUniverseMoonsMoonId(
                    moon_id=moon_id
                ).results(force_refresh=True)
            if res:
                names[moon_id] = res[0].name
        except Exception:
            continue

    return names


def _moons_from_esi(system_id):
    """
    All moons of a system from ESI — the fallback when eve_sde isn't installed
    or doesn't know the system. Cached for 30 days, since this walks every
    planet of the system and moons never move.
    """
    cache_key = f'miningtax:moons:{system_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    esi = _get_esi_client()

    def _fetch(force=False):
        return esi.client.Universe.GetUniverseSystemsSystemId(
            system_id=system_id
        ).results(force_refresh=force)

    try:
        try:
            systems = _fetch()
        except HTTPNotModified:
            # Our own cache missed, so the data isn't in hand — discard the
            # ETag once and refetch. Systems never change.
            systems = _fetch(force=True)
    except Exception as e:
        logger.warning(f'Could not load system {system_id} from ESI: {e}')
        return []

    if not systems:
        return []

    moon_ids = []
    for planet in (getattr(systems[0], 'planets', None) or []):
        moon_ids.extend(getattr(planet, 'moons', None) or [])

    names = _resolve_moon_names(esi, moon_ids) if moon_ids else {}
    moons = sorted(
        ({'id': mid, 'name': names.get(mid, f'Moon {mid}')} for mid in moon_ids),
        key=lambda m: m['name']
    )
    cache.set(cache_key, moons, 60 * 60 * 24 * 30)
    return moons


def get_moons_for_system(system_id):
    """
    All moons of a solar system as [{'id': ..., 'name': ...}], sorted by name.
    From eve_sde first — a single indexed query, since the SDE holds every
    moon — then eveuniverse, and from ESI only when neither has the system.
    """
    try:
        from eve_sde.models import Moon
        moons = [
            {'id': moon_id, 'name': name or f'Moon {moon_id}'}
            for moon_id, name in Moon.objects.filter(solar_system_id=system_id)
            .order_by('name').values_list('id', 'name')
        ]
        if moons:
            return moons
    except ImportError:
        pass

    try:
        from eveuniverse.models import EveMoon
        moons = [
            {'id': moon_id, 'name': name or f'Moon {moon_id}'}
            for moon_id, name in EveMoon.objects.filter(eve_planet__eve_solar_system_id=system_id)
            .order_by('name').values_list('id', 'name')
        ]
        if moons:
            return moons
    except ImportError:
        pass

    return _moons_from_esi(system_id)


def api_moons_for_system(request):
    """
    GET /miningtax/api/moons/?system_id=30004478
    Returns {"moons": [{"id": 40283499, "name": "M-PGT0 I - Moon 1"}, ...]}

    Gated to officers — same audience as the Settings page it feeds. The
    permission helper is imported lazily to avoid a circular import.
    """
    from .views import has_full_officer_access

    if not request.user.is_authenticated or not has_full_officer_access(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)

    system_id = request.GET.get('system_id')
    if not system_id or not system_id.isdigit():
        return JsonResponse({'moons': []})

    return JsonResponse({'moons': get_moons_for_system(int(system_id))})


# Scope for the ESI structure-search fallback. Needs no in-game role — it
# returns what the searching character can dock at.
SEARCH_SCOPE = 'esi-search.search_structures.v1'


def _search_token_for(user):
    """
    A search-capable token belonging to the requesting officer.

    Deliberately their own rather than any token on the system: search results
    depend on docking access, so using someone else's would show structures the
    officer cannot see, or hide ones they can, with nothing to explain the
    difference.
    """
    character_ids = list(
        user.character_ownerships.select_related('character')
        .values_list('character__character_id', flat=True)
    )
    if not character_ids:
        return None

    return (
        Token.objects
        .filter(character_id__in=character_ids)
        .require_scopes(SEARCH_SCOPE)
        .require_valid()
        .first()
    )


def _local_structure_names(system_name):
    """
    Structure names in a system from local data: Corptools' structure list
    (structures of every corp it audits), its location cache (anything it has
    seen through assets or similar), and names already stored in the mining
    ledger.
    """
    from .models import MiningLedgerEntry

    names = set()
    try:
        from corptools.models import EveLocation
        from corptools.models.structures import Structure

        names.update(
            Structure.objects.filter(system_name__name__iexact=system_name)
            .values_list('name', flat=True)
        )
        names.update(
            EveLocation.objects.filter(
                system__name__iexact=system_name,
                location_id__gt=STRUCTURE_ID_THRESHOLD,
            ).values_list('location_name', flat=True)
        )
    except ImportError:
        pass

    # EVE starts every structure name with its system by default, which is how
    # ledger entries (stored under the structure name) are matched to a system.
    names.update(
        MiningLedgerEntry.objects.filter(
            solar_system_id__gt=STRUCTURE_ID_THRESHOLD,
            solar_system_name__istartswith=system_name,
        ).values_list('solar_system_name', flat=True).distinct()
    )
    return {n for n in names if n}


def _esi_structure_names(system_name, token):
    """
    Structure names found by an ESI structure search with the officer's own
    token — the fallback when no local source knows a structure in the system.
    Returns (names, reason); reason is None on success.

    Works by searching for the system name, which EVE puts at the start of
    every structure name by default, so only structures the officer can dock
    at are found.
    """
    if not token:
        return set(), 'no_search_token'

    esi = _get_esi_client()

    def _search(force=False):
        return esi.client.Search.GetCharactersCharacterIdSearch(
            character_id=token.character_id,
            categories=['structure'],
            search=system_name,
            token=token,
        ).results(force_refresh=force)

    try:
        try:
            result = _search()
        except HTTPNotModified:
            result = _search(force=True)
    except Exception as e:
        logger.warning(f'Structure search for "{system_name}" failed: {e}')
        return set(), 'search_failed'

    names = set()
    for item in (result or []):
        for sid in (getattr(item, 'structure', None) or []):
            try:
                res = esi.client.Universe.GetUniverseStructuresStructureId(
                    structure_id=sid, token=token
                ).results()
                if res:
                    names.add(res[0].name)
            except Exception:
                # No docking access to this one, so no name — skipping is
                # right, an unidentifiable structure is no use in a dropdown.
                continue
    return names, None


def get_structures_in_system(system_name, user=None):
    """
    Structure names in a solar system, sorted. Local data first; the ESI
    structure search with the requesting officer's token only when no local
    source knows any structure there. Returns (names, reason) — reason is
    None on success, otherwise one of 'none_found', 'no_search_token',
    'search_failed', which the Settings page turns into a hint.

    The result also fills the cache key settings_view's stale-moon warning
    reads to learn which structures a system has.
    """
    names = _local_structure_names(system_name)
    reason = None

    if not names:
        token = _search_token_for(user) if user is not None else None
        names, reason = _esi_structure_names(system_name, token)

    names = sorted(names)
    if names:
        reason = None
    elif reason is None:
        reason = 'none_found'

    cache.set(f'miningtax:sys_structures:{system_name.lower()}', names, STRUCTURES_CACHE_TIMEOUT)
    return names, reason


def api_structures_for_system(request):
    """
    GET /miningtax/api/system-structures/?system=P9F-ZG
    Returns {"structures": [...], "reason": null}

    Officer-only, same gate as the Settings page that consumes it.
    """
    from .views import has_full_officer_access

    if not request.user.is_authenticated or not has_full_officer_access(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)

    system_name = (request.GET.get('system') or '').strip()
    if len(system_name) < 3:
        return JsonResponse({'structures': [], 'reason': None})

    names, reason = get_structures_in_system(system_name, user=request.user)
    return JsonResponse({'structures': names, 'reason': reason})