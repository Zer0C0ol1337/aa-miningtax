"""
Whole-ISK rounding for stored amounts.

Every ISK amount the plugin stores is a whole number (BigIntegerField, since
0.10.25). Unit prices and tax rates are the only values kept with decimals —
a unit price of 13.5 ISK rounded to 14 would overvalue every unit by almost
4 %, and a rate of 7.5 % has to stay 7.5.

Kept in a module of its own because services.py and billing.py both need it
and must not import each other at module level.
"""
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal


def whole_isk_down(value):
    """
    An amount a corp has to pay, as whole ISK, rounded DOWN. Down, so a stored
    due is never more than the exact amount: any transfer that covered the
    exact figure — cents included — still covers the stored one, and the
    payment check keeps recognising it.
    """
    return int(Decimal(str(value or 0)).to_integral_value(rounding=ROUND_FLOOR))


def whole_isk_nearest(value):
    """
    A value that isn't paid as such — a mining entry's worth, a mined total, a
    configured fee — as whole ISK, rounded half up. Nearest rather than up or
    down, so thousands of entries don't drift the totals in one direction.
    """
    return int(Decimal(str(value or 0)).to_integral_value(rounding=ROUND_HALF_UP))