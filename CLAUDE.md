# Topfmaschine Mayer

Erfassung und Auswertung der Topfmaschine im Betrieb Mayer. Die übergreifenden
Regeln stehen in der `CLAUDE.md` eine Ebene höher — **die zuerst lesen**, sie
enthält die vier Fallen der Erfassungsoberfläche und die Prüfliste für neue
Eingabefelder.

## Verhältnis zu Topfmaschine Stolze

Beide Anwendungen stammen aus derselben Vorlage und sind in der Erfassung nahezu
identisch — dieselbe Auftragslogik, dieselben Sammelzeilen, dasselbe
Kulturwechsel-Verfahren, dasselbe Sync-Modell mit Einzelvorgängen und geparkten
Einträgen.

**Der Unterschied:** Mayer hat **keine Anbindung an eine Maschinensteuerung.**
Kein LOGO!-Poller, keine Zählerkachel, kein „Menge übernehmen", kein Reset. Die
produzierte Menge wird ausschließlich von Hand erfasst.

Daraus folgt für Änderungen: **Was in Stolze am Zähler hängt, hat hier kein
Gegenstück.** Umgekehrt gehört alles, was die Auftrags- und Zeitlogik betrifft,
sinngemäß in beide Anwendungen. Beim Mengenfeld-Fehler im August war das der Fall
— gefunden in Stolze, vorhanden in beiden.

## Prüfregel bei jeder Änderung

Vor dem Abschluss einer Änderung in einer der beiden Anwendungen prüfen, ob sie
auch die andere betrifft:

```bash
grep -n "<charakteristische Zeile>" ../Topfmaschine_Stolze/app/static/index.html
```

Betrifft sie beide, in einem Zug erledigen. Sonst läuft der Stand auseinander,
und der nächste Fehler wird zweimal gesucht.

## Was hier ebenfalls fehlt

Die stündliche Auswertung und das Notizfeld am Auftrag gibt es bislang nur in
Stolze. Die Notiz wäre übertragbar — die Spalte `sonst` existiert auch hier und
ist bisher nur über den Admin-Dialog erreichbar. Die stündliche Auswertung nicht:
Sie beruht auf den Zählerdaten, die es hier nicht gibt.

## Deployment

Ein Dienst, kein Poller:

```bash
systemctl restart topfmaschine-mayer
```

Unit: `systemd/topfmaschine-mayer.service`.
