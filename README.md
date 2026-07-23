# Mining Tax — Alliance Auth Plugin

A Django app for Alliance Auth to manage EVE Online mining tax billing across alliance corporations.

## Features

- **Personal mining dashboard** — this month's ledger entries with calculated tax
- **Alliance-wide billing overview** — all corps, all members (grouped by main character), tax by ore category, moon rental fees, and total due
- **Configurable tax rates** per ore category (R4 / R8 / R16 / R32 / R64 / Ice / Ore / Gas / Mercoxit), plus any category you define yourself
- **Complete ore list, maintained by ESI** — every mineable type is imported and classified by its EVE group, so a newly introduced ore is never taxed at the Default rate unnoticed. The Settings page reports how many mined types still lack a category
- **Category rules** — assign ore to a category by name, ahead of EVE's own grouping: abyssal ore and Prismaticite sit in ordinary asteroid groups yet warrant their own rate. Rules apply to ore that doesn't exist yet, as long as the name matches. A category can also be locked so the automatic import leaves it alone
- **Refined-value pricing (optional)** — value ore by the market value of the minerals it reprocesses into (Janice split price × eveuniverse reprocessing recipes), instead of the manipulable raw ore price; falls back to ESI reference prices when disabled or unreachable
- **Tax exemptions** — exempt a whole corporation, or a single player by their main character; alts on the same Alliance Auth account are covered automatically, including ones registered later. Exemptions can be paused instead of deleted
- **Moon rentals** — corps renting a moon pay a flat monthly fee; mining there is tax-free for them
- **Tax-free event moons** — alliance moons can be marked tax-free, optionally scoped to a specific structure when several share one solar system
- **Dropdown moon configuration** — solar system, moon and structure are picked from lists rather than typed. Moons come from ESI for the chosen system, structures from the corp's mining observers, so a typo can no longer quietly break exemption matching
- **Per-pilot detail view** — every ledger entry of a player for a month, split by character and by ore category. Officers reach it from the billing member list, members from their own dashboard for their own characters. Characters with no mining are listed too, which is how an alt that never synced becomes visible
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
| `django-eveuniverse` | Yes | Authoritative ore type IDs/names/categories (`populate_ore_categories`) and reprocessing recipes for refined-value pricing. Load asteroid types with `eveuniverse_load_types miningtax --category_id 25` |
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

### 3. Install eveuniverse and run migrations

`populate_ore_categories` derives ore data from eveuniverse, so install and load it first:

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

Then migrate, load asteroid types, and populate categories:

```bash
python manage.py migrate
python manage.py eveuniverse_load_types miningtax --category_id 25
python manage.py populate_ore_categories
python manage.py collectstatic --noinput
```

### 3b. (Optional) Enable refined-value pricing

To value ore by its reprocessed mineral value instead of the ESI reference price,
load reprocessing recipes:

```bash
python manage.py populate_ore_reprocessing
```

Then, in **Settings → Pricing**, tick "Enable Janice pricing" and enter a
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
Also imports the full ore list from ESI on demand, reports how many mined types still have no category, and lets you create a rate for a category that has none — those are billed at the Default rate until you do.

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

### Systems
Add a reference corporation to keep a live list of the systems it holds sovereignty in, refreshed from ESI's public sovereignty data by the daily sync or the button. That list fills the solar-system dropdowns used when configuring moons — it has **no** effect on taxation. A status card shows how many systems are cached and when they were last refreshed.

### Exemptions
Exempt a whole corporation, or a single player picked by main character. Pick the alliance to narrow the corp list, then the corp to narrow the main list, so a large alliance stays navigable. Leaving the main on "Whole corporation" exempts every member of the chosen corp; picking a main exempts that player and all their alts. Exemptions can be paused rather than deleted.

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

## Ore Categories

