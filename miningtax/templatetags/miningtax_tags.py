from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from django import template

register = template.Library()


def whole_isk(value):
    """
    An amount as a whole number of ISK, rounded down — the same direction the
    stored amounts a corp pays are rounded (see amounts.py), so breakdown lines
    such as the tax per member never add up to more than the total due shown
    below them. Stored amounts are whole ISK already and pass through
    unchanged. Computed with Decimal, never float, which loses exactness on
    large amounts.
    """
    try:
        return int(Decimal(str(value)).to_integral_value(rounding=ROUND_FLOOR))
    except (InvalidOperation, ValueError, TypeError):
        return 0


def format_rate(value):
    """
    A tax rate without trailing zeros: 7.00 → "7", 7.50 → "7.5". Not rounded —
    a rate of 7.5 % has to stay 7.5, or the shown rate wouldn't match the tax.
    """
    try:
        return format(Decimal(str(value)).normalize(), 'f')
    except (InvalidOperation, ValueError, TypeError):
        return '0'


@register.filter
def isk(value):
    """
    An ISK amount in whole ISK with dots between the thousands groups:
    1234567.89 → 1.234.567. Dots read best for the officers using the tool.

    An EVE client that uses the dot as decimal point can misread a typed
    "45.141" as 45.14 ISK — which is why the payment box offers the amount to
    transfer as bare digits with a copy button (isk_plain), so nobody has to
    type it over. Amounts are whole ISK, so a dot here is never a decimal point.
    """
    return f'{whole_isk(value):,}'.replace(',', '.')


@register.filter
def isk_plain(value):
    """
    An ISK amount as bare digits: 45141. For the "amount to transfer" next to
    the payment code — it pastes into EVE's transfer field unchanged in any
    client language, with no separator to misread. The due it is used for is
    stored as whole ISK, so this is exactly the amount on record.
    """
    return str(whole_isk(value))


@register.filter
def rate(value):
    """A tax rate for display, without trailing zeros (see format_rate)."""
    return format_rate(value)