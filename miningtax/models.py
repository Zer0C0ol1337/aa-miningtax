from django.db import models
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo, EveAllianceInfo


# Erz-Kategorie pro EVE type_id (z.B. type_id 45498 -> "R16")
class OreCategory(models.Model):
    type_id = models.PositiveIntegerField(primary_key=True)
    type_name = models.CharField(max_length=255)
    category = models.CharField(max_length=50)  # z.B. R64, R32, R16, R8, R4, Ice, Ore

    def __str__(self):
        return f"{self.type_name} ({self.category})"


# Steuersatz pro Kategorie, im Admin-Panel editierbar
class TaxRate(models.Model):
    ore_category = models.CharField(max_length=50, unique=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=10.00)
    description = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.ore_category}: {self.tax_rate}%"


# Ein einzelner Mining-Ledger-Eintrag, direkt mit dem EveCharacter aus Alliance Auth verknüpft
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
        # Verhindert doppelte Einträge beim wiederholten Sync (entspricht unserem alten UNIQUE constraint)
        unique_together = ('character', 'date', 'type_id', 'solar_system_id')

    def __str__(self):
        return f"{self.character.character_name} - {self.type_name} x{self.quantity}"


# Alliance-Mond — entweder "public" (normal besteuert) oder "event" (steuerfrei für Mining-Officer-Events)
class AllianceMoon(models.Model):
    MOON_TYPES = [('public', 'Public'), ('event', 'Event')]

    name = models.CharField(max_length=255)
    solar_system_name = models.CharField(max_length=255, blank=True)
    ore_category = models.CharField(max_length=50, default='R64')
    moon_type = models.CharField(max_length=20, choices=MOON_TYPES, default='public')
    is_tax_free = models.BooleanField(default=False)  # True nur bei moon_type == 'event'

    def __str__(self):
        return f"{self.name} ({self.get_moon_type_display()})"


# Fleet-Session: schließt ein Erz/Kategorie in einem Zeitraum von der Abrechnung aus
class FleetSession(models.Model):
    name = models.CharField(max_length=255)
    ore_type_id = models.PositiveIntegerField(null=True, blank=True)
    ore_category = models.CharField(max_length=50, blank=True)  # alternative zu ore_type_id: ganze Kategorie ausschließen
    moon = models.ForeignKey(AllianceMoon, on_delete=models.SET_NULL, null=True, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    created_by = models.ForeignKey(EveCharacter, on_delete=models.SET_NULL, null=True, blank=True)
    exclude_from_billing = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


# Mond-Mietvertrag einer Corp — feste monatliche Gebühr, Mining auf der zugehörigen Struktur wird steuerfrei
class MoonRental(models.Model):
    corporation = models.ForeignKey(EveCorporationInfo, on_delete=models.CASCADE)
    moon_name = models.CharField(max_length=255)
    structure_name = models.CharField(max_length=255, blank=True)  # exakter solar_system_name Match im Ledger
    monthly_fee = models.DecimalField(max_digits=20, decimal_places=2)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.corporation.corporation_name} - {self.moon_name}"


# Gespeicherte Monats-Abrechnung pro Corp — entspricht unserer alten alliance_billing Tabelle
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('corporation', 'month', 'year')

    def __str__(self):
        return f"{self.corporation.corporation_name} {self.month}/{self.year}"
