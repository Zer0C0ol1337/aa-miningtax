# Changelog


## [0.10.9] - 2026-07-24

### Changed
- **Out-of-scope corporations no longer appear on the billing page at all.** Setting a scope zeroed their tax but left them listed with whatever they had mined, so NPC corporations and corps that had never been part of the alliance still sat among the invoices — correct figures that read as a bug every time someone scrolled past, prompting the same question each month. Their entries are left out of the alliance's books entirely now. Exempt corporations are the opposite case and stay visible: they are members, and that they owe nothing is worth seeing
- **Alliance membership is judged by the corporation, not by each character.** The alliance stored on a character goes stale — there is one copy per pilot, so a corporation leaving the alliance leaves hundreds of records still naming it, and every pilot in it kept being taxed however carefully the scope was set. The corporation's own record decides now, of which there is exactly one. Where Alliance Auth has no record of a corporation its membership cannot be confirmed, and it is left out rather than taxed on a guess: a scope says tax these and no others, and billing an outsider is the mistake people notice. That case is logged, so it stays a decision rather than a silence

### Fixed
- **Alliance billing was slow to the point of being unpleasant.** Tax is worked out per ledger entry, and each entry re-read the same handful of tiny tables — ore categories, rates, exemptions, fleet sessions, moons, rentals — for up to eleven queries apiece. Unnoticeable for one pilot's month; for an alliance's it meant six figures of queries against tables that mostly hold single-digit row counts. Those tables are read once and held for a minute instead, with invalidation hanging off model signals so edits made through the Django admin count too
- **The location repair could not see its oldest cases.** Placeholders written before the messages were translated read *Unbekannt (id)* and *Mond-Struktur (id)*, while the repair looked only for the English wording — so entries from the versions most likely to have them were the ones it could never reach. Since a tax-free moon is matched by its structure name, those were also the entries most likely to be taxed despite an exemption. Both spellings are recognised now

## [0.10.6] - 2026-07-23

### Documentation
- The README now states which other apps' data is used in place of ESI, and why `aa-structures` is not among them — it is neither run by the authors nor by the only production install, so the integration could not be tested, and shipping it untested would have made whoever installed that app the first person to find out

### Fixed
- **Ore that could not be classified was looked up at ESI on every page render.** The lookup added in 0.10.4 remembered its successes but not its failures, so a type nothing could categorise cost two ESI calls each time a ledger was drawn — for every such type, for every viewer. The negative answer is remembered for a day now, and dropped as soon as the rules change or the ore list is reimported, so a rule written for exactly that ore takes effect at once rather than after the cache expires


## [0.10.5] - 2026-07-23

### Changed
- **Registering corporations runs as a Celery task.** Registering a whole alliance took one ESI call for the corp list and another for every corp Alliance Auth did not already know — fifty-one requests for an alliance of fifty, all inside a web request that had to finish before the page came back. Both the single-corp and whole-alliance buttons dispatch tasks now, tagged with the officer who pressed them, and appear in the task monitor with the rest

### Notes
- Every action that talks to ESI is now a task. What remains synchronous is either a plain database write, where the officer needs to see the result on the page they are looking at, or a download, which cannot be a background job and still be a download
- `Mark as Paid` recalculates the month's billing before writing the record. That is the same work the page it sits on already does to render, so it costs no more than the view the officer just loaded


## [0.10.4] - 2026-07-23

Follow-up to 0.10.3, all of it from using it on a live install.

### Added
- **Structures are found by solar system, using the officer's own access.** Choosing the system a moon sits in fills the structure list by searching for structures that officer can dock at — which needs no corporation role, unlike every corp endpoint ESI offers. That is why the structure-corp picker returned nothing for members who could see those structures perfectly well in the client. Requires `esi-search.search_structures.v1`; without it the list falls back to names already seen in mining data

### Fixed
- **Choosing a structure corp emptied the structure list instead of extending it.** The picker replaced the field's contents, so selecting a corporation ESI would not answer for left nothing selectable, discarding the names already gathered from mining data. Picking a corp was worse than leaving the field alone
- **Ore that eveuniverse does not know stayed at the Default rate.** Classification relied on eveuniverse alone, so ore added by an expansion — or any ore where eveuniverse is not installed — could not be categorised however often the import ran. The type's group comes from ESI now when eveuniverse cannot answer, which also covers ore outside the Asteroid category the bulk import walks. A type matching no rule is logged with its name and group, so the missing rule can be written rather than guessed
- **A correctly configured moon was reported as broken if nobody had mined there yet.** The check compared the structure name against mining data alone. Structures now come from EVE's search, so naming one before any ore has been pulled from it is ordinary rather than a mistake — structures EVE reports for the system count as well, and where nothing is cached for a system no warning is given, since an unmined structure and a mistyped one cannot be told apart there

