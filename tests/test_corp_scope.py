"""Corp scope: a corp_billing holder sees only their own corporation — and
nothing at all when that corporation can't be resolved (fail closed)."""
import types

import pytest

from tests.helpers import load

NS = load('views.py', {'is_corp_scoped', 'own_corporation_id', 'corp_scope_for', 'outside_corp_scope'})


class _Ownerships:
    """Stand-in for user.character_ownerships (select_related().all())."""
    def __init__(self, corp_ids):
        self._items = [types.SimpleNamespace(character=types.SimpleNamespace(corporation_id=c)) for c in corp_ids]

    def select_related(self, *args):
        return self

    def all(self):
        return self._items


def _user(perms=(), superuser=False, main_corp=None, other_corps=()):
    """A user with the given permissions, main character corporation and further characters."""
    main = types.SimpleNamespace(corporation_id=main_corp) if main_corp else None
    return types.SimpleNamespace(
        is_superuser=superuser,
        has_perm=lambda p: p in perms,
        profile=types.SimpleNamespace(main_character=main),
        character_ownerships=_Ownerships(other_corps),
    )


CORP = 'miningtax.corp_billing'
OFFICER = 'miningtax.mining_officer'


def test_officer_is_not_scoped():
    assert NS['corp_scope_for'](_user(perms={OFFICER, CORP})) == (False, None)
    assert not NS['outside_corp_scope'](_user(perms={OFFICER}), 123)


def test_superuser_is_not_scoped():
    assert NS['corp_scope_for'](_user(superuser=True)) == (False, None)


def test_corp_holder_is_limited_to_main_corp():
    user = _user(perms={CORP}, main_corp=111)
    assert NS['corp_scope_for'](user) == (True, 111)
    assert not NS['outside_corp_scope'](user, 111)
    assert NS['outside_corp_scope'](user, 222)


def test_falls_back_to_any_registered_character():
    user = _user(perms={CORP}, other_corps=(333,))
    assert NS['corp_scope_for'](user) == (True, 333)
    assert not NS['outside_corp_scope'](user, 333)


@pytest.mark.parametrize('corporation_id', [111, 222, None])
def test_unresolvable_corp_fails_closed(corporation_id):
    # The old checks read "no corporation" as "no limit" and showed everything.
    user = _user(perms={CORP})
    assert NS['corp_scope_for'](user) == (True, None)
    assert NS['outside_corp_scope'](user, corporation_id)
