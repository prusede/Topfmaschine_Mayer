"""Bestandsschutz beim Erweitern der Kulturen und Topfgroessen."""
import uuid

import pytest

from app import db, main
from conftest import kopf, session_token


@pytest.fixture
def meister_kopf():
    return kopf(session=session_token("meister"))


@pytest.fixture(autouse=True)
def sauber():
    with db.get_conn() as c:
        c.execute("DELETE FROM mitarbeiter_zeiten")
        c.execute("DELETE FROM auftraege")
        c.execute("DELETE FROM tagesabschluesse")
        c.commit()



ALT = [
    "Clematis", "Hortensie", "Aronia", "Maulbeere", "Tibouchina",
    "Lantana", "Buddleja", "Datura", "Passiflora rot", "Passiflora blau",
    "Bougainvillea", "Abutilon", "Lonicera", "Solanum ram.", "Polygonum",
    "Camelia", "Oleander", "Dicentra", "Pampasgras", "Paeonia",
    "Fuchsie", "Stauden 9er rund",
]


def test_katalog_erhaelt_alle_bisherigen_kennungen(client):
    cfg = client.get("/api/config").json()
    optionen = {x["id"]: x["name"] for x in cfg["kultur_optionen"]}
    assert len(optionen) == len(set(optionen.values())) == 87
    assert [optionen[i] for i in range(1, 23)] == ALT
    assert cfg["kultur_freitext"] == 23
    assert 23 not in optionen
    assert min(i for i in optionen if i > 22) == 24
    # Auch ein altes, noch geoeffnetes Tablet bekommt keine verschobene Liste.
    assert cfg["kulturen"] == ALT
    assert client.get("/kulturen.js").status_code == 200


def test_historische_auftraege_und_freitext_bleiben_unveraendert(client, meister_kopf):
    datum = "2025-02-11"
    ids = {}
    for k in range(1, 24):
        aid = str(uuid.uuid4())
        ids[aid] = k
        db.create_auftrag({"id": aid, "auftragsnr": aid, "datum": datum,
                           "kultur": k, "kultur_frei": "Historische Sonderkultur" if k == 23 else "",
                           "topfgroesse": "9er", "auftrag_start": "07:00"})
        db.update_auftrag(aid, {"gesamtstueck": k * 100, "auftrag_ende": "08:00",
                               "status": "abgeschlossen"})
        db.upsert_mitarbeiter({"id": str(uuid.uuid4()), "auftrag_id": aid, "pnr": "1234",
                               "start": "07:00", "ende": "08:00", "pause": 0.25})
    vorher = [db.get_auftrag_by_id(aid) for aid in ids]
    rows = client.post("/api/auswertung/summary", json={"von": datum, "bis": datum},
                       headers=meister_kopf).json()
    rows = [r for r in rows if r["id"] in ids]
    assert len(rows) == 23
    assert sum(r["gesamtstueck"] for r in rows) == sum(range(1, 24)) * 100
    assert sum(r["total_mh"] for r in rows) == 23 * 0.75
    for r in rows:
        k = ids[r["id"]]
        assert r["kultur_id"] == k
        assert r["kultur"] == ("Historische Sonderkultur" if k == 23 else ALT[k-1])
    assert [db.get_auftrag_by_id(aid) for aid in ids] == vorher


@pytest.mark.parametrize("topf", ["9er", "10,5cm", "11er", "12cm", "13cm", "14cm",
                                  "15cm", "1,7L", "2L", "3L", "4L"])
def test_neue_kultur_und_topfgroessen_speichern_und_exportieren(client, meister_kopf, topf):
    aid = str(uuid.uuid4())
    datum = main.date.today().isoformat()
    r = client.post("/api/auftrag/new", json={"id": aid, "datum": datum,
                    "auftrag_start": "07:00", "kultur": 88, "topfgroesse": topf})
    assert r.status_code == 200, r.text
    assert db.get_auftrag_by_id(aid)["topfgroesse"] == topf
    summary = client.post("/api/auswertung/summary", json={"von": datum, "bis": datum},
                          headers=meister_kopf).json()
    row = next(r for r in summary if r["id"] == aid)
    assert row["kultur_id"] == 88
    assert row["kultur"] == main.KULTUREN[88]
    csv = client.post("/api/export.csv", json={"von": datum, "bis": datum, "kultur_id": 88},
                      headers=meister_kopf)
    assert csv.status_code == 200, csv.text
    assert topf in csv.text


@pytest.mark.parametrize("topf", ["WW", "ww", "wuwa"])
def test_keine_sonderkennzeichen_als_topfgroesse(client, topf):
    r = client.post("/api/auftrag/new", json={"id": str(uuid.uuid4()), "datum": main.date.today().isoformat(),
                    "auftrag_start": "07:00", "kultur": 24, "topfgroesse": topf})
    assert r.status_code == 400


def test_neuer_auftrag_hat_keinen_automatischen_topf(client):
    aid = str(uuid.uuid4())
    r = client.post("/api/auftrag/new", json={"id": aid, "datum": main.date.today().isoformat(),
                                            "auftrag_start": "07:00", "kultur": 24})
    assert r.status_code == 200
    assert not db.get_auftrag_by_id(aid)["topfgroesse"]
    assert "Topfgroesse" in db.get_auftrag_by_id(aid)["missing_fields"]
