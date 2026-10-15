"""Kultur-ID und CSV muessen dieselbe Auswahl wie die Oberflaeche verwenden."""
import csv
import io

import pytest

from app import db, main
from conftest import kopf, session_token

DATUM = "2040-01-02"
FREITEXT = len(main.KULTUREN) + 1


@pytest.fixture
def kulturen():
    for aid, kultur in [("filter-normal", 2), ("filter-frei", FREITEXT)]:
        db.create_auftrag({"id": aid, "auftragsnr": aid, "datum": DATUM,
                           "auftrag_start": "07:00", "kultur": kultur,
                           "kultur_frei": "Sonderkultur", "topfgroesse": "9er"})
    yield
    with db.get_conn() as c:
        c.execute("DELETE FROM auftraege WHERE datum=?", (DATUM,))
        c.commit()


def test_summary_enthaelt_stabile_kultur_ids(client, kulturen):
    antwort = client.post("/api/auswertung/summary",
                          json={"von": DATUM, "bis": DATUM},
                          headers=kopf(session=session_token("meister")))
    assert antwort.status_code == 200, antwort.text
    rows = {a["id"]: a for a in antwort.json()}
    assert rows["filter-normal"]["kultur_id"] == 2
    assert rows["filter-normal"]["kultur"] == "Hortensie"
    assert rows["filter-frei"]["kultur_id"] == FREITEXT
    assert rows["filter-frei"]["kultur"] == "Sonderkultur"


@pytest.mark.parametrize("auswahl, erwartet", [
    (None, {"filter-normal", "filter-frei"}),
    (2, {"filter-normal"}),
    (FREITEXT, {"filter-frei"}),
    (1, set()),
])
def test_csv_beruecksichtigt_kulturauswahl(client, kulturen, auswahl, erwartet):
    payload = {"von": DATUM, "bis": DATUM}
    if auswahl is not None:
        payload["kultur_id"] = auswahl
    antwort = client.post("/api/export.csv", json=payload,
                          headers=kopf(session=session_token("meister")))
    assert antwort.status_code == 200, antwort.text
    rows = csv.DictReader(io.StringIO(antwort.text.lstrip("\ufeff")), delimiter=";")
    assert {r["Auftragsnr"] for r in rows} == erwartet
