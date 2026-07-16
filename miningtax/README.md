# Mining Tax — Alliance Auth Plugin

A Django app for Alliance Auth to manage EVE Online mining tax billing across alliance corporations.

## Features

- **Personal mining dashboard** — this month's ledger entries with calculated tax
- **Alliance-wide billing overview** — all corps, all members, tax by ore category, moon rental fees, and total due
- **Configurable tax rates** per ore category (R4 / R8 / R16 / R32 / R64 / Ice / Ore)
- **Moon rentals** — corps renting a moon pay a flat monthly fee; mining there is tax-free for them
- **Tax-free event moons** — alliance moons can be marked tax-free
- **PDF invoices** — per-corp invoice or all corps as a ZIP
- **Corp Observer sync** — a director/CEO token pulls mining data for all moons/structures of a corp, covering members who never log in to Alliance Auth themselves
- **Corptools integration** — reads mining data directly from Corptools' DB when available (zero extra ESI calls), falls back to its own ESI sync otherwise
- **Automatic payment verification** — checks a configured treasury corp's wallet journal for incoming tax payments (reason keyword + amount + sender corp) and marks invoices as paid automatically
- **Manual override** — mark/unmark an invoice as paid at any time
- **Permissions** — `basic_access` (dashboard), `mining_officer` (billing + settings); superusers always have full access
- **i18n** — English by default; German fully translated; 6 more languages scaffolded
- **Extensive logging** — every sync, payment check, and admin action is logged with context, no shell debugging required

---

## Dependencies

| Package | Required | Notes |
|---|---|---|
| `allianceauth` | Yes | Core framework |
| `django-esi` | Yes | ESI access (fallback sync + market prices + wallet journal) |
| `reportlab` | Yes | PDF export |
| `allianceauth-corptools` | Recommended | Mining data from DB instead of ESI — significantly fewer API calls; required for the automatic payment check (corp wallet scope) |

Without Corptools the plugin falls back to its own ESI sync for mining data. The **automatic payment verification** feature specifically requires a director/accountant character of the treasury corp logged in via Corptools' Corporation Audit flow with the `esi-wallet.read_corporation_wallets.v1` scope — Character Audit alone does not grant this scope.

---

## Installation

### 1. Install the package

```bash
pip install git+https://github.com/Zer0C0ol1337/aa-miningtax.git
```

### 2. Add to `INSTALLED_APPS` in `local.py`

```python
INSTALLED_APPS += [
    'miningtax',
]
```

### 3. Run migrations

```bash
python manage.py migrate miningtax
python manage.py populate_ore_categories
python manage.py collectstatic --noinput
```

### 4. Set up logging (recommended)

```python
LOGGING['handlers']['miningtax_file'] = {
    'level': 'INFO',
    'class': 'logging.handlers.RotatingFileHandler',
    'filename': os.path.join(BASE_DIR, 'log', 'miningtax.log'),
    'formatter': 'verbose',
    'maxBytes': 5242880,
    'backupCount': 5,
}
LOGGING['loggers']['miningtax'] = {
    'handlers': ['console', 'miningtax_file'],
    'level': 'INFO',
    'propagate': False,
}
```

Every sync, payment match/miss, and admin action (mark paid/unpaid, settings changes) is logged to `log/miningtax.log` with the acting username — check this file first when something doesn't look right.

### 5. Restart

```bash
sudo supervisorctl restart myauth:
# or: python manage.py runserver
```

---

## Permissions

Assign in the Alliance Auth Admin under **Authentication → Users** or via groups.

| Permission | Codename | Access |
|---|---|---|
| Basic access | `miningtax.basic_access` | Personal mining dashboard |
| Mining Officer | `miningtax.mining_officer` | Alliance billing + settings + payment checks |

Superusers (`is_staff`/`is_superuser`) always have full access regardless of assigned permissions.

**Recommended assignment:**
- All alliance members: `basic_access`
- Alliance Leader, Co-Leader, Mining Officer roles: `mining_officer`

---

## Settings UI

Reachable at `/miningtax/settings/` (officer access only). Four tabs:

### Tax Rates
Per-category tax rate. `0.00%` is a valid, deliberate value (e.g. tax-free event ores) and is respected as-is.

### Moon Rentals
A corp renting a moon pays a flat monthly fee; mining there becomes tax-free for that corp. **Structure Name** must exactly match the `solar_system_name` value seen in the ledger.

### Alliance Moons
Moons can be marked `Event` (tax-free) or `Public` (normally taxed). Matching is done via a case-insensitive substring check against the ledger's `solar_system_name`.

### Treasury
Configure which corporation's wallet is monitored for incoming tax payments, and the keyword that must appear in the wallet journal's reason field (e.g. "Corp Tax"). A payment is matched when:
1. The wallet journal reason contains the configured keyword
2. The sending corporation matches an open billing record's corporation
3. The amount is at least the invoice's total due

Requires a director/accountant character of the treasury corp with the `esi-wallet.read_corporation_wallets.v1` scope (via Corptools' Corporation Audit, not Character Audit).

---

## Corp Observer Sync

Using a director/CEO character's `esi-industry.read_corporation_mining.v1` token, the plugin pulls mining data for every observer (structure) of the corp, covering all members mining there whether or not they've ever logged in to Alliance Auth. Unknown characters are automatically registered in Alliance Auth via ESI.

Corp observer data always takes precedence over personal ledger data for the same character/date/ore — the ledger's unique key is `(character, date, type_id)`, so there's never a duplicate between the two sync paths.

---

## Automatic Payment Verification

When a `TreasuryConfig` is active, the daily sync (and the manual "Check Payments Now" button) reads the configured corp's wallet journal and automatically marks matching invoices as paid (`auto_verified=True`). Invoices with nothing due (`total_due <= 0`) are skipped entirely, so a stray matching transaction can't trivially mark a zero-tax invoice as paid.

Use "Reset to Unpaid" to correct a mismatch, e.g. after testing.

---

## URL Overview

| URL | View | Access |
|---|---|---|
| `/miningtax/` | Dashboard | `basic_access` |
| `/miningtax/sync/` | Manual sync | `basic_access` |
| `/miningtax/alliance/` | Alliance billing | `mining_officer` |
| `/miningtax/alliance/check-payments/` | Manual payment check | `mining_officer` |
| `/miningtax/settings/` | Settings | `mining_officer` |
| `/miningtax/pdf/corp/<id>/` | PDF per corp | `mining_officer` |
| `/miningtax/pdf/all/` | ZIP of all corps | `mining_officer` |

---

## Celery Beat

For daily automatic sync in `local.py`:

```python
from celery.schedules import crontab

CELERYBEAT_SCHEDULE['miningtax_daily_sync'] = {
    'task': 'miningtax.tasks.daily_mining_sync',
    'schedule': crontab(hour=2, minute=0),
}
```

This runs: personal ledger sync, corp observer sync, market prices, billing record updates, payment check, all in one pass.

---

## Development

Tested with:
- Alliance Auth 5.2.0
- Django 4.2
- django-esi 9.5.0
- allianceauth-corptools 3.x
- reportlab 4.x