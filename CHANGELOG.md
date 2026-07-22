# Changelog


## [0.9.0] - 2026-07-21

Tax exemptions for players and corporations, dropdown-driven moon configuration,
and removal of location-based taxation.

### Added
- **Tax exemptions.** New `TaxExemption` model plus an *Exemptions* tab in Settings, letting officers exempt an entire corporation or a single player from mining tax (migration `0013_taxexemption`). Exemptions are granted per **main character** and automatically cover every alt that main owns in Auth — resolved live via `CharacterOwnership`, so an alt registered later is covered without any extra work. Exemptions are evaluated before ore categories, fleet sessions and moon settings, so they always take precedence
- Exemptions can be **paused** instead of deleted, for arrangements that only apply temporarily (events, trial periods)
- **Cascading pickers throughout the exemption form**: alliance narrows the corporation list, which in turn narrows the main-character list — an alliance with thousands of pilots stays navigable
- **Moon configuration by dropdown instead of free text.** Solar system, moon and structure are now picked from lists on the *Alliance Moons* and *Moon Rentals* tabs (add form and edit dialog alike). Moons are fetched from ESI for the chosen system via a new JSON endpoint (`api_views.py`) and cached for 30 days, so a typo can no longer silently break tax-exemption matching
- **Known Systems status card** on the *Systems* tab showing how many systems are cached and when they were last refreshed, so the state can be diagnosed from the UI without shell access on a live server

### Changed
- **Location no longer affects taxation.** The sovereignty tax filter has been removed: all mining is taxed regardless of where it took place. The sovereignty *system list* is kept and still syncs automatically, but now serves solely as the data source for the solar-system dropdowns. The former *Sovereignty* tab is now *Systems*, and its "Active" flag is gone
- Sovereignty sync now runs for **every** configured reference corporation instead of only active ones, so the system list is available even though it no longer influences billing

### Fixed
- **The sovereignty filter never actually worked.** `SovSystem` was populated and displayed, but no code path ever consulted it during tax calculation — mining outside the tracked space was taxed regardless of configuration. Rather than fix it, the feature was removed, matching how it was being used in practice
- Multi-line `{# ... #}` comment in `settings.html` rendered as visible text on every tab. Django's hash-comment syntax is single-line only; converted to a `{% comment %}` block


## [0.8.1] - 2026-07-17

Bugfix release: correct ore type IDs, names, and tax categories.

### Fixed
- **Ore tax categories and type IDs were systematically wrong.** The previous hand-maintained `populate_ore_categories` list contained many incorrect type IDs — base ores were swapped (e.g. Veldspar/Scordite), moon ores were on the wrong R-tier (e.g. Loparite as R32 instead of R64, Cobaltite as R4 instead of R8), and several IDs pointed at ship SKINs or blueprints entirely. Because tax category is resolved by type ID, this could mis-tax affected ore. `populate_ore_categories` now derives categories directly from eveuniverse asteroid groups (authoritative IDs straight from ESI), covering every base ore, II/III/IV-Grade, Bountiful/Shining moon-ore tier, and Compressed ice variant automatically — so it can never drift out of sync again
- **Wrong ore names were baked into ledger entries.** Ledger sync took the ore name from the (previously mis-seeded) `OreCategory` table first, so entries were stored with wrong names (and some as `Type NNNN` placeholders). `_get_type_name_db_first` now prefers eveuniverse, the authoritative source, so future syncs store correct names
- Added a one-off `fix_ledger_names` management command to repair existing ledger entries' names from eveuniverse (loading any missing ore types from ESI on the fly). Names only — tax categories resolve by type ID and were corrected by the new `populate_ore_categories`

### Changed
- `populate_ore_categories` now **requires django-eveuniverse** with asteroid types loaded (`eveuniverse_load_types miningtax --category_id 25`). This makes ore data authoritative instead of hand-maintained

