"""
Topfmaschine Mayer – FastAPI Backend.
Liefert die Web-App aus und stellt die gesamte API bereit.

Eigenstaendige Instanz analog zu Topfmaschine_Stolze, aber OHNE Modbus/LOGO!-
Zaehleranbindung - die Mayer-Maschine hat keinen Modbus-Zaehler, die
"Produzierte Menge" wird ausschliesslich manuell erfasst. Deshalb fehlen hier
bewusst: logo_poller.py, /api/zaehler/*, /api/auswertung/zaehler sowie die
zugehoerigen DB-Tabellen (siehe app/db.py).

Sicherheit:
- TM_ADMIN_PW muss beim Start gesetzt sein.
- Alle Admin-Endpunkte pruefen das Passwort serverseitig.
- Brute-Force-Lockout: 10 Fehlversuche -> 30s Sperre.
- Namen werden nicht gespeichert (DSGVO).
"""
import io
import csv
import os
import re
import sys
import time
import hmac
import secrets
import threading
import uuid
from datetime import datetime, date

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Any

from app import db

# ── Startup-Sicherheitspruefung ──────────────────────────────────────────────
ADMIN_PW   = os.environ.get("TM_ADMIN_PW", "")
VIEWER_PW  = os.environ.get("TM_VIEWER_PW", "")
_WEAK      = {"", "dominik", "admin", "test", "test123", "123456",
              "passwort", "password", "1234", "hallo", "topf", "maschine"}

if ADMIN_PW.lower() in _WEAK:
    print(
        "\n" + "=" * 60 + "\n"
        "FEHLER: TM_ADMIN_PW ist nicht gesetzt oder zu schwach.\n"
        "Bitte setzen: export TM_ADMIN_PW=MeinSicheresPasswort\n"
        + "=" * 60 + "\n",
        file=sys.stderr,
    )
    sys.exit(1)

# ── Konfiguration ─────────────────────────────────────────────────────────────
KULTUREN = [
    "Clematis", "Hortensie", "Aronia", "Maulbeere", "Tibouchina",
    "Lantana", "Buddleja", "Datura", "Passiflora rot", "Passiflora blau",
    "Bougainvillea", "Abutilon", "Lonicera", "Solanum ram.", "Polygnum",
    "Camelia", "Oleander", "Dicentra", "Pampasgras", "Paeonia",
    "Fuchsie", "Stauden 9er rund",
]  # Indizes 1-22; 23 = Freitext

ARBEITEN    = []  # Arbeitskategorien entfernt
TOPFGROESSEN = ["9er", "11er"]
PNR_MIN     = 1001
PNR_MAX     = 99999

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Topfmaschine Mayer – Erfassung")
db.init_db()

BASE   = os.path.dirname(__file__)
STATIC = os.path.join(BASE, "static")

# ── Auth / Brute-Force-Schutz ─────────────────────────────────────────────────
_LOCK_AFTER   = 10
_LOCK_SECONDS = 30
_auth_lock    = threading.Lock()
_auth_fails   = 0
_auth_blocked_until = 0.0


def _get_role(pw: str) -> Optional[str]:
    global _auth_fails, _auth_blocked_until
    if not pw:
        return None
    with _auth_lock:
        now = time.time()
        if now < _auth_blocked_until:
            wait = int(_auth_blocked_until - now) + 1
            raise HTTPException(429, f"Zu viele Fehlversuche – bitte {wait} s warten")
        if hmac.compare_digest(pw, ADMIN_PW):
            _auth_fails = 0
            return "admin"
        if VIEWER_PW and hmac.compare_digest(pw, VIEWER_PW):
            _auth_fails = 0
            return "viewer"
        _auth_fails += 1
        if _auth_fails >= _LOCK_AFTER:
            _auth_blocked_until = now + _LOCK_SECONDS
            _auth_fails = 0
        return None


def check_admin(pw: str):
    if _get_role(pw) != "admin":
        raise HTTPException(403, "Falsches Passwort")


def check_viewer(pw: str):
    if _get_role(pw) is None:
        raise HTTPException(403, "Falsches Passwort")


def _admin_ok(pw: str) -> bool:
    return _get_role(pw) == "admin"


_ACTION_SECRET = secrets.token_bytes(32)


def _auftrag_token(auftrag_id: str) -> str:
    """Nicht erratbarer, serverseitig geprüfter Token für Auftrag-Abschluss/
    Kulturwechsel. Wird bei jedem Laden eines Auftrags (`/api/auftrag/active`,
    `/api/auftrag/{id}`) mitgeliefert. Verhindert, dass ein blanker API-Aufruf
    ohne vorherigen, echten Seitenaufruf einen Auftrag abschließt oder einen
    Kulturwechsel auslöst – ohne dass sich am bestehenden 'kein Admin-Passwort
    nötig'-Workflow für Aufsichten etwas ändert. Secret lebt nur im
    Prozessspeicher, rotiert bei jedem Neustart."""
    return hmac.new(_ACTION_SECRET, f"auftrag|{auftrag_id}".encode("utf-8"), "sha256").hexdigest()[:32]


