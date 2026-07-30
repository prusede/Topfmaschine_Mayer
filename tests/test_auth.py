"""Testmatrix der Definition of Done: jeder Endpunkt gegen jeden Zustand.

Gleiche Matrix wie in Topfmaschine_Stolze, ohne die Zaehler-Endpunkte -- die
Mayer-Maschine hat keinen Modbus-Zaehler.

Ohne Cookie, Sitzung ohne Rolle fuer diese Anwendung, auswerter, meister,
admin, und -- seit Schritt 5 umgekehrt -- das alte geteilte Passwort, das
jetzt ueberall abgewiesen werden muss.

Das Step-up ist am 30.07.2026 fuer diese Anwendung abgeschaltet worden: einmal
anmelden genuegt. Ein zweites Passwort waere eine Abfrage mehr als vor der
Umstellung gewesen. Die Zustaende "mit" und "ohne Step-up" sind deshalb zu
einem verschmolzen; die Tests fuehren das Step-up-Cookie noch mit, um zu
belegen, dass es weder hilft noch schadet.

Geprueft wird ausschliesslich, ob die Berechtigung greift -- also 403 gegen
"nicht 403". Ob ein Aufruf danach an einem fehlenden Datensatz mit 404 oder an
einer Pflichtangabe mit 400 endet, ist fuer die Anmeldung ohne Belang und wuerde
den Test nur an Nutzlasten fesseln, die sich mit der Fachlogik aendern.

Die Zusicherungen, an denen der Scanbetrieb haengt, stehen unten: kein heute
offener Endpunkt wird schwerer erreichbar. Diese Faelle sind die Absicherung des
Maschinenbetriebs und duerfen nicht angetastet werden.
"""
from __future__ import annotations

import time

import pytest

from conftest import (
    APP_KEY,
    kopf,
    session_token,
    stepup_token,
)

# Das alte geteilte Passwort. Es existiert nirgends mehr -- der Wert steht hier
# nur, um zu beweisen, dass er keine Tuer mehr oeffnet.
ALTES_PASSWORT = "TestAdminGeheim!"

HEUTE = time.strftime("%Y-%m-%d")


# ── Die Endpunkte, nach Schutzbedarf gruppiert ───────────────────────────────
#
# Die Nutzlasten sind gerade so vollstaendig, dass die Anfrage die Pruefung
# erreicht. Platzhalter-Kennungen genuegen: ein 404 danach bedeutet, dass die
# Berechtigung getragen hat.

OFFEN = [
    ("/api/auftrag/new", {"id": "offen-1", "datum": HEUTE}),
    ("/api/auftrag/scan", {"auftrag_id": "x", "mz_id": "y", "pnr": "1234"}),
    ("/api/worker/update", {"mz_id": "y", "start": "07:00"}),
]

LESEN = [
    ("/api/auswertung/verify", {"pw": ""}),
    ("/api/auswertung/summary", {"pw": "", "von": HEUTE, "bis": HEUTE}),
    ("/api/auswertung/kulturen", {"pw": "", "von": HEUTE, "bis": HEUTE}),
    ("/api/export.csv", {"pw": "", "von": HEUTE, "bis": HEUTE}),
]

# Aendernd, ab Rolle meister. Nachtraeglich bearbeiten steht seit dem
# 30.07.2026 hier statt bei NUR_ADMIN -- die Aufsicht braucht es im Alltag.
SCHREIBEN = [
    ("/api/admin/verify", {"pw": ""}),
    ("/api/admin/day/reopen", {"datum": HEUTE, "pw": ""}),
    ("/api/admin/auftrag/delete", {"pw": "", "auftrag_id": "x"}),
    ("/api/worker/delete", {"pw": "", "mz_id": "y"}),
    ("/api/admin/auftrag/edit", {"pw": "", "auftrag_id": "x"}),
    ("/api/admin/worker/edit", {"pw": "", "mz_id": "y"}),
]

