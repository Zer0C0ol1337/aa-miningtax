from django import forms
from .models import TaxRate, MoonRental, AllianceMoon
from allianceauth.eveonline.models import EveCorporationInfo


# Formular zum Bearbeiten eines einzelnen Steuersatzes (inline in der Tabelle)
class TaxRateForm(forms.ModelForm):
    class Meta:
        model = TaxRate
        fields = ['tax_rate', 'description']
        widgets = {
            'tax_rate': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm',
                'step': '0.01',
                'min': '0',
                'max': '100',
            }),
            'description': forms.TextInput(attrs={
                'class': 'form-control form-control-sm',
            }),
        }


# Formular zum Anlegen / Bearbeiten eines Moon-Mietvertrags
class MoonRentalForm(forms.ModelForm):
    class Meta:
        model = MoonRental
        fields = ['corporation', 'moon_name', 'structure_name', 'monthly_fee', 'active']
        widgets = {
            'corporation': forms.Select(attrs={'class': 'form-control'}),
            'moon_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'z.B. Perimeter V - Moon 1'}),
            'structure_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Exakter solar_system_name aus dem Ledger'}),
            'monthly_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Nur Corps anzeigen, die in Alliance Auth bekannt sind
        self.fields['corporation'].queryset = EveCorporationInfo.objects.all().order_by('corporation_name')
        self.fields['corporation'].label = 'Corporation'
        self.fields['moon_name'].label = 'Mond-Name'
        self.fields['structure_name'].label = 'Struktur-Name (Ledger-Match)'
        self.fields['monthly_fee'].label = 'Monatliche Gebühr (ISK)'
        self.fields['active'].label = 'Aktiv'


# Formular zum Anlegen / Bearbeiten eines Alliance-Monds (inkl. Steuerfreiheit)
class AllianceMoonForm(forms.ModelForm):
    class Meta:
        model = AllianceMoon
        fields = ['name', 'solar_system_name', 'ore_category', 'moon_type', 'is_tax_free']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'z.B. P9F-ZG IV - Moon 3'}),
            'solar_system_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'z.B. P9F-ZG'}),
            'ore_category': forms.Select(attrs={'class': 'form-control'}, choices=[
                ('R4',  'R4'),
                ('R8',  'R8'),
                ('R16', 'R16'),
                ('R32', 'R32'),
                ('R64', 'R64'),
                ('Ice', 'Ice'),
                ('Ore', 'Ore'),
            ]),
            'moon_type': forms.Select(attrs={'class': 'form-control'}),
            'is_tax_free': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'Mond-Name',
            'solar_system_name': 'Sonnensystem',
            'ore_category': 'Erz-Kategorie',
            'moon_type': 'Mond-Typ',
            'is_tax_free': 'Steuerfrei',
        }
