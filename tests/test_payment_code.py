"""Payment codes: read as numbers, matched strictly, sender doesn't matter.

Months from 10/2026 use the marked code MT-corp-month-year; earlier
months keep the bare corp/month/year they were released with.
"""
import re
import types
import unicodedata
from decimal import Decimal

import pytest

from tests.helpers import load

NS = load('payments.py', {'CODE_MARKER', 'MARKED_CODE_FROM', '_LEGACY_CODE_RE', '_MARKED_CODE_RE',
                          'uses_marked_code', 'parse_payment_code', 'payment_code_for',
                          '_match_payments_against_journal'},
          {'re': re, 'unicodedata': unicodedata,
           'logger': types.SimpleNamespace(info=lambda m: None, debug=lambda m: None)})


class _Record:
    """An open invoice of Your Ex's."""
    def __init__(self):
        self.corporation = types.SimpleNamespace(corporation_id=98806948, corporation_name="Your Ex's")
        self.total_due = 45141
        self.paid = False

    def save(self, **kwargs):
        pass


def _matches(reason, month=9, year=2026, amount='45141', sender=98806948):
    """True when a single journal entry with this reason pays the invoice of month/year."""
    record = _Record()
    entry = types.SimpleNamespace(reason=reason, first_party_id=sender, amount=Decimal(amount))
    return NS['_match_payments_against_journal']([entry], year, month, [record], 'test') == 1 and record.paid


# ─── Which format a month uses ────────────────────────────────────────────────

@pytest.mark.parametrize('year, month, marked', [
    (2026, 9, False),
    (2026, 10, True),
    (2026, 12, True),
    (2027, 1, True),
    (2025, 12, False),
])
def test_marked_from_october_2026(year, month, marked):
    assert NS['uses_marked_code'](year, month) is marked


def test_code_shown_for_each_format():
    assert NS['payment_code_for'](98806948, 9, 2026) == '98806948/09/2026'
    assert NS['payment_code_for'](98806948, 10, 2026) == 'MT-98806948-10-2026'


def test_marked_code_does_not_contain_the_bare_one():
    # The other tool searches for corp/month/year — the new code must not
    # contain that pattern, not even as a substring.
    code = NS['payment_code_for'](98806948, 10, 2026)
    assert '98806948/10/2026' not in code
    assert '/' not in code


def test_marked_code_stays_short():
    # Kept well within a conservative 40-character budget for EVE's transfer
    # reason field; a ten-digit corp ID is the longest case (21 characters).
    assert len(NS['payment_code_for'](2147483647, 12, 2099)) <= 40


# ─── Bare code (months before 10/2026) ────────────────────────────────────────

@pytest.mark.parametrize('reason', [
    '98806948/09/2026',
    '98806948/9/2026',
    ' 98806948 / 9 / 2026 ',
    '９８８０６９４８／９／２０２６',
])
def test_recognised_bare_spellings(reason):
    assert _matches(reason)


@pytest.mark.parametrize('reason', [
    '98806948/8/2026',
    '98806949/9/2026',
    '98806948/9/2026 thx',
    'x98806948/9/2026',
    'MT-98806948-09-2026',
])
def test_rejected_bare_reasons(reason):
    assert not _matches(reason)


# ─── Marked code (months from 10/2026) ────────────────────────────────────────

@pytest.mark.parametrize('reason', [
    'MT-98806948-10-2026',
    'mt-98806948-10-2026',
    'Mt-98806948-10-2026',
    ' MT - 98806948 - 10 - 2026 ',
    'MT-98806948-10/2026',
    'MT/98806948/10/2026',
    'ＭＴ－９８８０６９４８－１０－２０２６',
])
def test_recognised_marked_spellings(reason):
    assert _matches(reason, month=10)


@pytest.mark.parametrize('reason', [
    '98806948/10/2026',              # the bare code — exactly what the other tool uses
    '98806948-10-2026',              # marker missing
    'MT-98806948-09-2026',           # wrong month
    'MT-98806949-10-2026',           # wrong corp
    'MT-98806948-10-2026 thx',
    'xMT-98806948-10-2026',
    'MTX-98806948-10-2026',
    'MININGTAX-98806948-10-2026',    # longer marker is not the marker
    '98806948-10-2026-MT',
])
def test_rejected_marked_reasons(reason):
    assert not _matches(reason, month=10)


# ─── Sender and amount (unchanged by the marker) ──────────────────────────────

def test_any_sender_is_accepted():
    assert _matches('98806948/09/2026', sender=1)
    assert _matches('MT-98806948-10-2026', month=10, sender=1)


def test_more_than_due_is_accepted():
    assert _matches('98806948/09/2026', amount='3000000000')
    assert _matches('MT-98806948-10-2026', month=10, amount='3000000000')


def test_less_than_due_is_rejected():
    assert not _matches('98806948/09/2026', amount='45140.99')
    assert not _matches('MT-98806948-10-2026', month=10, amount='45140.99')


def test_exact_cents_from_old_pdf_still_cover_a_rounded_down_due():
    assert _matches('98806948/09/2026', amount='45141.67')
