# Changelog


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