### Removed
- **The structure-corp picker.** Two controls filled the same field from different sources and overwrote one another, so which structures appeared depended on whichever had been touched last. The corp one could not work for most officers in any case, being gated behind an in-game role. Structures follow the solar system now, with mining data as the fallback — one control, one source

## [0.10.3] - 2026-07-23

### Added
- **Taxable scope** (`TaxableScope`, migration `0019`): the alliances and corporations whose characters are taxed at all. Player accounts routinely hold characters with no connection to the alliance — high-sec alts, trade characters, corps left behind — and their mining reaches the plugin through the owner's personal ledger regardless. Without a scope it was billed like anything else, charging players for ore mined where the alliance has no claim. Set on a new *Scope* tab; an alliance entry covers its corps as they come and go, a corporation entry is for corps outside it, such as renters. Mining outside the scope still appears in the miner's own ledger marked excluded rather than vanishing — the entry is a fact, only the billing changes
- **A single navigation bar on every page.** Each page carried its own hand-assembled set of links, and they had drifted: Settings offered no way back to the ledger, the pilot view knew only where it came from, and adding a page meant remembering to link it from three others. The bar reads permissions directly, so it works wherever it is included without a matching change in Python, and marks the current page

### Changed
- **The daily sync task is renamed** `daily_mining_sync` → `daily_mining_sync_task` (migration `0020`), so it reads like the seven others instead of being the one exception. Existing schedules are repointed by the migration: django-celery-beat stores the task as a string, and an entry left pointing at the old path would simply never find it — Celery reports that nowhere useful, so the daily sync would have stopped without a word
- **The maintenance buttons run as Celery tasks.** The sovereignty sync, ore import, location repair and price update ran inside the web request, so they appeared nowhere in the task monitor — no record of having run, by whom, or whether they finished — and the ore import, one ESI call per ore group, was slow enough to risk timing out the page. They are dispatched as tasks now, tagged with the officer who triggered them
- **Buttons look the same throughout.** Page actions had accumulated a dozen treatments — filled and outline, full size and small, in several colours — applied by whatever seemed right at the time, so appearance carried no information. They now match the navigation bar: small and outlined. Filled colour is kept only where it means something, on the call to action inside an alert and on the state-changing *Mark as Paid*

### Fixed
- **The ore import skipped every ordinary asteroid group, leaving those types at the Default rate.** Classification matched group names like *Exceptional Moon Asteroids*, but plain ore groups are named after the ore itself — *Veldspar*, *Arkonor*, *Scordite* — which matched nothing, so those groups were passed over silently. Everything in EVE's Asteroid category is mineable by definition, so an unrecognised group is now filed as plain Ore, and the groups taking that route are named in the log, keeping a genuinely missing rule visible
- The Settings warning about types taxed at Default names them instead of only counting them

### Packaging
- **Alliance Auth 5.0 or later is now required**, and the Django classifiers say 5.0–5.2 rather than 4.2. The metadata claimed support for AA 4.6 on Django 4.2, which nobody has run this on — the plugin has only ever been used against AA 5. Declaring compatibility that was never exercised is a promise the code cannot keep
- Python 3.13 is declared, matching what `requires-python` already allowed
- **`django-eveuniverse` is now optional rather than required**, and the README no longer contradicts that. Ore categories used to come from it; since this release they are imported from ESI directly, which is what made the difference. It remains recommended, because refined-value pricing needs its reprocessing recipes — without it ore is valued at the raw ESI price
- Homepage and changelog links added alongside the issue tracker

### Removed
- **The duplicate `miningtax/README.md`.** It was a subset of the root README, referenced nowhere since packaging points at the root, and had already fallen behind — it still named the pre-0.11.0 sync task and covered none of the pricing, ore category or moon configuration added since 0.9. Two documents where only one is maintained is worse than one, because nothing tells the reader which they have in front of them
- The pilot page's own back links, made redundant by the navigation bar

### Notes
- **The scope starts empty, which taxes everything** — exactly what every install did before, so upgrading changes nothing until someone sets one. Settings says so plainly while it is empty, and removing the last entry warns that everything is taxable again
- Scope is judged on a character's **current** corporation. Mining history carries no corporation, so present membership is all there is; someone who leaves the alliance takes any unpaid billing with them, which is the same outcome as leaving without paying
- The README now states the version, so it, the changelog and `__init__.py` can be checked against each other


