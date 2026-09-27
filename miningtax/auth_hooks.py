import logging
from datetime import datetime, timezone as dt_timezone

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from . import urls

logger = logging.getLogger(__name__)

# The menu is drawn on every page load for every user, so the badge count is
# held briefly. Any change to a billing record clears it straight away (see
# signals.py); this only bounds how long something no signal covers — the
# payment-code reveal time passing — takes to show.
BADGE_CACHE_SECONDS = 300
BADGE_ALLIANCE_KEY = 'miningtax:menu_badge:alliance'


def badge_corp_key(corp_id):
    """Cache key for one corporation's open-invoice count."""
    return f'miningtax:menu_badge:corp:{corp_id}'


def clear_badge_cache(corp_id=None):
    """
    Drops the cached badge counts affected by a change to one corporation's
    invoice: that corp's own count, and the alliance-wide count officers see.
    """
    keys = [BADGE_ALLIANCE_KEY]
    if corp_id:
        keys.append(badge_corp_key(corp_id))
    cache.delete_many(keys)


def last_issued_month(now=None):
    """
    (year, month) of the newest invoice corps can already pay: the most recent
    closed month whose payment code has been revealed (the day and UTC hour set
    under Payment Code Timing). Before that moment a corp has no code to pay
    with, so an invoice isn't counted as open yet — and the running month never
    is, or the badge would be lit all the time.
    """
    from .models import PaymentCodeSettings

    now = now or timezone.now()
    cfg = PaymentCodeSettings.get_solo()
    prev_year, prev_month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    reveal = datetime(now.year, now.month, cfg.reveal_day, cfg.reveal_hour_utc, tzinfo=dt_timezone.utc)
    if now >= reveal:
        return prev_year, prev_month
    return (prev_year, prev_month - 1) if prev_month > 1 else (prev_year - 1, 12)


def open_invoice_count(corp_id=None, now=None):
    """
    Unpaid invoices that are already issued and still have something due.

    corp_id limits the count to one corporation; None counts the whole
    alliance. Corporations outside the taxable scope are left out either way:
    the billing page hides them as well, and one that has left the alliance
    would otherwise keep the badge lit for good.

    Months before the start month set under Payment Code Timing don't count
    either — invoices from before billing was actually enforced, which stay on
    the billing pages untouched.
    """
    from .billing import is_corp_outside_taxable_scope
    from .models import AllianceBillingRecord, PaymentCodeSettings

    last_year, last_month = last_issued_month(now)
    start = PaymentCodeSettings.get_solo().open_invoices_from()
    records = AllianceBillingRecord.objects.filter(paid=False, total_due__gt=0)
    if corp_id is not None:
        records = records.filter(corporation__corporation_id=corp_id)

    count = 0
    for year, month, due, cid, cname in records.values_list(
        'year', 'month', 'total_due',
        'corporation__corporation_id', 'corporation__corporation_name',
    ):
        if (year, month) > (last_year, last_month):
            continue
        if start and (year, month) < start:
            continue
        if not due or due <= 0:
            continue
        if corp_id is not None and cid != corp_id:
            continue
        if is_corp_outside_taxable_scope(cid, cname):
            continue
        count += 1
    return count


def badge_count_for(user):
    """
    The number shown next to the menu entry for this user, or None for none.
    Mining officers (and superusers) see every open invoice of the alliance;
    corp_billing holders see their own corporation's; everyone else none.
    """
    from .views import has_full_officer_access, is_corp_scoped, own_corporation_id

    if has_full_officer_access(user):
        key, corp_id = BADGE_ALLIANCE_KEY, None
    elif is_corp_scoped(user):
        corp_id = own_corporation_id(user)
        if corp_id is None:
            return None
        key = badge_corp_key(corp_id)
    else:
        return None

    count = cache.get(key)
    if count is None:
        count = open_invoice_count(corp_id)
        cache.set(key, count, BADGE_CACHE_SECONDS)
    return count or None


# Single sidebar entry, leading to the personal dashboard. Billing and Settings
# are reached through the buttons inside the tool. Carries a badge with the
# number of open invoices for officers and corp billing holders.
class MiningTaxMenuItem(MenuItemHook):
    def __init__(self):
        MenuItemHook.__init__(
            self,
            _('Mining Tax'),
            'fas fa-cubes fa-fw',
            'miningtax:dashboard',
            navactive=['miningtax:']
        )

    def render(self, request):
        # Who sees the entry is decided by the basic_access permission alone,
        # as before — the badge only adds a number to an entry already shown.
        user = request.user
        if not (user.is_authenticated and user.has_perm('miningtax.basic_access')):
            return ''

        # Alliance Auth draws the badge when count is set. A failure here must
        # never take the menu down with it, so it just means no badge.
        try:
            self.count = badge_count_for(user)
        except Exception as e:
            logger.warning(f'Menu badge count failed for {user.username}: {e}')
            self.count = None
        return MenuItemHook.render(self, request)


@hooks.register('menu_item_hook')
def register_menu():
    return MiningTaxMenuItem()


# Mounts our urls.py under /miningtax/
@hooks.register('url_hook')
def register_urls():
    return UrlHook(urls, 'miningtax', r'^miningtax/')