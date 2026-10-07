"""Inside a web request (no_esi) the ESI fallbacks of the tax calculation
stay off, and "unknown" is not cached as a final answer."""
import types

import pytest
from django.core.cache import cache

from tests.helpers import load

GUARD = load('esi_guard.py', {'_esi_blocked', 'no_esi', 'esi_allowed'},
             {'contextmanager': __import__('contextlib').contextmanager,
              'ContextVar': __import__('contextvars').ContextVar})


def test_guard_blocks_only_inside():
    assert GUARD['esi_allowed']()
    with GUARD['no_esi']():
        assert not GUARD['esi_allowed']()
    assert GUARD['esi_allowed']()


def test_guard_is_released_after_an_exception():
    with pytest.raises(ValueError):
        with GUARD['no_esi']():
            raise ValueError
    assert GUARD['esi_allowed']()


@pytest.fixture
def ore():
    """get_ore_category with no local source knowing the type, and an ESI stand-in that records calls."""
    cache.clear()
    esi_calls = []

    def fake_esi(type_id):
        esi_calls.append(type_id)
        return '', ''

    ns = load('billing.py', {'get_ore_category'}, {
        'cache': cache, 'esi_allowed': GUARD['esi_allowed'], 'logger': types.SimpleNamespace(info=lambda m: None),
        '_ore_categories': lambda: {},
        '_type_and_group_from_sde': lambda t: ('', ''),
        '_type_and_group_from_eveuniverse': lambda t: ('', ''),
        '_type_and_group_from_esi': fake_esi,
        'category_from_rules': lambda n, g: None, 'classify_group_name': lambda g: None,
    })
    ns['esi_calls'] = esi_calls
    return ns


def test_unknown_ore_in_a_request_skips_esi_and_caches_nothing(ore):
    with GUARD['no_esi']():
        assert ore['get_ore_category'](99999) == 'Default'
    assert ore['esi_calls'] == []
    assert cache.get('miningtax:unclassifiable:99999') is None


def test_unknown_ore_in_a_task_asks_esi(ore):
    assert ore['get_ore_category'](99999) == 'Default'
    assert ore['esi_calls'] == [99999]
    assert cache.get('miningtax:unclassifiable:99999') is True
