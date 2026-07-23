# Topfmaschine Mayer – Erfassung

Eigenständige Instanz analog zu Topfmaschine_Stolze, aber ohne Modbus/LOGO!-Zähleranbindung
(Mayer hat keinen Modbus-Zähler – "Produzierte Menge" wird rein manuell erfasst).

Eckdaten:
- Backend-Port: 8084, Caddy-HTTPS-Port: 8447
- Kulturenliste, Personalnummernbereich (1001–99999) und Admin-/Viewer-Passwörter: identisch zu Stolze
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
- systemd/topfmaschine-mayer.service
- caddy/Caddyfile – HTTPS-Reverse-Proxy-Block für Port 8447
- scripts/backup_db.sh
