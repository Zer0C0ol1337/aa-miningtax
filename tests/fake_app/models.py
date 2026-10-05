"""The three amount-holding models as they were before migration 0027."""
from django.db import models


class MiningLedgerEntry(models.Model):
    total_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)


class MoonRental(models.Model):
    monthly_fee = models.DecimalField(max_digits=20, decimal_places=2)


class AllianceBillingRecord(models.Model):
    total_mined_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    mining_tax_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    moon_rental_total = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total_due = models.DecimalField(max_digits=20, decimal_places=2, default=0)
