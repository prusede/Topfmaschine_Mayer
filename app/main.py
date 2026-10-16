"""Topfmaschine Mayer – FastAPI Backend.
Liefert die Web-App aus und stellt die gesamte API bereit.

Eigenstaendige Instanz analog zu Topfmaschine_Stolze, aber OHNE Modbus/LOGO!-
Zaehleranbindung - die Mayer-Maschine hat keinen Modbus-Zaehler, die
"Produzierte Menge" wird ausschliesslich manuell erfasst. Deshalb fehlen hier
bewusst: logo_poller.py, /api/zaehler/*, /api/auswertung/zaehler sowie die
zugehoerigen DB-Tabellen (siehe app/db.py).

Sicherheit:
- Zugriffsrechte kommen ausschliesslich aus der zentralen Benutzerverwaltung
  (Auth-Dienst auf 8451). Rollen je Anwendung: auswerter, meister, admin.
- Einmal anmelden genuegt; ein zweites Passwort fuer Aenderungen gibt es nicht.
- Der Scanbetrieb laeuft weiterhin ohne Anmeldung, auch ueber HTTP 8084.
- Geteilte Passwoerter gibt es nicht mehr; die Fehlversuchssperre liegt im
  Auth-Dienst.
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
import uuid
from datetime import datetime, date
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
    FileResponse,
    RedirectResponse,
)
from pydantic import BaseModel, Field
from typing import Optional, List, Any, Literal

import erfassung_auth as auth

from app import db, plan_db
from app.kulturen import KULTUREN, KULTUR_FREITEXT, TOPFGROESSEN

# ── Startup-Sicherheitspruefung ──────────────────────────────────────────────
# An dieser Stelle stand bis Schritt 5 die Pruefung auf TM_ADMIN_PW. Es gibt
# keine geteilten Passwoerter mehr. Was jetzt zaehlt, ist das Verzeichnis mit
# den oeffentlichen Schluesseln: fehlt es, kann die Anwendung keine Sitzung mehr
# pruefen und waere fuer Auswertung und Admin-Funktionen still tot. Ein klarer
# Abbruch beim Start ist besser als ein unerklaerliches 403 im Betrieb.
_KEY_DIR = os.environ.get("AUTH_PUBLIC_KEY_DIR", "/etc/erfassung-auth/public")

if not (os.path.isdir(_KEY_DIR) and any(
        n.endswith(".pem") for n in os.listdir(_KEY_DIR))):
    print(
        "\n" + "=" * 60 + "\n"
        f"FEHLER: kein oeffentlicher Schluessel in {_KEY_DIR}.\n"
        "Ohne ihn kann keine Anmeldung geprueft werden.\n"
        "Auf dem CT anlegen mit gen_keys.sh aus erfassung_auth_dienst.\n"
        + "=" * 60 + "\n",
        file=sys.stderr,
    )
    sys.exit(1)

# ── Konfiguration ─────────────────────────────────────────────────────────────
ARBEITEN = []  # Arbeitskategorien entfernt

# Feste Pausenfenster der Anlage. Sie lagen bisher nur im Frontend
# (calcAutoPause in index.html) und damit ausserhalb jeder serverseitigen
# Rechnung. Fuer die stuendliche Auswertung werden sie hier gebraucht;
# ausgeliefert werden sie ueber /api/config, damit es genau eine Quelle gibt
# und Erfassung und Auswertung nicht auseinanderlaufen koennen.
PAUSEN_FENSTER = [("09:00", "09:15"), ("12:00", "12:15"), ("15:00", "15:15")]

PNR_MIN     = 1001
PNR_MAX     = 99999
# Sammelzeile: mehrere Personen ohne einzelne Personalnummer in einer Zeile.
# Obergrenze als Plausibilitaetsbremse gegen Tippfehler (z.B. 60 statt 6).
SAMMEL_MAX  = 20

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Topfmaschine Mayer – Erfassung")

# Zentrale Anmeldung. Die Middleware liest die beiden Cookies und stellt eine
# Identitaet bereit; ausgestellt werden die Tokens allein vom Auth-Dienst. Fehlt
# das Schluesselverzeichnis oder das Cookie, wird schlicht keine Identitaet
# erkannt und die Anwendung verhaelt sich exakt wie vor der Umstellung.
AUTH_CFG = auth.AuthConfig(
    app_key="topfmaschine_mayer",
    allowed_origins=[os.environ.get("TM_ORIGIN", "https://10.10.0.210:8447")],
    # Gleicher Variablenname wie im Auth-Dienst. Im Betrieb bleibt es beim
    # Vorgabewert; die Tests legen sich ein eigenes Verzeichnis an.
    public_key_dir=_KEY_DIR,
    legacy_enabled=False,
)
auth.install(app, AUTH_CFG)

db.init_db()

BASE   = os.path.dirname(__file__)
STATIC = os.path.join(BASE, "static")

# ── Torwaechter ───────────────────────────────────────────────────────────────
#
# Rechte kommen ausschliesslich aus der Benutzerverwaltung. Der Parameter pw
# bleibt in den Signaturen stehen, damit die rund 30 Aufrufstellen unveraendert
# bleiben; ausgewertet wird er nirgends mehr. Das Feld pw in den Modellen bleibt
# ebenfalls, damit ein noch nicht neu geladener Tablet-Browser keinen 422
# bekommt.
#
# Ueber HTTP (Port 8084, Scanbetrieb) erreicht das __Host--Cookie den Server
# nie. Geschuetzte Endpunkte antworten dort mit 403 -- das ist die gewollte
# Aufgabenteilung, kein Mangel: angemeldet wird ausschliesslich ueber HTTPS
# 8447, gescannt ausschliesslich ueber HTTP 8084.


def check_admin(pw: str = ""):
    """Aendernd: Rolle meister oder admin. Kein zweites Passwort.

    Das Step-up aus Vorgabe 3 des Konzepts ist fuer diese Anwendung bewusst
    abgeschaltet (Entscheidung 30.07.2026). Es haette bedeutet, dass die
    Aufsicht sich anmeldet und danach fuer jede Korrektur ein zweites Mal ihr
    Passwort eingibt -- eine Abfrage mehr als vor der Umstellung und damit
    genau das, was Vorgabe 0 ausschliesst.

    Was den Schutz traegt, ist die kurze Sitzung: in der Erfassung laeuft sie
    nach spaetestens 60 Minuten ab und erneuert sich dort nicht von selbst.
    Ein Tablet, das jemand offen stehen laesst, ist damit nicht den ganzen Tag
    schreibfaehig."""
    auth.require("meister")


def _is_today(datum: str) -> bool:
    return datum == date.today().isoformat()


def _require_write_for_date(datum: str, pw: str = ""):
    """Aenderungen ausserhalb des heutigen Produktionstags nur ab Meister.

    Die Endpunkte des laufenden Scanbetriebs bleiben fuer heute bewusst offen.
    Sobald ein Auftrag zu einem anderen Datum gehoert, gilt dagegen dieselbe
    zentrale Rollenpruefung wie bei einer administrativen Korrektur."""
    heute = date.today().isoformat()
    if datum > heute:
        raise HTTPException(400, "Zukünftige Tage können nicht als Nachtrag bearbeitet werden")
    if datum != heute:
        check_admin(pw)


def check_viewer(pw: str = ""):
    """Lesend: jede Rolle fuer diese Anwendung genuegt."""
    auth.require("auswerter")


def _admin_ok(pw: str = "") -> bool:
    """Weiche Pruefung fuer Endpunkte, die erst im gesperrten Zustand ein Recht
    verlangen (abgeschlossener Auftrag, abgeschlossener Tag). Ohne Sperre laeuft
    der Aufruf weiterhin ohne Anmeldung durch."""
    return auth.role() in ("meister", "admin")


def _nur_admin():
    """Dem Admin vorbehalten: das Audit-Protokoll.

    Nachtraeglich bearbeiten stand hier bis zum 30.07.2026 ebenfalls -- die
    Aufsicht braucht es im Alltag, deshalb hat der Meister es jetzt auch. Das
    Protokoll bleibt beim Admin: es ist das Mittel, mit dem Korrekturen
    kontrolliert werden, und gehoert damit nicht in die Hand derselben Person,
    die sie vornimmt."""
    auth.require("admin")


def _ueber_https(request: Request) -> bool:
    """Caddy setzt X-Forwarded-Proto; der direkte Zugang auf 8084 nicht.

    request.url.scheme taugt hier nicht als alleiniges Merkmal: ob dort https
    steht, haengt daran, dass uvicorn die Weiterleitungskoepfe auswertet. Der
    Kopf selbst ist eindeutig."""
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


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


def _actor(pw: str = "") -> str:
    """Eintrag fuer die Spalte actor im Audit-Log.

    Angemeldet: der Anmeldename -- der eigentliche Zweck des Vorhabens, weil im
    Protokoll damit steht, wer gehandelt hat, und nicht nur welche Rolle.

    Der Rueckfall 'system' greift nur noch bei den offenen Endpunkten, die
    bewusst ohne Anmeldung laufen (Scannen, Auftrag anlegen und schliessen,
    Kulturwechsel, Zeiten eintragen, Tagesabschluss). Dort bleibt
    unprotokolliert, wer gehandelt hat -- in Kauf genommen, damit der
    Maschinenbetrieb durch keine Anmeldung unterbrochen wird."""
    return auth.actor("system")


# ── Validierung ───────────────────────────────────────────────────────────────
def validate_pnr(pnr: str) -> str:
    try:
        n = int(str(pnr).strip())
    except (ValueError, TypeError):
        raise HTTPException(400, "Personalnummer muss eine Zahl sein")
    if not (PNR_MIN <= n <= PNR_MAX):
        raise HTTPException(400, f"Personalnummer muss zwischen {PNR_MIN} und {PNR_MAX} liegen")
    return str(n)


def validate_anzahl(n) -> int:
    """Personenzahl einer Sammelzeile."""
    try:
        v = int(n)
    except (ValueError, TypeError):
        raise HTTPException(400, "Personenanzahl muss eine Zahl sein")
    if not (1 <= v <= SAMMEL_MAX):
        raise HTTPException(400, f"Personenanzahl muss zwischen 1 und {SAMMEL_MAX} liegen")
    return v


def validate_topfgroesse(tg: Optional[str]) -> Optional[str]:
    if tg is None:
        return None
    if tg == "":
        return ""
    if tg not in TOPFGROESSEN and tg not in ("9er", "11er"):
        raise HTTPException(400, f"Ungueltige Topfgroesse. Erlaubt: {', '.join(TOPFGROESSEN)}")
    return tg


def validate_kultur(k: Optional[Any]) -> Optional[int]:
    if k is None:
        return None
    ki = int(k)
    if ki not in KULTUREN and ki != KULTUR_FREITEXT:
        raise HTTPException(
            400, f"Ungueltige Kultur: {ki}")
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
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError()
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
    if k == KULTUR_FREITEXT:
        return frei or "Sonstiges"
    return KULTUREN.get(k, "")


def _nowHM() -> str:
    d = datetime.now()
    return f"{d.hour:02d}:{d.minute:02d}"


# ── Pydantic-Modelle ──────────────────────────────────────────────────────────
class PwAction(BaseModel):
    # Bleibt fuer Bestandsclients erhalten, wird aber nicht mehr ausgewertet.
    pw: str = ""


class AuftragNewIn(BaseModel):
    planung_id: Optional[str] = Field(default=None, max_length=80)
    id: str
    datum: str
    auftrag_start: str = ""
    arbeit: str = Field(default="", max_length=200)
    kultur: Optional[int] = None
    kultur_frei: str = Field(default="", max_length=100)
    topfform: Literal["", "rund", "eckig"] = ""
    substrat_auftr_nr: str = Field(default="", max_length=100)
    substrat_art_nr: str = Field(default="", max_length=100)
    topfgroesse: str = ""
    sonst: str = Field(default="", max_length=500)
    changed_by: str = ""


class AuftragUpdateIn(BaseModel):
    planung_id: Optional[str] = Field(default=None, max_length=80)
    auftrag_id: str
    pw: str = ""
    arbeit: Optional[str] = Field(default=None, max_length=200)
    kultur: Optional[int] = None
    kultur_frei: Optional[str] = Field(default=None, max_length=100)
    topfform: Optional[Literal["", "rund", "eckig"]] = None
    substrat_auftr_nr: Optional[str] = Field(default=None, max_length=100)
    substrat_art_nr: Optional[str] = Field(default=None, max_length=100)
    topfgroesse: Optional[str] = None
    auftrag_start: Optional[str] = None
    sonst: Optional[str] = Field(default=None, max_length=500)
    gesamtstueck: Optional[int] = None


class ScanIn(BaseModel):
    auftrag_id: str
    mz_id: str
    pnr: str = ""
    rolle: str = "service"
    start: str = ""
    anzahl: int = 1        # nur bei sammel=True relevant
    sammel: bool = False   # True = Sammelzeile ohne einzelne Personalnummer
    updated_ms: Optional[int] = None


class WorkerUpdateIn(BaseModel):
    mz_id: str
    auftrag_id: str = ""
    start: Optional[str] = None
    ende: Optional[str] = None
    pause: Optional[float] = None
    rolle: Optional[str] = None
    anzahl: Optional[int] = None
    updated_ms: Optional[int] = None
    pw: str = ""  # nur nötig wenn zugehöriger Auftrag/Tag bereits abgeschlossen ist


class WorkerDeleteIn(BaseModel):
    # Bleibt fuer Bestandsclients erhalten, wird aber nicht mehr ausgewertet.
    pw: str = ""
    mz_id: str
    auftrag_id: str = ""


class AuftragDeleteIn(BaseModel):
    # Bleibt fuer Bestandsclients erhalten, wird aber nicht mehr ausgewertet.
    pw: str = ""
    auftrag_id: str


class AuftragCloseIn(BaseModel):
    auftrag_id: str
    auftrag_ende: str
    gesamtstueck: int
    pw: str = ""
    token: str = ""


class NeueKulturIn(BaseModel):
    planung_id: Optional[str] = Field(default=None, max_length=80)
    auftrag_id: str
    auftrag_ende: str
    gesamtstueck: int
    new_auftrag_id: str
    datum: str
    new_auftrag_start: str = ""
    arbeit: str = Field(default="", max_length=200)
    kultur: Optional[int] = None
    kultur_frei: str = Field(default="", max_length=100)
    topfform: Literal["", "rund", "eckig"] = ""
    substrat_auftr_nr: str = Field(default="", max_length=100)
    substrat_art_nr: str = Field(default="", max_length=100)
    topfgroesse: str = ""
    transfer_mz_ids: Optional[List[str]] = None
    pw: str = ""
    token: str = ""


class AuftragEditIn(BaseModel):
    planung_id: Optional[str] = Field(default=None, max_length=80)
    # Bleibt fuer Bestandsclients erhalten, wird aber nicht mehr ausgewertet.
    pw: str = ""
    auftrag_id: str
    arbeit: Optional[str] = Field(default=None, max_length=200)
    kultur: Optional[int] = None
    kultur_frei: Optional[str] = Field(default=None, max_length=100)
    topfform: Optional[Literal["", "rund", "eckig"]] = None
    substrat_auftr_nr: Optional[str] = Field(default=None, max_length=100)
    substrat_art_nr: Optional[str] = Field(default=None, max_length=100)
    topfgroesse: Optional[str] = None
    auftrag_start: Optional[str] = None
    auftrag_ende: Optional[str] = None
    gesamtstueck: Optional[int] = None
    status: Optional[str] = None
    sonst: Optional[str] = Field(default=None, max_length=500)


class WorkerEditIn(BaseModel):
    # Bleibt fuer Bestandsclients erhalten, wird aber nicht mehr ausgewertet.
    pw: str = ""
    mz_id: str
    auftrag_id: str = ""
    # Personalnummer richtigstellen. Nur hier moeglich, nicht ueber den fuer
    # den Scanbetrieb offenen /api/worker/update.
    pnr: Optional[str] = None
    start: Optional[str] = None
    # Leerer Wert heisst "wieder anmelden" -- die Endzeit faellt weg.
    ende: Optional[str] = None
    pause: Optional[float] = None
    rolle: Optional[str] = None
    anzahl: Optional[int] = None


class AuswertungRequest(BaseModel):
    # Bleibt fuer Bestandsclients erhalten, wird aber nicht mehr ausgewertet.
    pw: str = ""
    von: str
    bis: str
    kultur_id: Optional[int] = Field(default=None, ge=1, le=max(KULTUREN))


class AuditRequest(BaseModel):
    # Bleibt fuer Bestandsclients erhalten, wird aber nicht mehr ausgewertet.
    pw: str = ""
    limit: int = 200


class DayCloseIn(BaseModel):
    datum: str
    pw: str = ""
    token: str = ""


                  # autoritativer Wert (das ist gesamtstueck), daher kein
                  # Admin-Zwang; Feld bleibt fuer spaetere Zwecke bestehen.


def _abschluss_missing(auftrag: dict, auftrag_ende: Optional[str] = None,
                       gesamtstueck: Optional[int] = None) -> list:
    missing = []
    if not auftrag.get("auftrag_start"):
        missing.append("Auftragsstart")
    if auftrag.get("kultur") is None:
        missing.append("Kultur")
    elif int(auftrag.get("kultur")) == KULTUR_FREITEXT and not (auftrag.get("kultur_frei") or "").strip():
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
        if m.get("sammel"):
            # Sammelzeile hat keine Personalnummer - Pflichtfeld ist die Anzahl.
            if not m.get("anzahl") or int(m.get("anzahl") or 0) < 1:
                ma_missing.append("Personenanzahl")
        elif not m.get("pnr"):
            ma_missing.append("Personalnummer")
        if not m.get("start"):
            ma_missing.append("Startzeit")
        if ma_missing:
            missing.append(f"{db.worker_label(m)}: {', '.join(ma_missing)}")
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
                missing.append(f"{db.worker_label(m)}: Endzeit")
        if missing:
            problems.append({"auftrag": a.get("auftragsnr") or a.get("id"), "missing": missing})
    return problems


def _day_is_closed(datum: str) -> bool:
    return bool(db.get_day_close(datum).get("closed"))


# Toleranz fuer den Uhrenversatz zwischen Tablet und Server. Die Zeitstempel
# der Aenderung stammen vom Geraet, der Abschlusszeitpunkt vom Server.
def _auftragszeiten_nachfuehren(auftrag_id: str) -> None:
    """Auftragsstart und -ende ergeben sich aus den Mitarbeiterzeiten.

    Auftragszeiten folgen der Anwesenheit der erfassten Personen.
    So bleiben Auftragskopf und Mitarbeiterzeiten konsistent.

    Das Ende bleibt leer, solange auch nur eine Person angemeldet ist: erst
    wenn alle abgemeldet sind, steht fest, wann zuletzt gearbeitet wurde. Bei
    einem bereits abgeschlossenen Auftrag wird ein vorhandenes Ende nie
    geleert -- eine wieder angemeldete Person darf einen Abschluss nicht
    stillschweigend aufheben.

    Der Vergleich laeuft ueber die Zeichenketten "HH:MM", was fuer die
    Betriebszeiten der Anlage (5:45 bis hoechstens 18 Uhr) genuegt. Ein Auftrag
    ueber Mitternacht kaeme hier falsch heraus, existiert aber nicht.
    """
    auftrag = db.get_auftrag_by_id(auftrag_id)
    if not auftrag:
        return
    zeilen = auftrag.get("mitarbeiter") or []
    starts = [str(z.get("start") or "").strip() for z in zeilen if str(z.get("start") or "").strip()]
    if not starts:
        # Ohne erfasste Person gibt es nichts abzuleiten; die bisherige Zeit
        # bleibt stehen, statt sie zu loeschen.
        return

    neu_start = min(starts)
    enden = [str(z.get("ende") or "").strip() for z in zeilen if str(z.get("ende") or "").strip()]
    alle_beendet = len(enden) == len(zeilen)
    neu_ende = max(enden) if (alle_beendet and enden) else ""
    if not neu_ende and auftrag.get("status") == "abgeschlossen":
        neu_ende = str(auftrag.get("auftrag_ende") or "")

    fields: dict = {}
    if neu_start != str(auftrag.get("auftrag_start") or ""):
        fields["auftrag_start"] = neu_start
    if neu_ende != str(auftrag.get("auftrag_ende") or ""):
        fields["auftrag_ende"] = neu_ende
    if not fields:
        return
    db.update_auftrag(auftrag_id, fields)
    db.log_audit("auftragszeit_abgeleitet", "system", auftrag_id, str(fields))


# ── Statische Dateien ─────────────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(
        os.path.join(STATIC, "index.html"),
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/auswertung")
def auswertung_page(request: Request):
    """Serverseitig geschuetzt, seit der Passwortweg entfallen ist.

    Ueber HTTP zuerst auf den HTTPS-Zugang umleiten: eine Anmeldeseite auf 8084
    waere sinnlos, weil das __Host--Cookie diesen Port nie erreicht."""
    if not _ueber_https(request):
        return RedirectResponse("https://10.10.0.210:8447/auswertung", status_code=307)
    redirect = auth.guard_page(request, AUTH_CFG)   # verlangt mindestens auswerter
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC, "auswertung.html"))


@app.get("/kulturauswahl.js")
def kulturauswahl_js():
    return FileResponse(os.path.join(STATIC, "kulturauswahl.js"),
                        media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/kulturen.js")
def kulturen_js():
    return FileResponse(
        os.path.join(STATIC, "kulturen.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/sync.js")
def sync_js():
    return FileResponse(
        os.path.join(STATIC, "sync.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


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
        # Alte, noch geoeffnete Clients kennen nur die ersten 22 IDs.
        "kulturen":        [KULTUREN[i] for i in range(1, KULTUR_FREITEXT)],
        "kultur_optionen": [{"id": i, "name": name} for i, name in KULTUREN.items()],
        "kultur_freitext": KULTUR_FREITEXT,
        "arbeiten":        ARBEITEN,
        "topfgroessen":    TOPFGROESSEN,
        "pnr_min":         PNR_MIN,
        "pnr_max":         PNR_MAX,
        # Pausenfenster als [["09:00","09:15"], ...]. Das Frontend rechnet
        # seine automatische Pause daraus, statt eine eigene Kopie zu fuehren.
        "pausen_fenster":  [list(f) for f in PAUSEN_FENSTER],
    }


@app.get("/api/auftrag/active")
def get_active():
    result = db.get_active_auftrag(date.today().isoformat())
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
    _require_write_for_date(body.datum)
    if _day_is_closed(body.datum):
        raise HTTPException(400, "Tag ist bereits abgeschlossen")
    auftragsnr = db.create_auftrag_atomic({
        "id":           body.id,
        "datum":        body.datum,
        "arbeit":       body.arbeit,
        "kultur":       validate_kultur(body.kultur),
        "kultur_frei":  body.kultur_frei,
        "topfgroesse":  validate_topfgroesse(body.topfgroesse),
        "topfform": body.topfform,
        "substrat_auftr_nr": body.substrat_auftr_nr,
        "substrat_art_nr": body.substrat_art_nr,
        "auftrag_start": validate_time(body.auftrag_start, "Auftragsstart") or _nowHM(),
        "sonst":        body.sonst,
        "changed_by":   body.changed_by or "user",
        "planung_id":   body.planung_id or None,
    })
    db.log_audit("auftrag_new", _actor(), body.id,
                 f"auftragsnr={auftragsnr},datum={body.datum},planung={body.planung_id or ''}")
    return {"ok": True, "auftrag_id": body.id, "auftragsnr": auftragsnr}


@app.post("/api/auftrag/update")
def update_auftrag(body: AuftragUpdateIn):
    """Aktualisiert Auftragsdaten (Arbeit, Kultur, Topfgroesse etc.)."""
    auftrag = db.get_auftrag_by_id(body.auftrag_id)
    if not auftrag:
        raise HTTPException(404, "Auftrag nicht gefunden")
    if auftrag["status"] == "abgeschlossen" or not _is_today(auftrag.get("datum", "")) or _day_is_closed(auftrag.get("datum", "")):
        check_admin(body.pw)

    fields: dict = {}
    for key in ("topfform", "substrat_auftr_nr", "substrat_art_nr"):
        if getattr(body, key) is not None:
            fields[key] = getattr(body, key)
    if "planung_id" in body.model_fields_set:
        fields["planung_id"] = body.planung_id or None
    if body.arbeit is not None:
        fields["arbeit"] = body.arbeit
    if "kultur" in body.model_fields_set:
        fields["kultur"] = validate_kultur(body.kultur)
    if body.kultur_frei is not None:
        fields["kultur_frei"] = body.kultur_frei
    if body.topfgroesse is not None:
        fields["topfgroesse"] = validate_topfgroesse(body.topfgroesse)
    if body.auftrag_start is not None:
        fields["auftrag_start"] = validate_time(body.auftrag_start, "Auftragsstart")
    if body.sonst is not None:
        fields["sonst"] = body.sonst
    if body.gesamtstueck is not None:
        if body.gesamtstueck < 0:
            raise HTTPException(400, "Gesamtstueckzahl muss >= 0 sein")
        fields["gesamtstueck"] = body.gesamtstueck
    plan_db.update_config(body.auftrag_id, fields, _actor(body.pw))
    return {"ok": True}


@app.post("/api/auftrag/scan")
def scan_worker(body: ScanIn):
    """Fuegt einen Mitarbeiter zum aktiven Auftrag hinzu - entweder als
    Einzelperson mit Personalnummer oder als Sammelzeile fuer mehrere Personen
    ohne einzelne Erfassung (sammel=True). Die Aufsicht muss immer eine
    Personalnummer haben; eine Sammelzeile ist deshalb stets 'service'."""
    if body.rolle not in ("aufsicht", "service"):
        raise HTTPException(400, "Rolle muss 'aufsicht' oder 'service' sein")
    if body.sammel:
        if body.rolle == "aufsicht":
            raise HTTPException(400, "Die Aufsicht muss mit Personalnummer erfasst werden")
        pnr    = ""
        anzahl = validate_anzahl(body.anzahl)
    else:
        pnr    = validate_pnr(body.pnr)
        anzahl = 1

    auftrag = db.get_auftrag_by_id(body.auftrag_id)
    if not auftrag:
        raise HTTPException(404, "Auftrag nicht gefunden")
    _require_write_for_date(auftrag.get("datum", ""))

    ts = int(time.time() * 1000)
    start = validate_time(body.start, "Startzeit") or _nowHM()
    try:
        neu = db.upsert_mitarbeiter({
            "id":         body.mz_id,
            "auftrag_id": body.auftrag_id,
            "pnr":        pnr,
            "rolle":      body.rolle,
            "start":      start,
            "ende":       "",
            "pause":      0,
            "anzahl":     anzahl,
            "sammel":     bool(body.sammel),
            "created_ms": ts,
            "updated_ms": ts,
        }, nur_neu=True)
    except db.MitarbeiterKonflikt as exc:
        raise HTTPException(409, str(exc))
    except db.MitarbeiterGesperrt as exc:
        raise HTTPException(400, str(exc))
    if not neu:
        return {"ok": True, "pnr": pnr, "rolle": body.rolle, "anzahl": anzahl, "sammel": bool(body.sammel)}
    _auftragszeiten_nachfuehren(body.auftrag_id)
    if body.sammel:
        db.log_audit("sammel_add", _actor(), body.auftrag_id, f"anzahl={anzahl},start={start}")
    elif body.rolle == "aufsicht":
        db.log_audit("aufsicht_add", _actor(), body.auftrag_id, f"pnr={pnr},start={start}")
    return {"ok": True, "pnr": pnr, "rolle": body.rolle,
            "anzahl": anzahl, "sammel": bool(body.sammel)}


@app.post("/api/worker/update")
def update_worker(body: WorkerUpdateIn):
    """Offene Zeiten bearbeiten; gesperrte Aenderungen brauchen eine Schreibrolle.

    Ein frei waehlbarer Client-Zeitstempel belegt keinen Offline-Erfassungszeitpunkt.
    Identische Wiederholungen sind auch nach Abschluss erfolgreich; neue Korrekturen
    bleiben in der bestehenden Outbox zur Freigabe erhalten.
    """
    mz = db.get_mitarbeiter_by_id(body.mz_id)
    if mz is None:
        raise HTTPException(404, "Mitarbeiter-Eintrag nicht gefunden")
    if body.auftrag_id and body.auftrag_id != mz["auftrag_id"]:
        raise HTTPException(409, "Mitarbeiter-Eintrag gehört zu einem anderen Auftrag")
    auftrag = db.get_auftrag_by_id(mz["auftrag_id"])
    if not auftrag:
        raise HTTPException(404, "Auftrag nicht gefunden")
    _require_write_for_date(auftrag.get("datum", ""), body.pw)
    fields = {}
    if body.start is not None: fields["start"] = validate_time(body.start, "Startzeit")
    if body.ende is not None: fields["ende"] = validate_time(body.ende, "Endzeit")
    if body.pause is not None:
        fields["pause"] = validate_pause(body.pause, fields.get("start", mz.get("start", "")), fields.get("ende", mz.get("ende", "")))
    if body.rolle is not None: fields["rolle"] = validate_rolle(body.rolle)
    if body.anzahl is not None:
        if not mz.get("sammel"):
            raise HTTPException(400, "Personenanzahl ist nur bei Sammelzeilen änderbar")
        fields["anzahl"] = validate_anzahl(body.anzahl)
    if not fields:
        return {"ok": True}
    try:
        geaendert = db.update_mitarbeiter_sicher(body.mz_id, fields, _admin_ok(body.pw))
    except db.MitarbeiterGesperrt as exc:
        raise HTTPException(400, str(exc))
    if geaendert:
        _auftragszeiten_nachfuehren(mz["auftrag_id"])
        db.log_audit("worker_update", _actor(body.pw), body.mz_id,
                     f"auftrag={mz['auftrag_id']},fields={list(fields)}")
    return {"ok": True}


@app.post("/api/worker/delete")
def delete_worker(body: WorkerDeleteIn):
    check_admin(body.pw)
    mz = db.get_mitarbeiter_by_id(body.mz_id)
    auftrag_id = (mz or {}).get("auftrag_id") or body.auftrag_id
    db.remove_mitarbeiter(body.mz_id)
    # Faellt die frueheste oder letzte Zeile weg, verschiebt sich die
    # Auftragszeit mit.
    if auftrag_id:
        _auftragszeiten_nachfuehren(auftrag_id)
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
    _require_write_for_date(auftrag.get("datum", ""), body.pw)
    expected = _auftrag_token(body.auftrag_id)
    if not body.token or not hmac.compare_digest(body.token, expected):
        raise HTTPException(403, "Ungültiger Vorgang – Seite neu laden und erneut versuchen")
    if body.gesamtstueck < 0:
        raise HTTPException(400, "Gesamtstueckzahl muss >= 0 sein")
    validate_time(body.auftrag_ende, "Auftragsende")
    _raise_if_incomplete_for_close(auftrag, body.auftrag_ende, body.gesamtstueck)

    db.set_all_active_workers_ende(body.auftrag_id, body.auftrag_ende)
    db.close_auftrag(body.auftrag_id, body.auftrag_ende, body.gesamtstueck,
                     _actor(body.pw))
    # Nach dem Setzen der Endzeiten stimmen Auftragszeit und Personalzeiten
    # ueberein; hier faellt insbesondere ein Auftragsstart auf, der beim
    # Anlegen aus der Uhr kam statt vom ersten Mitarbeiter.
    _auftragszeiten_nachfuehren(body.auftrag_id)
    db.log_audit("auftrag_close", _actor(body.pw),
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
    if _day_is_closed(auftrag.get("datum", "")):
        raise HTTPException(400, "Tag ist bereits abgeschlossen")
    if auftrag["status"] == "abgeschlossen":
        raise HTTPException(400, "Auftrag ist bereits abgeschlossen")
    if body.datum != auftrag.get("datum"):
        raise HTTPException(400, "Kulturwechsel muss am Datum des Ausgangsauftrags bleiben")
    _require_write_for_date(auftrag.get("datum", ""), body.pw)
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
        {"id": str(uuid.uuid4()), "pnr": w["pnr"], "rolle": w["rolle"],
         "anzahl": int(w.get("anzahl") or 1), "sammel": bool(w.get("sammel"))}
        for w in active_workers
    ]

    # Kompletter Kulturwechsel (alten Auftrag abschliessen + neuen anlegen +
    # Mitarbeiter uebernehmen) in EINER Transaktion - kein Zwischenzustand
    # bei Fehler/Neustart moeglich.
    auftragsnr, transferred = db.neue_kultur_atomic(
        body.auftrag_id, body.auftrag_ende, body.gesamtstueck,
        _actor(),
        {
            "id":           body.new_auftrag_id,
            "datum":        body.datum,
            "arbeit":       body.arbeit,
            "kultur":       validate_kultur(body.kultur),
            "kultur_frei":  body.kultur_frei,
            "topfgroesse":  validate_topfgroesse(body.topfgroesse),
            "topfform": body.topfform,
            "substrat_auftr_nr": body.substrat_auftr_nr,
            "substrat_art_nr": body.substrat_art_nr,
            "auftrag_start": new_start,
            "changed_by":   "system",
            "planung_id":   body.planung_id or None,
        },
        transfer_workers,
    )

    # Beide Seiten des Wechsels: der abgeschlossene Auftrag bekommt die Zeiten
    # seiner Personen, der neue die der uebernommenen.
    _auftragszeiten_nachfuehren(body.auftrag_id)
    _auftragszeiten_nachfuehren(body.new_auftrag_id)
    db.log_audit("neue_kultur", _actor(),
                 body.new_auftrag_id,
                 f"von={body.auftrag_id},stueck={body.gesamtstueck},"
                 f"uebernommen={len(transferred)},nr={auftragsnr},planung={body.planung_id or ''}")

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
    validate_date(von, "Von")
    validate_date(bis, "Bis")
    if von > bis:
        raise HTTPException(400, "Von darf nicht nach Bis liegen")
    if von != date.today().isoformat() or bis != date.today().isoformat():
        check_viewer()
    return db.list_auftraege(von, bis)


@app.get("/api/auftrag/{auftrag_id}")
def get_auftrag(auftrag_id: str):
    a = db.get_auftrag_by_id(auftrag_id)
    if not a:
        raise HTTPException(404, "Auftrag nicht gefunden")
    if not _is_today(a.get("datum", "")):
        check_viewer()
    a["close_token"] = _auftrag_token(a["id"])
    return a


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.post("/api/day/close")
def day_close(body: DayCloseIn):
    validate_date(body.datum, "Datum")
    _require_write_for_date(body.datum, body.pw)
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
    actor = _actor(body.pw)
    db.close_day(body.datum, actor)
    db.log_audit("day_close", actor, body.datum, "alle Auftraege vollstaendig")
    return {"ok": True, "datum": body.datum}


@app.get("/api/day/status")
def day_status(datum: str = Query(default_factory=lambda: date.today().isoformat())):
    validate_date(datum, "Datum")
    if not _is_today(datum):
        check_viewer()
    status = db.get_day_close(datum)
    status["problems"] = _day_close_problems(datum)
    status["close_token"] = _day_token(datum)
    return status
@app.post("/api/admin/day/reopen")
def admin_day_reopen(body: DayCloseIn):
    check_admin(body.pw)
    validate_date(body.datum, "Datum")
    db.reopen_day(body.datum, _actor())
    db.log_audit("day_reopen", _actor(), body.datum, "Tag wieder geöffnet")
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
    """Korrektur eines abgeschlossenen oder offenen Auftrags. Ab meister."""
    check_admin(body.pw)
    auftrag = db.get_auftrag_by_id(body.auftrag_id)
    if not auftrag:
        raise HTTPException(404, "Auftrag nicht gefunden")

    fields: dict = {}
    for key in ("topfform", "substrat_auftr_nr", "substrat_art_nr"):
        if getattr(body, key) is not None:
            fields[key] = getattr(body, key)
    if "planung_id" in body.model_fields_set:
        fields["planung_id"] = body.planung_id or None
    if body.arbeit       is not None: fields["arbeit"]       = body.arbeit
    if "kultur" in body.model_fields_set: fields["kultur"] = validate_kultur(body.kultur)
    if body.kultur_frei  is not None: fields["kultur_frei"]  = body.kultur_frei
    if body.topfgroesse  is not None: fields["topfgroesse"]  = validate_topfgroesse(body.topfgroesse)
    if body.auftrag_start is not None: fields["auftrag_start"] = validate_time(body.auftrag_start, "Auftragsstart")
    if body.auftrag_ende  is not None: fields["auftrag_ende"]  = validate_time(body.auftrag_ende, "Auftragsende")
    if body.gesamtstueck is not None:
        if body.gesamtstueck < 0:
            raise HTTPException(400, "Gesamtstueckzahl muss >= 0 sein")
        fields["gesamtstueck"] = body.gesamtstueck
    if body.status is not None:
        if body.status not in ("offen", "abgeschlossen"):
            raise HTTPException(400, "Status muss offen oder abgeschlossen sein")
        fields["status"] = body.status
    if body.sonst         is not None: fields["sonst"]          = body.sonst
    plan_db.update_config(body.auftrag_id, fields, _actor(body.pw))
    # Die Auftragszeit folgt auch hier den Personalzeiten. Eine von Hand
    # gesetzte Zeit haette sonst nur bis zur naechsten Zeitkorrektur Bestand -
    # zwei Wahrheiten, die frueher oder spaeter auseinanderlaufen.
    _auftragszeiten_nachfuehren(body.auftrag_id)
    db.log_audit("admin_auftrag_edit", _actor(body.pw), body.auftrag_id, str(fields))
    return {"ok": True}


@app.post("/api/admin/worker/edit")
def admin_edit_worker(body: WorkerEditIn):
    """Korrektur einzelner Mitarbeiter-Zeilen. Ab meister.

    Deckt seit dem 30.07.2026 auch die beiden Faelle ab, die vorher gar nicht
    gingen: eine falsch erfasste Personalnummer richtigstellen und einen zu
    frueh abgemeldeten Mitarbeiter wieder anmelden (ende leeren). Beides
    ausschliesslich hier und nicht in /api/worker/update -- der Endpunkt ist
    fuer den Scanbetrieb ohne Anmeldung offen und darf keine Personalnummer
    umschreiben koennen.
    """
    check_admin(body.pw)
    mz = db.get_mitarbeiter_by_id(body.mz_id)
    if mz is None:
        raise HTTPException(404, "Mitarbeiter-Eintrag nicht gefunden")

    fields: dict = {}

    if body.pnr is not None:
        if mz.get("sammel"):
            raise HTTPException(400, "Eine Sammelzeile hat keine Personalnummer")
        neu = validate_pnr(body.pnr)
        # Dieselbe Nummer zweimal im selben Auftrag waere eine stille
        # Doppelerfassung -- die Stundensumme stimmte danach nicht mehr.
        auftrag_id = mz.get("auftrag_id") or body.auftrag_id
        auftrag = db.get_auftrag_by_id(auftrag_id) or {}
        for m in auftrag.get("mitarbeiter", []):
            if m.get("id") != body.mz_id and str(m.get("pnr") or "") == neu:
                raise HTTPException(
                    400, f"Personalnummer {neu} ist in diesem Auftrag bereits erfasst")
        fields["pnr"] = neu

    if body.start  is not None: fields["start"]  = validate_time(body.start, "start")
    if body.rolle  is not None: fields["rolle"]  = validate_rolle(body.rolle)
    if body.anzahl is not None: fields["anzahl"] = validate_anzahl(body.anzahl)

    if body.ende is not None:
        # Leerer Wert heisst "wieder anmelden": die Endzeit faellt weg, der
        # Mitarbeiter zaehlt wieder als anwesend. Die Pause muss dann mit,
        # sonst bliebe die automatisch gesetzte Pause einer Arbeitszeit
        # stehen, die es nicht mehr gibt.
        ende = (body.ende or "").strip()
        fields["ende"] = validate_time(ende, "ende") if ende else ""
        if not ende and body.pause is None:
            fields["pause"] = 0

    if body.pause is not None:
        fields["pause"] = validate_pause(
            body.pause,
            fields.get("start", mz.get("start", "")),
            fields.get("ende", mz.get("ende", "")),
        )

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
    _auftragszeiten_nachfuehren(mz.get("auftrag_id") or body.auftrag_id)
    # Die alte Personalnummer gehoert ins Protokoll, sonst laesst sich eine
    # Korrektur nachtraeglich nicht mehr nachvollziehen.
    vorher = f",vorher_pnr={mz.get('pnr')}" if "pnr" in fields else ""
    db.log_audit("admin_worker_edit", _actor(body.pw), body.mz_id,
                 f"auftrag={body.auftrag_id},{fields}{vorher}")
    return {"ok": True}


@app.post("/api/audit")
def audit_log_view(req: AuditRequest):
    check_admin(req.pw)
    _nur_admin()
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
                # Rohindex zusaetzlich zum Namen: der Kultur-Filter der
                # Auswertung verglich bisher den Anzeigetext der Auswahlliste
                # mit dem Kulturnamen der Zeile. Bei Freitext steht dort aber
                # der eingegebene Name und nie das Wort "Freitext", der Filter
                # lief also immer ins Leere. Ueber den Index trifft er.
                "kultur_id":    r["kultur"],
                "planung_id":   r.get("planung_id"),
                "topfgroesse":  r["topfgroesse"] or "",
                "topfform": r.get("topfform", ""),
                "substrat_auftr_nr": r.get("substrat_auftr_nr", ""),
                "substrat_art_nr": r.get("substrat_art_nr", ""),
                "arbeit":       r["arbeit"] or "",
                # Notiz aus der Erfassung (Spalte sonst). Sie wurde bisher
                # gelesen und wieder verworfen - damit stand sie nirgends, wo
                # jemand sie gelesen haette.
                "notiz":        r.get("sonst") or "",
                "auftrag_start": r["auftrag_start"] or "",
                "auftrag_ende": r["auftrag_ende"] or "",
                "gesamtstueck": r["gesamtstueck"],
                "status":       r["status"],
                "workers":      [],
                "total_mh":     0.0,
                "pnrs":         set(),
                "sammel_koepfe": 0,
            }
        if r.get("mz_id"):
            nh     = _netto_h(r.get("mz_start"), r.get("mz_ende"), r.get("mz_pause"))
            anzahl = int(r.get("mz_anzahl") or 1)
            sammel = bool(r.get("mz_sammel"))
            auftraege[aid]["workers"].append({
                # Die Zeilen-ID braucht die Auswertung, um eine Korrektur an
                # /api/admin/worker/edit schicken zu koennen. Ohne sie liesse
                # sich dort nur lesen.
                "id":     r["mz_id"],
                "pnr":    r["pnr"],
                "rolle":  r["rolle"],
                "start":  r.get("mz_start") or "",
                "ende":   r.get("mz_ende") or "",
                "pause":  r.get("mz_pause") or 0,
                "anzahl": anzahl,
                "sammel": sammel,
                "netto_h": round(nh, 2),                 # je Person
                "netto_h_gesamt": round(nh * anzahl, 2),  # ueber alle Personen der Zeile
            })
            # Stundensumme gewichtet: eine Sammelzeile mit 6 Personen zaehlt
            # sechsfach. Kopfzahl analog - Sammelzeilen haben keine PNR, ueber
            # die sie sich deduplizieren liessen, also werden sie addiert.
            auftraege[aid]["total_mh"] += nh * anzahl
            if sammel:
                auftraege[aid]["sammel_koepfe"] += anzahl
            else:
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
                lab = db.worker_label(wk)
                if not wk.get("start"):
                    missing_fields.append(f"{lab}: Startzeit")
                if not wk.get("ende"):
                    missing_fields.append(f"{lab}: Endzeit")
        result.append({
            "id":           a["id"],
            "auftragsnr":   a["auftragsnr"],
            "datum":        a["datum"],
            "kultur":       a["kultur"],
            "kultur_id":    a["kultur_id"],
            "planung_id":   a.get("planung_id"),
            "notiz":        a["notiz"],
            "topfgroesse":  a["topfgroesse"],
            "topfform": a["topfform"],
            "substrat_auftr_nr": a["substrat_auftr_nr"],
            "substrat_art_nr": a["substrat_art_nr"],
            "arbeit":       a["arbeit"],
            "auftrag_start": a["auftrag_start"],
            "auftrag_ende": a["auftrag_ende"],
            "gesamtstueck": a["gesamtstueck"],
            "status":       a["status"],
            "anzahl_ma":    len(a["pnrs"]) + a["sammel_koepfe"],
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
    validate_date(req.von, "Von")
    validate_date(req.bis, "Bis")
    if req.von > req.bis:
        raise HTTPException(400, "Von darf nicht nach Bis liegen")
    rows   = db.get_auftraege_flat(req.von, req.bis)
    return _build_auftrag_summary(rows)


@app.post("/api/auswertung/kulturen")
def auswertung_kulturen(req: AuswertungRequest):
    check_viewer(req.pw)
    validate_date(req.von, "Von")
    validate_date(req.bis, "Bis")
    if req.von > req.bis:
        raise HTTPException(400, "Von darf nicht nach Bis liegen")
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
    validate_date(req.von, "Von")
    validate_date(req.bis, "Bis")
    if req.von > req.bis:
        raise HTTPException(400, "Von darf nicht nach Bis liegen")
    rows      = db.get_auftraege_flat(req.von, req.bis)
    summaries = _build_auftrag_summary(rows)
    anrechnungen = {}
    for plan in plan_db.fortschritt(req.von, req.bis):
        for a in plan["auftraege"]:
            if req.von <= a["datum"] <= req.bis:
                menge = str(a["angerechnet"]) if a["angerechnet"] is not None else "Menge offen"
                if a["status"] != "abgeschlossen":
                    menge += " vorlaeufig"
                anrechnungen.setdefault(a["id"], []).append(f"{plan['id']}: {menge}")
    if req.kultur_id is not None:
        summaries = [a for a in summaries if a["kultur_id"] == req.kultur_id]
    db.log_audit("csv_export", _actor(req.pw), None,
                 f"von={req.von},bis={req.bis},kultur_id={req.kultur_id}")

    DAY_NAMES = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    # Spalte "Anzahl" (Personen je Zeile): bei Einzelpersonen immer 1, bei
    # Sammelzeilen die erfasste Personenzahl. Netto_h bleibt der Wert JE
    # PERSON, Netto_h_gesamt ist die Summe ueber die Zeile - nur letztere
    # summiert sich zu den MA-Stunden der Auswertung auf.
    w.writerow([
        "Tag", "Datum", "Auftragsnr", "Kultur", "Topfgroesse", "Arbeit",
        "Pers.nr.", "Anzahl", "Rolle", "Start", "Ende", "Pause_h",
        "Netto_h", "Netto_h_gesamt",
        "Gesamtstueck", "Stueck_pro_MA-Std", "Status", "Notiz", "Produktionsvorgabe", "Plananrechnung_Stueck", "Topfform", "Substrate Auftr.Nr.", "Art.Nr.",
    ])
    for a in summaries:
        try:
            d   = date.fromisoformat(a["datum"])
            tag = DAY_NAMES[d.weekday()]
        except Exception:
            tag = ""
        for wk in (a["workers"] or [{"pnr": "—", "rolle": "—", "start": "", "ende": "",
                                      "pause": 0, "netto_h": 0, "netto_h_gesamt": 0,
                                      "anzahl": "", "sammel": False}]):
            w.writerow([
                tag, a["datum"], a["auftragsnr"],
                a["kultur"], a["topfgroesse"], a["arbeit"],
                ("SAMMEL" if wk.get("sammel") else wk["pnr"]),
                wk.get("anzahl", 1),
                wk["rolle"],
                wk.get("start", ""), wk.get("ende", ""),
                str(wk.get("pause", 0)).replace(".", ","),
                str(wk.get("netto_h", 0)).replace(".", ","),
                str(wk.get("netto_h_gesamt", wk.get("netto_h", 0))).replace(".", ","),
                a["gesamtstueck"] if a["gesamtstueck"] is not None else "",
                str(a["stueck_pro_mah"] or "").replace(".", ","),
                a["status"],
                # Notiz steht in jeder Zeile des Auftrags. Redundant, aber beim
                # Sortieren und Filtern in Excel ist das der brauchbarere Weg -
                # eine nur in der ersten Zeile gefuellte Spalte verliert beim
                # Umsortieren ihren Bezug.
                (a["notiz"] or "").replace("\r\n", " ").replace("\n", " "),
                a.get("planung_id") or "",
                " | ".join(anrechnungen.get(a["id"], [])),
                a["topfform"], a["substrat_auftr_nr"], a["substrat_art_nr"],
            ])
    buf.seek(0)
    fn = f"Topfmaschine_{req.von}_{req.bis}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fn}"'},
    )


# Produktionsplanung bleibt von den tatsaechlich erfassten Auftraegen getrennt.
class PlanSaveIn(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    kultur: int
    kultur_frei: str = Field(default="", max_length=100)
    topfform: Literal["", "rund", "eckig"] = ""
    topfgroesse: str
    arbeit: str = Field(default="Topfen", min_length=1, max_length=200)
    sollmenge: int = Field(gt=0, le=1000000000)
    termin: str
    beginn: Optional[str] = None
    terminart: Literal["tag", "bis"] = "bis"
    prioritaet: int = Field(default=0, ge=0, le=2)
    bemerkung: str = Field(default="", max_length=500)
    archiviert: bool = False
    version: Optional[int] = None


@app.exception_handler(plan_db.PlanConflict)
async def plan_konflikt(request: Request, exc: plan_db.PlanConflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/planung")
def planung_page(request: Request):
    if not _ueber_https(request):
        return RedirectResponse("https://10.10.0.210:8447/planung", status_code=307)
    redirect = auth.guard_page(request, AUTH_CFG)
    if redirect:
        return redirect
    return FileResponse(os.path.join(STATIC, "planung.html"), headers={"Cache-Control": "no-cache"})


@app.get("/planung.js")
def planung_js():
    return FileResponse(os.path.join(STATIC, "planung.js"), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/planung.css")
def planung_css():
    return FileResponse(os.path.join(STATIC, "planung.css"), media_type="text/css",
                        headers={"Cache-Control": "no-cache"})


def _plan_namen(rows):
    for p in rows:
        p["kultur_name"] = _kultur_name(p["kultur"], p["kultur_frei"])
    return rows


@app.get("/api/planung/auswahl")
def planung_auswahl():
    # Die Aufsicht braucht diese Sachinformationen auch im HTTP-Scanbetrieb.
    # Keine Personalzeiten oder Bearbeiternamen an anonyme Clients ausliefern.
    rows = _plan_namen(plan_db.auswahl())
    keys = ("id", "kultur", "kultur_frei", "kultur_name", "topfgroesse", "arbeit",
            "sollmenge", "termin", "terminart", "prioritaet", "bemerkung", "topfform", "ist", "fehlt")
    return [{k: p[k] for k in keys} for p in rows if not p["archiviert"]]


@app.post("/api/auswertung/planung")
def planung_auswertung(req: AuswertungRequest):
    check_viewer(req.pw)
    validate_date(req.von, "Von")
    validate_date(req.bis, "Bis")
    if req.von > req.bis:
        raise HTTPException(400, "Von darf nicht nach Bis liegen")
    rows = _plan_namen(plan_db.fortschritt(req.von, req.bis))
    return [p for p in rows if req.kultur_id is None or p["kultur"] == req.kultur_id]


@app.post("/api/planung/save")
def planung_speichern(body: PlanSaveIn):
    check_admin()
    validate_date(body.termin, "Produktionsende")
    old = plan_db.get_plan(body.id)
    arbeit = old["arbeit"] if old and "arbeit" not in body.model_fields_set else body.arbeit.strip()
    kultur = validate_kultur(body.kultur)
    topf = validate_topfgroesse(body.topfgroesse)
    if not topf or not arbeit:
        raise HTTPException(400, "Topfgröße und Tätigkeit sind erforderlich")
    if kultur == KULTUR_FREITEXT and not body.kultur_frei.strip():
        raise HTTPException(400, "Freitext-Kultur ist erforderlich")
    beginn = body.beginn if body.beginn is not None else (
        old["beginn"] if old else datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat())
    validate_date(beginn, "Produktionsbeginn")
    if beginn > body.termin:
        raise HTTPException(400, "Produktionsbeginn darf nicht nach dem Produktionsende liegen")
    data = body.model_dump(exclude={"version"})
    # Nicht mehr sichtbare Felder alter Vorgaben beim Bearbeiten erhalten.
    for key in ("terminart", "prioritaet", "bemerkung", "topfform"):
        if old and key not in body.model_fields_set:
            data[key] = old[key]
    data.update(kultur=kultur, beginn=beginn, topfgroesse=topf, arbeit=arbeit,
                kultur_frei=body.kultur_frei.strip() if kultur == KULTUR_FREITEXT else "",
                bemerkung=data["bemerkung"].strip(), archiviert=int(body.archiviert))
    saved = plan_db.save_plan(data, body.version, _actor())
    return {"ok": True, "id": saved["id"], "version": saved["updated_ms"]}