def _day_token(datum: str) -> str:
    """Analog zu _auftrag_token, für den Tagesabschluss (`/api/day/close`)."""
    return hmac.new(_ACTION_SECRET, f"day|{datum}".encode("utf-8"), "sha256").hexdigest()[:32]


def _actor(pw: str) -> str:
    """Rollen-Marker fuers Audit-Log. Absichtlich OHNE Passwort-Zeichen -
    vorher wurden die ersten 2 Zeichen des eingegebenen Passworts im Log
    gespeichert (Informationsleck bei kurzen/vorhersehbaren Passwoertern)."""
    if not pw:
        return "user"
    role = _get_role(pw)
    return role or "unknown"


# ── Validierung ───────────────────────────────────────────────────────────────
def validate_pnr(pnr: str) -> str:
    try:
        n = int(str(pnr).strip())
    except (ValueError, TypeError):
        raise HTTPException(400, "Personalnummer muss eine Zahl sein")
    if not (PNR_MIN <= n <= PNR_MAX):
        raise HTTPException(400, f"Personalnummer muss zwischen {PNR_MIN} und {PNR_MAX} liegen")
    return str(n)


def validate_topfgroesse(tg: Optional[str]) -> Optional[str]:
    if tg is None:
        return None
    if tg == "":
        return ""
    if tg not in TOPFGROESSEN:
        raise HTTPException(400, f"Topfgroesse muss '9er' oder '11er' sein")
    return tg


def validate_kultur(k: Optional[Any]) -> Optional[int]:
    if k is None:
        return None
    ki = int(k)
    if not (1 <= ki <= 23):
        raise HTTPException(400, f"Ungueltige Kultur: {ki} (erlaubt: 1-23)")
    return ki


_RE_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_VALID_ROLLEN = {"aufsicht", "service"}


def validate_time(val, field: str = "Zeitfeld") -> str:
    """Leere Werte erlaubt; befüllte Werte müssen HH:MM sein."""
    v = str(val or "").strip()
    if not v:
        return ""
    if not _RE_TIME.match(v):
        raise HTTPException(400, f"{field} muss leer oder im Format HH:MM sein")
    return v


def validate_date(val: str, field: str = "Datum") -> str:
    """Muss ein gültiges ISO-Datum (JJJJ-MM-TT) sein."""
    v = str(val or "").strip()
    try:
        date.fromisoformat(v)
    except ValueError:
        raise HTTPException(400, f"{field} muss ein gültiges Datum im Format JJJJ-MM-TT sein")
    return v


_PAUSE_MAX_MIN = 24 * 60


def validate_pause(pause, start: str = "", ende: str = "") -> float:
    """Pause darf nicht negativ, nicht unrealistisch groß und nicht größer
    als die aus Start/Ende berechnete Bruttoarbeitszeit sein."""
    if pause is None:
        return None
    try:
        p = float(pause or 0)
    except (ValueError, TypeError):
        raise HTTPException(400, "Pause muss eine Zahl sein")
    if p < 0:
        raise HTTPException(400, "Pause darf nicht negativ sein")
    if p * 60 > _PAUSE_MAX_MIN:
        raise HTTPException(400, "Pause ist unrealistisch groß")
    if start and ende:
        try:
            sh, sm = map(int, start.split(":"))
            eh, em = map(int, ende.split(":"))
            brutto_min = (eh * 60 + em) - (sh * 60 + sm)
            if brutto_min < 0:
                brutto_min += 24 * 60
            if p * 60 > brutto_min:
                raise HTTPException(400, "Pause darf nicht größer als die Arbeitszeit sein")
        except ValueError:
            pass
    return p


def validate_rolle(rolle) -> str:
    if rolle is None:
        return None
    if rolle not in _VALID_ROLLEN:
        raise HTTPException(400, "Rolle muss 'aufsicht' oder 'service' sein")
    return rolle


def _netto_h(start, ende, pause) -> float:
    if not start or not ende:
        return 0.0
    try:
        sh, sm = map(int, start.split(":"))
        eh, em = map(int, ende.split(":"))
    except ValueError:
        return 0.0
    mins = (eh * 60 + em) - (sh * 60 + sm)
    if mins < 0:
        mins += 24 * 60
    mins -= (pause or 0) * 60
    return max(0.0, mins / 60.0)


def _kultur_name(k, frei="") -> str:
    if k == 23:
        return frei or "Sonstiges"
    if k and 1 <= k <= len(KULTUREN):
        return KULTUREN[k - 1]
    return ""


def _nowHM() -> str:
    d = datetime.now()
    return f"{d.hour:02d}:{d.minute:02d}"


# ── Pydantic-Modelle ──────────────────────────────────────────────────────────
class PwAction(BaseModel):
    pw: str


class AuftragNewIn(BaseModel):
    id: str
    datum: str
    auftrag_start: str = ""
    arbeit: str = Field(default="", max_length=200)
    kultur: Optional[int] = None
    kultur_frei: str = Field(default="", max_length=100)
    topfgroesse: str = ""
    sonst: str = Field(default="", max_length=500)
    changed_by: str = ""