# Dem Admin vorbehalten: das Audit-Protokoll. Es ist das Mittel, mit dem
# Korrekturen kontrolliert werden, und gehoert nicht in dieselbe Hand.
NUR_ADMIN = [
    ("/api/audit", {"pw": "", "limit": 10}),
]

ALLE_GESCHUETZT = LESEN + SCHREIBEN + NUR_ADMIN


def ruf(client, pfad, nutzlast, pw=None, **kopf_args):
    """Einen Endpunkt aufrufen und den Status zurueckgeben."""
    koerper = dict(nutzlast)
    if pw is not None and "pw" in koerper:
        koerper["pw"] = pw
    return client.post(pfad, json=koerper, headers=kopf(**kopf_args)).status_code


def abgewiesen(status):
    return status == 403


# ── Zustand 1: ohne Cookie ───────────────────────────────────────────────────


@pytest.mark.parametrize("pfad,nutzlast", OFFEN)
def test_1_ohne_cookie_bleibt_offen_offen(client, pfad, nutzlast):
    assert not abgewiesen(ruf(client, pfad, nutzlast, origin=None, csrf=None))


@pytest.mark.parametrize("pfad,nutzlast", ALLE_GESCHUETZT)
def test_1_ohne_cookie_bleibt_geschuetzt_geschuetzt(client, pfad, nutzlast):
    assert abgewiesen(ruf(client, pfad, nutzlast, csrf=None))


# ── Zustand 2: Sitzung ohne Rolle fuer diese Anwendung ───────────────────────


@pytest.mark.parametrize("pfad,nutzlast", ALLE_GESCHUETZT)
def test_2_sitzung_fuer_fremde_app_zaehlt_nicht(client, pfad, nutzlast):
    """Ein Admin-Cookie fuer Halle 1 gilt hier nicht. Rechte werden ausdruecklich
    vergeben, nicht entzogen."""
    s = session_token(roles={"halle1": "admin"})
    assert abgewiesen(ruf(client, pfad, nutzlast, session=s, stepup=stepup_token()))


# ── Zustand 3: auswerter ─────────────────────────────────────────────────────


@pytest.mark.parametrize("pfad,nutzlast", LESEN)
def test_3_auswerter_darf_lesen(client, pfad, nutzlast):
    assert not abgewiesen(ruf(client, pfad, nutzlast, session=session_token("auswerter")))


@pytest.mark.parametrize("pfad,nutzlast", SCHREIBEN + NUR_ADMIN)
def test_3_auswerter_darf_nicht_aendern(client, pfad, nutzlast):
    s = session_token("auswerter")
    assert abgewiesen(ruf(client, pfad, nutzlast, session=s, stepup=stepup_token()))


# ── Zustand 4: meister ───────────────────────────────────────────────────────


@pytest.mark.parametrize("pfad,nutzlast", LESEN + SCHREIBEN)
def test_4_meister_darf_lesen_und_aendern(client, pfad, nutzlast):
    """Einmal anmelden genuegt -- kein zweites Passwort, kein Step-up."""
    assert not abgewiesen(ruf(client, pfad, nutzlast, session=session_token("meister")))


@pytest.mark.parametrize("pfad,nutzlast", NUR_ADMIN)
def test_4_meister_bleibt_vom_admin_vorbehalt_ausgeschlossen(client, pfad, nutzlast):
    """Das Audit-Protokoll bleibt beim Admin."""
    s = session_token("meister")
    assert abgewiesen(ruf(client, pfad, nutzlast, session=s, stepup=stepup_token()))


@pytest.mark.parametrize("pfad,nutzlast", LESEN + SCHREIBEN)
def test_4_stepup_cookie_aendert_nichts(client, pfad, nutzlast):
    """Ein mitgefuehrtes Step-up-Cookie darf weder helfen noch schaden -- sonst
    haette das Abschalten eine versteckte zweite Wirkung."""
    s = session_token("meister")
    ohne = ruf(client, pfad, nutzlast, session=s)
    mit = ruf(client, pfad, nutzlast, session=s, stepup=stepup_token())
    assert abgewiesen(ohne) == abgewiesen(mit)


