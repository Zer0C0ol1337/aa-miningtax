from django.db import models
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo, EveAllianceInfo


class General(models.Model):
    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = 'general'
        verbose_name_plural = 'general'
        # Three tiers: View, Corp, Admin. The labels lead with the tier name so
        # the permission list reads as a ladder rather than three unrelated
        # entries. Codenames are deliberately left alone — they are what group
        # assignments point at, and renaming them would silently void every
        # existing assignment on a running instance.
        permissions = (
            ('basic_access', 'View — own mining ledger and profile'),
            ('corp_billing', "Corp — billing for own corporation only"),
            ('mining_officer', 'Admin — alliance-wide billing, settings and sync'),
        )


class OreCategory(models.Model):
    type_id = models.PositiveIntegerField(primary_key=True)
    type_name = models.CharField(max_length=255)
    category = models.CharField(max_length=50)
    # Protects a hand-picked category from the daily ore import, which would
    # otherwise reclassify it from the type's group every night. Needed whenever
    # an ore is deliberately filed somewhere other than where EVE's data would
    # put it — e.g. an ore parked in its own category to be taxed at 0%.
    locked = models.BooleanField(
        default=False,
        help_text="Keep this category as set; the ore import won't touch it"
    )

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"{self.type_name} ({self.category})"


class TaxRate(models.Model):
    ore_category = models.CharField(max_length=50, unique=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=10.00)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"{self.ore_category}: {self.tax_rate}%"


class MiningLedgerEntry(models.Model):
    character = models.ForeignKey(EveCharacter, on_delete=models.CASCADE, related_name='mining_entries')
    date = models.DateField()
    solar_system_id = models.BigIntegerField(null=True, blank=True)
    solar_system_name = models.CharField(max_length=255, blank=True)
    type_id = models.PositiveIntegerField()
    type_name = models.CharField(max_length=255, blank=True)
    quantity = models.BigIntegerField()
    price_per_unit = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        # Location is part of the identity: the same ore mined on the same day
        # in a belt and at a moon are two separate facts. Without it the belt
        # entry had nowhere to live and was dropped in favour of the more
        # precise structure entry.
        unique_together = ('character', 'date', 'type_id', 'solar_system_id')

    def __str__(self):
        return f"{self.character.character_name} - {self.type_name} x{self.quantity}"


class AllianceMoon(models.Model):
    MOON_TYPES = [('public', 'Public'), ('event', 'Event')]

    name = models.CharField(max_length=255)
    solar_system_name = models.CharField(max_length=255, blank=True)
    # Exact in-game structure name (refinery / moon drill), matched against a
    # mining observer entry's structure name. Set this when several moon
    # structures share one solar system so that ONLY this structure is exempted
    # when is_tax_free is on -- not every structure in the system. Leave blank to
    # keep the legacy behaviour (substring match on the solar system).
    structure_name = models.CharField(max_length=255, blank=True)
    ore_category = models.CharField(max_length=50, default='R64')
    moon_type = models.CharField(max_length=20, choices=MOON_TYPES, default='public')
    is_tax_free = models.BooleanField(default=False)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"{self.name} ({self.get_moon_type_display()})"


