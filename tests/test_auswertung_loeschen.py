"""Auftrag aus der Auswertung loeschen: nur Admin, nur vergangene Tage, mit Protokoll."""
from conftest import kopf, session_token
import main


def _loesch(client, aid, rolle="admin"):
    return client.post("/api/auswertung/auftrag/loeschen", json={"auftrag_id": aid},
                       headers=kopf(session=session_token(rolle)))


def test_admin_loescht_vergangenen_auftrag_samt_mitarbeiterzeiten(client, auftrag, monkeypatch):
    protokoll = []
    monkeypatch.setattr(main.db, "log_audit", lambda *a: protokoll.append(a))
    # Heute: gesperrt, das Tablet haelt den Auftrag noch.
    assert _loesch(client, auftrag["id"]).status_code == 409
    with main.db.get_conn() as c:
        c.execute("UPDATE auftraege SET datum='2026-07-02' WHERE id=?", (auftrag["id"],))
        c.commit()
    assert _loesch(client, auftrag["id"], "meister").status_code == 403
    assert _loesch(client, auftrag["id"]).status_code == 200
    assert main.db.get_auftrag_by_id(auftrag["id"]) is None
    assert main.db.get_mitarbeiter_by_id(auftrag["mz_id"]) is None
    assert _loesch(client, auftrag["id"]).status_code == 404
    aktion, _, ziel, detail = protokoll[0]
    assert aktion == "auswertung_auftrag_delete" and ziel == auftrag["id"]
    assert "datum=2026-07-02" in detail and "mitarbeiter=[1234/aufsicht/07:00-" in detail


def test_alte_planungsseite_leitet_in_die_auswertung(client):
    """Seit 22.10.2026 ist die Planung eine Ansicht der Auswertung."""
    antwort = client.get("/planung", headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    assert antwort.status_code == 302 and antwort.headers["location"] == "/auswertung#planung"