class AuftragUpdateIn(BaseModel):
    auftrag_id: str
    pw: str = ""
    arbeit: Optional[str] = Field(default=None, max_length=200)
    kultur: Optional[int] = None
    kultur_frei: Optional[str] = Field(default=None, max_length=100)
    topfgroesse: Optional[str] = None
    auftrag_start: Optional[str] = None
    sonst: Optional[str] = Field(default=None, max_length=500)
    gesamtstueck: Optional[int] = None


class ScanIn(BaseModel):
    auftrag_id: str
    mz_id: str
    pnr: str
    rolle: str = "service"
    start: str = ""
    updated_ms: Optional[int] = None


class WorkerUpdateIn(BaseModel):
    mz_id: str
    auftrag_id: str = ""
    start: Optional[str] = None
    ende: Optional[str] = None
    pause: Optional[float] = None
    rolle: Optional[str] = None
    updated_ms: Optional[int] = None
    pw: str = ""  # nur nötig wenn zugehöriger Auftrag/Tag bereits abgeschlossen ist


class WorkerDeleteIn(BaseModel):
    pw: str
    mz_id: str
    auftrag_id: str = ""


class AuftragDeleteIn(BaseModel):
    pw: str
    auftrag_id: str


class AuftragCloseIn(BaseModel):
    auftrag_id: str
    auftrag_ende: str
    gesamtstueck: int
    pw: str = ""
    token: str = ""


class NeueKulturIn(BaseModel):
    auftrag_id: str
    auftrag_ende: str
    gesamtstueck: int
    new_auftrag_id: str
    datum: str
    new_auftrag_start: str = ""
    arbeit: str = Field(default="", max_length=200)
    kultur: Optional[int] = None
    kultur_frei: str = Field(default="", max_length=100)
    topfgroesse: str = ""
    transfer_mz_ids: Optional[List[str]] = None
    pw: str = ""
    token: str = ""


class AuftragEditIn(BaseModel):
    pw: str
    auftrag_id: str
    arbeit: Optional[str] = Field(default=None, max_length=200)
    kultur: Optional[int] = None
    kultur_frei: Optional[str] = Field(default=None, max_length=100)
    topfgroesse: Optional[str] = None
    auftrag_start: Optional[str] = None
    auftrag_ende: Optional[str] = None
    gesamtstueck: Optional[int] = None
    status: Optional[str] = None
    sonst: Optional[str] = Field(default=None, max_length=500)


class WorkerEditIn(BaseModel):
    pw: str
    mz_id: str
    auftrag_id: str = ""
    start: Optional[str] = None
    ende: Optional[str] = None
    pause: Optional[float] = None
    rolle: Optional[str] = None


class AuswertungRequest(BaseModel):
    pw: str
    von: str
    bis: str


class AuditRequest(BaseModel):
    pw: str
    limit: int = 200


class DayCloseIn(BaseModel):
    datum: str
    pw: str = ""
    token: str = ""


def _abschluss_missing(auftrag: dict, auftrag_ende: Optional[str] = None,
                       gesamtstueck: Optional[int] = None) -> list:
    missing = []
    if not auftrag.get("auftrag_start"):
        missing.append("Auftragsstart")
    if auftrag.get("kultur") is None:
        missing.append("Kultur")
    elif int(auftrag.get("kultur")) == 23 and not (auftrag.get("kultur_frei") or "").strip():
        missing.append("Freitext-Kultur")
    if not auftrag.get("topfgroesse"):
        missing.append("Topfgroesse")
    if not (auftrag_ende or auftrag.get("auftrag_ende")):
        missing.append("Auftragsende")
    stueck = gesamtstueck if gesamtstueck is not None else auftrag.get("gesamtstueck")
    if stueck is None:
        missing.append("Gesamtstueck")
    for m in auftrag.get("mitarbeiter", []):
        ma_missing = []
        if not m.get("pnr"):
            ma_missing.append("Personalnummer")
        if not m.get("start"):
            ma_missing.append("Startzeit")
        if ma_missing:
            missing.append(f"Mitarbeiter {m.get('pnr') or m.get('id')}: {', '.join(ma_missing)}")
    return missing


def _raise_if_incomplete_for_close(auftrag: dict, auftrag_ende: str, gesamtstueck: int):
    missing = _abschluss_missing(auftrag, auftrag_ende, gesamtstueck)
    if missing:
        raise HTTPException(400, {"message": "Pflichtangaben fehlen", "missing": missing})


def _day_close_problems(datum: str) -> list:
    problems = []
    for a in db.list_auftraege(datum, datum):
        if a.get("status") != "abgeschlossen":
            problems.append({"auftrag": a.get("auftragsnr") or a.get("id"), "missing": ["Auftrag ist noch offen"]})
            continue
        missing = _abschluss_missing(a)
        for m in a.get("mitarbeiter", []):
            if not m.get("ende"):
                missing.append(f"Mitarbeiter {m.get('pnr') or m.get('id')}: Endzeit")
        if missing:
            problems.append({"auftrag": a.get("auftragsnr") or a.get("id"), "missing": missing})
    return problems


def _day_is_closed(datum: str) -> bool:
    return bool(db.get_day_close(datum).get("closed"))


# ── Statische Dateien ─────────────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/auswertung")
def auswertung_page():
    return FileResponse(os.path.join(STATIC, "auswertung.html"))


