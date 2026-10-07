import logging
import re
import unicodedata
from datetime import date

from .models import TreasuryConfig, AllianceBillingRecord

logger = logging.getLogger(__name__)


# Payment codes carry the marker MT from 10/2026 on: MT-98806948-10-2026.
# The bare form corp_id/month/year was also used by another tool, so a transfer
# meant for that tool could mark a mining tax invoice as paid (and the other way
# round). Hyphens instead of slashes, so the new code doesn't even contain the
# old pattern as a substring. Months before 10/2026 keep their bare code: it was
# already released, and corps may have paid — or still pay — with it.
CODE_MARKER = 'MT'
MARKED_CODE_FROM = (2026, 10)

# Bare code: corp_id / month / year — digits only, optional spaces around the slashes.
_LEGACY_CODE_RE = re.compile(r'^\s*(\d+)\s*/\s*(\d{1,2})\s*/\s*(\d{4})\s*$')
# Marked code: MT-corp_id-month-year — marker in any case, "-" or "/"
# between the parts, optional spaces around them.
_MARKED_CODE_RE = re.compile(
    r'^\s*' + CODE_MARKER + r'\s*[-/]\s*(\d+)\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{4})\s*$',
    re.IGNORECASE,
)


def uses_marked_code(year, month):
    """True when the payment code of this month carries the MT marker."""
    return (int(year), int(month)) >= MARKED_CODE_FROM


