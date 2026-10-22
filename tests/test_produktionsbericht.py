"""Chef-Export zaehlt Auftragsmengen einmal; Sachfilter gelten auch im Export."""
import csv
import io

import pytest

from app import db, main, plan_db
from conftest import kopf, session_token


def zeile(auftrag="A", mz="W1", **extra):
    return {"id": auftrag, "auftragsnr": auftrag, "datum": "2026-10-13", "kultur": 1,
            "kultur_frei": "", "topfgroesse": "9er", "topfform": "rund", "arbeit": "Topfen",
            "auftrag_start": "08:00", "auftrag_ende": "09:00", "gesamtstueck": 1040,
            "status": "abgeschlossen", "mz_id": mz, "pnr": "1001", "rolle": "Mitarbeiter",
            "mz_start": "08:00", "mz_ende": "09:00", "mz_pause": 0, "mz_anzahl": 1,
            "mz_sammel": False, **extra}


def test_mengenexport_eine_zeile_je_auftrag(client, monkeypatch):
    rows = [zeile(), zeile(mz="W2", pnr="1002", rolle="Aufsicht")]
    monkeypatch.setattr(db, "get_auftraege_flat", lambda *args: rows)
    monkeypatch.setattr(plan_db, "fortschritt", lambda *args: [])
    headers = kopf(session=session_token("auswerter"))
    body = {"von": "2026-10-13", "bis": "2026-10-13", "ansicht": "mengen"}
    result = client.post("/api/export.csv", json=body, headers=headers)
    assert result.status_code == 200
    exported = list(csv.DictReader(io.StringIO(result.text.lstrip("\ufeff")), delimiter=";"))
    assert len(exported) == 1
    assert exported[0]["Gesamtstueck"] == "1040"
    assert exported[0]["Mitarbeiterstunden"] == "2,0"
    assert exported[0]["Mengenstatus"] == "bestaetigt"
    summary = client.post("/api/auswertung/summary", json=body, headers=headers).json()
    assert summary[0]["gesamtstueck"] == 1040
    assert summary[0]["stueck_pro_mah"] == 520


@pytest.mark.parametrize("path", ["/api/auswertung/summary", "/api/auswertung/kulturen", "/api/export.csv"])
def test_groesse_form_kultur_auch_im_export(client, monkeypatch, path):
    rows = [zeile(), zeile("B", topfform="eckig"), zeile("C", topfgroesse="11cm"), zeile("D", kultur=2)]
    monkeypatch.setattr(db, "get_auftraege_flat", lambda *args: rows)
    monkeypatch.setattr(plan_db, "fortschritt", lambda *args: [])
    result = client.post(path, json={"von":"2026-10-13", "bis":"2026-10-13", "topfgroesse":"9cm",
                        "topfform":"rund", "kultur_id":1, "ansicht":"mengen"},
                        headers=kopf(session=session_token("auswerter")))
    assert result.status_code == 200
    if path.endswith("csv"):
        exported = list(csv.DictReader(io.StringIO(result.text.lstrip("\ufeff")), delimiter=";"))
        assert [r["Auftragsnr"] for r in exported] == ["A"]
    else:
        assert len(result.json()) == 1


def test_leserecht_fuer_jahresauswahl(client):
    assert client.post("/api/auswertung/jahre").status_code == 403
    assert client.post("/api/auswertung/jahre", headers=kopf(session=session_token("auswerter"))).status_code == 200

