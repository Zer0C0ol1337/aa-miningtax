from django import forms
from .models import TaxRate, MoonRental, AllianceMoon, TreasuryConfig
from allianceauth.eveonline.models import EveCorporationInfo


class TaxRateForm(forms.ModelForm):
    class Meta:
        model = TaxRate
        fields = ['tax_rate', 'description']
        widgets = {
            'tax_rate': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm',
                'step': '0.01', 'min': '0', 'max': '100',
            }),
            'description': forms.TextInput(attrs={'class': 'form-control form-control-sm'}),
        }


# Adds a data-alliance attribute to each <option> so a separate alliance
# dropdown can filter the corporation list client-side via JS, instead of
# showing every corp in the database at once.
class CorporationSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value:
            try:
                corp_id = value.value if hasattr(value, 'value') else value
                corp = EveCorporationInfo.objects.select_related('alliance').get(pk=corp_id)
                option['attrs']['data-alliance'] = str(corp.alliance_id or '')
            except EveCorporationInfo.DoesNotExist:
                pass
        return option


class MoonRentalForm(forms.ModelForm):
    class Meta:
        model = MoonRental
        fields = ['corporation', 'moon_name', 'structure_name', 'monthly_fee', 'active']
        widgets = {
            'corporation': CorporationSelect(attrs={'class': 'form-control'}),
            'moon_name': forms.TextInput(attrs={'class': 'form-control'}),
            'structure_name': forms.TextInput(attrs={'class': 'form-control'}),
            'monthly_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['corporation'].queryset = EveCorporationInfo.objects.select_related('alliance').order_by('corporation_name')


class AllianceMoonForm(forms.ModelForm):
    class Meta:
        model = AllianceMoon
        fields = ['name', 'solar_system_name', 'ore_category', 'moon_type', 'is_tax_free']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'solar_system_name': forms.TextInput(attrs={'class': 'form-control'}),
            'ore_category': forms.Select(attrs={'class': 'form-control'}, choices=[
                ('R4', 'R4'), ('R8', 'R8'), ('R16', 'R16'),
                ('R32', 'R32'), ('R64', 'R64'), ('Ice', 'Ice'), ('Ore', 'Ore'), ('Gas', 'Gas'),
            ]),
            'moon_type': forms.Select(attrs={'class': 'form-control'}),
            'is_tax_free': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class TreasuryConfigForm(forms.ModelForm):
    class Meta:
        model = TreasuryConfig
        fields = ['corporation', 'wallet_division', 'active']
        widgets = {
            'corporation': CorporationSelect(attrs={'class': 'form-control', 'id': 'id_treasury_corporation'}),
            'wallet_division': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '7'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['corporation'].queryset = EveCorporationInfo.objects.select_related('alliance').order_by('corporation_name')
        self.fields['corporation'].label = 'Receiving Corp (Treasury)'
        self.fields['wallet_division'].label = 'Wallet Division (1-7)'