## [0.10.2] - 2026-07-23

Follow-up to 0.10.1, from running it on a live install. Every item here is the
same shape of problem: something failed quietly and left the plugin looking
healthy while it under-charged, over-charged, or did nothing at all.

### Added
- **The daily sync schedules itself.** It previously relied on the administrator adding a `CELERYBEAT_SCHEDULE` entry to `local.py`. Forgetting it raised nothing and logged nothing, so the plugin looked fine while no sync ever ran, and the omission surfaced only when someone noticed their ledger was weeks stale. A periodic task is created on first migrate and then left alone — retiming, renaming or disabling it in the admin all stick. Deleting it brings it back, since an absent schedule is indistinguishable from a fresh install; untick *enabled* to stop it for good

### Fixed
- **Locations that failed to resolve once stayed broken forever, and silently taxed exempt moons.** A failed lookup wrote `Unknown (id)` into the ledger and nothing revisited it, because later syncs prefer a stored name and found that one. Beyond reading badly it defeats tax-free moons: the exemption matches on the structure name, a placeholder matches nothing, and the ore is taxed with no sign of why. Re-resolved by the daily sync, with a warning and a button in Settings while any remain
- **Tax-free moons whose structure name matches nothing are now flagged.** Same failure from the other end: a name that no ledger entry carries can never match, so the moon is inert and the page gives no hint of it. This became widespread when the structure field moved from free text to a dropdown, since hand-typed names rarely agree with ESI character for character. The Alliance Moons tab lists the affected moons with the closest observed name as a hint, left for an officer to apply — matching automatically could exempt the wrong structure, losing revenue with nothing to show for it
- **A failed price lookup left everything at zero value, and said so only at debug level.** No price means no taxable value, so a pricing outage under-charges the whole alliance while the plugin appears to work. Failures are warnings now, the ore types that could not be priced are named rather than counted, and Settings reports how many entries are affected with a button to retry


## [0.10.1] - 2026-07-22

Retag of 0.10.0 with no code changes — the original tag had already been
published and was left in place rather than moved.

---

## [0.10.0] - 2026-07-22

### Added
- **CSV export** for the personal ledger, a single pilot, and the alliance billing summary, alongside the existing PDF invoices. Files are written with a BOM and semicolons, since they are opened in Excel far more often than parsed — without that, ore names with non-ASCII characters are mangled and the whole file lands in one column wherever the comma is the decimal separator. Exports reuse the access rules of the page they belong to
- **Per-pilot detail view.** Every ledger entry of a player for a month, split by character and by ore category. Officers reach it from the alliance billing member list, members from their own dashboard. It covers the whole account rather than one character, since tax is assessed per player, and lists characters with no mining as well — which is how an alt whose ledger never synced becomes visible instead of quietly missing. Access is decided per character: your own always, anyone's as an officer, own corporation only as a CEO
- **Complete ore list imported from ESI.** Walks EVE's Asteroid category down to every mineable type and classifies each by its group, so completeness follows from the data rather than from anyone remembering to add an ore. Runs with the daily sync, plus a button on the Tax Rates tab
- **Category rules** (migration `0015`). Assign ore to a category by matching a substring of its name or group, evaluated ahead of EVE's own grouping. Abyssal ore and Prismaticite ship as rules, since both sit in ordinary asteroid groups yet warrant their own rate. Rules apply to ore that doesn't exist yet, as long as the name matches
- **Locked categories** (migration `0014`). A category set by hand can be protected from the automatic import. Without it, an ore deliberately parked in its own category to be taxed at 0% would be reclassified overnight and taxed again, with nothing in the UI to explain why
- **Tax rates can be created from Settings**, and categories in use without a rate are flagged — ore in them is billed at the Default rate with nothing else to indicate it

