from django.contrib import admin
from .models import (
    General, OreCategory, TaxRate, TaxRateHistory, MiningLedgerEntry, AllianceMoon,
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


# Steuersätze direkt in der Liste editierbar (Inline-Edit ohne extra Klick).
#
# save_model() und save_formset() sind überschrieben, damit eine Änderung —
# egal ob über das Inline-Feld in der Liste oder die normale Detail-Ansicht —
# durch billing.set_tax_rate() läuft statt Django's Standardverhalten (schreibt
# nur TaxRate.tax_rate). Ohne das würde eine im Admin geänderte Rate keine
# TaxRateHistory-Zeile bekommen und beim nächsten Neuberechnen eines
# vergangenen Monats rückwirkend angewendet — genau das, was set_tax_rate()
# verhindert. Beide Codepfade wurden mit einem echten Django-Formset/-Form
# getestet, nicht nur nach Dokumentation angenommen.
@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = ('ore_category', 'tax_rate', 'description')
    list_editable = ('tax_rate',)

    def save_model(self, request, obj, form, change):
        """Detail-page save path (add/change form, not the list_editable one)."""
        if change and 'tax_rate' in form.changed_data:
            from .billing import set_tax_rate
            set_tax_rate(obj.ore_category, obj.tax_rate)
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        """
        list_editable inline-edit path: Django saves the whole changelist
        formset at once here rather than calling save_model() per row, so the
        same set_tax_rate() routing has to happen separately or an inline edit
        would silently skip the history.

        formset.changed_objects is populated by formset.save(commit=False):
        a list of (instance, [changed_field_names]) for rows that actually
        changed — verified against a live Django formset before relying on it,
        since guessing at admin internals is how subtle bugs get shipped.
        """
        instances = formset.save(commit=False)
        changed_fields_by_pk = {
            obj.pk: fields for obj, fields in formset.changed_objects
        }
        from .billing import set_tax_rate
        for obj in instances:
            if 'tax_rate' in changed_fields_by_pk.get(obj.pk, []):
                set_tax_rate(obj.ore_category, obj.tax_rate)
            obj.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()


# Read-only view of every rate change ever made — the audit trail that makes
# set_tax_rate() trustworthy. Never editable here: the only correct way to add
# a row is through set_tax_rate(), so the admin path is intentionally closed
# rather than offering a second way to write history that could disagree with
# what get_tax_rate() actually used.
@admin.register(TaxRateHistory)
class TaxRateHistoryAdmin(admin.ModelAdmin):
    list_display = ('ore_category', 'tax_rate', 'effective_from', 'created_at')
    list_filter = ('ore_category',)
    ordering = ('-effective_from',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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

    # Ein Typ, der bisher nicht einzuordnen war, wird nicht erneut bei ESI
    # erfragt — bis eine neue Regel genau das ändern soll. Deshalb hier
    # verwerfen, sonst wirkt die Regel erst am nächsten Tag.
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        from .billing import forget_unclassifiable_types
        forget_unclassifiable_types()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        from .billing import forget_unclassifiable_types
        forget_unclassifiable_types()


# Legt fest, welche Charaktere überhaupt besteuert werden. Leere Tabelle =
# alles wird besteuert (Verhalten vor Einführung der Reichweite).
@admin.register(TaxableScope)
class TaxableScopeAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'alliance', 'corporation', 'note')
    search_fields = ('alliance__alliance_name', 'corporation__corporation_name', 'note')