# ── Zustand 5: admin ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("pfad,nutzlast", ALLE_GESCHUETZT)
def test_5_admin_darf_alles(client, pfad, nutzlast):
    assert not abgewiesen(ruf(client, pfad, nutzlast, session=session_token("admin")))


# ── Zustand 7: das alte geteilte Passwort oeffnet nichts mehr ────────────────
#
# Bis Schritt 4 war dies der Zustand "Passwortweg funktioniert weiter". Er hat
# sich umgedreht: das geteilte Passwort ist der Kern dessen, was ersetzt wurde,
# und muss ueberall abgewiesen werden.


@pytest.mark.parametrize("pfad,nutzlast", ALLE_GESCHUETZT)
def test_7_altes_passwort_oeffnet_nichts_mehr(client, pfad, nutzlast):
    assert abgewiesen(ruf(client, pfad, nutzlast, pw=ALTES_PASSWORT, csrf=None))


@pytest.mark.parametrize("pfad,nutzlast", ALLE_GESCHUETZT)
def test_7_beliebiges_passwort_bleibt_403(client, pfad, nutzlast):
    assert abgewiesen(ruf(client, pfad, nutzlast, pw="falsch", csrf=None))


def test_7_passwort_ohne_rolle_hilft_nicht(client):
    """Ein auswerter, der zusaetzlich das alte Passwort mitschickt, darf
    weiterhin nichts aendern. Sonst waere die Ersetzung nur eine Ergaenzung."""
    for pfad, nutzlast in SCHREIBEN:
        status = ruf(client, pfad, nutzlast, pw=ALTES_PASSWORT,
                     session=session_token("auswerter"))
        assert abgewiesen(status), pfad


# ── Bestandsschutz ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("pfad,nutzlast", OFFEN)
def test_offener_endpunkt_in_jedem_zustand_erreichbar(client, pfad, nutzlast):
    faelle = [
        {},
        {"origin": None, "csrf": None},
        {"session": session_token("meister")},
        {"session": session_token("meister"), "mit_csrf_kopf": False},
        {"session": "kaputt"},
        {"session": session_token("meister"), "origin": "https://10.10.0.210:8088"},
        {"session": session_token("admin"), "stepup": stepup_token()},
    ]
    for fall in faelle:
        assert not abgewiesen(ruf(client, pfad, nutzlast, **fall)), fall


def test_scanbetrieb_ohne_origin_und_ohne_cookie(client, auftrag):
    """Scanner und Tablets im HTTP-Betrieb schicken weder Origin noch Cookie --
    das __Host--Cookie wird ueber HTTP ohnehin nicht gesendet. Sie duerfen von
    der Umstellung nichts merken."""
    antwort = client.post(
        "/api/auftrag/scan",
        json={"auftrag_id": auftrag["id"], "mz_id": "scan-neu", "pnr": "1234"},
        headers=kopf(origin=None, csrf=None),
    )
    assert antwort.status_code == 200, antwort.text


def test_tagesabschluss_per_token_ohne_anmeldung(client, auftrag):
    """Der Tagesabschluss per 5x-Klick laeuft ueber ein HMAC-Token und ohne
    Passwort. Bestandsschutz, ausdruecklich in der Auftragsdatei genannt."""
    status = client.get("/api/day/status", params={"datum": auftrag["datum"]})
    assert status.status_code == 200
    token = status.json().get("close_token")
    assert token, "close_token fehlt"
    antwort = client.post(
        "/api/day/close",
        json={"datum": auftrag["datum"], "token": token},
        headers=kopf(origin=None, csrf=None),
    )
    # 200 oder 400 (unvollstaendige Auftraege) -- nur nicht 403.
    assert antwort.status_code != 403, antwort.text


