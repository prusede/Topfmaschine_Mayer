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

## Uebernommener Stand vom 16.10.2026

Erfassung, Kulturkatalog, Produktionsplanung, automatische Soll-Ist-Anrechnung
und die Bedienung der Auswertung entsprechen Stolze. Mayer behaelt Petrol/Blau,
die Rolle `topfmaschine_mayer`, HTTP 8084, HTTPS 8447 und die eigene Datenbank
`/opt/topfmaschine_mayer/data/topfmaschine_mayer.db`. Keine Messwerttabellen,
Zaehlerabfragen, Reset-Routen oder zaehlerbasierte Stundenansicht ergaenzen.
Die manuelle Gesamtstueckzahl beim Auftragsabschluss ist verbindlich.

87 Kulturen mit festen IDs in `app/kulturen.py`: bisherige IDs 1-22 bleiben,
Freitext bleibt 23, neue IDs beginnen bei 24. ID 15 wird als Polygonum angezeigt;
bestehende Datensaetze werden nicht umgeschrieben. 9er/11er bleiben als alte
Speicherwerte zulaessig, Anzeige und Neuauswahl verwenden 9cm/11cm.

Kultursuche filtert alphabetisch nach Namensanfang; nach Auswahl bleibt nur der
gewaehlte Chip. Abwahl zeigt eine leere Suche, Topf muss neu gewaehlt werden.
Fertig verlangt Kultur, ggf. Freitextname, Topfgroesse, Topfform und beide
Substratnummern. Notiz bleibt optional. Fertig klappt alle Eingaben ein;
Aendern zeigt gespeicherte Werte. Zusammenfassung enthaelt Topfform und Nummern.

Topfform und Substratnummern haben die eigene Outbox-Kennung `azusatz-<id>`,
Kultur/Topf `akonfig-<id>`, Notiz `anot-<id>`. Unbestaetigte lokale Werte gegen
Server-Reload schuetzen; Scanner-Ausnahmen, Auftrag-ID und Sperrzustaende beachten.
Bestehende IndexedDB-Outbox `tm_outbox` bleibt kompatibel.

Produktionsplanung steht nur in der Auswertung (Ansicht "Produktionsplanung"),
nicht in der Erfassung; `/planung` leitet seit 22.10.2026 auf `/auswertung#planung`. Neue Vorgaben verlangen sechs gueltige Angaben; Speichern ist sonst
grau. Alte bereits angerechnete Vorgaben ohne Form bleiben mit Hinweis bearbeitbar.
Automatische Zuordnung verwendet Kultur, Topfgroesse, gewaehlte Topfform und
Produktionsbeginn. Fruehester Termin zuerst; jede Menge wird nur einmal verteilt.
Abgeschlossene Mengen sind verbindlich, laufende nur vorlaeufig. Archivieren
sichert bereits angerechnete Anteile. Bestehende feste Zuordnungen haben Vorrang.

Auswertung laedt bei gueltiger Datumsauswahl/Schnellwahl automatisch. Alte Antworten
duerfen neue Ergebnisse nicht ueberschreiben. Produktion offen zeigt nur aktive
unerfuellte Vorgaben. Der Kulturfilter ist ein einzelnes durchsuchbares Feld;
erst Auswahl/Enter uebernimmt die ID. CSV exportiert auch Form, Nummern,
Notiz und Plananrechnung. Keine Stolze-Rolle fuer Mayer-Schreibrechte verwenden.

## Einheitliche Auswertung wie Stolze (22.10.2026)

Die Auswertung ist aus der Stolze-Seite uebernommen, ohne deren Zaehler- und
Stundenteile. Mayer-eigen bleiben Petrol/Blau, Rolle `topfmaschine_mayer`,
Ports, Datenbank und manuelle Mengenerfassung.

* Zeitwahl Heute/Woche/Monat/Jahr, Jahres-/Monatsauswahl (`/api/auswertung/jahre`),
  Matrix Kulturen x Monate mit Tagen, Auftraegen und Mitarbeiterzeiten im
  geoeffneten Tag. **Keine Stundenwerte im Tag** -- es gibt keinen Zaehler.
* `produktion-uebersicht.js` und `tests/test_produktionsuebersicht.cjs` sind
  byte-identisch mit Halle 1 (CRLF) und werden nur dort geaendert und hierher
  kopiert. Die Farben setzt Mayer ueber die `--pu-*`-Variablen in `:root` von
  `auswertung.html`; die gemeinsame Datei behaelt das Gruen als Vorgabe.
* `produktionsbericht.js` entspricht Stolze ohne Stunden-Platzhalter; die
  Funktionsnamen `stolze*` sind bewusst gleich geblieben (gleiche Tests).
* Ansicht: Produktion | Produktionsplanung | Offene Produktion (Wert in `#fRange`).
  Die Planung ist eingebettet (`TMPlan.planungLaden()`), Zeitraum und Kultur
  kommen aus den Filtern der Auswertung.
* Filter Topfgroesse/Topfform wirken auf Summary, Kulturen und beide CSV;
  `ansicht="mengen"` liefert eine Zeile je Auftrag (`Mayer_Mengen_...csv`).
* Admin (nur Rolle admin) loescht Auftraege vergangener Tage in der Auftragstabelle
  und im geoeffneten Tag: `POST /api/auswertung/auftrag/loeschen`, 409 fuer heute,
  Protokoll `auswertung_auftrag_delete` mit vollstaendigem Inhalt.
* Navigation mit allen Anwendungen (17 Eintraege wie Stolze), aktuelle Seite
  "Mayer Auswertung". Kopfknoepfe der Planung wie "Abmelden".
* `auswertung.html` und die Skripte werden mit `Cache-Control: no-cache`
  ausgeliefert; bei Skriptaenderungen `?v=` erhoehen.

Migrationen fuegen Spalten/Planungstabellen hinzu, ohne Mengen, Kulturkennungen
oder Mitarbeiterzeiten zu veraendern. Vor dem Einspielen Datenbank mit
`scripts/backup_db.sh` sichern. Nach Neustart Browserseiten neu laden.

Pruefung: `pytest` sowie `node --test tests/*.cjs`. Zusaetzlich Erfassung,
Offline-Reload, Plananrechnung, Navigation und Auswertungsfilter im Browser testen.

## Deployment

## Abnahme-Haertung 16.10.2026

Gemeinsame Korrekturen mit Stolze: historische Lesezugriffe verlangen Auswerter;
heutiger Scanbetrieb bleibt offen. Scans duerfen vorhandene Zeilen weder einem
fremden Auftrag zuordnen noch deren Zeiten zuruecksetzen. Sperrpruefung und
Schreiben liegen in einer Transaktion. Identische Wiederholungen bleiben erlaubt.
updated_ms vom Browser hebt keine Abschlusssperre auf. Nicht angewandte Updates
nach Abschluss bleiben in der Outbox geparkt, bis ein Meister sie freigibt;
bereits angewandte identische Updates werden quittiert. Keine Daten verwerfen.
Meisterrechte bleiben bestehen. Eingaben/Datumsbereiche sind einheitlich geprueft,
Tagesstatus protokolliert den Bearbeiter, echte Migrationsfehler brechen den Start ab.
scripts/deploy.sh bricht bei Backup-/Installations-/Prueffehlern ab. Kein Deployment
oder Serverbackup wurde durch diese Codeaenderung ausgefuehrt.

Ein Dienst, kein Poller:

```bash
systemctl restart topfmaschine-mayer
```

Unit: `systemd/topfmaschine-mayer.service`.
