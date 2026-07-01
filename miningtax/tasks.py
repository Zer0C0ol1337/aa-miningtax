from celery import shared_task

from .services import sync_all_characters, update_market_prices


# Synct den Mining-Ledger für ALLE Characters, die ein gültiges Mining-Token haben
# (nicht nur die des aktuell eingeloggten Users — das übernimmt sync_all_characters bereits).
# Wird täglich automatisch über celery beat ausgeführt (siehe CELERYBEAT_SCHEDULE in local.py).
@shared_task
def daily_mining_sync():
    synced = sync_all_characters()
    priced = update_market_prices()
    return f'{synced} Ledger-Einträge synchronisiert, {priced} Preise aktualisiert'