def test_csrf_luecke_verwirft_die_identitaet(client):
    """Fehlt der CSRF-Kopf, verwirft die Middleware die Cookie-Identitaet -- sie
    weist die Anfrage nicht ab, sondern laesst sie ohne Identitaet weiterlaufen.
    Fuer einen geschuetzten Endpunkt endet das seit Schritt 5 mit 403; der
    Passwortweg, der das frueher aufgefangen hat, existiert nicht mehr."""
    s = session_token("meister")
    ohne = dict(session=s, stepup=stepup_token(), mit_csrf_kopf=False)
    assert abgewiesen(ruf(client, "/api/admin/verify", {"pw": ""}, **ohne))
    assert abgewiesen(ruf(client, "/api/admin/verify", {"pw": ""},
                          pw=ALTES_PASSWORT, **ohne))


def test_fremder_ursprung_verwirft_die_identitaet(client):
    """Eine Seite auf Port 8088 darf mit dem Cookie des Benutzers hier nichts
    schreiben. SameSite hilft nicht, weil alle Anwendungen auf demselben Host
    liegen."""
    s = session_token("meister")
    status = ruf(client, "/api/admin/verify", {"pw": ""}, session=s,
                 stepup=stepup_token(), origin="https://10.10.0.210:8088")
    assert abgewiesen(status)


# ── Audit ────────────────────────────────────────────────────────────────────


def test_audit_schreibt_den_anmeldenamen(client, auftrag):
    """Der Zweck des ganzen Vorhabens: im Protokoll steht, wer gehandelt hat."""
    s = session_token("admin", sub="jens")
    antwort = client.post(
        "/api/admin/day/reopen",
        json={"datum": auftrag["datum"], "pw": ""},
        headers=kopf(session=s, stepup=stepup_token()),
    )
    assert antwort.status_code == 200, antwort.text

    protokoll = client.post(
        "/api/audit", json={"pw": "", "limit": 20},
        headers=kopf(session=s, stepup=stepup_token()),
    ).json()
    eintraege = [z for z in protokoll if z["aktion"] == "day_reopen"]
    assert eintraege and eintraege[0]["actor"] == "jens", protokoll[:3]


def test_audit_der_offenen_endpunkte_traegt_system(client, auftrag):
    """Bei den bewusst offenen Endpunkten gibt es keinen Anmeldenamen. Der
    Marker 'system' macht sichtbar, dass hier niemand zugeordnet werden kann --
    in Kauf genommen, damit der Maschinenbetrieb ohne Anmeldung laeuft."""
    antwort = client.post(
        "/api/auftrag/scan",
        json={"auftrag_id": auftrag["id"], "mz_id": "audit-offen", "pnr": "1234"},
        headers=kopf(origin=None, csrf=None),
    )
    assert antwort.status_code == 200, antwort.text

    s = session_token("admin", sub="jens")
    protokoll = client.post(
        "/api/audit", json={"limit": 50},
        headers=kopf(session=s, stepup=stepup_token()),
    ).json()
    eintraege = [z for z in protokoll if z["actor"] == "system"]
    assert eintraege, protokoll[:3]


# ── Rollenabhaengige Sonderfaelle ────────────────────────────────────────────


def _auftrag_schliessen(client, auftrag):
    """Den Testauftrag abschliessen -- ueber den offenen Endpunkt, mit dem Token
    genau dieses Auftrags. `locked` in /api/worker/update haengt schon am
    Auftrag allein; der Tagesabschluss scheiterte hier regelmaessig an offenen
    Auftraegen anderer Tests."""
    token = client.get(f"/api/auftrag/{auftrag['id']}").json().get("close_token", "")
    antwort = client.post(
        "/api/auftrag/close",
        json={"auftrag_id": auftrag["id"], "auftrag_ende": "15:00",
              "gesamtstueck": 100, "token": token},
        headers=kopf(origin=None, csrf=None),
    )
    assert antwort.status_code == 200, antwort.text
    assert client.get(f"/api/auftrag/{auftrag['id']}").json()["status"] == "abgeschlossen"


