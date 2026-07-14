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


class MoonRentalForm(forms.ModelForm):
    class Meta:
        model = MoonRental
        fields = ['corporation', 'moon_name', 'structure_name', 'monthly_fee', 'active']
        widgets = {
            'corporation': forms.Select(attrs={'class': 'form-control'}),
            'moon_name': forms.TextInput(attrs={'class': 'form-control'}),
            'structure_name': forms.TextInput(attrs={'class': 'form-control'}),
            'monthly_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['corporation'].queryset = EveCorporationInfo.objects.all().order_by('corporation_name')


class AllianceMoonForm(forms.ModelForm):
    class Meta:
        model = AllianceMoon
        fields = ['name', 'solar_system_name', 'ore_category', 'moon_type', 'is_tax_free']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'solar_system_name': forms.TextInput(attrs={'class': 'form-control'}),
            'ore_category': forms.Select(attrs={'class': 'form-control'}, choices=[
                ('R4', 'R4'), ('R8', 'R8'), ('R16', 'R16'),
                ('R32', 'R32'), ('R64', 'R64'), ('Ice', 'Ice'), ('Ore', 'Ore'),
            ]),
            'moon_type': forms.Select(attrs={'class': 'form-control'}),
            'is_tax_free': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


# Formular für die Treasury-Konfiguration — welche Corp-Wallet auf Zahlungen geprüft wird
class TreasuryConfigForm(forms.ModelForm):
    class Meta:
        model = TreasuryConfig
        fields = ['corporation', 'payment_reason_keyword', 'wallet_division', 'active']
        widgets = {
            'corporation': forms.Select(attrs={'class': 'form-control'}),
            'payment_reason_keyword': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'z.B. Corp Steuer',
            }),
            'wallet_division': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '7'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['corporation'].queryset = EveCorporationInfo.objects.all().order_by('corporation_name')
        self.fields['corporation'].label = 'Empfänger-Corp (Treasury)'
        self.fields['payment_reason_keyword'].label = 'Zahlungsgrund-Stichwort'
        self.fields['wallet_division'].label = 'Wallet-Division (1-7)'