@app.get("/sync.js")
def sync_js():
    return FileResponse(os.path.join(STATIC, "sync.js"), media_type="application/javascript")


@app.get("/logo_DOMINIK.png")
def logo():
    return FileResponse(os.path.join(STATIC, "logo_DOMINIK.png"), media_type="image/png")


@app.get("/logo_DOMINIK_white.png")
def logo_white():
    return FileResponse(os.path.join(STATIC, "logo_DOMINIK_white.png"), media_type="image/png")


# ── Health + Config ────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time() * 1000), "app": "Topfmaschine",
            "pnr_min": PNR_MIN, "pnr_max": PNR_MAX}


@app.get("/api/config")
def api_config():
    return {
        "kulturen":     KULTUREN,
        "arbeiten":     ARBEITEN,
        "topfgroessen": TOPFGROESSEN,
        "pnr_min":      PNR_MIN,
        "pnr_max":      PNR_MAX,
    }


# ── Aktiver Auftrag ───────────────────────────────────────────────────────────
@app.get("/api/auftrag/active")
def get_active():
    result = db.get_active_auftrag()
    if not result:
        return {"id": None, "status": "none"}
    result["close_token"] = _auftrag_token(result["id"])
    return result


@app.post("/api/auftrag/new")
def create_new_auftrag(body: AuftragNewIn):
    """Erstellt einen neuen Auftrag. Wird aufgerufen, wenn die Aufsicht erfasst wird."""
    # Idempotenz: Wurde diese Operation bereits erfolgreich verarbeitet (z.B.
    # Client hat nach einem Netzwerk-Timeout erneut gesendet, der Server hatte
    # aber schon gespeichert), wird der bestehende Auftrag als Erfolg
    # quittiert statt eines Fehlers - sonst blockiert die Outbox dauerhaft.
    existing = db.get_auftrag_by_id(body.id)
    if existing:
        return {"ok": True, "auftrag_id": existing["id"], "auftragsnr": existing["auftragsnr"]}
    validate_date(body.datum, "Datum")
    if _day_is_closed(body.datum):
        raise HTTPException(400, "Tag ist bereits abgeschlossen")
    auftragsnr = db.create_auftrag_atomic({
        "id":           body.id,
        "datum":        body.datum,
        "arbeit":       body.arbeit,
        "kultur":       validate_kultur(body.kultur),
        "kultur_frei":  body.kultur_frei,
        "topfgroesse":  validate_topfgroesse(body.topfgroesse),
        "auftrag_start": validate_time(body.auftrag_start, "Auftragsstart") or _nowHM(),
        "sonst":        body.sonst,
        "changed_by":   body.changed_by or "user",
    })
    db.log_audit("auftrag_new", body.changed_by or "user", body.id,
                 f"auftragsnr={auftragsnr},datum={body.datum}")
    return {"ok": True, "auftrag_id": body.id, "auftragsnr": auftragsnr}


@app.post("/api/auftrag/update")
def update_auftrag(body: AuftragUpdateIn):
    """Aktualisiert Auftragsdaten (Arbeit, Kultur, Topfgroesse etc.)."""
    auftrag = db.get_auftrag_by_id(body.auftrag_id)
    if not auftrag:
        raise HTTPException(404, "Auftrag nicht gefunden")
    if auftrag["status"] == "abgeschlossen":
        check_admin(body.pw)

    fields: dict = {}
    if body.arbeit is not None:
        fields["arbeit"] = body.arbeit
    if body.kultur is not None:
        fields["kultur"] = validate_kultur(body.kultur)
    if body.kultur_frei is not None:
        fields["kultur_frei"] = body.kultur_frei
    if body.topfgroesse is not None:
        fields["topfgroesse"] = validate_topfgroesse(body.topfgroesse)
    if body.auftrag_start is not None:
        fields["auftrag_start"] = body.auftrag_start
    if body.sonst is not None:
        fields["sonst"] = body.sonst
    if body.gesamtstueck is not None:
        if body.gesamtstueck < 0:
            raise HTTPException(400, "Gesamtstueckzahl muss >= 0 sein")
        fields["gesamtstueck"] = body.gesamtstueck
    fields["changed_by"] = _actor(body.pw) if body.pw else "user"
    db.update_auftrag(body.auftrag_id, fields)
    return {"ok": True}


@app.post("/api/auftrag/scan")
def scan_worker(body: ScanIn):
    """Fuegt einen Mitarbeiter (Aufsicht oder Service) zum aktiven Auftrag hinzu."""
    pnr = validate_pnr(body.pnr)
    if body.rolle not in ("aufsicht", "service"):
        raise HTTPException(400, "Rolle muss 'aufsicht' oder 'service' sein")

    auftrag = db.get_auftrag_by_id(body.auftrag_id)
    if not auftrag:
        raise HTTPException(404, "Auftrag nicht gefunden")
    if _day_is_closed(auftrag.get("datum", "")):
        raise HTTPException(400, "Tag ist bereits abgeschlossen")

    ts = body.updated_ms or int(time.time() * 1000)
    db.upsert_mitarbeiter({
        "id":         body.mz_id,
        "auftrag_id": body.auftrag_id,
        "pnr":        pnr,
        "rolle":      body.rolle,
        "start":      body.start or _nowHM(),
        "ende":       "",
        "pause":      0,
        "created_ms": ts,
        "updated_ms": ts,
    })
    if body.rolle == "aufsicht":
        db.log_audit("aufsicht_add", "user", body.auftrag_id, f"pnr={pnr},start={body.start or _nowHM()}")
    return {"ok": True, "pnr": pnr, "rolle": body.rolle}


