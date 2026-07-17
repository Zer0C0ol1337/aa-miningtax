# Mining Tax — Alliance Auth Plugin

A Django app for Alliance Auth to manage EVE Online mining tax billing across alliance corporations.

## Features

- **Personal mining dashboard** — this month's ledger entries with calculated tax
- **Alliance-wide billing overview** — all corps, all members (grouped by main character), tax by ore category, moon rental fees, and total due
- **Configurable tax rates** per ore category (R4 / R8 / R16 / R32 / R64 / Ice / Ore / Gas / Mercoxit)
- **Refined-value pricing (optional)** — value ore by the market value of the minerals it reprocesses into (Janice split price × eveuniverse reprocessing recipes), instead of the manipulable raw ore price; falls back to ESI reference prices when disabled or unreachable
- **Sovereignty tax filter (optional)** — tax only mining that happens inside a corporation's current sovereignty systems, refreshed automatically from ESI's sovereignty map
- **Moon rentals** — corps renting a moon pay a flat monthly fee; mining there is tax-free for them
- **Tax-free event moons** — alliance moons can be marked tax-free, optionally scoped to a specific structure when several share one solar system
- **PDF invoices** — per-corp invoice or all corps as a ZIP
- **Corp Observer sync** — a director/CEO token pulls mining data for all moons/structures of a corp, covering members who never log in to Alliance Auth themselves
- **Corptools integration** — reads mining data directly from Corptools' DB when available (zero extra ESI calls), falls back to its own ESI sync otherwise
- **Automatic payment verification** — checks a configured treasury corp's wallet journal for incoming tax payments (reason keyword + amount + sender corp) and marks invoices as paid automatically; the required payment reason is shown with a one-click copy button
- **Manual override** — mark/unmark an invoice as paid at any time
- **CEO auto-access** — a corp's CEO automatically sees their own corp's billing (read-only view, restricted to their corp) without needing a permission assigned; full Settings and alliance-wide actions still require the `mining_officer` permission
- **Background sync** — manual sync and payment checks run as Celery tasks, avoiding request timeouts on large datasets
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
| `django-eveuniverse` | Optional | Required only for **refined-value pricing** — supplies reprocessing recipes (`EveTypeMaterial`). Not needed if you price by ESI reference price |
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

### 3b. (Optional) Enable refined-value pricing

Skip this if you want to price ore by ESI reference price. To value ore by its
reprocessed mineral value instead:

```bash
pip install django-eveuniverse
```

Add eveuniverse to `INSTALLED_APPS` and enable type-material loading in `local.py`:

```python
INSTALLED_APPS += [
    'eveuniverse',
]
EVEUNIVERSE_LOAD_TYPE_MATERIALS = True
```

Then migrate and load reprocessing recipes:

```bash
python manage.py migrate
python manage.py populate_ore_reprocessing
```

Finally, in **Settings → Pricing**, tick "Enable Janice pricing" and enter a
Janice API key (request one via the Janice Discord). The key is stored
server-side and never shown to members. Reprocessing efficiency uses Janice
defaults (Ore 0.9063, Gas 0.95), and only whole reprocessing portions are taxed
— any non-divisible remainder is left untaxed.

If Janice is left disabled or becomes unreachable, pricing automatically falls
back to ESI reference prices.

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

### 5. Celery worker required

"Sync Now" and "Check Payments Now" run as background Celery tasks, so a Celery worker must be running for them to complete (they queue instantly but need the worker to process). This is normally already running as part of a standard Alliance Auth deployment.

(The `populate_ore_reprocessing` command runs synchronously and does **not** require a Celery worker.)

### 6. Restart

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
| Mining Officer | `miningtax.mining_officer` | Alliance-wide billing + settings + payment checks |

Superusers (`is_staff`/`is_superuser`) always have full access regardless of assigned permissions.

**CEO auto-access:** a corp's CEO (per `EveCorporationInfo.ceo_id`) automatically gets read access to their own corp's billing and can mark their own corp paid/unpaid, without needing `mining_officer` assigned. They do **not** get access to Settings or alliance-wide actions (Check Payments Now, editing tax rates/moons/treasury) — those still require the real permission.

**Recommended assignment:**
- All alliance members: `basic_access`
- Alliance Leader, Co-Leader, Mining Officer roles: `mining_officer`
- Corp CEOs: no assignment needed, access is automatic and scoped to their own corp

---

## Settings UI

Reachable at `/miningtax/settings/` (requires the real `mining_officer` permission or superuser — CEO auto-access does not reach this page). Six tabs:

### Tax Rates
Per-category tax rate, including a dedicated **Mercoxit** category. `0.00%` is a valid, deliberate value (e.g. tax-free event ores) and is respected as-is.

### Moon Rentals
A corp renting a moon pays a flat monthly fee; mining there becomes tax-free for that corp. **Structure Name** must exactly match the `solar_system_name` value seen in the ledger.

### Alliance Moons
Moons can be marked `Event` (tax-free) or `Public` (normally taxed). Matching is done via a case-insensitive substring check against the ledger's `solar_system_name`. An optional **Structure Name** narrows a tax-free moon to a single structure when several structures share the same solar system.