Every mineable type in EVE is imported from ESI and classified by its group:
`Exceptional Moon Asteroids` becomes R64, `Harvestable Cloud` becomes Gas, and
so on. Completeness follows from the data rather than from anyone remembering to
add an ore, and the import runs with the daily sync as well as on demand from
Settings.

Where EVE's grouping isn't the right answer for taxation, **category rules**
take precedence. A rule matches a substring of the ore or group name and assigns
a category — abyssal ore and Prismaticite ship as rules out of the box, since
both sit in ordinary asteroid groups yet warrant their own rate. Because rules
match by name, a variant that doesn't exist yet is categorised correctly the
first time it appears.

A category set by hand can be **locked**, which keeps the import from
reclassifying it. That matters for an ore deliberately parked in its own
category to be taxed at 0%: without the lock, the nightly import would put it
back and the tax would quietly return.

Categories in use without a rate of their own are listed as a warning in
Settings, because ore in them is billed at the Default rate with nothing else to
indicate it.

---

## Moon Configuration

System, moon and structure are chosen from dropdowns rather than typed, both
when adding a moon and when editing one.

**Systems** come from the sovereignty cache, so only your own space is offered
instead of all ~8000 systems in EVE. Configure a reference corporation on the
Systems tab first, otherwise the dropdown stays empty.

**Moons** are fetched from ESI for the chosen system and cached for 30 days —
the first pick of a system takes a moment, everything after that is instant.

**Structures** come from the corp's mining observers, which covers every moon
drill the corp owns rather than only the ones already seen in mining data. ESI
gates this behind an **in-game role** (Accountant or Director) on top of the
`esi-industry.read_corporation_mining.v1` scope, so it works only where a member
of that corp holds one. All available tokens for the corp are tried before
giving up, and the dropdown states which of the possible causes applies —
missing token, missing role, no structures, or an ESI error — rather than simply
showing nothing. Structure names already seen in mining data remain available
regardless.

---

## Two Sources, One Ledger

Mining reaches the plugin two ways, and both are needed for a complete picture.

**Personal ledgers** need a token from each pilot
(`esi-industry.read_character_mining.v1`) and cover everything they mined,
wherever it happened. **Corp observers** need one token from a corp member with
the appropriate in-game role and report what was mined at the corp's own
structures — by every pilot, including ones who never registered in Alliance
Auth.

Moon mining therefore arrives twice: once in the pilot's ledger against the
solar system, once per structure via the observer. The personal sync stores only
the difference between its day total and what the observers already account for,
so structure mining is counted once while belt and anomaly mining is kept
separately alongside it.

The practical consequence is worth knowing: a pilot **without** a personal token
still shows up, but only with moon mining, since that is reported by the
structure rather than by them. Their dashboard names the affected characters and
links to Alliance Auth's add-character flow.

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

Clicking a player opens their **detail view**: every ledger entry for the month,
split by character and by ore category. It covers the whole account rather than
one character, since tax is assessed per player, and lists characters with no
mining as well — which is how an alt whose ledger never synced becomes visible
instead of quietly missing. Members reach the same page from their own dashboard
by clicking one of their characters; access is decided per character, so anyone
may open their own, officers may open anyone, and CEOs stay limited to their own
corporation.

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
| `/miningtax/alliance/pilot/<character_id>/` | Per-pilot detail (own characters, or anyone as officer) | `basic_access` (own) / `mining_officer` |
| `/miningtax/settings/sov-filter/sync-now/` | Refresh the known systems list | `mining_officer` only |
| `/miningtax/settings/ore-categories/sync/` | Import the ore list from ESI | `mining_officer` only |
| `/miningtax/settings/taxrate/add/` | Create a rate for a category | `mining_officer` only |
| `/miningtax/settings/exemption/add/` | Add a tax exemption | `mining_officer` only |
| `/miningtax/api/moons/` | Moons of a system (JSON, for the dropdowns) | `mining_officer` only |
| `/miningtax/api/structures/` | Structures of a corp (JSON, for the dropdowns) | `mining_officer` only |
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