@app.post("/api/worker/update")
def update_worker(body: WorkerUpdateIn):
    """Aktualisiert Zeiten/Pause/Rolle eines Mitarbeiters. Sobald der
    zugehoerige Auftrag abgeschlossen oder der Tag abgeschlossen ist, ist
    eine Aenderung nur noch mit gueltigem Admin-Passwort moeglich - vorher
    konnte jeder Client Mitarbeiterdaten jederzeit ueberschreiben, auch nach
    Abschluss."""
    mz = db.get_mitarbeiter_by_id(body.mz_id)
    if mz is None:
        raise HTTPException(404, "Mitarbeiter-Eintrag nicht gefunden")
    auftrag = db.get_auftrag_by_id(mz["auftrag_id"])
    locked = bool(auftrag) and (
        auftrag.get("status") == "abgeschlossen" or _day_is_closed(auftrag.get("datum", ""))
    )
    if locked and not _admin_ok(body.pw):
        raise HTTPException(400, "Auftrag/Tag abgeschlossen – Änderung nur im Admin-Modus")

    ts = body.updated_ms or int(time.time() * 1000)
    fields: dict = {}
    if body.start  is not None: fields["start"]  = validate_time(body.start, "start")
    if body.ende   is not None: fields["ende"]   = validate_time(body.ende, "ende")
    if body.pause  is not None: fields["pause"]  = validate_pause(
        body.pause, fields.get("start", mz.get("start", "")), fields.get("ende", mz.get("ende", "")))
    if body.rolle  is not None: fields["rolle"]  = validate_rolle(body.rolle)
    if not fields:
        return {"ok": True}
    sets = ", ".join(f"{k}=:{k}" for k in fields)
    fields["mz_id"] = body.mz_id
    fields["ts"]    = ts
    with db.get_conn() as c:
        c.execute(f"UPDATE mitarbeiter_zeiten SET {sets}, updated_ms=:ts WHERE id=:mz_id",
                  fields)
        c.commit()
    db.log_audit("worker_update", _actor(body.pw) if body.pw else "user", body.mz_id,
                 f"auftrag={mz['auftrag_id']},locked={locked},fields={list(fields.keys())}")
    return {"ok": True}


@app.post("/api/worker/delete")
def delete_worker(body: WorkerDeleteIn):
    check_admin(body.pw)
    db.remove_mitarbeiter(body.mz_id)
    db.log_audit("worker_delete", _actor(body.pw), body.mz_id,
                 f"auftrag={body.auftrag_id}")
    return {"ok": True}


@app.post("/api/admin/auftrag/delete")
def delete_auftrag_endpoint(body: AuftragDeleteIn):
    """Admin: Loescht einen Auftrag und alle zugehoerigen Mitarbeiterdaten vollstaendig."""
    check_admin(body.pw)
    auftrag = db.get_auftrag_by_id(body.auftrag_id)
    if not auftrag:
        raise HTTPException(404, "Auftrag nicht gefunden")
    auftragsnr = auftrag.get("auftragsnr", "?")
    db.delete_auftrag(body.auftrag_id)
    db.log_audit("auftrag_delete", _actor(body.pw), body.auftrag_id,
                 f"auftragsnr={auftragsnr},datum={auftrag.get('datum','?')}")
    return {"ok": True}


@app.post("/api/auftrag/close")
def close_auftrag(body: AuftragCloseIn):
    """Schliesst den Auftrag ab. Setzt Endzeit fuer alle noch aktiven Mitarbeiter."""
    auftrag = db.get_auftrag_by_id(body.auftrag_id)
    if not auftrag:
        raise HTTPException(404, "Auftrag nicht gefunden")
    if auftrag["status"] == "abgeschlossen":
        # Idempotenz: Diese Operation wurde bereits erfolgreich verarbeitet
        # (z.B. Retry nach Netzwerk-Timeout). Als Erfolg quittieren statt
        # eines Fehlers, der die Offline-Outbox dauerhaft blockieren wuerde.
        return {"ok": True, "already_closed": True}
    expected = _auftrag_token(body.auftrag_id)
    if not body.token or not hmac.compare_digest(body.token, expected):
        raise HTTPException(403, "Ungültiger Vorgang – Seite neu laden und erneut versuchen")
    if body.gesamtstueck < 0:
        raise HTTPException(400, "Gesamtstueckzahl muss >= 0 sein")
    validate_time(body.auftrag_ende, "Auftragsende")
    _raise_if_incomplete_for_close(auftrag, body.auftrag_ende, body.gesamtstueck)

    db.set_all_active_workers_ende(body.auftrag_id, body.auftrag_ende)
    db.close_auftrag(body.auftrag_id, body.auftrag_ende, body.gesamtstueck,
                     _actor(body.pw) if body.pw else "user")
    db.log_audit("auftrag_close", _actor(body.pw) if body.pw else "user",
                 body.auftrag_id,
                 f"ende={body.auftrag_ende},stueck={body.gesamtstueck}")
    return {"ok": True}


