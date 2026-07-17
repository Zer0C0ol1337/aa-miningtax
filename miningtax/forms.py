from django import forms
from .models import TaxRate, MoonRental, AllianceMoon, TreasuryConfig, SovFilterConfig, JaniceConfig
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
# dropdown can filter the corporation list client-side via JS.
class CorporationSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value:
            try:
                corp_id = value.value if hasattr(value, 'value') else value
                corp = EveCorporationInfo.objects.select_related('alliance').get(pk=corp_id)
                # Use the real EVE alliance_id (not corp.alliance_id, which is the
                # internal DB primary key of the EveAllianceInfo row). The alliance
                # dropdown in the template renders the real EVE alliance_id as its
                # option value, so data-alliance must match that or the client-side
                # filter never finds any corps and the dropdown stays empty.
                alliance_eve_id = corp.alliance.alliance_id if corp.alliance_id else ''
                option['attrs']['data-alliance'] = str(alliance_eve_id)
            except EveCorporationInfo.DoesNotExist:
                pass
        return option


def _corps_for_alliances(alliance_ids):
    """
    Corporation queryset scoped to a set of alliance IDs — used so forms
    default to showing only the officer's own alliance's corps instead of
    every corp Alliance Auth has ever resolved via ESI. Falls back to all
    corps with a known alliance if no alliance_ids are given.
    """
    qs = EveCorporationInfo.objects.select_related('alliance')
    if alliance_ids:
        qs = qs.filter(alliance__alliance_id__in=alliance_ids)
    else:
        qs = qs.filter(alliance__isnull=False)
    return qs.order_by('corporation_name')


class MoonRentalForm(forms.ModelForm):
    class Meta:
        model = MoonRental
        fields = ['corporation', 'moon_name', 'structure_name', 'monthly_fee', 'active']
        widgets = {
            'corporation': CorporationSelect(attrs={'class': 'form-control', 'id': 'id_corporation'}),
            'moon_name': forms.TextInput(attrs={'class': 'form-control'}),
            'structure_name': forms.TextInput(attrs={
                'class': 'form-control',
                'list': 'known-structure-names',
                'placeholder': 'Start typing to see known structures...',
            }),
            'monthly_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, alliance_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['corporation'].queryset = _corps_for_alliances(alliance_ids)


class AllianceMoonForm(forms.ModelForm):
    class Meta:
        model = AllianceMoon
        fields = ['name', 'solar_system_name', 'structure_name', 'ore_category', 'moon_type', 'is_tax_free']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'solar_system_name': forms.TextInput(attrs={'class': 'form-control'}),
            'structure_name': forms.TextInput(attrs={
                'class': 'form-control',
                'list': 'known-structure-names',
                'placeholder': 'Exact structure name (optional)',
            }),
            'ore_category': forms.Select(attrs={'class': 'form-control'}, choices=[
                ('R4', 'R4'), ('R8', 'R8'), ('R16', 'R16'),
                ('R32', 'R32'), ('R64', 'R64'), ('Ice', 'Ice'), ('Ore', 'Ore'),
                ('Mercoxit', 'Mercoxit'), ('Gas', 'Gas'),
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

    def __init__(self, *args, alliance_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['corporation'].queryset = _corps_for_alliances(alliance_ids)
        self.fields['corporation'].label = 'Receiving Corp (Treasury)'
        self.fields['wallet_division'].label = 'Wallet Division (1-7)'


# Designates which corporation's EVE sovereignty defines taxable systems.
class SovFilterConfigForm(forms.ModelForm):
    class Meta:
        model = SovFilterConfig
        fields = ['corporation', 'active']
        widgets = {
            'corporation': CorporationSelect(attrs={'class': 'form-control', 'id': 'id_sov_corporation'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, alliance_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['corporation'].queryset = _corps_for_alliances(alliance_ids)
        self.fields['corporation'].label = 'Sovereignty Reference Corp'


# Config for the Janice refined-value pricing integration.
class JaniceConfigForm(forms.ModelForm):
    class Meta:
        model = JaniceConfig
        fields = ['enabled', 'api_key']
        widgets = {
            'enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'api_key': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Janice API key',
                'autocomplete': 'off',
            }),
        }