from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds the month from which unpaid invoices count towards the open-invoice
    badge. Both fields are optional; empty keeps every month counting, which is
    exactly the behaviour before this migration.
    """

    dependencies = [
        ('miningtax', '0024_alter_paymentcodesettings_id_alter_taxratehistory_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentcodesettings',
            name='open_invoices_from_year',
            field=models.PositiveSmallIntegerField(
                null=True, blank=True,
                help_text='Year of the first month whose unpaid invoices count towards the open-invoice badge. '
                          'Leave both fields empty to count every month.'
            ),
        ),
        migrations.AddField(
            model_name='paymentcodesettings',
            name='open_invoices_from_month',
            field=models.PositiveSmallIntegerField(
                null=True, blank=True,
                help_text='Month (1-12) of the first month whose unpaid invoices count towards the open-invoice badge. '
                          'Leave both fields empty to count every month.'
            ),
        ),
    ]