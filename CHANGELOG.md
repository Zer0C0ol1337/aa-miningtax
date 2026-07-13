# Changelog

## [0.2.0] - 2026-07-01

### Changed
- Manueller Sync-Button nur noch für Officers sichtbar (mining_officer, admin_access)
- Sync-Kommentar aktualisiert: Corptools-DB statt ESI-Token

### Added
- "Als bezahlt markieren" pro Corp in der Alliance Abrechnung
- Bezahlstatus wird in AllianceBillingRecord gespeichert
- Corptools-Integration: liest Mining-Daten direkt aus DB
- Bulk-Preisabruf via /markets/prices/ (1 ESI-Call statt N)

## [0.1.0] - 2026-07-01

### Added
- Persönliches Mining-Dashboard
- Alliance-weite Abrechnung nach Corp
- Steuersätze pro Erz-Kategorie (R4/R8/R16/R32/R64/Ice/Ore)
- Moon Rentals
- Steuerfreie Event-Monde
- PDF-Export pro Corp + ZIP aller Corps
- Permissions: basic_access, mining_officer, admin_access
- Web-UI für Einstellungen
