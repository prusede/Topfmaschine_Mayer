"""Regressionstests: Ein abgeschlossener Tag darf keine neuen Auftraege bekommen."""
from __future__ import annotations

import time

from app import main
from conftest import kopf, session_token


def _meister():
    return kopf(session=session_token("meister"))


def _id(prefix: str) -> str:
    return f"{prefix}-{time.time_ns()}"


def test_neuer_auftrag_wird_an_abgeschlossenem_tag_abgewiesen(client):
    datum = "2001-01-15"
    main.db.close_day(datum, "test")

    antwort = client.post(
        "/api/auftrag/new",
        json={"id": _id("tag-zu-neu"), "datum": datum, "auftrag_start": "07:00"},
        headers=_meister(),
    )

    assert antwort.status_code == 400, antwort.text
    assert "Tag ist bereits abgeschlossen" in antwort.text


def test_kulturwechsel_wird_an_abgeschlossenem_tag_abgewiesen(client):
    datum = "2001-01-16"
    auftrag_id = _id("tag-zu-alt")
    angelegt = client.post(
        "/api/auftrag/new",
        json={
            "id": auftrag_id,
            "datum": datum,
            "auftrag_start": "07:00",
            "kultur": 1,
            "topfgroesse": "9er",
        },
        headers=_meister(),
    )
    assert angelegt.status_code == 200, angelegt.text
    token = client.get(f"/api/auftrag/{auftrag_id}").json()["close_token"]
    main.db.close_day(datum, "test")

    antwort = client.post(
        "/api/auftrag/neue_kultur",
        json={
            "auftrag_id": auftrag_id,
            "auftrag_ende": "09:00",
            "gesamtstueck": 100,
            "new_auftrag_id": _id("tag-zu-folge"),
            "datum": datum,
            "new_auftrag_start": "09:00",
            "kultur": 2,
            "topfgroesse": "9er",
            "transfer_mz_ids": [],
            "token": token,
        },
        headers=_meister(),
    )

    assert antwort.status_code == 400, antwort.text
    assert "Tag ist bereits abgeschlossen" in antwort.text


def test_auftrag_neu_button_beruecksichtigt_tagesabschluss(client):
    seite = client.get("/")
    assert seite.status_code == 200
    assert "const tagGesperrt = state.dayIsClosed;" in seite.text
    assert "const bereit    = !tagGesperrt && has" in seite.text
    assert seite.text.count("if(state.dayIsClosed)") >= 3
