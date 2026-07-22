from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import (
    OreCategory, TaxRate, FleetSession, AllianceMoon, MoonRental,
    AllianceBillingRecord, TaxExemption,
)
from .services import STRUCTURE_ID_THRESHOLD


# Hardcoded fallback used only if no "Default" TaxRate row exists in the DB
# at all (e.g. right after install, before populate_ore_categories creates
# one). Once a "Default" row exists, get_tax_rate() falls back to it and
# it's fully editable by officers in the Settings UI — no code change
# needed to adjust the rate for unrecognized ore categories.
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
        try:
            return TaxRate.objects.get(ore_category='Default').tax_rate
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
    # A tax-free alliance moon exempts ONLY ore mined at that moon's structure,
    # not everything in the whole solar system. Moon mining always comes from a
    # corp mining observer, so such entries carry the structure ID in
    # solar_system_id (> STRUCTURE_ID_THRESHOLD) and the structure name in
    # solar_system_name. Belt and anomaly mining (incl. Mercoxit) comes from the
    # personal ledger with a real system id below the threshold — so gating on
    # the threshold guarantees belts/anomalies are never wrongly exempted, even
    # if they share a system with a tax-free moon.
    if not entry.solar_system_name:
        return False
    if not entry.solar_system_id or entry.solar_system_id <= STRUCTURE_ID_THRESHOLD:
        return False

    entry_structure = entry.solar_system_name.strip().lower()
    for moon in AllianceMoon.objects.filter(is_tax_free=True):
        if moon.structure_name:
            # Precise per-structure match — required when several moon structures
            # share one system, so only the named structure is exempted.
            if moon.structure_name.strip().lower() == entry_structure:
                return True
        elif moon.solar_system_name:
            # Backward-compatible fallback for moons configured before the
            # structure_name field existed (substring match on the system field).
            if moon.solar_system_name.lower() in entry_structure:
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


def _get_main_character(character):
    """
    The main character behind a given character, via
    CharacterOwnership -> User -> UserProfile.main_character.
    Returns None if the character isn't registered in Auth, has no owning
    user, or that user never set a main.
    """
    try:
        return character.character_ownership.user.profile.main_character
    except Exception:
        return None


def is_tax_exempt(entry):
    # Exemptions are granted per MAIN character (or per corporation), never per
    # alt: exempting a main automatically covers every alt that main owns in
    # Auth, so an officer doesn't have to tick 50 alts by hand. The direct
    # character check stays as a fallback for pilots whose main can't be
    # resolved (not registered in Auth, or no main set on the profile).
    # Evaluated before every other exclusion, so an exemption always wins over
    # ore category, fleet sessions and moon configuration.
    character = entry.character

    if TaxExemption.objects.filter(active=True, character=character).exists():
        return True

    main = _get_main_character(character)
    if main and main.pk != character.pk:
        if TaxExemption.objects.filter(active=True, character=main).exists():
            return True

    corp_id = character.corporation_id
    if corp_id and TaxExemption.objects.filter(
        active=True, corporation__corporation_id=corp_id
    ).exists():
        return True

    return False


def calculate_entry_tax(entry, corporation=None):
    category = get_ore_category(entry.type_id)

    # Gas cloud materials (Cytoserocin, Mykoserocin, Fullerite, Tricarboxyl
    # Vapor) aren't in the static OreCategory table with verified type_ids,
    # so they're recognized here by name instead — safer than guessing
    # type_ids, and adapts automatically to whatever ESI reports.
    if category == 'Default' and entry.type_name:
        name_lower = entry.type_name.lower()
        if any(k in name_lower for k in ('cytoserocin', 'mykoserocin', 'fullerite', 'tricarboxyl')):
            category = 'Gas'

    excluded = (
        is_tax_exempt(entry)
        or is_excluded_by_fleet_session(entry, category)
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