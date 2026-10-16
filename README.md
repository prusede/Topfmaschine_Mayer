# Topfmaschine Mayer – Erfassung

Eigenständige Instanz analog zu Topfmaschine_Stolze, aber ohne Modbus/LOGO!-Zähleranbindung
(Mayer hat keinen Modbus-Zähler – "Produzierte Menge" wird rein manuell erfasst).

Eckdaten:
- Backend-Port: 8084, Caddy-HTTPS-Port: 8447
- Gemeinsamer Kulturkatalog mit stabilen IDs und Personalnummernbereich 1001–99999
- Zentrale Anmeldung; eigene Rollen unter `topfmaschine_mayer`
- Eigene Farbgebung (Petrol/Blau statt Grün) zur klaren optischen Unterscheidung von der Stolze-App
- Header-Link zum direkten Wechseln zwischen Stolze und Mayer (auf demselben Tablet)
- Sync-Logik (sync.js) mit denselben Robustheits-Verbesserungen wie in Blumenband_Produktionserfassung
  (Watchdogs gegen hängende Sync-Schleife bei visibilitychange/pageshow, periodischer Reload stößt
  Sync-Loop zusätzlich an, defensiver IndexedDB-Reset bei Fehlern, klickbarer Sync-Status als
  manueller Sofort-Sync)

Installation: siehe INSTALL.txt.

Verzeichnisstruktur analog zu Topfmaschine_Stolze:
- app/main.py, app/db.py – Backend (FastAPI/SQLite), ohne logo_poller.py und Zähler-Endpunkte
- app/static/ – index.html (Erfassung), auswertung.html (Auswertung), sync.js
- app/plan_db.py, app/kulturen.py – Produktionsplanung und stabiler Kulturkatalog
- app/static/planung.html – Produktionsvorgaben mit automatischem Soll-Ist-Vergleich
- systemd/topfmaschine-mayer.service
- caddy/Caddyfile – HTTPS-Reverse-Proxy-Block für Port 8447
- scripts/backup_db.sh

Stand 16.10.2026: Kultur-/Topfauswahl mit Suche, Topfform, Substratnummern,
optionale Auftragsnotiz, Pflichtfeldpruefung, Produktionsplanung und automatische
Auswertungsfilter aus Stolze uebernommen. Bestehende Kulturkennungen und Mengen
bleiben erhalten; die Datenbank wird durch additive Migrationen erweitert.
Vor Einspielen sichern, danach nur den Mayer-Dienst neu starten und Browser
neu laden. Die LOGO!-Funktionen bleiben ausschliesslich bei Stolze.