def test_abgeschlossener_auftrag_nur_mit_rolle_aenderbar(client, auftrag):
    """_admin_ok in /api/worker/update: bei abgeschlossenem Auftrag entscheidet
    es darueber, ob eine Nachbuchung noch erlaubt ist. Der einzige Torwaechter,
    den die uebrige Matrix nicht beruehrt."""
    _auftrag_schliessen(client, auftrag)

    ohne = client.post("/api/worker/update",
                       json={"mz_id": auftrag["mz_id"], "ende": "16:00"},
                       headers=kopf(origin=None, csrf=None))
    mit = client.post("/api/worker/update",
                      json={"mz_id": auftrag["mz_id"], "ende": "16:00"},
                      headers=kopf(session=session_token("meister")))
    assert ohne.status_code == 400, ohne.text   # Nachzuegler-Regel, wie heute
    assert mit.status_code == 200, mit.text


# ── Personalnummer aendern und wieder anmelden (seit 30.07.2026) ─────────────


def test_pnr_aendern_ab_meister(client, auftrag):
    antwort = client.post(
        "/api/admin/worker/edit",
        json={"mz_id": auftrag["mz_id"], "auftrag_id": auftrag["id"], "pnr": "4711"},
        headers=kopf(session=session_token("meister")),
    )
    assert antwort.status_code == 200, antwort.text
    a = client.get(f"/api/auftrag/{auftrag['id']}").json()
    zeile = [m for m in a["mitarbeiter"] if m["id"] == auftrag["mz_id"]][0]
    assert zeile["pnr"] == "4711"


def test_pnr_aendern_nicht_als_auswerter(client, auftrag):
    antwort = client.post(
        "/api/admin/worker/edit",
        json={"mz_id": auftrag["mz_id"], "pnr": "4712"},
        headers=kopf(session=session_token("auswerter")),
    )
    assert antwort.status_code == 403


def test_pnr_darf_im_auftrag_nicht_doppelt_sein(client, auftrag):
    """Zweimal dieselbe Nummer im selben Auftrag waere eine stille
    Doppelerfassung -- die Stundensumme stimmte danach nicht mehr."""
    zweiter = f"test-m2-{int(time.time() * 1000000)}"
    client.post("/api/auftrag/scan", json={
        "auftrag_id": auftrag["id"], "mz_id": zweiter, "pnr": "5001",
        "rolle": "service", "start": "07:00",
    }, headers=kopf(origin=None, csrf=None))

    antwort = client.post(
        "/api/admin/worker/edit",
        json={"mz_id": auftrag["mz_id"], "auftrag_id": auftrag["id"], "pnr": "5001"},
        headers=kopf(session=session_token("meister")),
    )
    assert antwort.status_code == 400, antwort.text


def test_pnr_nicht_ueber_den_offenen_endpunkt(client, auftrag):
    """/api/worker/update ist fuer den Scanbetrieb ohne Anmeldung offen. Eine
    Personalnummer darf dort nicht umschreibbar sein, sonst waere die
    Korrekturfunktion ein Loch im Bestandsschutz."""
    vorher = client.get(f"/api/auftrag/{auftrag['id']}").json()
    alt = [m for m in vorher["mitarbeiter"] if m["id"] == auftrag["mz_id"]][0]["pnr"]

    client.post("/api/worker/update",
                json={"mz_id": auftrag["mz_id"], "pnr": "9999"},
                headers=kopf(origin=None, csrf=None))

    nachher = client.get(f"/api/auftrag/{auftrag['id']}").json()
    neu = [m for m in nachher["mitarbeiter"] if m["id"] == auftrag["mz_id"]][0]["pnr"]
    assert neu == alt


