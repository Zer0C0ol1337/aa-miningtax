"""Whole-ISK rounding of stored amounts."""
from decimal import Decimal

from miningtax.amounts import whole_isk_down, whole_isk_nearest


def test_payable_amounts_round_down():
    assert whole_isk_down(Decimal('45141.67')) == 45141
    assert whole_isk_down(Decimal('45141.00')) == 45141


def test_other_values_round_to_nearest():
    assert whole_isk_nearest(Decimal('1234.50')) == 1235
    assert whole_isk_nearest(Decimal('1234.49')) == 1234


def test_large_amounts_stay_exact():
    # A float would turn this into ...992 — Decimal keeps it exact.
    assert whole_isk_down(Decimal('9007199254740993.5')) == 9007199254740993


def test_empty_values_are_zero():
    assert whole_isk_down(None) == 0
    assert whole_isk_nearest(None) == 0
