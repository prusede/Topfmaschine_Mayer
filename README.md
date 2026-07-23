# Topfmaschine Mayer – Erfassung

Scaffold-Stand: Ordnerstruktur angelegt, Code folgt.

Eigenständige Instanz analog zu Topfmaschine_Stolze, aber ohne Modbus/LOGO!-Zähleranbindung
(Mayer hat keinen Modbus-Zähler – "Produzierte Menge" wird rein manuell erfasst).

Geplante Eckdaten (siehe Planungsgespräch):
- Backend-Port: 8084
- Caddy-HTTPS-Port: 8447
- Kulturenliste, Personalnummernbereich (1001–99999) und Admin-/Viewer-Passwörter: identisch zu Stolze
- Eigene Farbgebung (Header/Akzentfarbe) zur klaren optischen Unterscheidung von der Stolze-App
- Sync-Logik (sync.js) mit denselben Robustheits-Verbesserungen wie in Blumenband_Produktionserfassung
  (Watchdogs gegen hängende Sync-Schleife, defensiver IndexedDB-Reset, manueller Sync-Button)
