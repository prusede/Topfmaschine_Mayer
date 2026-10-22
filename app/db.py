"""
Datenbankzugriff fuer die Topfmaschinen-Erfassung.
SQLite im WAL-Modus – einfache Sicherung per .backup.

Schema: auftragsbasiert (ein Datensatz je Auftrag/Kultur-Abschnitt).
- auftraege: Auftragskopf mit Kultur, Topfgroesse, Arbeit, Gesamtstueckzahl
- mitarbeiter_zeiten: Personalbeteiligung mit individuellen Zeiten je Auftrag
- audit_log: Protokoll aller Admin-Aktionen

DSGVO: Namen werden nicht gespeichert; nur Personalnummern.
"""
import sqlite3
import logging
import os
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import contextmanager
from typing import Optional

from app.kulturen import KULTUR_FREITEXT

DB_PATH = os.environ.get("TM_DB_PATH", "/opt/topfmaschine_mayer/data/topfmaschine_mayer.db")

_local = threading.local()


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


@contextmanager
def get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _connect()
    try:
        yield _local.conn
    except Exception:
        _local.conn.rollback()
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS produktionsplaene (
    id TEXT PRIMARY KEY,
    kultur INTEGER NOT NULL,
    kultur_frei TEXT NOT NULL DEFAULT '',
    topfgroesse TEXT NOT NULL,
    arbeit TEXT NOT NULL,
    sollmenge INTEGER NOT NULL CHECK(sollmenge > 0),
    termin TEXT NOT NULL,
    terminart TEXT NOT NULL DEFAULT 'bis',
    prioritaet INTEGER NOT NULL DEFAULT 0,
    bemerkung TEXT NOT NULL DEFAULT '',
    archiviert INTEGER NOT NULL DEFAULT 0,
    created_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL,
    changed_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS auftraege (
    id              TEXT PRIMARY KEY,
    auftragsnr      TEXT NOT NULL,
    datum           TEXT NOT NULL,
    arbeit          TEXT DEFAULT '',
    kultur          INTEGER,
    kultur_frei     TEXT DEFAULT '',
    topfgroesse     TEXT DEFAULT '',
    auftrag_start   TEXT DEFAULT '',
    auftrag_ende    TEXT DEFAULT '',
    gesamtstueck    INTEGER,
    status          TEXT DEFAULT 'offen',
    sonst           TEXT DEFAULT '',
    created_ms      INTEGER NOT NULL,
    updated_ms      INTEGER NOT NULL,
    changed_by      TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_auftraege_datum  ON auftraege(datum);
CREATE INDEX IF NOT EXISTS idx_auftraege_status ON auftraege(status);
CREATE INDEX IF NOT EXISTS idx_auftraege_nr     ON auftraege(auftragsnr);

-- Beim Archivieren die bereits errechneten Anteile festhalten, damit neue
-- Vorgaben keine abgeschlossene archivierte Planung nachtraeglich aufbrauchen.
CREATE TABLE IF NOT EXISTS plan_archiv_anrechnungen (
    planung_id TEXT NOT NULL REFERENCES produktionsplaene(id) ON DELETE CASCADE,
    auftrag_id TEXT NOT NULL REFERENCES auftraege(id) ON DELETE CASCADE,
    menge INTEGER NOT NULL CHECK(menge >= 0),
    PRIMARY KEY(planung_id, auftrag_id)
);

-- anzahl/sammel: Sammelzeilen fuer mehrere Personen ohne einzelne
-- Personalnummer. Eine Einzelperson ist schlicht anzahl=1, sammel=0 - die
-- gesamte uebrige Logik bleibt dadurch identisch. Bei sammel=1 ist pnr leer
-- und anzahl gibt die Personenzahl an; alle Stundensummen rechnen
-- netto_h * anzahl.
CREATE TABLE IF NOT EXISTS mitarbeiter_zeiten (
    id              TEXT PRIMARY KEY,
    auftrag_id      TEXT NOT NULL,
    pnr             TEXT NOT NULL,
    rolle           TEXT NOT NULL DEFAULT 'service',
    start           TEXT DEFAULT '',
    ende            TEXT DEFAULT '',
    pause           REAL DEFAULT 0,
    anzahl          INTEGER DEFAULT 1,
    sammel          INTEGER DEFAULT 0,
    created_ms      INTEGER NOT NULL,
    updated_ms      INTEGER NOT NULL,
    FOREIGN KEY(auftrag_id) REFERENCES auftraege(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_mz_auftrag ON mitarbeiter_zeiten(auftrag_id);
CREATE INDEX IF NOT EXISTS idx_mz_pnr     ON mitarbeiter_zeiten(pnr);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    aktion     TEXT NOT NULL,
    actor      TEXT DEFAULT '',
    target     TEXT DEFAULT '',
    detail     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS tagesabschluesse (
    datum       TEXT PRIMARY KEY,
    closed      INTEGER DEFAULT 0,
    closed_ms   INTEGER,
    changed_by  TEXT DEFAULT ''
);

"""

# Bestehende Produktionsdaten durch additive Migrationen erhalten.
_MIGRATIONS: list = [
    "ALTER TABLE auftraege ADD COLUMN topfform TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE auftraege ADD COLUMN substrat_auftr_nr TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE auftraege ADD COLUMN substrat_art_nr TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE produktionsplaene ADD COLUMN topfform TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE produktionsplaene ADD COLUMN beginn TEXT NOT NULL DEFAULT ''",
    # Bestehende Auftraege bleiben ohne Vorgabe; keine Mengen werden umgeschrieben.
    "ALTER TABLE auftraege ADD COLUMN planung_id TEXT REFERENCES produktionsplaene(id)",
    "ALTER TABLE mitarbeiter_zeiten ADD COLUMN anzahl INTEGER DEFAULT 1",
    "ALTER TABLE mitarbeiter_zeiten ADD COLUMN sammel INTEGER DEFAULT 0",
    # closed_ms: Zeitpunkt des Auftragsabschlusses. Wird gebraucht, um eine
    # verspaetet eintreffende Aenderung (offline erfasst, VOR dem Abschluss)
    # von einer nachtraeglichen Korrektur unterscheiden zu koennen - siehe
    # main.py update_worker.
    "ALTER TABLE auftraege ADD COLUMN closed_ms INTEGER",
    "UPDATE mitarbeiter_zeiten SET anzahl=1 WHERE anzahl IS NULL",
    "UPDATE mitarbeiter_zeiten SET sammel=0 WHERE sammel IS NULL",
]


def _run_migrations(conn):
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError as exc:
            # ADD COLUMN darf beim zweiten Start bereits vorhanden sein.
            if "duplicate column name:" not in str(exc).lower() or not sql.startswith("ALTER TABLE "):
                logging.exception("Datenbankmigration fehlgeschlagen: %s", sql)
                raise
    # Eigene Behandlung fuer den UNIQUE-Index: schlaegt er wegen bereits
    # vorhandener doppelter Auftragsnummern fehl (IntegrityError statt
    # OperationalError), darf das den Start nicht verhindern - nur einmalig
    # protokollieren, damit das Problem sichtbar bleibt.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_auftraege_nr_unique ON auftraege(auftragsnr)")
    except sqlite3.IntegrityError:
        import sys
        print("WARNUNG: Doppelte Auftragsnummern in der bestehenden Datenbank gefunden - "
              "UNIQUE-Index auf auftraege.auftragsnr konnte nicht angelegt werden.",
              file=sys.stderr)
    except sqlite3.OperationalError:
        logging.exception("Index der Auftragsnummern konnte nicht angelegt werden")
        raise
    # Altvorgaben starten standardmaessig an ihrem Erstellungstag in Berlin.
    # Bereits explizit zugeordnete Auftraege bleiben davon unberuehrt.
    for row in conn.execute("SELECT id,created_ms FROM produktionsplaene WHERE beginn=''").fetchall():
        beginn = datetime.fromtimestamp(row[1] / 1000, ZoneInfo("Europe/Berlin")).date().isoformat()
        conn.execute("UPDATE produktionsplaene SET beginn=? WHERE id=?", (beginn, row[0]))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_auftraege_planung ON auftraege(planung_id)")
    conn.commit()


def init_db():
    with get_conn() as c:
        c.executescript(SCHEMA)
        _run_migrations(c)
        c.commit()


def get_active_auftrag(heute: str) -> Optional[dict]:
    """Gibt den zuletzt gestarteten offenen Auftrag des heutigen Tages zurueck.

    Historische Nachtraege duerfen nie zum aktiven Auftrag der Hallentablets
    werden. Ohne Datumsfilter wuerde ein spaeter angelegter Auftrag von gestern
    den laufenden Auftrag von heute verdraengen."""
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM auftraege WHERE status='offen' AND datum=? "
            "ORDER BY created_ms DESC LIMIT 1",
            (heute,),
        ).fetchone()
        if not row:
            return None
        a = dict(row)
        a["mitarbeiter"] = _get_mitarbeiter(c, row["id"])
        return _with_completion_state(a, require_closed=False)


def get_auftrag_by_id(auftrag_id: str) -> Optional[dict]:
    with get_conn() as c:
        row = c.execute("SELECT * FROM auftraege WHERE id=?", (auftrag_id,)).fetchone()
        if not row:
            return None
        a = dict(row)
        a["mitarbeiter"] = _get_mitarbeiter(c, auftrag_id)
        return _with_completion_state(a, require_closed=a.get("status") == "abgeschlossen")


def _get_mitarbeiter(conn, auftrag_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM mitarbeiter_zeiten WHERE auftrag_id=? ORDER BY created_ms",
        (auftrag_id,)
    ).fetchall()
    return [dict(r) for r in rows]



def _missing_fields(a: dict, require_closed: bool = False) -> list:
    missing = []
    if not a.get("datum"):
        missing.append("Datum")
    if not a.get("auftrag_start"):
        missing.append("Auftragsstart")
    if a.get("kultur") is None:
        missing.append("Kultur")
    elif int(a.get("kultur")) == KULTUR_FREITEXT and not (a.get("kultur_frei") or "").strip():
        missing.append("Freitext-Kultur")
    if not a.get("topfgroesse"):
        missing.append("Topfgroesse")
    if require_closed:
        if not a.get("auftrag_ende"):
            missing.append("Auftragsende")
        if a.get("gesamtstueck") is None:
            missing.append("Gesamtstueck")
    return missing


def worker_label(m: dict) -> str:
    """Bezeichnung eines Zeiteintrags fuer Fehlermeldungen - eine Sammelzeile
    hat keine Personalnummer, ueber die man sie benennen koennte."""
    if m.get("sammel"):
        return f"Sammelzeile ({int(m.get('anzahl') or 0)} Pers.)"
    return f"Mitarbeiter {m.get('pnr') or m.get('id')}"


def _incomplete_workers(a: dict, require_end: bool = False) -> list:
    result = []
    for m in a.get("mitarbeiter", []):
        fields = []
        if m.get("sammel"):
            # Sammelzeile: statt der Personalnummer ist die Personenzahl das
            # Pflichtfeld, sonst identische Anforderungen.
            if not m.get("anzahl") or int(m.get("anzahl") or 0) < 1:
                fields.append("Personenanzahl")
        elif not m.get("pnr"):
            fields.append("Personalnummer")
        if not m.get("start"):
            fields.append("Startzeit")
        if require_end and not m.get("ende"):
            fields.append("Endzeit")
        if fields:
            result.append({"id": m.get("id"), "pnr": m.get("pnr", ""),
                           "sammel": int(m.get("sammel") or 0),
                           "anzahl": int(m.get("anzahl") or 1),
                           "label": worker_label(m), "missing": fields})
    return result


def _with_completion_state(a: dict, require_closed: bool = False) -> dict:
    a["missing_fields"] = _missing_fields(a, require_closed)
    a["incomplete_workers"] = _incomplete_workers(a, require_closed)
    a["has_missing_data"] = bool(a["missing_fields"] or a["incomplete_workers"])
    return a
def list_auftraege(datum_von: str, datum_bis: str) -> list:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM auftraege WHERE datum BETWEEN ? AND ? ORDER BY datum, created_ms",
            (datum_von, datum_bis)
        ).fetchall()
        result = []
        for row in rows:
            a = dict(row)
            a["mitarbeiter"] = _get_mitarbeiter(c, row["id"])
            result.append(_with_completion_state(a, require_closed=a.get("status") == "abgeschlossen"))
        return result


def next_auftragsnr(datum: str) -> str:
    """Gibt die naechste Auftragsnummer fuer das Datum zurueck: TM-YYYYMMDD-NNN"""
    date_compact = datum.replace("-", "")
    with get_conn() as c:
        count = c.execute(
            "SELECT COUNT(*) FROM auftraege WHERE datum=?", (datum,)
        ).fetchone()[0]
    return f"TM-{date_compact}-{count + 1:03d}"


def create_auftrag(data: dict) -> str:
    from app import plan_db
    ts = int(time.time() * 1000)
    with get_conn() as c:
        c.execute("BEGIN IMMEDIATE")
        plan_db.konfiguration(data)
        c.execute("""
            INSERT INTO auftraege
              (id, auftragsnr, datum, arbeit, kultur, kultur_frei, topfgroesse,
               auftrag_start, auftrag_ende, gesamtstueck, status, sonst,
               created_ms, updated_ms, changed_by, planung_id, topfform, substrat_auftr_nr, substrat_art_nr)
            VALUES
              (:id, :auftragsnr, :datum, :arbeit, :kultur, :kultur_frei, :topfgroesse,
               :auftrag_start, '', NULL, 'offen', :sonst, :ts, :ts, :changed_by, :planung_id, :topfform, :substrat_auftr_nr, :substrat_art_nr)
        """, {
            "id":           data["id"],
            "auftragsnr":   data["auftragsnr"],
            "datum":        data["datum"],
            "arbeit":       data.get("arbeit", ""),
            "kultur":       data.get("kultur"),
            "kultur_frei":  data.get("kultur_frei", ""),
            "topfgroesse":  data.get("topfgroesse", ""),
            "auftrag_start": data.get("auftrag_start", ""),
            "sonst":        data.get("sonst", ""),
            "changed_by":   data.get("changed_by", ""),
            "planung_id":   data.get("planung_id"),
            "topfform": data.get("topfform", ""),
            "substrat_auftr_nr": data.get("substrat_auftr_nr", ""),
            "substrat_art_nr": data.get("substrat_art_nr", ""),
            "ts":           ts,
        })
        c.commit()
    return data["id"]


def _insert_auftrag_with_unique_nr(c, data: dict, max_retries: int = 8) -> str:
    """Berechnet die naechste Auftragsnummer und fuegt den Auftrag ein - auf
    der UEBERGEBENEN, bereits offenen Verbindung/Transaktion (kein eigener
    Commit). Der UNIQUE-Index auf auftragsnr verhindert doppelte Nummern auch
    bei echter Parallelitaet; kollidiert eine berechnete Nummer dennoch
    (zwei Anfragen gleichzeitig), wird automatisch mit der naechsten Nummer
    erneut versucht statt eine doppelte Nummer zu vergeben oder dem Nutzer
    einen rohen 500er-Fehler zu zeigen."""
    from app import plan_db
    if not c.in_transaction:
        c.execute("BEGIN IMMEDIATE")
    plan_db.konfiguration(data)
    date_compact = data["datum"].replace("-", "")
    ts = data.get("ts") or int(time.time() * 1000)
    for attempt in range(max_retries):
        count = c.execute(
            "SELECT COUNT(*) FROM auftraege WHERE datum=?", (data["datum"],)
        ).fetchone()[0]
        auftragsnr = f"TM-{date_compact}-{count + 1 + attempt:03d}"
        try:
            c.execute("""
                INSERT INTO auftraege
                  (id, auftragsnr, datum, arbeit, kultur, kultur_frei, topfgroesse,
                   auftrag_start, auftrag_ende, gesamtstueck, status, sonst,
                   created_ms, updated_ms, changed_by, planung_id, topfform, substrat_auftr_nr, substrat_art_nr)
                VALUES
                  (:id, :auftragsnr, :datum, :arbeit, :kultur, :kultur_frei, :topfgroesse,
                   :auftrag_start, '', NULL, 'offen', :sonst, :ts, :ts, :changed_by, :planung_id, :topfform, :substrat_auftr_nr, :substrat_art_nr)
            """, {
                "id":           data["id"],
                "auftragsnr":   auftragsnr,
                "datum":        data["datum"],
                "arbeit":       data.get("arbeit", ""),
                "kultur":       data.get("kultur"),
                "kultur_frei":  data.get("kultur_frei", ""),
                "topfgroesse":  data.get("topfgroesse", ""),
                "auftrag_start": data.get("auftrag_start", ""),
                "sonst":        data.get("sonst", ""),
                "changed_by":   data.get("changed_by", ""),
                "planung_id":   data.get("planung_id"),
                "topfform": data.get("topfform", ""),
                "substrat_auftr_nr": data.get("substrat_auftr_nr", ""),
                "substrat_art_nr": data.get("substrat_art_nr", ""),
                "ts":           ts,
            })
            return auftragsnr
        except sqlite3.IntegrityError:
            continue  # Kollision (auftragsnr oder id) - naechster Versuch
    raise RuntimeError("Konnte keine eindeutige Auftragsnummer vergeben (zu viele Kollisionen)")


def create_auftrag_atomic(data: dict) -> str:
    """Nummernvergabe + INSERT in einer einzigen Transaktion (vorher: separate
    next_auftragsnr()-Abfrage und create_auftrag()-Insert ohne gemeinsame
    Transaktion - zwei parallele Anfragen konnten dieselbe Nummer berechnen)."""
    with get_conn() as c:
        auftragsnr = _insert_auftrag_with_unique_nr(c, data)
        c.commit()
    return auftragsnr


def neue_kultur_atomic(old_auftrag_id: str, auftrag_ende: str, gesamtstueck: int,
                       closed_by: str, new_data: dict,
                       transfer_workers: list) -> tuple[str, list]:
    """Fuehrt den kompletten Kulturwechsel - alten Auftrag abschliessen, neuen
    Auftrag anlegen, aktive Mitarbeiter uebernehmen - in EINER Transaktion aus.
    Vorher liefen dies vier unabhaengige Commits; ein Abbruch/Neustart
    dazwischen konnte einen halb abgeschlossenen Auftrag oder verlorene
    Mitarbeiteruebernahmen erzeugen.

    transfer_workers: Liste von {"id": neue_mz_id, "pnr":..., "rolle":...}."""
    ts = int(time.time() * 1000)
    with get_conn() as c:
        c.execute("""
            UPDATE mitarbeiter_zeiten SET ende=?, updated_ms=?
            WHERE auftrag_id=? AND (ende='' OR ende IS NULL)
        """, (auftrag_ende, ts, old_auftrag_id))
        c.execute("""
            UPDATE auftraege
            SET status='abgeschlossen', auftrag_ende=?, gesamtstueck=?,
                closed_ms=?, updated_ms=?, changed_by=?
            WHERE id=?
        """, (auftrag_ende, gesamtstueck, ts, ts, closed_by, old_auftrag_id))

        new_data = dict(new_data)
        new_data["ts"] = ts
        auftragsnr = _insert_auftrag_with_unique_nr(c, new_data)

        transferred = []
        for w in transfer_workers:
            c.execute("""
                INSERT INTO mitarbeiter_zeiten
                  (id, auftrag_id, pnr, rolle, start, ende, pause, anzahl, sammel,
                   created_ms, updated_ms)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (w["id"], new_data["id"], w["pnr"], w["rolle"],
                  new_data.get("auftrag_start", ""), "", 0,
                  int(w.get("anzahl") or 1), 1 if w.get("sammel") else 0, ts, ts))
            transferred.append(w["pnr"] or f"SAMMEL x{int(w.get('anzahl') or 1)}")
        c.commit()
    return auftragsnr, transferred


def get_mitarbeiter_by_id(mz_id: str) -> Optional[dict]:
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM mitarbeiter_zeiten WHERE id=?", (mz_id,)).fetchone()
        return dict(row) if row else None


def update_auftrag(auftrag_id: str, fields: dict):
    ts = int(time.time() * 1000)
    allowed = {"topfform", "substrat_auftr_nr", "substrat_art_nr", "arbeit", "kultur", "kultur_frei", "topfgroesse", "auftrag_start",
               "auftrag_ende", "gesamtstueck", "status", "sonst", "changed_by", "planung_id"}
    sets = ", ".join(f"{k}=:{k}" for k in fields if k in allowed)
    if not sets:
        return
    params = {k: v for k, v in fields.items() if k in allowed}
    params["id"] = auftrag_id
    params["updated_ms"] = ts
    with get_conn() as c:
        c.execute(f"UPDATE auftraege SET {sets}, updated_ms=:updated_ms WHERE id=:id", params)
        c.commit()


def close_auftrag(auftrag_id: str, auftrag_ende: str, gesamtstueck: int,
                  changed_by: str = ""):
    ts = int(time.time() * 1000)
    with get_conn() as c:
        c.execute("""
            UPDATE auftraege
            SET status='abgeschlossen', auftrag_ende=?, gesamtstueck=?,
                closed_ms=?, updated_ms=?, changed_by=?
            WHERE id=?
        """, (auftrag_ende, gesamtstueck, ts, ts, changed_by, auftrag_id))
        c.commit()


class MitarbeiterKonflikt(ValueError):
    pass


class MitarbeiterGesperrt(ValueError):
    pass


def upsert_mitarbeiter(data: dict, nur_neu: bool = False):
    """Idempotentes Insert/Update eines Mitarbeiter-Zeiteintrags."""
    ts = int(time.time() * 1000)
    with get_conn() as c:
        if nur_neu:
            c.execute("BEGIN IMMEDIATE")
            alt = c.execute("SELECT * FROM mitarbeiter_zeiten WHERE id=?", (data["id"],)).fetchone()
            if alt:
                if alt["auftrag_id"] != data["auftrag_id"] or str(alt["pnr"] or "") != str(data["pnr"] or "") or bool(alt["sammel"]) != bool(data.get("sammel")):
                    raise MitarbeiterKonflikt("Mitarbeiter-Kennung ist bereits anderweitig vergeben")
                c.commit()
                return False
            a = c.execute("SELECT status,datum FROM auftraege WHERE id=?", (data["auftrag_id"],)).fetchone()
            tag = c.execute("SELECT closed FROM tagesabschluesse WHERE datum=?", (a["datum"],)).fetchone() if a else None
            if not a or a["status"] != "offen" or (tag and tag["closed"]):
                raise MitarbeiterGesperrt("Auftrag/Tag abgeschlossen – Änderung nur im Admin-Modus")
        c.execute("""
            INSERT INTO mitarbeiter_zeiten
              (id, auftrag_id, pnr, rolle, start, ende, pause, anzahl, sammel,
               created_ms, updated_ms)
            VALUES
              (:id, :auftrag_id, :pnr, :rolle, :start, :ende, :pause, :anzahl, :sammel,
               :created_ms, :updated_ms)
            ON CONFLICT(id) DO UPDATE SET
              rolle=excluded.rolle, start=excluded.start, ende=excluded.ende,
              pause=excluded.pause, anzahl=excluded.anzahl, sammel=excluded.sammel,
              updated_ms=excluded.updated_ms
            WHERE excluded.updated_ms >= mitarbeiter_zeiten.updated_ms
        """, {
            "id":         data["id"],
            "auftrag_id": data["auftrag_id"],
            "pnr":        data["pnr"],
            "rolle":      data.get("rolle", "service"),
            "start":      data.get("start", ""),
            "ende":       data.get("ende", ""),
            "pause":      data.get("pause", 0),
            "anzahl":     int(data.get("anzahl") or 1),
            "sammel":     1 if data.get("sammel") else 0,
            "created_ms": data.get("created_ms", ts),
            "updated_ms": data.get("updated_ms", ts),
        })
        c.commit()
    return True


def set_all_active_workers_ende(auftrag_id: str, ende: str):
    ts = int(time.time() * 1000)
    with get_conn() as c:
        c.execute("""
            UPDATE mitarbeiter_zeiten
            SET ende=?, updated_ms=?
            WHERE auftrag_id=? AND (ende='' OR ende IS NULL)
        """, (ende, ts, auftrag_id))
        c.commit()


def remove_mitarbeiter(mz_id: str):
    with get_conn() as c:
        c.execute("DELETE FROM mitarbeiter_zeiten WHERE id=?", (mz_id,))
        c.commit()


def get_auftraege_flat(datum_von: str, datum_bis: str) -> list:
    with get_conn() as c:
        rows = c.execute("""
            SELECT
                a.id, a.auftragsnr, a.datum, a.arbeit,
                a.kultur, a.kultur_frei, a.topfgroesse, a.topfform, a.substrat_auftr_nr, a.substrat_art_nr,
                a.auftrag_start, a.auftrag_ende, a.gesamtstueck,
                a.status, a.sonst, a.planung_id,
                mz.id    AS mz_id,
                mz.pnr,  mz.rolle,
                mz.start AS mz_start,
                mz.ende  AS mz_ende,
                mz.pause AS mz_pause,
                mz.anzahl AS mz_anzahl,
                mz.sammel AS mz_sammel
            FROM auftraege a
            LEFT JOIN mitarbeiter_zeiten mz ON mz.auftrag_id = a.id
            WHERE a.datum BETWEEN ? AND ?
            ORDER BY a.datum, a.created_ms, mz.created_ms
        """, (datum_von, datum_bis)).fetchall()
        return [dict(r) for r in rows]


def log_audit(aktion: str, actor: str, target: str, detail: str):
    ts = int(time.time() * 1000)
    with get_conn() as c:
        c.execute("""
            INSERT INTO audit_log (ts, aktion, actor, target, detail)
            VALUES (?,?,?,?,?)
        """, (ts, aktion, actor or "", target or "", detail or ""))
        c.commit()


def get_audit_log(limit: int = 200) -> list:
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def auswertungs_jahre():
    """Jahre mit Auftraegen, fuer die Jahresauswahl der Auswertung."""
    with get_conn() as c:
        return [int(r[0]) for r in c.execute(
            "SELECT DISTINCT substr(datum,1,4) FROM auftraege ORDER BY 1 DESC")
            if str(r[0]).isdigit()]


def delete_auftrag(auftrag_id: str):
    """Löscht einen Auftrag und alle zugehörigen Mitarbeiter-Zeiten vollständig (CASCADE)."""
    with get_conn() as c:
        c.execute("DELETE FROM auftraege WHERE id=?", (auftrag_id,))
        c.commit()


def close_day(datum: str, changed_by: str = ""):
    ts = int(time.time() * 1000)
    with get_conn() as c:
        c.execute("""
            INSERT INTO tagesabschluesse (datum, closed, closed_ms, changed_by)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(datum) DO UPDATE SET closed=1, closed_ms=?, changed_by=?
        """, (datum, ts, changed_by, ts, changed_by))
        c.commit()


def reopen_day(datum: str, changed_by: str = ""):
    ts = int(time.time() * 1000)
    with get_conn() as c:
        c.execute("""
            INSERT INTO tagesabschluesse (datum, closed, closed_ms, changed_by)
            VALUES (?, 0, ?, ?)
            ON CONFLICT(datum) DO UPDATE SET closed=0, closed_ms=?, changed_by=?
        """, (datum, ts, changed_by, ts, changed_by))
        c.commit()


def get_day_close(datum: str) -> dict:
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM tagesabschluesse WHERE datum=?", (datum,)
        ).fetchone()
        return dict(row) if row else {"datum": datum, "closed": 0, "closed_ms": None, "changed_by": ""}




def update_mitarbeiter_sicher(mz_id: str, fields: dict, berechtigt: bool):
    # Sperrpruefung und Schreiben sind eine Transaktion: ein paralleler Abschluss
    # darf zwischen Pruefung und UPDATE keine unbemerkte Aenderung zulassen.
    allowed = {"start", "ende", "pause", "rolle", "anzahl"}
    if not fields or not set(fields) <= allowed:
        raise ValueError("Ungueltige Mitarbeiterfelder")
    with get_conn() as c:
        c.execute("BEGIN IMMEDIATE")
        mz = c.execute("SELECT * FROM mitarbeiter_zeiten WHERE id=?", (mz_id,)).fetchone()
        a = c.execute("SELECT status,datum FROM auftraege WHERE id=?", (mz["auftrag_id"],)).fetchone() if mz else None
        if not mz or not a:
            raise MitarbeiterGesperrt("Mitarbeiter-Eintrag oder Auftrag nicht gefunden")
        if all(mz[k] == v for k, v in fields.items()):
            c.commit()
            return False
        tag = c.execute("SELECT closed FROM tagesabschluesse WHERE datum=?", (a["datum"],)).fetchone()
        if not berechtigt and (a["status"] != "offen" or (tag and tag["closed"])):
            raise MitarbeiterGesperrt("Auftrag/Tag abgeschlossen – Änderung nur im Admin-Modus")
        c.execute("UPDATE mitarbeiter_zeiten SET " + ",".join(f"{k}=?" for k in fields) + ",updated_ms=? WHERE id=?",
                  [*fields.values(), int(time.time() * 1000), mz_id])
        c.commit()
        return True
