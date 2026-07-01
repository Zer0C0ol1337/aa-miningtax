from django.contrib import admin
from .models import (
    OreCategory, TaxRate, MiningLedgerEntry, AllianceMoon,
    FleetSession, MoonRental, AllianceBillingRecord
)


# Steuersätze direkt in der Liste editierbar (Inline-Edit ohne extra Klick)
@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = ('ore_category', 'tax_rate', 'description')
    list_editable = ('tax_rate',)


# Alliance-Monde mit Filter nach Typ (public/event), damit man schnell zwischen beiden wechseln kann
@admin.register(AllianceMoon)
class AllianceMoonAdmin(admin.ModelAdmin):
    list_display = ('name', 'solar_system_name', 'ore_category', 'moon_type', 'is_tax_free')
    list_filter = ('moon_type', 'ore_category')
    list_editable = ('moon_type', 'is_tax_free')
    search_fields = ('name', 'solar_system_name')


# Mining-Ledger durchsuchbar nach Character und Erz-Typ, mit Datumsfilter
@admin.register(MiningLedgerEntry)
class MiningLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ('character', 'date', 'type_name', 'quantity', 'total_value', 'solar_system_name')
    list_filter = ('date',)
    search_fields = ('character__character_name', 'type_name', 'solar_system_name')
    date_hierarchy = 'date'


# Fleet-Sessions mit Übersicht über Zeitraum und Ausschluss-Status
@admin.register(FleetSession)
class FleetSessionAdmin(admin.ModelAdmin):
    list_display = ('name', 'ore_category', 'moon', 'start_time', 'end_time', 'exclude_from_billing')
    list_filter = ('exclude_from_billing',)


# Moon Rentals pro Corp, mit Möglichkeit aktive/inaktive direkt umzuschalten
@admin.register(MoonRental)
class MoonRentalAdmin(admin.ModelAdmin):
    list_display = ('corporation', 'moon_name', 'monthly_fee', 'active')
    list_editable = ('active',)
    search_fields = ('moon_name', 'corporation__corporation_name')


# Abrechnungs-Snapshots pro Corp/Monat, mit Bezahlt-Status zum Abhaken
@admin.register(AllianceBillingRecord)
class AllianceBillingRecordAdmin(admin.ModelAdmin):
    list_display = ('corporation', 'month', 'year', 'total_due', 'paid')
    list_filter = ('paid', 'year', 'month')
    list_editable = ('paid',)


# Einfache Models ohne besondere Konfiguration
admin.site.register(OreCategory)
