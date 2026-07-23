from django.contrib import admin
from .models import (
    General, OreCategory, TaxRate, MiningLedgerEntry, AllianceMoon,
    FleetSession, MoonRental, AllianceBillingRecord, TaxExemption,
    OreCategoryRule, TaxableScope
)


# General wird nur für Permissions genutzt — kein eigenes Admin-Interface nötig,
# aber registrieren damit die Permissions im Admin sichtbar und zuweisbar sind
@admin.register(General)
class GeneralAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# Steuersätze direkt in der Liste editierbar (Inline-Edit ohne extra Klick)
@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = ('ore_category', 'tax_rate', 'description')
    list_editable = ('tax_rate',)


# Alliance-Monde mit Filter nach Typ (public/event)
@admin.register(AllianceMoon)
class AllianceMoonAdmin(admin.ModelAdmin):
    list_display = ('name', 'solar_system_name', 'ore_category', 'moon_type', 'is_tax_free')
    list_filter = ('moon_type', 'ore_category')
    list_editable = ('moon_type', 'is_tax_free')
    search_fields = ('name', 'solar_system_name')


# Mining-Ledger durchsuchbar nach Character und Erz-Typ
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


# Moon Rentals pro Corp
@admin.register(MoonRental)
class MoonRentalAdmin(admin.ModelAdmin):
    list_display = ('corporation', 'moon_name', 'monthly_fee', 'active')
    list_editable = ('active',)
    search_fields = ('moon_name', 'corporation__corporation_name')


# Abrechnungs-Snapshots pro Corp/Monat
@admin.register(AllianceBillingRecord)
class AllianceBillingRecordAdmin(admin.ModelAdmin):
    list_display = ('corporation', 'month', 'year', 'total_due', 'paid')
    list_filter = ('paid', 'year', 'month')
    list_editable = ('paid',)


# Erz-Kategorien — nur lesend, werden per Management Command befüllt
@admin.register(OreCategory)
class OreCategoryAdmin(admin.ModelAdmin):
    list_display = ('type_id', 'type_name', 'category', 'locked')
    list_filter = ('category', 'locked')
    list_editable = ('category', 'locked')
    search_fields = ('type_name',)


# Steuerbefreiungen — entweder einzelner Character ODER ganze Corp.
# Das jeweils andere Feld leer lassen. "active" ist direkt in der Liste
# umschaltbar, so lässt sich eine Befreiung pausieren statt sie zu löschen.
@admin.register(TaxExemption)
class TaxExemptionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'character', 'corporation', 'reason', 'active')
    list_filter = ('active',)
    list_editable = ('active',)
    search_fields = (
        'character__character_name',
        'corporation__corporation_name',
        'reason',
    )


# Namensregeln für die Erz-Einordnung. Greifen vor EVEs eigener Gruppierung
# und gelten auch für Erze, die es noch gar nicht gibt — solange der Name passt.
@admin.register(OreCategoryRule)
class OreCategoryRuleAdmin(admin.ModelAdmin):
    list_display = ('contains', 'match_field', 'category', 'priority', 'active', 'note')
    list_editable = ('category', 'priority', 'active')
    list_filter = ('active', 'match_field', 'category')
    search_fields = ('contains', 'category', 'note')


# Legt fest, welche Charaktere überhaupt besteuert werden. Leere Tabelle =
# alles wird besteuert (Verhalten vor Einführung der Reichweite).
@admin.register(TaxableScope)
class TaxableScopeAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'alliance', 'corporation', 'note')
    search_fields = ('alliance__alliance_name', 'corporation__corporation_name', 'note')