def parse_payment_code(reason, marked=True):
    """
    Reads a transfer reason as a payment code and returns (corp_id, month,
    year) as numbers, or None if the reason isn't one.

    `marked` picks the format: True for MT-corp-month-year (months from
    10/2026), False for the bare corp/month/year of earlier months. Only the
    one format is accepted, so a bare code never pays a month that uses the
    marked one — that is what keeps another tool's identical codes out.

    Compared as numbers rather than as text, so the way a pilot happens to
    type it doesn't decide whether a payment is recognised: "9" and "09" are
    the same month, spaces around the separators don't matter, the marker may
    be written in any case, and full-width characters from Chinese input
    methods ("／", "９", "ＭＴ") are turned into normal ones first.
    Anything else in the reason — extra words, a second code — still means it
    isn't a code, so a payment is never matched by accident.
    """
    text = unicodedata.normalize('NFKC', reason or '')
    pattern = _MARKED_CODE_RE if marked else _LEGACY_CODE_RE
    match = pattern.match(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def payment_code_for(corp_id, month, year):
    """
    Builds the expected wallet transfer reason code for a corp/month/year:
    "MT-98606304-10-2026" from 10/2026 on, the bare "98606304/09/2026"
    before. Members put this exact string in the reason field when
    transferring their tax payment, and the payment check matches on it —
    unique per corp per month, no manual keyword needed.
    """
    if uses_marked_code(year, month):
        return f"{CODE_MARKER}-{corp_id}-{month:02d}-{year}"
    return f"{corp_id}/{month:02d}/{year}"


def _get_treasury_token_for_config(config):
    """
    Gets a valid token with the esi-wallet.read_corporation_wallets.v1 scope
    for a specific treasury corp — used by the ESI fallback when Corptools
    doesn't audit the treasury corp.
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


def _get_corptools_journal(config, year, month):
    """
    The treasury corp's wallet journal for one division, from Corptools.

    The primary source; returns None when Corptools isn't installed, hasn't
    audited the treasury corp, or has never synced the configured division —
    any of which means "ask ESI instead". A division it knows but with no
    matching entries is a real answer and returns [].

    Only entries from the first day of the billed month onward are read: a
    payment for August cannot have arrived before August began, and without
    that floor every check loaded the division's entire history.

    Corptools syncs this on its own schedule; since a payment code only
    reveals on the 2nd of the following month, that lag never matters —
    for the automatic daily check or the manual button alike.
    """
    try:
        from corptools.models import CorporationAudit
        from corptools.models.wallets import (
            CorporationWalletDivision, CorporationWalletJournalEntry,
        )
    except ImportError:
        return None

    audit = CorporationAudit.objects.filter(
        corporation__corporation_id=config.corporation.corporation_id
    ).first()
    if not audit:
        return None

    if not CorporationWalletDivision.objects.filter(
        corporation=audit, division=config.wallet_division
    ).exists():
        return None

    return list(
        CorporationWalletJournalEntry.objects.filter(
            division__corporation=audit,
            division__division=config.wallet_division,
            date__date__gte=date(year, month, 1),
        )
    )


def _match_payments_against_journal(journal, year, month, open_records, source_label):
    """
    The actual matching logic, shared between the Corptools path and the ESI
    path — both hand this the same shape of journal entries (objects with
    .reason and .amount), just sourced differently, so the
    matching rules only need to exist once.

    Matches require the journal reason to be exactly the per-corp code of the
    month — "MT-{corp_id}-{month}-{year}" from 10/2026 on, the bare
    "{corp_id}/{month}/{year}" before — read as numbers by
    parse_payment_code(), so "9" and "09" or a full-width slash don't make a
    correct payment fail; never just a substring, so one corp's code can't be
    found inside another string. The amount must be at least what is due —
    more is fine.

    Who sends the transfer doesn't matter: the code alone names the corp and
    the month, so a CEO paying from his own character, or a member paying for
    the corp, is recognised just like a transfer from the corp wallet.
    """
    from django.utils import timezone
    from decimal import Decimal

    matched = 0
    # The format is a property of the month, so it is decided once here.
    marked = uses_marked_code(year, month)

    for record in open_records:
        paying_corp_id = record.corporation.corporation_id
        paying_corp_name = record.corporation.corporation_name
        expected_code = payment_code_for(paying_corp_id, month, year)
        expected = (int(paying_corp_id), int(month), int(year))

        found = False
        for entry in journal:
            reason = getattr(entry, 'reason', '') or ''
            # Compared as Decimal on both sides, never mixed with float:
            # Corptools' own amount field is already a Decimal, ESI's is a
            # float — converting record.total_due (a Decimal) to float to
            # compare against it can introduce a rounding error of a
            # fraction of an ISK, which was enough to make an exact match
            # wrongly fail once Corptools' Decimal amounts were compared
            # this way (verified against a real case before shipping this).
            amount = Decimal(str(getattr(entry, 'amount', 0) or 0))

            if parse_payment_code(reason, marked) != expected:
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


def _check_payments_for_treasury(config, year, month, open_records):
    """
    Checks ONE treasury corp's wallet journal against the given open billing
    records: Corptools' synced journal first, a live ESI call only when
    Corptools has no data for this corp/division.
    """
    journal = _get_corptools_journal(config, year, month)
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
        logger.debug(
            f'Treasury {config.corporation.corporation_name} (division {config.wallet_division}): '
            f'{len(journal)} journal entries via ESI'
        )
    except Exception as e:
        logger.warning(f'Treasury {config.corporation.corporation_name}: wallet journal request failed: {e}')
        return 0

    return _match_payments_against_journal(
        journal, year, month, open_records,
        source_label=f'{config.corporation.corporation_name} via ESI',
    )


def check_corp_payments(year, month):
    """
    Checks the wallet journals of ALL active treasury configs for incoming
    payments and matches them against open AllianceBillingRecord entries
    using the exact per-corp code "{corp_id}/{month}/{year}" in the reason
    field and an amount of at least what is due, whoever sent it.
    A billing record already matched in one treasury is not checked again
    in another.
    """
    configs = TreasuryConfig.objects.filter(active=True).select_related('corporation')

    if not configs.exists():
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
        total_matched += _check_payments_for_treasury(config, year, month, still_open)

    logger.info(f'Payment check for {month:02d}/{year} complete: {total_matched}/{open_count} corp(s) marked as paid')
    return total_matched


def months_with_open_invoices(now=None):
    """
    Every month whose payment code is already out and that still has unpaid
    invoices with something due — the months a payment can actually arrive
    for. The running month is never among them: nobody can pay it yet.
    """
    from .auth_hooks import last_issued_month

    last = last_issued_month(now)
    months = set(
        AllianceBillingRecord.objects.filter(paid=False, total_due__gt=0)
        .values_list('year', 'month')
    )
    return sorted(m for m in months if m <= last)


def check_open_payments(now=None):
    """
    The nightly payment check: check_corp_payments() for every month in
    months_with_open_invoices(). It used to check only the running month —
    the one month whose invoices can't have been paid yet — so payments for
    the months actually being paid were only ever found by pressing
    Check Payments Now on that month's page.
    """
    matched = 0
    for year, month in months_with_open_invoices(now):
        matched += check_corp_payments(year, month)
    return matched