from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import OreCategory, TaxRate, FleetSession, AllianceMoon, MoonRental, AllianceBillingRecord


DEFAULT_TAX_RATE = Decimal('10.00')


def get_ore_category(type_id):
    try:
        return OreCategory.objects.get(type_id=type_id).category
    except OreCategory.DoesNotExist:
        return 'Default'


def get_tax_rate(category):
    try:
        rate_obj = TaxRate.objects.get(ore_category=category)
        return rate_obj.tax_rate
    except TaxRate.DoesNotExist:
        return DEFAULT_TAX_RATE


def is_excluded_by_fleet_session(entry, ore_category):
    entry_datetime = timezone.make_aware(
        timezone.datetime.combine(entry.date, timezone.datetime.min.time())
    )
    matching_sessions = FleetSession.objects.filter(
        exclude_from_billing=True,
        start_time__lte=entry_datetime,
        end_time__gte=entry_datetime,
    )
    for session in matching_sessions:
        if session.ore_type_id and session.ore_type_id == entry.type_id:
            return True
        if session.ore_category and session.ore_category == ore_category:
            return True
        if not session.ore_type_id and not session.ore_category:
            return True
    return False


def is_excluded_by_alliance_moon(entry):
    if not entry.solar_system_name:
        return False
    for moon in AllianceMoon.objects.filter(is_tax_free=True):
        if moon.solar_system_name and moon.solar_system_name.lower() in entry.solar_system_name.lower():
            return True
    return False


def is_excluded_by_moon_rental(entry, corporation):
    if not entry.solar_system_name or not corporation:
        return False
    return MoonRental.objects.filter(
        corporation=corporation,
        active=True,
        structure_name__iexact=entry.solar_system_name
    ).exists()


def calculate_entry_tax(entry, corporation=None):
    category = get_ore_category(entry.type_id)

    excluded = (
        is_excluded_by_fleet_session(entry, category)
        or is_excluded_by_alliance_moon(entry)
        or is_excluded_by_moon_rental(entry, corporation)
    )

    if excluded:
        return {
            'category': category,
            'tax_rate': Decimal('0.00'),
            'tax_amount': Decimal('0.00'),
            'excluded': True,
        }

    tax_rate = get_tax_rate(category)
    tax_amount = entry.total_value * (tax_rate / Decimal('100'))

    return {
        'category': category,
        'tax_rate': tax_rate,
        'tax_amount': tax_amount,
        'excluded': False,
    }


def _get_main_character_name(character):
    """
    Resolves the main character name for a given character via
    CharacterOwnership -> User -> UserProfile.main_character.
    Falls back to the character's own name if it's not registered,
    has no owning user, or no main character is set.
    """
    try:
        ownership = character.character_ownership
        user = ownership.user
        main_char = user.profile.main_character
        if main_char:
            return main_char.character_name
    except Exception:
        pass
    return character.character_name


def calculate_alliance_billing(year, month):
    from .models import MiningLedgerEntry

    entries = MiningLedgerEntry.objects.filter(
        date__year=year, date__month=month
    ).select_related(
        'character',
        'character__character_ownership__user__profile__main_character',
    )

    corps_data = {}
    alliance_totals = {'mined': Decimal('0'), 'tax': Decimal('0')}

    for entry in entries:
        corp = entry.character.corporation_id
        corp_name = entry.character.corporation_name or 'Unknown'

        tax_info = calculate_entry_tax(entry, corporation=_get_corp_info(corp))

        if corp not in corps_data:
            corps_data[corp] = {
                'corp_name': corp_name,
                'total_mined': Decimal('0'),
                'total_tax': Decimal('0'),
                'members': {},
                'categories': {},
            }

        corp_entry = corps_data[corp]
        corp_entry['total_mined'] += entry.total_value
        corp_entry['total_tax'] += tax_info['tax_amount']

        # Group by main character — alts' mining rolls up into their main's total
        member_name = _get_main_character_name(entry.character)
        if member_name not in corp_entry['members']:
            corp_entry['members'][member_name] = {'mined': Decimal('0'), 'tax': Decimal('0')}
        corp_entry['members'][member_name]['mined'] += entry.total_value
        corp_entry['members'][member_name]['tax'] += tax_info['tax_amount']

        cat = tax_info['category']
        if cat not in corp_entry['categories']:
            corp_entry['categories'][cat] = {'value': Decimal('0'), 'tax': Decimal('0'), 'rate': tax_info['tax_rate']}
        corp_entry['categories'][cat]['value'] += entry.total_value
        corp_entry['categories'][cat]['tax'] += tax_info['tax_amount']

        alliance_totals['mined'] += entry.total_value
        alliance_totals['tax'] += tax_info['tax_amount']

    return {'corps': corps_data, 'totals': alliance_totals}


def save_billing_records_for_month(year, month):
    """
    Saves an AllianceBillingRecord for all corps for a given month.
    Called daily after the sync to keep billing records up to date.
    """
    data = calculate_alliance_billing(year, month)
    saved = 0
    for corp_id, corp_data in data['corps'].items():
        record = save_billing_record(corp_id, corp_data, year, month)
        if record:
            saved += 1
    return saved


def save_billing_record(corp_id, corp_data, year, month):
    """
    Creates or updates an AllianceBillingRecord for a corp.
    Only updates existing records that are not yet paid.
    """
    corp_obj = _get_corp_info(corp_id)
    if not corp_obj:
        return None

    rental_total = MoonRental.objects.filter(
        corporation=corp_obj, active=True
    ).aggregate(total=Sum('monthly_fee'))['total'] or Decimal('0')

    total_due = corp_data['total_tax'] + rental_total

    record, created = AllianceBillingRecord.objects.get_or_create(
        corporation=corp_obj,
        month=month,
        year=year,
        defaults={
            'total_mined_value': corp_data['total_mined'],
            'mining_tax_amount': corp_data['total_tax'],
            'moon_rental_total': rental_total,
            'total_due': total_due,
            'category_snapshot': {
                cat: {
                    'value': str(data['value']),
                    'tax': str(data['tax']),
                    'rate': str(data['rate']),
                }
                for cat, data in corp_data['categories'].items()
            },
        }
    )

    if not created and not record.paid:
        record.total_mined_value = corp_data['total_mined']
        record.mining_tax_amount = corp_data['total_tax']
        record.moon_rental_total = rental_total
        record.total_due = total_due
        record.category_snapshot = {
            cat: {
                'value': str(data['value']),
                'tax': str(data['tax']),
                'rate': str(data['rate']),
            }
            for cat, data in corp_data['categories'].items()
        }
        record.save()

    return record


def mark_corp_paid(corp_id, corp_data, year, month):
    """Saves the billing record and marks it as paid."""
    record = save_billing_record(corp_id, corp_data, year, month)
    if record:
        record.paid = True
        record.paid_at = timezone.now()
        record.save(update_fields=['paid', 'paid_at'])
    return record


_corp_cache = {}


def _get_corp_info(corp_id):
    from allianceauth.eveonline.models import EveCorporationInfo
    if corp_id in _corp_cache:
        return _corp_cache[corp_id]
    try:
        corp = EveCorporationInfo.objects.get(corporation_id=corp_id)
    except EveCorporationInfo.DoesNotExist:
        corp = None
    _corp_cache[corp_id] = corp
    return corp