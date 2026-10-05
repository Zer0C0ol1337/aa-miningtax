"""Migration 0027 and the consistency of models and migrations."""
import ast
import importlib.util
from decimal import Decimal

import pytest
from django.db import connection, models

from tests.fake_app.models import AllianceBillingRecord, MiningLedgerEntry, MoonRental
from tests.helpers import PKG, model_fields


@pytest.fixture
def tables():
    """Creates the pre-0027 tables and drops them afterwards."""
    with connection.schema_editor() as editor:
        for model in (MiningLedgerEntry, MoonRental, AllianceBillingRecord):
            editor.create_model(model)
    yield
    with connection.schema_editor() as editor:
        for model in (MiningLedgerEntry, MoonRental, AllianceBillingRecord):
            editor.delete_model(model)


def _run_0027_rounding():
    spec = importlib.util.spec_from_file_location('m0027', PKG / 'migrations' / '0027_whole_isk_amounts.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Apps:
        def get_model(self, app, name):
            return {'MiningLedgerEntry': MiningLedgerEntry, 'MoonRental': MoonRental,
                    'AllianceBillingRecord': AllianceBillingRecord}[name]

    module.round_to_whole_isk(Apps(), None)


def test_0027_rounds_payable_amounts_down_so_earlier_payments_still_match(tables):
    D = Decimal
    cases = [  # tax, rental, a transfer made before the migration
        ('45141.67', '0', '45141.67'),
        ('45141.67', '0', '45142'),
        ('100.60', '200.70', '301.30'),
    ]
    for tax, rental, _ in cases:
        AllianceBillingRecord.objects.create(mining_tax_amount=D(tax), moon_rental_total=D(rental), total_due=D(tax) + D(rental))
    MiningLedgerEntry.objects.create(total_value=D('1234.50'))
    _run_0027_rounding()
    for record, (tax, rental, paid) in zip(AllianceBillingRecord.objects.order_by('id'), cases):
        assert record.total_due == record.mining_tax_amount + record.moon_rental_total
        assert record.total_due <= D(tax) + D(rental)
        assert D(paid) >= record.total_due
        assert record.total_due == record.total_due.to_integral_value()
    assert MiningLedgerEntry.objects.get().total_value == D('1235')


def _deconstruct(node):
    return eval(compile(ast.Expression(node), 'field', 'eval'), {'models': models}).deconstruct()[1:]


@pytest.mark.parametrize('migration, model_map', [
    ('0026_corpalliancejoin', None),
    ('0027_whole_isk_amounts', {'miningledgerentry': 'MiningLedgerEntry', 'moonrental': 'MoonRental',
                                'alliancebillingrecord': 'AllianceBillingRecord'}),
])
def test_models_match_their_migrations(migration, model_map):
    tree = ast.parse((PKG / 'migrations' / f'{migration}.py').read_text(encoding='utf-8'))
    checked = 0
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        kind = getattr(call.func, 'attr', '')
        kw = {k.arg: k.value for k in call.keywords}
        if kind == 'AlterField':
            fields = model_fields('models.py', model_map[kw['model_name'].value])
            assert _deconstruct(kw['field']) == _deconstruct(fields[kw['name'].value])
            checked += 1
        elif kind == 'CreateModel':
            fields = model_fields('models.py', kw['name'].value)
            for pair in kw['fields'].elts:
                name, node = pair.elts[0].value, pair.elts[1]
                if name != 'id':
                    assert _deconstruct(node) == _deconstruct(fields[name])
                    checked += 1
    assert checked