@app.post("/api/auftrag/neue_kultur")
def neue_kultur(body: NeueKulturIn):
    """
    Schliesst den aktuellen Auftrag ab und startet einen neuen.
    Noch aktive Mitarbeiter werden in den neuen Auftrag uebernommen.
    Mitarbeiter mit eigener Endzeit werden NICHT uebernommen.
    """
    if body.gesamtstueck < 0:
        raise HTTPException(400, "Gesamtstueckzahl muss >= 0 sein")
    validate_date(body.datum, "Datum")

    # Idempotenz: Existiert der neue Auftrag bereits, wurde dieser
    # Kulturwechsel schon einmal erfolgreich durchgefuehrt (z.B. Retry nach
    # Netzwerk-Timeout, Server hatte aber schon gespeichert). Als Erfolg
    # quittieren statt eines Fehlers ("Auftrag bereits abgeschlossen"), der
    # die Offline-Outbox sonst dauerhaft blockieren wuerde.
    existing_new = db.get_auftrag_by_id(body.new_auftrag_id)
    if existing_new:
        return {
            "ok":             True,
            "new_auftrag_id": existing_new["id"],
            "auftragsnr":     existing_new["auftragsnr"],
            "transferred":    [w["pnr"] for w in existing_new.get("mitarbeiter", [])],
            "new_start":      existing_new.get("auftrag_start", ""),
        }

    auftrag = db.get_auftrag_by_id(body.auftrag_id)
    if not auftrag:
        raise HTTPException(404, "Auftrag nicht gefunden")
    if auftrag["status"] == "abgeschlossen":
        raise HTTPException(400, "Auftrag ist bereits abgeschlossen")
    expected = _auftrag_token(body.auftrag_id)
    if not body.token or not hmac.compare_digest(body.token, expected):
        raise HTTPException(403, "Ungültiger Vorgang – Seite neu laden und erneut versuchen")
    validate_time(body.auftrag_ende, "Auftragsende")
    _raise_if_incomplete_for_close(auftrag, body.auftrag_ende, body.gesamtstueck)

    # Nur Mitarbeiter OHNE eigene Endzeit werden uebernommen; im Client kann die Aufsicht die Liste anpassen.
    active_workers = [w for w in auftrag["mitarbeiter"] if not w.get("ende")]
    if body.transfer_mz_ids is not None:
        wanted = set(body.transfer_mz_ids)
        active_workers = [w for w in active_workers if w.get("id") in wanted]

    new_start = body.new_auftrag_start or body.auftrag_ende or _nowHM()
    transfer_workers = [
        {"id": str(uuid.uuid4()), "pnr": w["pnr"], "rolle": w["rolle"]}
        for w in active_workers
    ]

    # Kompletter Kulturwechsel (alten Auftrag abschliessen + neuen anlegen +
    # Mitarbeiter uebernehmen) in EINER Transaktion - kein Zwischenzustand
    # bei Fehler/Neustart moeglich.
    auftragsnr, transferred = db.neue_kultur_atomic(
        body.auftrag_id, body.auftrag_ende, body.gesamtstueck,
        _actor(body.pw) if body.pw else "system",
        {
            "id":           body.new_auftrag_id,
            "datum":        body.datum,
            "arbeit":       body.arbeit,
            "kultur":       validate_kultur(body.kultur),
            "kultur_frei":  body.kultur_frei,
            "topfgroesse":  validate_topfgroesse(body.topfgroesse),
            "auftrag_start": new_start,
            "changed_by":   "system",
        },
        transfer_workers,
    )

    db.log_audit("neue_kultur", _actor(body.pw) if body.pw else "system",
                 body.new_auftrag_id,
                 f"von={body.auftrag_id},stueck={body.gesamtstueck},"
                 f"uebernommen={len(transferred)},nr={auftragsnr}")

    return {
        "ok":             True,
        "new_auftrag_id": body.new_auftrag_id,
        "auftragsnr":     auftragsnr,
        "transferred":    transferred,
        "new_start":      new_start,
    }


# ── Auftraege abrufen ─────────────────────────────────────────────────────────
@app.get("/api/auftraege")
def list_auftraege(
    von: str = Query(default_factory=lambda: date.today().isoformat()),
    bis: str = Query(default_factory=lambda: date.today().isoformat()),
):
    return db.list_auftraege(von, bis)