### Upgrade steps
On an existing install, after updating:
1. `python manage.py eveuniverse_load_types miningtax --category_id 25` (if not already loaded)
2. `python manage.py fix_ledger_names` (repair stored names; use `--dry-run` first to preview)
3. `python manage.py populate_ore_categories` (rebuild categories from eveuniverse)
4. Recompute prices: reset `price_per_unit`/`total_value` to 0 and run `update_market_prices()`, or wait for the daily sync


## [0.8.0] - 2026-07-17

Major release: refined-value pricing, sovereignty-based taxation, a dedicated
Mercoxit category, and structure-level tax-free moons.

### Added
- **Refined-value pricing (Janice + eveuniverse).** Ore can now be valued by the market value of the minerals it reprocesses into, rather than the raw ore price. Raw moon-ore prices (R32/R64) are thin and easily manipulated, whereas the mineral value is both higher and more stable. Mineral prices are pulled live from the Janice API (Jita 4-4 split price) and reprocessing recipes come from `django-eveuniverse`. Pricing uses a safe fallback chain — refined value first, then the item's raw Janice split price (for things without a recipe, e.g. gas), then ESI `adjusted_price` — so billing is never blocked if Janice is disabled or unreachable
- **Whole-portion billing for refined ore.** Ore only reprocesses in full batches (e.g. 100 units), so any remainder that doesn't fill a complete portion is left untaxed instead of being valued at the thin, manipulable raw price
- **Pricing** tab in Settings to enable Janice and store the API key (kept server-side, never shown to members)
- New `JaniceConfig` model, `JaniceConfigForm`, and the `settings_save_janice` view/URL (migration `0012_janiceconfig`)
- New `populate_ore_reprocessing` management command that loads reprocessing recipes synchronously (no Celery worker required)
- **Sovereignty tax filter.** New `SovFilterConfig` taxes only mining that happens inside a corporation's *current* sovereignty systems. The system list refreshes automatically from ESI's public sovereignty map (no manual list to maintain), backed by the new `SovSystem` cache and a "Sync Sovereignty Now" button in Settings (migration `0010_sovsystem_and_more`)
- **Mercoxit as its own tax category**, so it can carry a rate separate from generic "Ore". Appears in the Tax-Rates tab and in billing/PDF exports automatically
- **Structure-level tax-free moons.** `AllianceMoon` gained a `structure_name` field (migration `0011_alliancemoon_structure_name`), so a moon can be marked tax-free for a specific structure even when several structures share the same solar system; the field autocompletes from structure names already seen in the ledger

### Fixed
- **Mercoxit (and other belt/anomaly ore) was wrongly treated as tax-free.** The alliance-moon exclusion now only excludes structure-sourced entries (solar system ID above the structure threshold), so belt, anomaly, and Mercoxit mining is taxed correctly
- **Corporation dropdowns showed the wrong alliance.** The corp select now uses the real EVE alliance ID instead of Alliance Auth's internal primary key
- **Duplicate sovereignty notification** in Settings removed — the message is rendered once by the Alliance Auth base template instead of twice

### Changed
- **ESI market prices are now cached with ETag-aware fallback.** The `/markets/prices/` bulk call keeps respecting ESI ETags (consistent with 0.4.5 and the rest of the sync code): the stored ETag is sent, and a `304 Not Modified` is handled by serving the last successful price list from the Django cache (Redis) instead of raising or returning an empty list. The price list is refreshed only when ESI actually reports a change. This fixes ore being left unpriced when ESI returned 304, without disabling ETags

### Deployment notes
Refined-value pricing requires `django-eveuniverse`:
1. `pip install django-eveuniverse`
2. Add `eveuniverse` to `INSTALLED_APPS` and set `EVEUNIVERSE_LOAD_TYPE_MATERIALS = True` in `local.py`
3. `python manage.py migrate`
4. `python manage.py populate_ore_categories` (applies the Mercoxit category)
5. `python manage.py populate_ore_reprocessing` (loads reprocessing recipes)
6. In Settings → Pricing, enable Janice and enter an API key

