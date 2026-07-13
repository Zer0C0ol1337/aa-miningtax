from celery import shared_task

from .services import sync_all_characters, sync_all_corp_observers, update_market_prices


# Täglicher Sync: persönliche Ledger (via Corptools oder ESI) + Corp Observer + Marktpreise
@shared_task
def daily_mining_sync():
    # 1. Persönliche Mining-Ledger aller Characters
    synced_chars = sync_all_characters()

    # 2. Corp Observer (Monde/Strukturen) — braucht Director-Token
    #    Liefert genauere Daten: Struktur-Namen statt nur System-Namen
    synced_corps = sync_all_corp_observers()

    # 3. Marktpreise aktualisieren
    priced = update_market_prices()

    return (
        f'{synced_chars} persönliche Einträge, '
        f'{synced_corps} Corp Observer Einträge, '
        f'{priced} Preise aktualisiert'
    )