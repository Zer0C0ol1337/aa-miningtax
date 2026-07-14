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
            ('mining_officer', 'Can manage Mining Tax'),
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
    corporation = models.ForeignKey(EveCorporationInfo, on_delete=models.CASCADE)
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
    # True wenn der Zahlungseingang automatisch über die Wallet-Journal-Prüfung
    # erkannt wurde statt manuell per Button bestätigt
    auto_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        unique_together = ('corporation', 'month', 'year')

    def __str__(self):
        return f"{self.corporation.corporation_name} {self.month}/{self.year}"


# Konfiguration der Treasury-Corp deren Wallet auf Steuerzahlungen geprüft wird.
# Es sollte immer nur einen aktiven Eintrag geben.
class TreasuryConfig(models.Model):
    corporation = models.ForeignKey(
        EveCorporationInfo, on_delete=models.CASCADE,
        help_text='Corp deren Wallet auf eingehende Steuerzahlungen geprüft wird'
    )
    payment_reason_keyword = models.CharField(
        max_length=255, default='Corp Steuer',
        help_text='Text der im Wallet-Journal-Grund enthalten sein muss (z.B. "Corp Steuer")'
    )
    wallet_division = models.PositiveSmallIntegerField(
        default=1,
        help_text='Wallet-Division die geprüft wird (1-7, Standard: 1 = Master Wallet)'
    )
    active = models.BooleanField(default=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return f"Treasury: {self.corporation.corporation_name}"