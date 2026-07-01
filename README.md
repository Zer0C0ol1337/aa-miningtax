# Mining Tax — Alliance Auth Plugin

Django-App für Alliance Auth zur Verwaltung von Mining-Steuern in EVE Online.

## Features

- **Persönliches Mining-Dashboard** — Ledger-Einträge des aktuellen Monats inkl. Steuerberechnung
- **Alliance-weite Abrechnung** — alle Corps, alle Mitglieder, Steuer nach Erz-Kategorie
- **Steuersätze** — konfigurierbar pro Erz-Kategorie (R4 / R8 / R16 / R32 / R64 / Ice / Ore)
- **Moon Rentals** — mietende Corps zahlen eine Pauschalgebühr, Mining ist steuerfrei
- **Steuerfreie Event-Monde** — Alliance-Monde können als steuerfrei markiert werden
- **PDF-Export** — Abrechnung pro Corp als PDF, alle Corps als ZIP
- **Corptools-Integration** — liest Mining-Daten direkt aus der Corptools-DB (keine eigenen ESI-Calls nötig)
- **Permissions** — granulare Zugriffssteuerung über Alliance Auth

---

## Abhängigkeiten

| Paket | Pflicht | Hinweis |
|---|---|---|
| `allianceauth` | ✅ | Basis-Framework |
| `django-esi` | ✅ | ESI-Anbindung (Fallback-Sync + Marktpreise) |
| `reportlab` | ✅ | PDF-Export |
| `allianceauth-corptools` | ⭐ Empfohlen | Mining-Daten aus DB statt ESI — deutlich weniger API-Calls |

> **Hinweis:** Ohne Corptools fällt das Plugin auf einen eigenen ESI-Sync zurück. Mit Corptools werden Mining-Daten, Type-Namen und System-Namen direkt aus der Corptools-DB gelesen — das spart erheblich ESI-Anfragen.

---

## Installation

### 1. Paket installieren

```bash
pip install reportlab
```

### 2. `INSTALLED_APPS` in `local.py` erweitern

```python
INSTALLED_APPS += [
    'miningtax',
]
```

### 3. Migration ausführen

```bash
python manage.py migrate miningtax
python manage.py collectstatic
```

### 4. Dienst neu starten

```bash
# Supervisor
sudo supervisorctl restart myauth:

# Oder systemd
sudo systemctl restart allianceauth

# Oder lokal
python manage.py runserver
```

---

## Permissions

Permissions werden im Alliance Auth Admin unter **Authentication → Users** oder über Gruppen zugewiesen.

| Permission | Codename | Zugriff |
|---|---|---|
| Basis-Zugriff | `miningtax.basic_access` | Persönliches Mining-Dashboard |
| Mining Officer | `miningtax.mining_officer` | Alliance Abrechnung + Einstellungen |
| Admin/Developer | `miningtax.admin_access` | Voller Zugriff |

**Empfohlene Zuweisung:**
- Alle Alliance-Mitglieder → `basic_access`
- Alliance Leader, Co-Leader, Mining Officer → `mining_officer`
- Developer / Admins → `admin_access`

---

## Einstellungen

Die Web-UI für Einstellungen ist unter `/miningtax/settings/` erreichbar (nur für `mining_officer` und `admin_access`).

### Steuersätze

Steuersätze werden pro Erz-Kategorie konfiguriert. Ein Steuersatz von `0.00` ist ein gültiger Wert (steuerfrei) und wird korrekt behandelt.

### Moon Rentals

Corps die einen Mond mieten zahlen eine monatliche Pauschalgebühr. Mining auf der zugehörigen Struktur wird für diese Corp steuerfrei gerechnet. Der **Struktur-Name** muss exakt dem `solar_system_name`-Wert im Mining-Ledger entsprechen.

### Alliance-Monde

Monde können als `Event` (steuerfrei) oder `Public` (normal besteuert) markiert werden. Der Match läuft über den `solar_system_name` — es reicht wenn der System-Name im Ledger-Eintrag enthalten ist.

---

## Corptools-Integration

Wenn `allianceauth-corptools` installiert ist, liest das Plugin Mining-Daten direkt aus der Corptools-Datenbank:

```
corptools.CharacterMiningLedger
  → character  (CharacterAudit → EveCharacter)
  → date       (DateField)
  → type_name  (FK → ItemType, liefert type_id + name)
  → system     (FK → SolarSystem, liefert solar_system_id + name)
  → quantity   (IntegerField)
```

**Vorteile:**
- Keine eigenen ESI-Calls für Mining-Ledger, Type-Namen oder System-Namen
- Marktpreise werden weiterhin über einen einzigen Bulk-ESI-Call aktualisiert (`/markets/prices/`)
- Automatischer Fallback auf eigenen ESI-Sync wenn Corptools nicht verfügbar ist

---

## URL-Übersicht

| URL | View | Zugriff |
|---|---|---|
| `/miningtax/` | Dashboard | `basic_access` |
| `/miningtax/sync/` | Manueller Sync | `basic_access` |
| `/miningtax/alliance/` | Alliance Abrechnung | `mining_officer` |
| `/miningtax/settings/` | Einstellungen | `mining_officer` |
| `/miningtax/pdf/corp/<id>/` | PDF pro Corp | `mining_officer` |
| `/miningtax/pdf/all/` | ZIP aller Corps | `mining_officer` |

---

## Celery Beat (automatischer Sync)

Für täglichen automatischen Sync in `local.py` eintragen:

```python
from celery.schedules import crontab

CELERYBEAT_SCHEDULE['miningtax_daily_sync'] = {
    'task': 'miningtax.tasks.daily_mining_sync',
    'schedule': crontab(hour=2, minute=0),  # täglich um 02:00 Uhr
}
```

---

## Entwicklung

Getestet mit:
- Alliance Auth v4.6+
- Django 4.2
- django-esi 9.5.0
- allianceauth-corptools 3.x
- reportlab 4.x