### Treasury
Configure which corporation's wallet is monitored for incoming tax payments, and the keyword that must appear in the wallet journal's reason field (e.g. "Corp Tax"). This keyword is also displayed with a copy button on the alliance overview page so members know what to put in the transfer description. A payment is matched when:
1. The wallet journal reason contains the configured keyword
2. The sending corporation matches an open billing record's corporation
3. The amount is at least the invoice's total due

Requires a director/accountant character of the treasury corp with the `esi-wallet.read_corporation_wallets.v1` scope (via Corptools' Corporation Audit, not Character Audit).

### Sovereignty
Add a `SovFilterConfig` for a corporation to tax only the mining that happens inside that corp's current sovereignty systems. The system list is refreshed from ESI's public sovereignty map — click "Sync Sovereignty Now" or let the daily sync update it. No manual system list to maintain.

### Pricing
Enable Janice refined-value pricing and store the API key (server-side, never shown to members). When enabled, ore is valued by its reprocessed mineral value instead of the raw ore price; when disabled or unreachable, pricing falls back to ESI reference prices. Requires the optional refined-value setup (step 3b).

---

## Refined-Value Pricing

When enabled (Settings → Pricing), ore is valued by the market value of the
minerals it reprocesses into, rather than the raw ore price:

1. Reprocessing recipes come from `django-eveuniverse` (`EveTypeMaterial`),
   loaded once via `populate_ore_reprocessing`.
2. Mineral prices are fetched live from the Janice API (Jita 4-4 split price).
3. Per-unit value = Σ(mineral qty × efficiency × mineral split price) ÷ portion
   size. Efficiency uses Janice defaults (Ore 0.9063, Gas 0.95).
4. Only whole reprocessing portions are taxed; a non-divisible remainder is left
   untaxed rather than valued at the thin, manipulable raw price.

The fallback chain is: refined value → the item's raw Janice split price (for
things without a recipe, e.g. gas) → ESI `adjusted_price`. Janice being disabled
or unreachable therefore never blocks billing — it simply falls back to ESI.

Re-run `populate_ore_reprocessing` whenever new ore types are added to
`OreCategory`.

---

## Sovereignty Tax Filter

When one or more `SovFilterConfig` entries are active, mining is only taxed if it
occurred inside a tracked corporation's current sovereignty systems. The list of
systems is rebuilt from ESI's public sovereignty map (`/sovereignty/map/`, no
token required) on every sync, so it always reflects live sovereignty — there is
no manual system list to maintain. Use the "Sync Sovereignty Now" button in
Settings to refresh it on demand.

If no `SovFilterConfig` is active, this filter does nothing and all mining is
taxed as normal.

---

## Corp Observer Sync

Using a director/CEO character's `esi-industry.read_corporation_mining.v1` token, the plugin pulls mining data for every observer (structure) of the corp, covering all members mining there whether or not they've ever logged in to Alliance Auth. Unknown characters are automatically registered in Alliance Auth via ESI.

Corp observer data always takes precedence over personal ledger data for the same character/date/ore — the ledger's unique key is `(character, date, type_id)`, so there's never a duplicate between the two sync paths.

---

## Automatic Payment Verification

When a `TreasuryConfig` is active, the daily sync (and the manual "Check Payments Now" button, run as a Celery task) reads the configured corp's wallet journal and automatically marks matching invoices as paid (`auto_verified=True`). Invoices with nothing due (`total_due <= 0`) are skipped entirely, so a stray matching transaction can't trivially mark a zero-tax invoice as paid.

Use "Reset to Unpaid" to correct a mismatch, e.g. after testing.

---

## Member Breakdown

The "Members" table on each corp card groups mining activity by **main character** — an alt's mining is combined into its main's total via Alliance Auth's `UserProfile.main_character`. A character with no registered main (or not registered in Alliance Auth at all) is listed under its own name as a fallback.

---

## URL Overview

| URL | View | Access |
|---|---|---|
| `/miningtax/` | Dashboard | `basic_access` (or CEO) |
| `/miningtax/sync/` | Manual sync (background task) | `basic_access` (or CEO) |
| `/miningtax/alliance/` | Alliance billing (full for officers, own-corp-only for CEOs) | `mining_officer` or CEO |
| `/miningtax/alliance/check-payments/` | Manual payment check (background task) | `mining_officer` only |
| `/miningtax/settings/` | Settings | `mining_officer` only |
| `/miningtax/settings/janice/save/` | Save Janice pricing config | `mining_officer` only |
| `/miningtax/settings/sov-filter/sync-now/` | Refresh sovereignty systems | `mining_officer` only |
| `/miningtax/pdf/corp/<id>/` | PDF per corp | `mining_officer` or CEO |
| `/miningtax/pdf/all/` | ZIP of all corps | `mining_officer` only |

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
- Django 5.2
- django-esi 9.x
- django-eveuniverse (optional, for refined-value pricing)
- allianceauth-corptools 3.x
- reportlab 4.x