@app.get("/api/auftrag/{auftrag_id}")
def get_auftrag(auftrag_id: str):
    a = db.get_auftrag_by_id(auftrag_id)
    if not a:
        raise HTTPException(404, "Auftrag nicht gefunden")
    a["close_token"] = _auftrag_token(a["id"])
    return a


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.post("/api/day/close")
def day_close(body: DayCloseIn):
    if _day_is_closed(body.datum):
        # Idempotenz: Tag ist bereits abgeschlossen (z.B. Retry nach
        # Netzwerk-Timeout) - als Erfolg quittieren statt eines Fehlers.
        return {"ok": True, "datum": body.datum, "already_closed": True}
    expected = _day_token(body.datum)
    if not body.token or not hmac.compare_digest(body.token, expected):
        raise HTTPException(403, "Ungültiger Vorgang – Seite neu laden und erneut versuchen")
    problems = _day_close_problems(body.datum)
    if problems:
        raise HTTPException(400, {"message": "Tagesabschluss nicht moeglich", "problems": problems})
    actor = _actor(body.pw) if body.pw else "user"
    db.close_day(body.datum, actor)
    db.log_audit("day_close", actor, body.datum, "alle Auftraege vollstaendig")
    return {"ok": True, "datum": body.datum}


@app.get("/api/day/status")
def day_status(datum: str = Query(default_factory=lambda: date.today().isoformat())):
    status = db.get_day_close(datum)
    status["problems"] = _day_close_problems(datum)
    status["close_token"] = _day_token(datum)
    return status
@app.post("/api/admin/day/reopen")
def admin_day_reopen(body: DayCloseIn):
    check_admin(body.pw)
    db.reopen_day(body.datum, "admin")
    db.log_audit("day_reopen", "admin", body.datum, "Tag wieder geöffnet")
    return {"ok": True, "datum": body.datum}


@app.post("/api/admin/verify")
def admin_verify(body: PwAction):
    check_admin(body.pw)
    return {"ok": True}


@app.post("/api/auswertung/verify")
def auswertung_verify(body: PwAction):
    check_viewer(body.pw)
    return {"ok": True}


@app.post("/api/admin/auftrag/edit")
def admin_edit_auftrag(body: AuftragEditIn):
    """Admin: Korrektur eines abgeschlossenen oder offenen Auftrags."""
    check_admin(body.pw)
    auftrag = db.get_auftrag_by_id(body.auftrag_id)
    if not auftrag:
        raise HTTPException(404, "Auftrag nicht gefunden")

    fields: dict = {}
    if body.arbeit       is not None: fields["arbeit"]       = body.arbeit
    if body.kultur       is not None: fields["kultur"]       = validate_kultur(body.kultur)
    if body.kultur_frei  is not None: fields["kultur_frei"]  = body.kultur_frei
    if body.topfgroesse  is not None: fields["topfgroesse"]  = body.topfgroesse
    if body.auftrag_start is not None: fields["auftrag_start"] = body.auftrag_start
    if body.auftrag_ende  is not None: fields["auftrag_ende"]  = body.auftrag_ende
    if body.gesamtstueck  is not None: fields["gesamtstueck"]  = body.gesamtstueck
    if body.status        is not None: fields["status"]         = body.status
    if body.sonst         is not None: fields["sonst"]          = body.sonst
    fields["changed_by"] = _actor(body.pw)
    db.update_auftrag(body.auftrag_id, fields)
    db.log_audit("admin_auftrag_edit", _actor(body.pw), body.auftrag_id, str(fields))
    return {"ok": True}


@app.post("/api/admin/worker/edit")
def admin_edit_worker(body: WorkerEditIn):
    """Admin: Korrektur einzelner Mitarbeiter-Zeiten."""
    check_admin(body.pw)
    fields: dict = {}
    if body.start  is not None: fields["start"]  = body.start
    if body.ende   is not None: fields["ende"]   = body.ende
    if body.pause  is not None: fields["pause"]  = body.pause
    if body.rolle  is not None: fields["rolle"]  = body.rolle
    if fields:
        ts = int(time.time() * 1000)
        sets = ", ".join(f"{k}=:{k}" for k in fields)
        fields["mz_id"] = body.mz_id
        fields["ts"]    = ts
        with db.get_conn() as c:
            c.execute(
                f"UPDATE mitarbeiter_zeiten SET {sets}, updated_ms=:ts WHERE id=:mz_id",
                fields,
            )
            c.commit()
    db.log_audit("admin_worker_edit", _actor(body.pw), body.mz_id,
                 f"auftrag={body.auftrag_id},{fields}")
    return {"ok": True}


@app.post("/api/audit")
def audit_log_view(req: AuditRequest):
    check_admin(req.pw)
    return db.get_audit_log(req.limit)