If Janice is left disabled, the plugin falls back to ESI reference prices and behaves as before.


## [0.7.6] - 2026-07-14

### Added
- "Gas" added as a standard, editable tax rate category alongside R4/R8/R16/R32/R64/Ore/Ice, and as a selectable category for Alliance Moons
- Gas cloud materials (Cytoserocin, Mykoserocin, Fullerite, Tricarboxyl Vapor variants) are now automatically categorized as "Gas" by matching their ESI-reported name, rather than relying on a static list of type IDs


## [0.7.5] - 2026-07-14

### Added
- All standard tax rate categories (Default, Ore, Ice, R4, R8, R16, R32, R64) are now auto-created and guaranteed to appear in the Settings UI, not just "Default" — officers never need a superadmin/developer to add a missing category row


## [0.7.4] - 2026-07-14

### Added
- Alliance filter dropdown added before the corporation dropdown in Moon Rentals and Treasury settings — pick an alliance first to narrow down the corporation list instead of scrolling through every corp in the database


## [0.7.3] - 2026-07-14

### Added
- Personal dashboard now has month navigation (previous/next), matching the alliance overview
- "Default" tax rate (fallback for unrecognized ore categories) is now auto-created and editable in the Settings UI — officers no longer need a superadmin/developer to adjust it

## [0.7.2] - 2026-07-14

### Changed
- Payment code is now revealed from the 2nd of the following month onward (instead of immediately after month end), giving a one-day buffer for the final daily sync to settle the billed month's totals before the code is shown

## [0.7.1] - 2026-07-14

### Changed
- Payment matching now uses per-corp/month unique codes ("{corp_id}/{month}/{year}") instead of a free-text keyword, matched with exact reason equality instead of substring containment — eliminates any ambiguity between corps and removes the need to configure a keyword
- `TreasuryConfig.payment_reason_keyword` removed from the Settings form (field remains in the model for backward compatibility but is no longer used in matching)

## [0.7.0] - 2026-07-14

### Changed
- Reduced log verbosity: per-observer, per-journal-entry, and per-record detail moved from INFO to DEBUG level, so a full sync no longer floods the log for large alliances. Only sync summaries, newly auto-registered characters, detected payments, and warnings/errors remain at INFO
- Log level can be temporarily raised back to DEBUG in `local.py` for deep debugging without touching code

## [0.6.9] - 2026-07-14

### Added
- Member breakdown in the alliance overview now groups by main character — alts' mining rolls up into their main's total instead of listing every character separately

## [0.6.8] - 2026-07-14

### Added
- CEOs viewing the alliance overview now see only their own corporation, with a banner explaining the restriction
- Payment reason keyword shown at the top of the alliance overview with a one-click copy button (superseded in 0.7.1 by per-corp codes)

### Changed
- Settings page and alliance-wide actions (Check Payments Now, tax rates, moon rentals, alliance moons, treasury config) now require the real `mining_officer` permission or superuser status — CEO auto-access no longer grants these, only the billing view for their own corp
- `mark_paid` / `mark_unpaid` now verify a CEO-only user is acting on their own corporation before allowing the action

## [0.6.7] - 2026-07-14

### Added
- CEOs automatically get officer-level billing access via `EveCorporationInfo.ceo_id`, without needing the `mining_officer` permission assigned manually

### Changed
- Permission label changed from "Can manage Mining Tax" to "Can access Alliance Billing and Settings (Mining Officer)" for clarity in the admin

## [0.6.6] - 2026-07-14

### Changed
- "Sync Now" and "Check Payments Now" now dispatch as background Celery tasks instead of running synchronously in the request, avoiding timeouts on large datasets (many characters, many corp observers, 304-handling)

## [0.6.5] - 2026-07-14

### Fixed
- Explicit `related_name` on `MoonRental.corporation` and `TreasuryConfig.corporation` to avoid a reverse accessor clash with Alliance Auth's built-in `moons` app, which also defines a `MoonRental` model