def test_wieder_anmelden_leert_endzeit_und_pause(client, auftrag):
    """Ein zu frueh abgemeldeter Mitarbeiter zaehlt danach wieder als
    anwesend. Die Pause muss mit, sonst bliebe die automatisch gesetzte Pause
    einer Arbeitszeit stehen, die es nicht mehr gibt."""
    client.post("/api/worker/update",
                json={"mz_id": auftrag["mz_id"], "ende": "15:00", "pause": 0.5},
                headers=kopf(origin=None, csrf=None))

    antwort = client.post(
        "/api/admin/worker/edit",
        json={"mz_id": auftrag["mz_id"], "auftrag_id": auftrag["id"], "ende": ""},
        headers=kopf(session=session_token("meister")),
    )
    assert antwort.status_code == 200, antwort.text

    a = client.get(f"/api/auftrag/{auftrag['id']}").json()
    zeile = [m for m in a["mitarbeiter"] if m["id"] == auftrag["mz_id"]][0]
    assert not zeile["ende"]
    assert (zeile["pause"] or 0) == 0


def test_wieder_anmelden_auch_bei_geschlossenem_auftrag(client, auftrag):
    """Genau dafuer ist die Funktion da -- der Fehler faellt meist erst nach dem
    Abschluss auf."""
    client.post("/api/worker/update",
                json={"mz_id": auftrag["mz_id"], "ende": "15:00"},
                headers=kopf(origin=None, csrf=None))
    _auftrag_schliessen(client, auftrag)

    antwort = client.post(
        "/api/admin/worker/edit",
        json={"mz_id": auftrag["mz_id"], "auftrag_id": auftrag["id"], "ende": ""},
        headers=kopf(session=session_token("meister")),
    )
    assert antwort.status_code == 200, antwort.text


def test_pnr_aenderung_steht_im_protokoll(client, auftrag):
    """Ohne die alte Nummer im Protokoll waere eine Korrektur nachtraeglich
    nicht mehr nachvollziehbar."""
    vorher = client.get(f"/api/auftrag/{auftrag['id']}").json()
    alt = [m for m in vorher["mitarbeiter"] if m["id"] == auftrag["mz_id"]][0]["pnr"]

    client.post(
        "/api/admin/worker/edit",
        json={"mz_id": auftrag["mz_id"], "auftrag_id": auftrag["id"], "pnr": "4713"},
        headers=kopf(session=session_token("meister")),
    )
    protokoll = client.post(
        "/api/audit", json={"limit": 50},
        headers=kopf(session=session_token("admin")),
    ).json()
    eintraege = [z for z in protokoll if z["aktion"] == "admin_worker_edit"]
    assert any(f"vorher_pnr={alt}" in (z["detail"] or "") for z in eintraege), eintraege[:3]


# ── /auswertung: serverseitiger Schutz und HTTP-Umleitung ────────────────────


def test_auswertung_ohne_sitzung_leitet_zur_anmeldung(client):
    antwort = client.get("/auswertung", headers={"X-Forwarded-Proto": "https"},
                         follow_redirects=False)
    assert antwort.status_code == 303
    assert antwort.headers["location"].startswith("https://10.10.0.210:8451/login")


def test_auswertung_mit_sitzung_wird_ausgeliefert(client):
    antwort = client.get(
        "/auswertung",
        headers={**kopf(session=session_token("auswerter")),
                 "X-Forwarded-Proto": "https"},
        follow_redirects=False,
    )
    assert antwort.status_code == 200


def test_auswertung_ueber_http_leitet_auf_https(client):
    """Ueber 8083 erreicht das __Host--Cookie den Server nie. Eine Seite
    auszuliefern, die dann nichts laden kann, waere die schlechtere Antwort."""
    antwort = client.get("/auswertung", follow_redirects=False)
    assert antwort.status_code == 307
    assert antwort.headers["location"] == "https://10.10.0.210:8447/auswertung"