# ── Auswertung ────────────────────────────────────────────────────────────────
def _build_auftrag_summary(rows: list) -> list:
    """Aggregiert flache DB-Zeilen zu Auftrags-Zusammenfassungen."""
    auftraege: dict = {}
    for r in rows:
        aid = r["id"]
        if aid not in auftraege:
            auftraege[aid] = {
                "id":           aid,
                "auftragsnr":   r["auftragsnr"],
                "datum":        r["datum"],
                "kultur":       _kultur_name(r["kultur"], r.get("kultur_frei", "")),
                "topfgroesse":  r["topfgroesse"] or "",
                "arbeit":       r["arbeit"] or "",
                "auftrag_start": r["auftrag_start"] or "",
                "auftrag_ende": r["auftrag_ende"] or "",
                "gesamtstueck": r["gesamtstueck"],
                "status":       r["status"],
                "workers":      [],
                "total_mh":     0.0,
                "pnrs":         set(),
            }
        if r.get("mz_id"):
            nh = _netto_h(r.get("mz_start"), r.get("mz_ende"), r.get("mz_pause"))
            auftraege[aid]["workers"].append({
                "pnr":    r["pnr"],
                "rolle":  r["rolle"],
                "start":  r.get("mz_start") or "",
                "ende":   r.get("mz_ende") or "",
                "pause":  r.get("mz_pause") or 0,
                "netto_h": round(nh, 2),
            })
            auftraege[aid]["total_mh"] += nh
            auftraege[aid]["pnrs"].add(r["pnr"])

    result = []
    for a in auftraege.values():
        mh   = a["total_mh"]
        stk  = a["gesamtstueck"] or 0
        sph  = round(stk / mh, 1) if mh > 0 else None
        missing_fields = []
        if not a["auftrag_start"]:
            missing_fields.append("Auftragsstart")
        if not a["kultur"]:
            missing_fields.append("Kultur")
        if not a["topfgroesse"]:
            missing_fields.append("Topfgroesse")
        if a["status"] == "abgeschlossen":
            if not a["auftrag_ende"]:
                missing_fields.append("Auftragsende")
            if a["gesamtstueck"] is None:
                missing_fields.append("Gesamtstueck")
            for wk in a["workers"]:
                if not wk.get("start"):
                    missing_fields.append(f"Mitarbeiter {wk.get('pnr')}: Startzeit")
                if not wk.get("ende"):
                    missing_fields.append(f"Mitarbeiter {wk.get('pnr')}: Endzeit")
        result.append({
            "id":           a["id"],
            "auftragsnr":   a["auftragsnr"],
            "datum":        a["datum"],
            "kultur":       a["kultur"],
            "topfgroesse":  a["topfgroesse"],
            "arbeit":       a["arbeit"],
            "auftrag_start": a["auftrag_start"],
            "auftrag_ende": a["auftrag_ende"],
            "gesamtstueck": a["gesamtstueck"],
            "status":       a["status"],
            "anzahl_ma":    len(a["pnrs"]),
            "total_mh":     round(mh, 2),
            "stueck_pro_mah": sph,
            "workers":      a["workers"],
            "missing_fields": missing_fields,
            "has_missing_data": bool(missing_fields),
        })
    result.sort(key=lambda x: (x["datum"], x["auftragsnr"]))
    return result


@app.post("/api/auswertung/summary")
def auswertung_summary(req: AuswertungRequest):
    check_viewer(req.pw)
    rows   = db.get_auftraege_flat(req.von, req.bis)
    return _build_auftrag_summary(rows)


@app.post("/api/auswertung/kulturen")
def auswertung_kulturen(req: AuswertungRequest):
    check_viewer(req.pw)
    rows = db.get_auftraege_flat(req.von, req.bis)
    summaries = _build_auftrag_summary(rows)

    agg: dict = {}
    for a in summaries:
        if a["status"] != "abgeschlossen":
            continue
        k = a["kultur"] or "Unbekannt"
        if k not in agg:
            agg[k] = {"kultur": k, "stueck": 0, "mh": 0.0, "auftraege": 0}
        agg[k]["stueck"]    += a["gesamtstueck"] or 0
        agg[k]["mh"]        += a["total_mh"]
        agg[k]["auftraege"] += 1

    result = []
    for a in agg.values():
        sph = round(a["stueck"] / a["mh"], 1) if a["mh"] > 0 else None
        result.append({**a, "mh": round(a["mh"], 2), "stueck_pro_mah": sph})
    result.sort(key=lambda x: -(x["stueck_pro_mah"] or 0))
    return result


@app.post("/api/export.csv")
def export_csv(req: AuswertungRequest):
    check_viewer(req.pw)
    rows      = db.get_auftraege_flat(req.von, req.bis)
    summaries = _build_auftrag_summary(rows)
    db.log_audit("csv_export", _actor(req.pw), None, f"von={req.von},bis={req.bis}")

    DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow([
        "Tag", "Datum", "Auftragsnr", "Kultur", "Topfgroesse", "Arbeit",
        "Pers.nr.", "Rolle", "Start", "Ende", "Pause_h", "Netto_h",
        "Gesamtstueck", "Stueck_pro_MA-Std", "Status",
    ])
    for a in summaries:
        try:
            d   = date.fromisoformat(a["datum"])
            tag = DAY_NAMES[d.weekday()]
        except Exception:
            tag = ""
        for wk in (a["workers"] or [{"pnr": "—", "rolle": "—", "start": "", "ende": "",
                                      "pause": 0, "netto_h": 0}]):
            w.writerow([
                tag, a["datum"], a["auftragsnr"],
                a["kultur"], a["topfgroesse"], a["arbeit"],
                wk["pnr"], wk["rolle"],
                wk.get("start", ""), wk.get("ende", ""),
                str(wk.get("pause", 0)).replace(".", ","),
                str(wk.get("netto_h", 0)).replace(".", ","),
                a["gesamtstueck"] if a["gesamtstueck"] is not None else "",
                str(a["stueck_pro_mah"] or "").replace(".", ","),
                a["status"],
            ])
    buf.seek(0)
    fn = f"Topfmaschine_Mayer_{req.von}_{req.bis}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fn}"'},
    )