class FleetSession(models.Model):
    name = models.CharField(max_length=255)
    ore_type_id = models.PositiveIntegerField(null=True, blank=True)
    ore_category = models.CharField(max_length=50, blank=True)
    moon = models.ForeignKey(AllianceMoon, on_delete=models.SET_NULL, null=True, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    created_by = models.ForeignKey(EveCharacter, on_delete=models.SET_NULL, null=True, blank=True)
    exclude_from_billing = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return self.name


class MoonRental(models.Model):
    corporation = models.ForeignKey(
        EveCorporationInfo, on_delete=models.CASCADE,
        related_name='miningtax_moon_rentals'
    )
    moon_name = models.CharField(max_length=255)
    structure_name = models.CharField(max_length=255, blank=True)
    monthly_fee = models.DecimalField(max_digits=20, decimal_places=2)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"{self.corporation.corporation_name} - {self.moon_name}"


class AllianceBillingRecord(models.Model):
    corporation = models.ForeignKey(EveCorporationInfo, on_delete=models.CASCADE)
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    total_mined_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    mining_tax_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    moon_rental_total = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total_due = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    category_snapshot = models.JSONField(null=True, blank=True)
    paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    auto_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        unique_together = ('corporation', 'month', 'year')

    def __str__(self):
        return f"{self.corporation.corporation_name} {self.month}/{self.year}"


class TreasuryConfig(models.Model):
    corporation = models.ForeignKey(
        EveCorporationInfo, on_delete=models.CASCADE,
        related_name='miningtax_treasury_configs',
        help_text='Corporation whose wallet is checked for incoming tax payments'
    )
    payment_reason_keyword = models.CharField(
        max_length=255, default='Corp Tax',
        help_text='Legacy field, no longer used for matching (kept for backward compatibility)'
    )
    wallet_division = models.PositiveSmallIntegerField(
        default=1,
        help_text='Wallet division to check (1-7, default: 1 = master wallet)'
    )
    active = models.BooleanField(default=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"Treasury: {self.corporation.corporation_name}"


# Designates a corporation whose EVE sovereignty systems define WHERE mining
# is taxable. When active, only mining reported from a system currently
# held (per ESI's public sovereignty map) by this corporation counts toward
# tax -- mining anywhere else is excluded (0% tax), same mechanism as
# tax-free moons. The actual system list (SovSystem below) is refreshed
# automatically by the daily sync, not maintained by hand.
class SovFilterConfig(models.Model):
    corporation = models.ForeignKey(
        EveCorporationInfo, on_delete=models.CASCADE,
        related_name='miningtax_sov_filter_configs',
        help_text="Only mining within this corporation's current sovereignty systems is taxed"
    )
    active = models.BooleanField(default=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"Sov Filter: {self.corporation.corporation_name}"


# Cache of systems currently held by a SovFilterConfig's corporation,
# refreshed from ESI's public /sovereignty/map/ endpoint. Rebuilt fully on
# each sync so it always reflects current sovereignty, never goes stale
# from manual upkeep.
class SovSystem(models.Model):
    system_id = models.BigIntegerField(primary_key=True)
    system_name = models.CharField(max_length=255)
    corporation_id = models.BigIntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"{self.system_name} ({self.corporation_id})"


# Singleton config for the Janice pricing integration. When enabled and an API
# key is set, ore is valued by its reprocessed mineral value (Janice Jita split
# price) instead of the raw ESI adjusted_price — far harder to manipulate,
# especially for moon ore where the raw ore price sits well below mineral value.
# Falls back to ESI automatically when disabled or when Janice is unreachable.
class JaniceConfig(models.Model):
    enabled = models.BooleanField(
        default=False,
        help_text='Use Janice refined value for ore pricing. When off, ESI adjusted_price is used.'
    )
    api_key = models.CharField(
        max_length=255, blank=True,
        help_text='Janice API key (request one via their Discord). Stored server-side, never shown to members.'
    )

    class Meta:
        default_permissions = ()
        verbose_name = 'Janice configuration'

    def __str__(self):
        return f"Janice ({'enabled' if self.enabled else 'disabled'})"

    @classmethod
    def get_solo(cls):
        # Always returns the single config row, creating it on first access so
        # callers never have to handle DoesNotExist.
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# Exempts either a single character OR an entire corporation from mining tax.
# When an active exemption matches, that mining is excluded from billing the
# same way tax-free moons are (0% tax), regardless of ore category, fleet
# sessions or moon configuration. Checked first so an exemption always wins.
class TaxExemption(models.Model):
    character = models.ForeignKey(
        EveCharacter, on_delete=models.CASCADE, null=True, blank=True,
        related_name='miningtax_exemptions',
        help_text='Exempt a single character (leave blank when exempting a whole corporation)'
    )
    corporation = models.ForeignKey(
        EveCorporationInfo, on_delete=models.CASCADE, null=True, blank=True,
        related_name='miningtax_exemptions',
        help_text='Exempt an entire corporation (leave blank when exempting a single character)'
    )
    reason = models.CharField(
        max_length=255, blank=True,
        help_text='Optional note why this character/corp is exempt'
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        if self.corporation:
            return f"Corp exempt: {self.corporation.corporation_name}"
        if self.character:
            return f"Character exempt: {self.character.character_name}"
        return 'Incomplete exemption'


# Assigns ore to a category by name, ahead of the automatic classification.
#
# EVE's own grouping is the right default, but it doesn't always match how an
# alliance wants to tax things: abyssal ore and Prismaticite sit in ordinary
# asteroid groups, yet warrant their own rate. Rules keep that decision out of
# the code — a new ore matching an existing rule is categorised the moment it
# first appears, without anyone editing anything.
class OreCategoryRule(models.Model):
    MATCH_FIELDS = [
        ('type_name', 'Ore name'),
        ('group_name', 'EVE group name'),
    ]

    match_field = models.CharField(
        max_length=20, choices=MATCH_FIELDS, default='type_name',
        help_text='Whether to match against the ore name or its EVE group'
    )
    contains = models.CharField(
        max_length=255,
        help_text='Case-insensitive substring, e.g. "prismaticite" or "bezdnacine"'
    )
    category = models.CharField(
        max_length=50,
        help_text='Category to assign. Add a matching tax rate for it, '
                  'otherwise the Default rate applies'
    )
    priority = models.PositiveSmallIntegerField(
        default=100,
        help_text='Lower runs first. Use it to let a specific rule beat a broader one'
    )
    active = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        default_permissions = ()
        ordering = ('priority', 'contains')
        verbose_name = 'ore category rule'

    def __str__(self):
        return f'{self.get_match_field_display()} contains "{self.contains}" -> {self.category}'