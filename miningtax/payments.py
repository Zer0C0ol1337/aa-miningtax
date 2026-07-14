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
                logger.info(f'Treasury {config.corporation.corporation_name}: token found ({character.character_name})')
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
        logger.info(f'Treasury {config.corporation.corporation_name} (division {config.wallet_division}): {len(journal)} journal entries retrieved')
    except Exception as e:
        logger.warning(f'Treasury {config.corporation.corporation_name}: wallet journal request failed: {e}')
        return 0

    matched = 0
    keyword = config.payment_reason_keyword.lower()

    for record in open_records:
        paying_corp_id = record.corporation.corporation_id
        paying_corp_name = record.corporation.corporation_name
        logger.info(
            f'Treasury {config.corporation.corporation_name}: checking incoming payment for '
            f'{paying_corp_name} (ID: {paying_corp_id}), due: {record.total_due} ISK'
        )

        found = False
        for entry in journal:
            reason = (getattr(entry, 'reason', '') or '')
            first_party_id = getattr(entry, 'first_party_id', None)
            amount = getattr(entry, 'amount', 0)

            if keyword not in reason.lower():
                continue

            logger.debug(
                f'  Journal entry with keyword match: from corp {first_party_id}, '
                f'amount {amount} ISK, reason: "{reason}"'
            )

            if first_party_id != paying_corp_id:
                logger.debug(f'  → sender corp {first_party_id} does not match {paying_corp_name} ({paying_corp_id}) — skipped')
                continue
            if amount < float(record.total_due):
                logger.debug(f'  → amount {amount} ISK is not enough (required: {record.total_due} ISK) — skipped')
                continue

            from django.utils import timezone
            record.paid = True
            record.paid_at = timezone.now()
            record.auto_verified = True
            record.save(update_fields=['paid', 'paid_at', 'auto_verified'])

            logger.info(
                f'✅ PAYMENT DETECTED (via {config.corporation.corporation_name}): {paying_corp_name} — '
                f'{amount} ISK received (due: {record.total_due} ISK) — automatically marked as paid'
            )
            matched += 1
            found = True
            break

        if not found:
            logger.info(
                f'❌ No matching payment found for {paying_corp_name} in treasury '
                f'{config.corporation.corporation_name}'
            )

    return matched


def check_corp_payments(year, month):
    """
    Checks the wallet journals of ALL active treasury configs for incoming
    payments and matches them against open AllianceBillingRecord entries
    (amount + sender corp + reason keyword).
    A billing record already matched in one treasury is not checked again
    in another.
    """
    logger.info(f'=== Payment check started for {month:02d}/{year} ===')

    configs = TreasuryConfig.objects.filter(active=True).select_related('corporation')
    config_count = configs.count()
    logger.info(f'Active treasury configurations: {config_count}')

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
    logger.info(f'Open (unpaid, total_due > 0) billing records for {month:02d}/{year}: {open_count}')

    if open_count == 0:
        logger.info(
            'No open billing records found. Records are only created by the '
            'daily sync or "Mark as Paid".'
        )
        return 0

    total_matched = 0

    for config in configs:
        still_open = [r for r in open_records if not r.paid]
        if not still_open:
            logger.info('All open records already matched — skipping remaining treasuries')
            break

        matched = _check_payments_for_treasury(config, year, month, still_open)
        total_matched += matched

    logger.info(f'=== Payment check complete: {total_matched}/{open_count} corp(s) automatically marked as paid ===')
    return total_matched