"""Payment codes: read as numbers, matched strictly, sender doesn't matter."""
import re
import types
import unicodedata
from decimal import Decimal

import pytest

from tests.helpers import load

NS = load('payments.py', {'_PAYMENT_CODE_RE', 'parse_payment_code', 'payment_code_for',
                          '_match_payments_against_journal'},
          {'re': re, 'unicodedata': unicodedata,
           'logger': types.SimpleNamespace(info=lambda m: None, debug=lambda m: None)})


class _Record:
    """An open invoice of Your Ex's for 09/2026."""
    def __init__(self):
        self.corporation = types.SimpleNamespace(corporation_id=98806948, corporation_name="Your Ex's")
        self.total_due = 45141
        self.paid = False

    def save(self, **kwargs):
        pass


def _matches(reason, amount='45141', sender=98806948):
    record = _Record()
    entry = types.SimpleNamespace(reason=reason, first_party_id=sender, amount=Decimal(amount))
    return NS['_match_payments_against_journal']([entry], 2026, 9, [record], 'test') == 1 and record.paid


@pytest.mark.parametrize('reason', [
    '98806948/09/2026',
    '98806948/9/2026',
    ' 98806948 / 9 / 2026 ',
    '９８８０６９４８／９／２０２６',
])
def test_recognised_spellings(reason):
    assert _matches(reason)


@pytest.mark.parametrize('reason', [
    '98806948/8/2026',
    '98806949/9/2026',
    '98806948/9/2026 thx',
    'x98806948/9/2026',
])
def test_rejected_reasons(reason):
    assert not _matches(reason)


def test_any_sender_is_accepted():
    assert _matches('98806948/09/2026', sender=1)


def test_more_than_due_is_accepted():
    assert _matches('98806948/09/2026', amount='3000000000')


def test_less_than_due_is_rejected():
    assert not _matches('98806948/09/2026', amount='45140.99')


def test_exact_cents_from_old_pdf_still_cover_a_rounded_down_due():
    assert _matches('98806948/09/2026', amount='45141.67')
