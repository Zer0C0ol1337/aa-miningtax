"""
Small JSON endpoints used by the Settings UI to fill dependent dropdowns.

Kept in its own module so views.py stays focused on page rendering. Nothing
here renders a template — these are called by JS from settings.html only.
"""
import logging

from django.core.cache import cache
from django.http import JsonResponse

from esi.exceptions import HTTPNotModified
from esi.models import Token

from .services import _get_esi_client

logger = logging.getLogger(__name__)

# Moons never move, so their names can be cached aggressively. This turns the
# (potentially slow) first lookup of a system into a one-off cost per system.
MOON_CACHE_TIMEOUT = 60 * 60 * 24 * 30  # 30 days


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


def get_moons_for_system(system_id):
    """
    All moons of a solar system as [{'id': ..., 'name': ...}], sorted by name.
    Cached, since this walks every planet of the system.
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
        systems = _fetch()
    except HTTPNotModified:
        # 304 means ESI has this system cached against a stored ETag but our own
        # result cache above already missed — so the data isn't in hand. Discard
        # the ETag once and refetch. Systems never change, so this happens at
        # most once per system, then our 30-day cache covers it.
        try:
            systems = _fetch(force=True)
        except Exception as e:
            logger.warning(f'Could not load system {system_id} from ESI after refetch: {e}')
            return []
    except Exception as e:
        logger.warning(f'Could not load system {system_id} from ESI: {e}')
        return []

    if not systems:
        return []

    moon_ids = []
    for planet in (getattr(systems[0], 'planets', None) or []):
        moon_ids.extend(getattr(planet, 'moons', None) or [])

    if not moon_ids:
        cache.set(cache_key, [], MOON_CACHE_TIMEOUT)
        return []

    names = _resolve_moon_names(esi, moon_ids)
    moons = sorted(
        ({'id': mid, 'name': names.get(mid, f'Moon {mid}')} for mid in moon_ids),
        key=lambda m: m['name']
    )

    cache.set(cache_key, moons, MOON_CACHE_TIMEOUT)
    return moons


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


# Scope for reading a corporation's mining observers — every observer_id is a
# structure ID, which is exactly what the structure picker needs, and it's the
# same scope the corp observer sync already relies on, so no new authorization
# is required. ESI still gates this behind an in-game role (Accountant or
# Director) on top of the scope, so a token from a role-less member returns 403;
# that case is reported to the UI rather than silently yielding nothing.
CORP_MINING_SCOPE = 'esi-industry.read_corporation_mining.v1'
STRUCTURES_CACHE_TIMEOUT = 60 * 60 * 6  # 6 hours — structures change rarely


def _corp_mining_tokens(corporation_id):
    """
    Every valid corp-mining token belonging to a character in the given corp.

    Returns a list rather than a single token on purpose: the scope alone is not
    enough, ESI additionally requires an in-game role, and nothing in Auth
    records who holds one. Picking just the first token would succeed or fail
    depending on which member happened to authorize first, so callers try them
    in turn until ESI accepts one.
    """
    from allianceauth.eveonline.models import EveCharacter

    char_ids = EveCharacter.objects.filter(
        corporation_id=corporation_id
    ).values_list('character_id', flat=True)
    if not char_ids:
        return []

    return list(
        Token.objects
        .filter(character_id__in=list(char_ids))
        .require_scopes(CORP_MINING_SCOPE)
        .require_valid()
    )


def get_structures_for_corp(corporation_id):
    """
    Names of the corporation's mining structures, sorted, cached. Sourced from
    the mining-observer list (every observer_id is a structure ID), so it
    covers all moon drills the corp owns — not just ones mined recently, and
    without needing director access. Returns [] on any failure so the caller
    can fall back to ledger-observed names.
    """
    cache_key = f'miningtax:corp_structures:{corporation_id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, None

    tokens = _corp_mining_tokens(corporation_id)
    if not tokens:
        # Distinguish "nobody authorized the scope" from "ESI gave nothing" —
        # otherwise an empty dropdown has no discernible cause without shell
        # access on the server.
        logger.info(
            f'No valid {CORP_MINING_SCOPE} token for corp {corporation_id}; '
            'a member of that corp must authorize the corp mining scope'
        )
        return [], 'no_token'

    esi = _get_esi_client()

    def _fetch(token, force=False):
        return esi.client.Industry.GetCorporationCorporationIdMiningObservers(
            corporation_id=corporation_id, token=token
        ).results(force_refresh=force)

    # ESI gates corp mining data behind an in-game role (Accountant or Director)
    # on top of the scope, and Auth has no record of who holds one — so each
    # token is tried until one is accepted. A 403 only rules out that character,
    # not the corp.
    observers = None
    last_error = None
    for token in tokens:
        try:
            try:
                observers = _fetch(token)
            except HTTPNotModified:
                observers = _fetch(token, force=True)
            break
        except Exception as e:
            last_error = e
            if getattr(e, 'status_code', None) == 403:
                logger.debug(
                    f'Corp {corporation_id}: character {token.character_id} lacks the '
                    f'in-game role for corp mining data, trying next token'
                )
                continue
            logger.warning(f'Could not load mining observers for corp {corporation_id}: {e}')
            return [], 'esi_error'

    if observers is None:
        if getattr(last_error, 'status_code', None) == 403:
            logger.info(
                f'Corp {corporation_id}: none of the {len(tokens)} corp-mining token(s) '
                f'belong to a character with the required in-game role'
            )
            return [], 'no_role'
        logger.warning(f'Could not load mining observers for corp {corporation_id}: {last_error}')
        return [], 'esi_error' 

    # Each observer_id is a structure ID. Prefer a name already stored in the
    # ledger (resolved during a previous sync), then fall back to a bulk
    # universe/names lookup for any not yet seen.
    from .models import MiningLedgerEntry

    observer_ids = [
        getattr(o, 'observer_id', None) for o in (observers or [])
        if getattr(o, 'observer_id', None)
    ]
    if not observer_ids:
        cache.set(cache_key, [], STRUCTURES_CACHE_TIMEOUT)
        return [], 'no_observers' 

    known = dict(
        MiningLedgerEntry.objects
        .filter(solar_system_id__in=observer_ids)
        .exclude(solar_system_name='')
        .values_list('solar_system_id', 'solar_system_name')
    )
    missing = [oid for oid in observer_ids if oid not in known]
    resolved = _resolve_moon_names(esi, missing) if missing else {}

    names = sorted({
        known.get(oid) or resolved.get(oid, f'Structure {oid}')
        for oid in observer_ids
    })

    cache.set(cache_key, names, STRUCTURES_CACHE_TIMEOUT)
    return names, None


def api_structures_for_corp(request):
    """
    GET /miningtax/api/structures/?corporation_id=98399796
    Returns {"structures": ["P9F-ZG - Foo", ...]}

    Officer-only, same gate as the Settings page that consumes it.
    """
    from .views import has_full_officer_access

    if not request.user.is_authenticated or not has_full_officer_access(request.user):
        return JsonResponse({'error': 'forbidden'}, status=403)

    corp_id = request.GET.get('corporation_id')
    if not corp_id or not corp_id.isdigit():
        return JsonResponse({'structures': [], 'reason': None})

    names, reason = get_structures_for_corp(int(corp_id))
    return JsonResponse({'structures': names, 'reason': reason})