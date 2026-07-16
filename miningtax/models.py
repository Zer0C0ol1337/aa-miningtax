from django.db import models
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo, EveAllianceInfo


class General(models.Model):
    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = 'general'
        verbose_name_plural = 'general'
        permissions = (
            ('basic_access', 'Can view Mining Tax'),
            ('mining_officer', 'Can access Alliance Billing and Settings (Mining Officer)'),
        )


class OreCategory(models.Model):
    type_id = models.PositiveIntegerField(primary_key=True)
    type_name = models.CharField(max_length=255)
    category = models.CharField(max_length=50)

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
        unique_together = ('character', 'date', 'type_id')

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