### Changed
- **Permissions are presented as three tiers — View, Corp, Admin** (migration `0018`), so the list reads as a ladder instead of three unrelated entries. Labels only; codenames are unchanged, since group assignments point at them and renaming would quietly void every existing one
- **Permission labels now actually update on existing installations.** Django builds permissions from a model's `Meta` in a `post_migrate` handler that only inserts the ones missing, matching on codename — it never rewrites the name of a row that already exists. Every label change this plugin has made since 0.6 therefore applied to fresh installs only, which is why running instances still showed *Can manage Mining Tax*, the wording from migration `0003`. The rename is now carried out as a data migration against the permission rows themselves
- **Corp-level access is a permission now, not something the plugin works out for itself** (migration `0017`). Access used to be granted automatically to anyone detected as a corp CEO via `EveCorporationInfo.ceo_id`. That put the plugin in charge of a decision belonging to whoever runs the Auth instance: the grant was invisible in the permission UI, could not be revoked there, and travelled with any alt who happened to be CEO of an unrelated one-man corp. The new `corp_billing` permission covers the same ground — billing for the holder's own corporation, scoped everywhere including both exports — and is assigned through groups like any other. **Existing CEOs lose their automatic access and need the permission assigned.**
- Ore with no category is now classified from its group in eveuniverse instead of falling through to the Default rate
- **A ledger entry is now identified by location as well** (migration `0016`), and corp observers sync before personal ledgers so the subtraction below has its figures in place


### Fixed
- **The manual sync accepted any member.** Its button is officer-only, but the URL behind it was not, so a member who found it could start a full alliance-wide ESI run as often as they liked. It now sits with the other alliance-wide actions behind the real officer permission rather than the CEO bypass
- **CEOs could see and download data beyond their own corporation.** The corp list on the billing page was scoped to them, but the summary cards still showed the alliance's total mined value and tax, and neither the PDF invoice nor the all-corps ZIP checked the restriction at all — any corp's invoice was one edited URL away. Totals are now recomputed from what the viewer may see, both exports enforce the same scope, and the card is labelled "Corp" rather than "Alliance" when the view is restricted
- **PDF invoice figures ran past their cell borders** once amounts reached the billions. The tax column in the member table had 30 mm, about 24 mm of it usable after padding, while such a number needs roughly 26 mm. Columns are rebalanced, the redundant ` ISK` suffix is dropped from cells since the header already carries it, and long names now wrap instead of overflowing. Verified against a rendered invoice with quadrillion-ISK values
- **Belt mining disappeared whenever the same ore was also mined at a moon that day.** Entries were identified by character, date and ore alone, so there was only one row for both, and the personal sync skipped its own rather than overwrite the more precise structure entry — leaving players with a ledger of nothing but moon ore. Since moon chunks contain ordinary asteroid ore, the overlap is routine rather than rare. Both now coexist, and the personal sync stores the difference between its day total and what the observers report, so nothing is overwritten and nothing is counted twice
- **Characters with no personal mining token are now named on the dashboard**, with a link to authorize them. Their belt and anomaly mining cannot be read at all, while their moon mining still arrives through the corp observer sync — so the gap looked like a bug in the tool rather than a missing token
- Selecting a solar system could return an empty moon list when ESI answered 304 while the local cache had missed. The system lookup and both name-resolution paths now discard the ETag and refetch once — harmless, since systems and moon names are static
- Solar system names were stored as `Unknown (id)` placeholders for the same reason, leaving the dropdown showing IDs. Rows already stored that way are repaired on the next sync
- The structure picker read from an endpoint requiring Director, so a member with in-game structure access still saw nothing. It now uses the corp's mining observers and tries every available token before concluding the role is missing
- Character links in tax-excluded ledger rows were unreadable in dark themes; they now inherit the row's own text colour
- The back link on the pilot detail page followed the viewer's role, so an officer opening their own character from the dashboard was sent to alliance billing — a page they had not come from. It now follows whose account is on screen

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
- **Sovereignty sync was broken end to end** and silently tracked zero systems, leaving the new dropdowns without any systems to offer. Six separate faults had to be cleared: the operation was called `GetSovereigntyMap` (the client exposes `GetSovereigntySystems`); only corp-held sovereignty was matched, while null-sec is held by *alliances*; a 304 response was treated as a failed request; the recovery from a stale ETag was rate-limited so strictly that the manual sync button could not retry; `results()` was used on an unpaginated endpoint, wrapping the whole response in a one-element list; and the payload is nested (`solar_systems[].claim`) with the claim wrapped in a Pydantic `RootModel`, so the alliance never resolved
- Selecting a solar system could return an empty moon list when ESI answered 304 while the local result cache had missed. The system lookup and both name-resolution paths now discard the ETag and refetch once — harmless, since systems and moon names are static
- Add-moon form columns summed to 13 of 12, pushing the last field out of view

### Notes
- The structure picker reads a corporation's mining observers, which ESI gates behind an in-game role (Accountant or Director) *in addition* to the `esi-industry.read_corporation_mining.v1` scope. Where no capable token exists the dropdown says so explicitly — distinguishing a missing token, a missing role, a corp without drills, and a genuine ESI error — and falls back to structure names already seen in the ledger


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