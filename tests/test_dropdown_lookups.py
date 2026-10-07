"""Settings dropdowns: the JSON endpoints never call ESI in the request.

Local data first; when it has nothing, a Celery task is queued and the
endpoint answers {"pending": true} until the task's result is in the cache.
"""
import json
import sys
import types

import pytest
from django.core.cache import cache
from django.http import JsonResponse

from tests.helpers import load

NAMES = {'FAILED_LOOKUP_TIMEOUT', 'STRUCTURES_CACHE_TIMEOUT', 'moons_cache_key', 'moons_failed_key',
         'system_structures_key', 'structure_search_key', 'local_moons_for_system',
         'api_moons_for_system', 'api_structures_for_system'}


class _Task:
    """Stand-in for a Celery task: records .delay() calls instead of running."""
    def __init__(self):
        self.calls = []

    def delay(self, *args):
        self.calls.append(args)


@pytest.fixture
def api(monkeypatch):
    """api_views functions with stand-ins for the plugin's views and tasks modules."""
    cache.clear()
    tasks = types.SimpleNamespace(load_system_moons_task=_Task(), search_system_structures_task=_Task())
    views = types.SimpleNamespace(has_full_officer_access=lambda user: True)
    monkeypatch.setitem(sys.modules, 'miningtax', types.ModuleType('miningtax'))
    monkeypatch.setitem(sys.modules, 'miningtax.tasks', tasks)
    monkeypatch.setitem(sys.modules, 'miningtax.views', views)
    ns = load('api_views.py', NAMES, {
        '__package__': 'miningtax', '__name__': 'miningtax.api_views',
        'cache': cache, 'JsonResponse': JsonResponse,
        '_local_structure_names': lambda name: set(),
        '_search_token_for': lambda user: object(),
    })
    ns['tasks'] = tasks
    return ns


def _request(**params):
    """A GET request by an authenticated officer."""
    user = types.SimpleNamespace(is_authenticated=True, pk=7)
    return types.SimpleNamespace(user=user, GET=params)


def _json(response):
    return json.loads(response.content)


# ─── Moons ────────────────────────────────────────────────────────────────────

def test_moons_unknown_locally_queue_a_task(api):
    data = _json(api['api_moons_for_system'](_request(system_id='30004478')))
    assert data == {'moons': [], 'pending': True}
    assert api['tasks'].load_system_moons_task.calls == [(30004478,)]


def test_moons_from_a_finished_task_come_from_the_cache(api):
    cache.set(api['moons_cache_key'](30004478), [{'id': 1, 'name': 'Moon 1'}])
    data = _json(api['api_moons_for_system'](_request(system_id='30004478')))
    assert data == {'moons': [{'id': 1, 'name': 'Moon 1'}]}
    assert api['tasks'].load_system_moons_task.calls == []


def test_a_system_without_moons_is_not_pending(api):
    cache.set(api['moons_cache_key'](30004478), [])
    assert _json(api['api_moons_for_system'](_request(system_id='30004478'))) == {'moons': []}


def test_a_failed_moon_lookup_stops_the_waiting(api):
    cache.set(api['moons_failed_key'](30004478), True)
    data = _json(api['api_moons_for_system'](_request(system_id='30004478')))
    assert data == {'moons': [], 'reason': 'esi_failed'}
    assert api['tasks'].load_system_moons_task.calls == []


# ─── Structures ───────────────────────────────────────────────────────────────

def test_local_structures_answer_at_once(api):
    api['_local_structure_names'] = lambda name: {'P9F-ZG - Refinery'}
    data = _json(api['api_structures_for_system'](_request(system='P9F-ZG')))
    assert data == {'structures': ['P9F-ZG - Refinery'], 'reason': None}
    assert api['tasks'].search_system_structures_task.calls == []
    assert cache.get(api['system_structures_key']('P9F-ZG')) == ['P9F-ZG - Refinery']


def test_unknown_structures_queue_the_officers_search(api):
    data = _json(api['api_structures_for_system'](_request(system='P9F-ZG')))
    assert data == {'structures': [], 'reason': None, 'pending': True}
    assert api['tasks'].search_system_structures_task.calls == [('P9F-ZG', 7)]


def test_a_finished_search_comes_from_the_cache(api):
    cache.set(api['structure_search_key'](7, 'P9F-ZG'), {'names': ['P9F-ZG - Athanor'], 'reason': None})
    data = _json(api['api_structures_for_system'](_request(system='P9F-ZG')))
    assert data == {'structures': ['P9F-ZG - Athanor'], 'reason': None}
    assert api['tasks'].search_system_structures_task.calls == []


def test_without_a_search_token_nothing_is_queued(api):
    api['_search_token_for'] = lambda user: None
    data = _json(api['api_structures_for_system'](_request(system='P9F-ZG')))
    assert data == {'structures': [], 'reason': 'no_search_token'}
    assert api['tasks'].search_system_structures_task.calls == []
