"""Regressionstests fuer historische Nachtraege und den Stolze-Zaehler."""
from __future__ import annotations

from datetime import date, timedelta
import time

from app import main
from conftest import kopf, session_token


GESTERN = (date.today() - timedelta(days=1)).isoformat()
MORGEN = (date.today() + timedelta(days=1)).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}-{time.time_ns()}"


def _meister():
    return kopf(session=session_token("meister"))


def _historischen_auftrag(client, *, mitarbeiter: bool = True) -> dict:
    aid = _id("nachtrag-a")
    antwort = client.post(
        "/api/auftrag/new",
        json={
            "id": aid,
            "datum": GESTERN,
            "auftrag_start": "07:00",
            "kultur": 1,
            "topfgroesse": "9er",
        },
        headers=_meister(),
    )
    assert antwort.status_code == 200, antwort.text
    result = {"id": aid, "datum": GESTERN}
    if mitarbeiter:
        mz_id = _id("nachtrag-m")
        antwort = client.post(
            "/api/auftrag/scan",
            json={
                "auftrag_id": aid,
                "mz_id": mz_id,
                "pnr": "1172",
                "rolle": "aufsicht",
                "start": "07:00",
            },
            headers=_meister(),
        )
        assert antwort.status_code == 200, antwort.text
        result["mz_id"] = mz_id
    return result


def _close_token(client, aid: str) -> str:
    antwort = client.get(f"/api/auftrag/{aid}", headers=_meister())
    assert antwort.status_code == 200, antwort.text
    return antwort.json()["close_token"]


def test_historischen_auftrag_duerfen_nur_meister_und_admin_anlegen(client):
    basis = {
        "datum": GESTERN,
        "auftrag_start": "07:00",
        "kultur": 1,
        "topfgroesse": "9er",
    }
    ohne = client.post(
        "/api/auftrag/new",
        json={**basis, "id": _id("ohne")},
        headers=kopf(origin=None, csrf=None),
    )
    auswerter = client.post(
        "/api/auftrag/new",
        json={**basis, "id": _id("auswerter")},
        headers=kopf(session=session_token("auswerter")),
    )
    meister = client.post(
        "/api/auftrag/new",
        json={**basis, "id": _id("meister")},
        headers=_meister(),
    )
    assert ohne.status_code == 403, ohne.text
    assert auswerter.status_code == 403, auswerter.text
    assert meister.status_code == 200, meister.text


def test_zukuenftiger_auftrag_wird_auch_fuer_admin_abgewiesen(client):
    antwort = client.post(
        "/api/auftrag/new",
        json={"id": _id("zukunft"), "datum": MORGEN, "auftrag_start": "07:00"},
        headers=kopf(session=session_token("admin")),
    )
    assert antwort.status_code == 400, antwort.text
    assert "Zukünftige Tage" in antwort.text


def test_nachtrags_ui_und_sync_werden_nicht_veraltet_gecached(client):
    index = client.get("/")
    sync = client.get("/sync.js?v=20260922-1")
    assert index.status_code == 200
    assert sync.status_code == 200
    assert index.headers["cache-control"] == "no-cache"
    assert sync.headers["cache-control"] == "no-cache"
    assert 'id="btnChangeDate"' in index.text
    assert 'id="fremdesDatumBanner"' in index.text
    assert 'sync.js?v=20261016-topfform-1' in index.text


def test_alle_normalen_aenderungen_am_fremdtag_brauchen_schreibrolle(client):
    auftrag = _historischen_auftrag(client)

    ohne_scan = client.post(
        "/api/auftrag/scan",
        json={"auftrag_id": auftrag["id"], "mz_id": _id("scan"),
              "pnr": "1234", "rolle": "service", "start": "08:00"},
        headers=kopf(origin=None, csrf=None),
    )
    ohne_auftrag = client.post(
        "/api/auftrag/update",
        json={"auftrag_id": auftrag["id"], "sonst": "nicht erlaubt"},
        headers=kopf(origin=None, csrf=None),
    )
    ohne_worker = client.post(
        "/api/worker/update",
        json={"mz_id": auftrag["mz_id"], "ende": "09:00"},
        headers=kopf(origin=None, csrf=None),
    )
    mit_worker = client.post(
        "/api/worker/update",
        json={"mz_id": auftrag["mz_id"], "ende": "09:00"},
        headers=_meister(),
    )

    assert ohne_scan.status_code == 403, ohne_scan.text
    assert ohne_auftrag.status_code == 403, ohne_auftrag.text
    assert ohne_worker.status_code == 403, ohne_worker.text
    assert mit_worker.status_code == 200, mit_worker.text


def test_historischer_auftrag_wird_nicht_zum_heutigen_aktiven(client, auftrag):
    historisch = _historischen_auftrag(client, mitarbeiter=False)
    aktiv = client.get("/api/auftrag/active")
    assert aktiv.status_code == 200, aktiv.text
    assert aktiv.json()["id"] == auftrag["id"]
    assert aktiv.json()["id"] != historisch["id"]


def test_kulturwechsel_darf_datum_nicht_wechseln(client):
    auftrag = _historischen_auftrag(client)
    antwort = client.post(
        "/api/auftrag/neue_kultur",
        json={
            "auftrag_id": auftrag["id"],
            "auftrag_ende": "09:00",
            "gesamtstueck": 100,
            "new_auftrag_id": _id("falscher-tag"),
            "datum": date.today().isoformat(),
            "new_auftrag_start": "09:00",
            "kultur": 2,
            "topfgroesse": "9er",
            "transfer_mz_ids": [],
            "token": _close_token(client, auftrag["id"]),
        },
        headers=_meister(),
    )
    assert antwort.status_code == 400, antwort.text


def test_fremden_tag_abschliessen_braucht_schreibrolle(client):
    datum = "2000-01-02"
    ohne = client.post(
        "/api/day/close",
        json={"datum": datum, "token": "ungueltig"},
        headers=kopf(origin=None, csrf=None),
    )
    mit = client.post(
        "/api/day/close",
        json={"datum": datum, "token": "ungueltig"},
        headers=_meister(),
    )
    assert ohne.status_code == 403, ohne.text
    # Mit Rolle wird erst danach der absichtlich falsche Vorgangstoken geprueft.
    assert mit.status_code == 403
    assert "Ungültiger Vorgang" in mit.text
