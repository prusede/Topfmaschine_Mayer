"""
Datenbankzugriff fuer die Topfmaschine-Mayer-Erfassung.
SQLite im WAL-Modus – einfache Sicherung per .backup.

Schema: auftragsbasiert (ein Datensatz je Auftrag/Kultur-Abschnitt).
- auftraege: Auftragskopf mit Kultur, Topfgroesse, Arbeit, Gesamtstueckzahl
- mitarbeiter_zeiten: Personalbeteiligung mit individuellen Zeiten je Auftrag
- audit_log: Protokoll aller Admin-Aktionen

Im Unterschied zu Topfmaschine_Stolze OHNE Modbus/LOGO!-Zaehleranbindung
(kein zaehlerstaende/logo_status-Schema, kein logo_poller-Dienst) - die
Mayer-Maschine hat keinen Modbus-Zaehler, "Produzierte Menge" wird
ausschliesslich manuell erfasst (gesamtstueck).

DSGVO: Namen werden nicht gespeichert; nur Personalnummern.
"""
import sqlite3
import os
import time
import threading
from contextlib import contextmanager
from typing import Optional

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

CREATE TABLE IF NOT EXISTS mitarbeiter_zeiten (
    id              TEXT PRIMARY KEY,
    auftrag_id      TEXT NOT NULL,
    pnr             TEXT NOT NULL,
    rolle           TEXT NOT NULL DEFAULT 'service',
    start           TEXT DEFAULT '',
    ende            TEXT DEFAULT '',
    pause           REAL DEFAULT 0,
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

_MIGRATIONS: list = []


def _run_migrations(conn):
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
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
        pass
    conn.commit()


def init_db():
    with get_conn() as c:
        c.executescript(SCHEMA)
        _run_migrations(c)
        c.commit()


def get_active_auftrag() -> Optional[dict]:
    """Gibt den aktuell offenen Auftrag zurueck (der zuletzt gestartete)."""
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM auftraege WHERE status='offen' ORDER BY created_ms DESC LIMIT 1"
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
    elif int(a.get("kultur")) == 23 and not (a.get("kultur_frei") or "").strip():
        missing.append("Freitext-Kultur")
    if not a.get("topfgroesse"):
        missing.append("Topfgroesse")
    if require_closed:
        if not a.get("auftrag_ende"):
            missing.append("Auftragsende")
        if a.get("gesamtstueck") is None:
            missing.append("Gesamtstueck")
    return missing


def _incomplete_workers(a: dict, require_end: bool = False) -> list:
    result = []
    for m in a.get("mitarbeiter", []):
        fields = []
        if not m.get("pnr"):
            fields.append("Personalnummer")
        if not m.get("start"):
            fields.append("Startzeit")
        if require_end and not m.get("ende"):
            fields.append("Endzeit")
        if fields:
            result.append({"id": m.get("id"), "pnr": m.get("pnr", ""), "missing": fields})
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
    ts = int(time.time() * 1000)
    with get_conn() as c:
        c.execute("""
            INSERT INTO auftraege
              (id, auftragsnr, datum, arbeit, kultur, kultur_frei, topfgroesse,
               auftrag_start, auftrag_ende, gesamtstueck, status, sonst,
               created_ms, updated_ms, changed_by)
            VALUES
              (:id, :auftragsnr, :datum, :arbeit, :kultur, :kultur_frei, :topfgroesse,
               :auftrag_start, '', NULL, 'offen', :sonst, :ts, :ts, :changed_by)
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
                   created_ms, updated_ms, changed_by)
                VALUES
                  (:id, :auftragsnr, :datum, :arbeit, :kultur, :kultur_frei, :topfgroesse,
                   :auftrag_start, '', NULL, 'offen', :sonst, :ts, :ts, :changed_by)
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
                updated_ms=?, changed_by=?
            WHERE id=?
        """, (auftrag_ende, gesamtstueck, ts, closed_by, old_auftrag_id))

        new_data = dict(new_data)
        new_data["ts"] = ts
        auftragsnr = _insert_auftrag_with_unique_nr(c, new_data)

        transferred = []
        for w in transfer_workers:
            c.execute("""
                INSERT INTO mitarbeiter_zeiten
                  (id, auftrag_id, pnr, rolle, start, ende, pause, created_ms, updated_ms)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (w["id"], new_data["id"], w["pnr"], w["rolle"],
                  new_data.get("auftrag_start", ""), "", 0, ts, ts))
            transferred.append(w["pnr"])
        c.commit()
    return auftragsnr, transferred


def get_mitarbeiter_by_id(mz_id: str) -> Optional[dict]:
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM mitarbeiter_zeiten WHERE id=?", (mz_id,)).fetchone()
        return dict(row) if row else None


def update_auftrag(auftrag_id: str, fields: dict):
    ts = int(time.time() * 1000)
    allowed = {"arbeit", "kultur", "kultur_frei", "topfgroesse", "auftrag_start",
               "auftrag_ende", "gesamtstueck", "status", "sonst", "changed_by"}
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
                updated_ms=?, changed_by=?
            WHERE id=?
        """, (auftrag_ende, gesamtstueck, ts, changed_by, auftrag_id))
        c.commit()


def upsert_mitarbeiter(data: dict):
    """Idempotentes Insert/Update eines Mitarbeiter-Zeiteintrags."""
    ts = int(time.time() * 1000)
    with get_conn() as c:
        c.execute("""
            INSERT INTO mitarbeiter_zeiten
              (id, auftrag_id, pnr, rolle, start, ende, pause, created_ms, updated_ms)
            VALUES
              (:id, :auftrag_id, :pnr, :rolle, :start, :ende, :pause, :created_ms, :updated_ms)
            ON CONFLICT(id) DO UPDATE SET
              rolle=excluded.rolle, start=excluded.start, ende=excluded.ende,
              pause=excluded.pause, updated_ms=excluded.updated_ms
            WHERE excluded.updated_ms >= mitarbeiter_zeiten.updated_ms
        """, {
            "id":         data["id"],
            "auftrag_id": data["auftrag_id"],
            "pnr":        data["pnr"],
            "rolle":      data.get("rolle", "service"),
            "start":      data.get("start", ""),
            "ende":       data.get("ende", ""),
            "pause":      data.get("pause", 0),
            "created_ms": data.get("created_ms", ts),
            "updated_ms": data.get("updated_ms", ts),
        })
        c.commit()


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
                a.kultur, a.kultur_frei, a.topfgroesse,
                a.auftrag_start, a.auftrag_ende, a.gesamtstueck,
                a.status, a.sonst,
                mz.id    AS mz_id,
                mz.pnr,  mz.rolle,
                mz.start AS mz_start,
                mz.ende  AS mz_ende,
                mz.pause AS mz_pause
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


