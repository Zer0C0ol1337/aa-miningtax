import logging
from datetime import date

from .models import TreasuryConfig, AllianceBillingRecord

logger = logging.getLogger(__name__)


def _get_treasury_token_for_config(config):
    """
    Gets a valid token with the esi-wallet.read_corporation_wallets.v1 scope
    for a specific treasury corp.
    """
    from esi.models import Token
    from allianceauth.eveonline.models import EveCharacter

    tokens = Token.objects.filter(
        scopes__name='esi-wallet.read_corporation_wallets.v1'
    ).require_valid()

    for token in tokens:
        try:
            character = EveCharacter.objects.get(character_id=token.character_id)
            if character.corporation_id == config.corporation.corporation_id:
                return token
        except EveCharacter.DoesNotExist:
            continue

    logger.warning(
        f'No wallet token found for treasury corp {config.corporation.corporation_name} '
        f'({config.corporation.corporation_id}). A character of this corp must log in via '
        f'Alliance Auth with the esi-wallet.read_corporation_wallets.v1 scope.'
    )
    return None


def _check_payments_for_treasury(config, year, month, open_records):
    """
    Checks the wallet journal of ONE treasury corp against the given open
    billing records. Returns the number of matches.

    Per-record and per-journal-entry detail is logged at DEBUG to avoid
    flooding the log when there are many open records or a large journal —
    only matches (successes) and errors are logged at INFO/WARNING.
    """
    from .services import _get_esi_client

    token = _get_treasury_token_for_config(config)
    if not token:
        return 0

    esi = _get_esi_client()

    try:
        journal = esi.client.Wallet.GetCorporationsCorporationIdWalletsDivisionJournal(
            corporation_id=config.corporation.corporation_id,
            division=config.wallet_division,
            token=token
        ).results()
        logger.debug(f'Treasury {config.corporation.corporation_name} (division {config.wallet_division}): {len(journal)} journal entries retrieved')
    except Exception as e:
        logger.warning(f'Treasury {config.corporation.corporation_name}: wallet journal request failed: {e}')
        return 0

    matched = 0
    keyword = config.payment_reason_keyword.lower()

    for record in open_records:
        paying_corp_id = record.corporation.corporation_id
        paying_corp_name = record.corporation.corporation_name

        found = False
        for entry in journal:
            reason = (getattr(entry, 'reason', '') or '')
            first_party_id = getattr(entry, 'first_party_id', None)
            amount = getattr(entry, 'amount', 0)

            if keyword not in reason.lower():
                continue
            if first_party_id != paying_corp_id:
                continue
            if amount < float(record.total_due):
                continue

            from django.utils import timezone
            record.paid = True
            record.paid_at = timezone.now()
            record.auto_verified = True
            record.save(update_fields=['paid', 'paid_at', 'auto_verified'])

            logger.info(
                f'✅ Payment detected (via {config.corporation.corporation_name}): {paying_corp_name} — '
                f'{amount} ISK received (due: {record.total_due} ISK) — automatically marked as paid'
            )
            matched += 1
            found = True
            break

        if not found:
            logger.debug(f'No matching payment found for {paying_corp_name} in treasury {config.corporation.corporation_name}')

    return matched


def check_corp_payments(year, month):
    """
    Checks the wallet journals of ALL active treasury configs for incoming
    payments and matches them against open AllianceBillingRecord entries
    (amount + sender corp + reason keyword).
    A billing record already matched in one treasury is not checked again
    in another.
    """
    configs = TreasuryConfig.objects.filter(active=True).select_related('corporation')
    config_count = configs.count()

    if config_count == 0:
        logger.warning(
            'No active TreasuryConfig found. Please add at least one receiving '
            'corporation in the Settings UI (Treasury tab).'
        )
        return 0

    open_records = list(
        AllianceBillingRecord.objects.filter(
            year=year, month=month, paid=False, total_due__gt=0
        ).select_related('corporation')
    )
    open_count = len(open_records)

    if open_count == 0:
        logger.info(f'Payment check for {month:02d}/{year}: no open billing records to check')
        return 0

    total_matched = 0

    for config in configs:
        still_open = [r for r in open_records if not r.paid]
        if not still_open:
            break

        matched = _check_payments_for_treasury(config, year, month, still_open)
        total_matched += matched

    logger.info(f'Payment check for {month:02d}/{year} complete: {total_matched}/{open_count} corp(s) marked as paid')
    return total_matched