from django.db import migrations, models
from django.db.models import F
from django.db.models.functions import Floor, Round


def round_to_whole_isk(apps, schema_editor):
    """
    Rounds every stored ISK amount to whole ISK before the columns become
    BigIntegerField, by the same rules the code uses from now on (amounts.py).

    What a corp has to pay — tax, rental and due — is rounded DOWN, and the due
    is the sum of the rounded tax and rental. So every due only gets smaller —
    by less than 1 ISK when the rental is whole, as rentals practically always
    are, at most by less than 2 ISK — and never larger: a transfer that covered an invoice before
    — even one of the exact figure with cents, as older PDFs showed it — still
    covers it afterwards, and the payment check keeps recognising it. Values
    that aren't paid as such (a ledger entry's worth, the mined total, a
    configured fee) are rounded to the nearest whole ISK.

    Touches only this app's own tables.
    """
    Ledger = apps.get_model('miningtax', 'MiningLedgerEntry')
    Rental = apps.get_model('miningtax', 'MoonRental')
    Record = apps.get_model('miningtax', 'AllianceBillingRecord')

    Ledger.objects.update(total_value=Round('total_value'))
    Rental.objects.update(monthly_fee=Round('monthly_fee'))
    Record.objects.update(
        total_mined_value=Round('total_mined_value'),
        mining_tax_amount=Floor('mining_tax_amount'),
        moon_rental_total=Floor('moon_rental_total'),
    )
    Record.objects.update(total_due=F('mining_tax_amount') + F('moon_rental_total'))


class Migration(migrations.Migration):
    """
    ISK amounts become whole numbers (BigIntegerField). Unit prices and tax
    rates keep their decimals.
    """

    dependencies = [
        ('miningtax', '0026_corpalliancejoin'),
    ]

    operations = [
        migrations.RunPython(round_to_whole_isk, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='miningledgerentry',
            name='total_value',
            field=models.BigIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='moonrental',
            name='monthly_fee',
            field=models.BigIntegerField(),
        ),
        migrations.AlterField(
            model_name='alliancebillingrecord',
            name='total_mined_value',
            field=models.BigIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='alliancebillingrecord',
            name='mining_tax_amount',
            field=models.BigIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='alliancebillingrecord',
            name='moon_rental_total',
            field=models.BigIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='alliancebillingrecord',
            name='total_due',
            field=models.BigIntegerField(default=0),
        ),
    ]