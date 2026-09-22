import logging
from datetime import date

from .models import TreasuryConfig, AllianceBillingRecord

logger = logging.getLogger(__name__)


def payment_code_for(corp_id, month, year):
    """
    Builds the expected wallet transfer reason code for a corp/month/year,
    e.g. "98606304/07/2026". Members put this exact string in the reason
    field when transferring their tax payment, and the payment check
    matches on it — unique per corp per month, no manual keyword needed.
    """
    return f"{corp_id}/{month:02d}/{year}"


def _get_treasury_token_for_config(config):
    """
    Gets a valid token with the esi-wallet.read_corporation_wallets.v1 scope
    for a specific treasury corp. Still needed even on the Corptools-first
    path: Corptools' own wallet sync requires exactly this scope on one of
    its own audited characters, and the ESI fallback below needs it directly.
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


def _get_corptools_journal(config):
    """
    Reads the treasury corp's wallet journal from Corptools' own database
    instead of ESI. Returns None if Corptools isn't installed, hasn't
    audited this corp yet, or has no entry for the configured division —
    any of which means "try ESI instead", not "this corp has no journal".

    Corptools syncs this on its own schedule regardless of this plugin, so
    reading it here costs no ESI call at all for a corp it already tracks.
    The catch, and the reason the manual "Check Payments Now" button still
    goes to ESI (see check_corp_payments): a DB read is only as fresh as
    Corptools' last sync, whereas a payment code only becomes relevant from
    the 2nd of the month onward anyway — the automatic daily check has
    plenty of time to catch up regardless of that lag, which is why it's
    fine to read from here.
    """
    try:
        from corptools.models import CorporationAudit
        from corptools.models.wallets import CorporationWalletJournalEntry
    except ImportError:
        return None

    audit = CorporationAudit.objects.filter(
        corporation__corporation_id=config.corporation.corporation_id
    ).first()
    if not audit:
        return None

    entries = CorporationWalletJournalEntry.objects.filter(
        division__corporation=audit,
        division__division=config.wallet_division,
    )
    # An empty queryset here is ambiguous the same way _get_corptools_entries()
    # was for the mining ledger: it could mean "no transactions yet" or "this
    # division was never actually synced". .exists() on the DIVISION itself
    # (not the journal) is the more honest signal — a division Corptools has
    # never seen at all means "don't trust this, ask ESI", while a division
    # that exists but genuinely has no matching entries yet is a real answer.
    from corptools.models.wallets import CorporationWalletDivision
    division_known = CorporationWalletDivision.objects.filter(
        corporation=audit, division=config.wallet_division
    ).exists()
    if not division_known:
        return None

    return list(entries)


def _match_payments_against_journal(journal, year, month, open_records, source_label):
    """
    The actual matching logic, shared between the Corptools path and the ESI
    path — both hand this the same shape of journal entries (objects with
    .reason, .first_party_id, .amount), just sourced differently, so the
    matching rules only need to exist once.

    Matches require the journal reason to be EXACTLY the per-corp code
    "{corp_id}/{month}/{year}" (after stripping whitespace), not just a
    substring — this rules out any ambiguity where one corp's code could
    accidentally be contained within another string, plus amount + sender
    corp are checked as before.
    """
    from django.utils import timezone
    from decimal import Decimal

    matched = 0

    for record in open_records:
        paying_corp_id = record.corporation.corporation_id
        paying_corp_name = record.corporation.corporation_name
        expected_code = payment_code_for(paying_corp_id, month, year)

        found = False
        for entry in journal:
            reason = (getattr(entry, 'reason', '') or '').strip()
            first_party_id = getattr(entry, 'first_party_id', None)
            # Compared as Decimal on both sides, never mixed with float:
            # Corptools' own amount field is already a Decimal, ESI's is a
            # float — converting record.total_due (a Decimal) to float to
            # compare against it can introduce a rounding error of a
            # fraction of an ISK, which was enough to make an exact match
            # wrongly fail once Corptools' Decimal amounts were compared
            # this way (verified against a real case before shipping this).
            amount = Decimal(str(getattr(entry, 'amount', 0) or 0))

            if reason != expected_code:
                continue
            if first_party_id != paying_corp_id:
                continue
            if amount < Decimal(str(record.total_due)):
                continue

            record.paid = True
            record.paid_at = timezone.now()
            record.auto_verified = True
            record.save(update_fields=['paid', 'paid_at', 'auto_verified'])

            logger.info(
                f'✅ Payment detected (via {source_label}): {paying_corp_name} — '
                f'{amount} ISK received (code "{expected_code}", due: {record.total_due} ISK) — automatically marked as paid'
            )
            matched += 1
            found = True
            break

        if not found:
            logger.debug(f'No matching payment found for {paying_corp_name} (expected code "{expected_code}") via {source_label}')

    return matched


def _check_payments_for_treasury(config, year, month, open_records, prefer_corptools=True):
    """
    Checks the wallet journal of ONE treasury corp against the given open
    billing records.

    prefer_corptools=True (the default, used by the daily automatic check)
    tries Corptools' own synced journal first, falling back to a live ESI
    call only if Corptools has no data for this corp/division yet.
    prefer_corptools=False (used by the manual "Check Payments Now" button)
    skips straight to ESI, for the case an officer wants to know right now
    rather than whenever Corptools last happened to sync — the whole point
    of a manual, on-demand check.
    """
    if prefer_corptools:
        journal = _get_corptools_journal(config)
        if journal is not None:
            logger.debug(
                f'Treasury {config.corporation.corporation_name} (division {config.wallet_division}): '
                f'{len(journal)} journal entries from Corptools'
            )
            return _match_payments_against_journal(
                journal, year, month, open_records,
                source_label=f'{config.corporation.corporation_name} via Corptools',
            )

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
        logger.debug(f'Treasury {config.corporation.corporation_name} (division {config.wallet_division}): {len(journal)} journal entries retrieved via ESI')
    except Exception as e:
        logger.warning(f'Treasury {config.corporation.corporation_name}: wallet journal request failed: {e}')
        return 0

    return _match_payments_against_journal(
        journal, year, month, open_records,
        source_label=config.corporation.corporation_name,
    )


def check_corp_payments(year, month, prefer_corptools=True):
    """
    Checks the wallet journals of ALL active treasury configs for incoming
    payments and matches them against open AllianceBillingRecord entries
    using the exact per-corp code "{corp_id}/{month}/{year}" in the reason
    field, plus amount + sender corp.
    A billing record already matched in one treasury is not checked again
    in another.

    prefer_corptools is passed straight through to _check_payments_for_treasury
    — see there for what it changes. The daily automatic sync leaves it at the
    default (True): payment codes only reveal from the 2nd of the month, so
    there's no urgency that would make Corptools' own sync lag matter. The
    manual "Check Payments Now" button passes False, since asking for that
    explicitly IS the urgency.
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

        matched = _check_payments_for_treasury(config, year, month, still_open, prefer_corptools=prefer_corptools)
        total_matched += matched

    logger.info(f'Payment check for {month:02d}/{year} complete: {total_matched}/{open_count} corp(s) marked as paid')
    return total_matched