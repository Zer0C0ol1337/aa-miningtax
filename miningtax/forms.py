from django import forms
from .models import (
    TaxRate, MoonRental, AllianceMoon, TreasuryConfig, SovFilterConfig,
    JaniceConfig, TaxExemption,
)
from allianceauth.eveonline.models import EveCharacter
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


# Adds a data-corp attribute to each <option> so the corporation dropdown can
# narrow the main-character list client-side — same mechanism the alliance
# filter already uses for corporations. The value stored in data-corp is the
# EveCorporationInfo primary key, because that is what the corporation <select>
# renders as its option values.
class MainCharacterSelect(forms.Select):
    def __init__(self, *args, corp_by_char=None, **kwargs):
        self.corp_by_char = corp_by_char or {}
        super().__init__(*args, **kwargs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value:
            char_pk = value.value if hasattr(value, 'value') else value
            corp_pk = self.corp_by_char.get(int(char_pk))
            if corp_pk:
                option['attrs']['data-corp'] = str(corp_pk)
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


# Designates whose sovereignty defines the known system list. Purely a data
# source for the moon dropdowns — taxation is never restricted by location.
class SovFilterConfigForm(forms.ModelForm):
    class Meta:
        model = SovFilterConfig
        fields = ['corporation']
        widgets = {
            'corporation': CorporationSelect(attrs={'class': 'form-control', 'id': 'id_sov_corporation'}),
        }

    def __init__(self, *args, alliance_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['corporation'].queryset = _corps_for_alliances(alliance_ids)
        self.fields['corporation'].label = 'Reference Corporation'


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


# Exempts a single character or a whole corporation from mining tax.
# Exactly one of the two fields must be filled — validated in clean() so an
# officer can't accidentally save an exemption that matches nothing (or, worse,
# one that silently exempts an entire corp when only one pilot was meant).
class TaxExemptionForm(forms.ModelForm):
    class Meta:
        model = TaxExemption
        fields = ['character', 'corporation', 'reason', 'active']
        widgets = {
            'reason': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Alliance leadership, newbro programme...',
            }),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, alliance_ids=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['corporation'].queryset = _corps_for_alliances(alliance_ids)
        self.fields['corporation'].widget = CorporationSelect(
            attrs={'class': 'form-control', 'id': 'id_exempt_corporation'},
            choices=[('', '— None —')] + [
                (c.pk, c.corporation_name) for c in _corps_for_alliances(alliance_ids)
            ],
        )
        self.fields['corporation'].required = False

        # Only MAIN characters are listed. An exemption on a main automatically
        # covers all of that player's alts (resolved at billing time via
        # CharacterOwnership), so nobody has to pick 50 alts one by one.
        # The list is further narrowed by the corporation dropdown in the UI,
        # so an alliance with thousands of pilots stays navigable.
        from allianceauth.authentication.models import UserProfile

        main_ids = UserProfile.objects.filter(
            main_character__isnull=False
        ).values_list('main_character_id', flat=True)
        mains = EveCharacter.objects.filter(
            pk__in=main_ids
        ).order_by('character_name')

        # Map each main to the DB primary key of its corporation, so the
        # client-side filter can compare against the corporation <select>.
        corp_pk_by_eve_id = dict(
            EveCorporationInfo.objects.values_list('corporation_id', 'pk')
        )
        corp_by_char = {
            c.pk: corp_pk_by_eve_id.get(c.corporation_id)
            for c in mains
            if corp_pk_by_eve_id.get(c.corporation_id)
        }

        self.fields['character'].queryset = mains
        self.fields['character'].widget = MainCharacterSelect(
            attrs={'class': 'form-control', 'id': 'id_exempt_character'},
            choices=[('', '— Whole corporation —')] + [
                (c.pk, c.character_name) for c in mains
            ],
            corp_by_char=corp_by_char,
        )
        self.fields['character'].required = False
        self.fields['character'].label = 'Main Character'

    def clean(self):
        cleaned = super().clean()
        character = cleaned.get('character')
        corporation = cleaned.get('corporation')

        if not character and not corporation:
            raise forms.ValidationError(
                'Select a corporation — and optionally a main character within it.'
            )

        # The corporation dropdown does double duty: it narrows the main list,
        # and it defines the exemption when no main is picked. So once a main
        # is chosen, the corporation was only a filter and must not be stored,
        # otherwise the whole corp would silently become exempt.
        if character:
            cleaned['corporation'] = None

        return cleaned