## [0.6.4] - 2026-07-14

### Added
- Navigation buttons (My Ledger, Alliance Billing) on the Settings page

## [0.6.3] - 2026-07-14

### Fixed
- "Due" amount on the alliance overview now calculated live (tax + rental) instead of using a stale stored `total_due`, unless the invoice is already paid

## [0.6.2] - 2026-07-14

### Added
- Moon rental fee shown as its own line item in the alliance billing overview
- New "Billing Summary" table per corp: Mining Tax + Moon Rental + Total Due
- Corps with only an active moon rental (no mining that month) now appear in the overview too

## [0.6.1] - 2026-07-14

### Fixed
- Payment check now skips billing records with `total_due <= 0`, preventing a coincidental wallet transaction from marking a zero-tax invoice as paid

## [0.6.0] - 2026-07-14

### Changed
- English is now the default language throughout: all Python code (messages, logs, PDF, help text). Templates already used translatable strings; German remains available as a full translation

## [0.5.2] - 2026-07-14

### Added
- Central exception logging in the `check_access` decorator with full traceback; form validation errors now logged as warnings

## [0.5.1] - 2026-07-14

### Added
- All admin actions (mark paid/unpaid, settings changes, manual sync) logged with the acting username

## [0.5.0] - 2026-07-14

### Added
- `TreasuryConfig` model: configure which corp's wallet is monitored for incoming tax payments
- Automatic payment verification: matches wallet journal entries (reason keyword + amount + sender corp) against open billing records
- "Check Payments Now" button plus automatic daily check
- "Reset to Unpaid" button for manual corrections
- Extensive logging throughout `payments.py` for debugging without shell access

## [0.4.7] - 2026-07-13

### Fixed
- `unique_together` changed to `(character, date, type_id)` only, permanently eliminating duplicate entries between the personal ledger sync and the corp observer sync. Corp observer data always takes precedence.

## [0.4.6] - 2026-07-13

### Changed
- ISK amounts displayed in European format, rounded up, no decimals (matches in-game transfer precision)

## [0.4.5] - 2026-07-13

### Changed
- ESI ETags are now respected — removed the cache-clearing workaround; `304 Not Modified` responses correctly fall back to existing DB data instead of being treated as zero results

## [0.4.4] - 2026-07-13

### Added
- Improved logging throughout the sync pipeline; clearer error messages when a director token or corp data is missing

## [0.4.3] - 2026-07-13

### Removed
- `no_access` permission and all Django auto-generated per-model permissions — only `basic_access` and `mining_officer` remain

## [0.4.2] - 2026-07-13

### Fixed
- Superusers now have full access without needing explicit permissions assigned

## [0.4.1] - 2026-07-13

### Added
- `no_access` permission (later removed in 0.4.3 as redundant)

## [0.4.0] - 2026-07-13

### Added
- `General` model permissions cleaned up to appear only as "Miningtax | general | ..." in the admin
- `populate_ore_categories` management command
- Month navigation moved from the template into the view
- Billing records automatically kept up to date by the daily sync

## [0.3.x] - 2026-07-13

### Added
- Corp Observer sync via director/CEO token — pulls mining data for all moons/structures of a corp
- Unknown characters automatically registered in Alliance Auth via ESI
- Manual sync button also triggers the corp observer sync
- Logging replaces all `print()` calls

## [0.2.x] - 2026-07-01

### Added
- "Mark as Paid" per corp, stored in `AllianceBillingRecord`
- Corptools integration: reads mining data directly from Corptools' DB when available
- Bulk market price fetch via `/markets/prices/`

## [0.1.0] - 2026-07-01

### Added
- Personal mining dashboard
- Alliance-wide billing overview by corp
- Tax rates per ore category (R4/R8/R16/R32/R64/Ice/Ore)
- Moon rentals
- Tax-free event moons
- PDF export per corp + ZIP of all corps
- Permissions: basic_access, mining_officer, admin_access
- Settings web UI