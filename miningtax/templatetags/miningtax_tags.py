import math
from django import template

register = template.Library()


@register.filter
def isk(value):
    """
    Formatiert einen ISK-Betrag europäisch mit Tausenderpunkten, aufgerundet, ohne Nachkommastellen.
    Beispiel: 1234567.89 → 1.234.568
    """
    try:
        rounded = math.ceil(float(value))
        return f'{rounded:,}'.replace(',', '.')
    except (ValueError, TypeError):
        return '0'
