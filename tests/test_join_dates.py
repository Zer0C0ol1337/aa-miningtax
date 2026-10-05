"""Alliance join dates: fetched from ESI once per corp, then read from storage."""
import datetime
import sys
import types

import pytest
from django.core.cache import cache

from tests.helpers import load

UTC = datetime.timezone.utc


@pytest.fixture
def world(monkeypatch):
    """ESI, Alliance Auth and the CorpAllianceJoin table as in-memory stand-ins."""
    calls, store = [], {}
    history = {1: [types.SimpleNamespace(alliance_id=99, record_id=10, start_date=datetime.datetime(2018, 3, 8, tzinfo=UTC))]}
    aa_alliance = {1: 99}

    class Op:
        def __init__(self, cid):
            self.cid = cid

        def results(self, force_refresh=False):
            calls.append(self.cid)
            return history.get(self.cid, [])

    esi = types.SimpleNamespace(client=types.SimpleNamespace(Corporation=types.SimpleNamespace(
        GetCorporationsCorporationIdAlliancehistory=lambda corporation_id: Op(corporation_id))))

    class Rows(list):
        def first(self):
            return self[0] if self else None

        def values_list(self, field, flat=False):
            return Rows(getattr(r, field) for r in self)

    class JoinManager:
        def filter(self, corporation_id=None, corporation_id__in=None):
            if corporation_id__in is not None:
                return Rows(store[c] for c in corporation_id__in if c in store)
            return Rows([store[corporation_id]] if corporation_id in store else [])

        def update_or_create(self, corporation_id, defaults):
            store[corporation_id] = types.SimpleNamespace(corporation_id=corporation_id, **defaults)
            return store[corporation_id], True

    class CorpManager:
        def filter(self, corporation_id):
            class Query:
                def select_related(self, *args):
                    return self

                def first(self):
                    alliance = aa_alliance.get(corporation_id)
                    return types.SimpleNamespace(alliance_id=1 if alliance else None,
                                                 alliance=types.SimpleNamespace(alliance_id=alliance) if alliance else None)
            return Query()

    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)

    module('miningtax.services', _get_esi_client=lambda: esi)
    module('miningtax.models', CorpAllianceJoin=types.SimpleNamespace(objects=JoinManager()))
    module('esi')
    module('esi.exceptions', HTTPNotModified=type('HTTPNotModified', (Exception,), {}))
    module('allianceauth')
    module('allianceauth.eveonline')
    module('allianceauth.eveonline.models', EveCorporationInfo=types.SimpleNamespace(objects=CorpManager()))
    cache.clear()
    ns = load('billing.py', {'CORP_JOIN_DATE_CACHE_TTL', 'CORP_JOIN_DATE_RETRY_TTL', '_corp_join_cache_key',
                             '_corp_join_failed_key', '_stored_join_is_current', '_fetch_corp_join_date_from_esi',
                             'get_corp_join_date', 'stored_corp_join_date', 'refresh_corp_join_dates'},
              {'__name__': 'miningtax.billing', '__package__': 'miningtax', 'cache': cache,
               'logger': types.SimpleNamespace(warning=lambda m: None),
               'AllianceBillingRecord': types.SimpleNamespace(objects=types.SimpleNamespace(
                   values_list=lambda *a, **k: [1]))})
    return types.SimpleNamespace(ns=ns, calls=calls, history=history, aa_alliance=aa_alliance)


def test_first_night_fetches_then_stores(world):
    assert world.ns['refresh_corp_join_dates']() == 1
    assert world.ns['stored_corp_join_date'](1) == datetime.date(2018, 3, 8)


def test_second_night_costs_no_esi_call(world):
    world.ns['refresh_corp_join_dates']()
    world.calls.clear()
    cache.clear()
    assert world.ns['refresh_corp_join_dates']() == 0
    assert world.calls == []


def test_alliance_switch_triggers_a_new_lookup(world):
    world.ns['refresh_corp_join_dates']()
    world.aa_alliance[1] = 77
    world.history[1] = [types.SimpleNamespace(alliance_id=77, record_id=30, start_date=datetime.datetime(2026, 10, 1, tzinfo=UTC))]
    cache.clear()
    assert world.ns['refresh_corp_join_dates']() == 1
    assert world.ns['stored_corp_join_date'](1) == datetime.date(2026, 10, 1)
