"""Mengenplanung: Migration, Rollen, eindeutige Zuordnung und Bestandsdaten."""
import sqlite3
import uuid

import pytest

from app import db, main, plan_db
from conftest import kopf, session_token


@pytest.fixture(autouse=True)
def leere_planung():
    def leeren():
        with db.get_conn() as c:
            for table in ("mitarbeiter_zeiten", "auftraege", "produktionsplaene", "tagesabschluesse"):
                c.execute(f"DELETE FROM {table}")
            c.commit()
    leeren()
    yield
    leeren()


@pytest.fixture
def meister():
    return kopf(session=session_token("meister"))


@pytest.fixture
def payload():
    return {"id": str(uuid.uuid4()), "kultur": 1, "topfgroesse": "12cm", "arbeit": "Topfen",
            "sollmenge": 20000, "termin": "2026-10-20", "terminart": "bis", "bemerkung": "Testvorgabe"}


def test_topfformen_werden_getrennt_automatisch_angerechnet(client, meister, payload):
    rund = speichern(client, meister, dict(payload, beginn="2026-10-16", topfform="rund", topfgroesse="9cm"))
    eckig = speichern(client, meister, dict(payload, beginn="2026-10-16", id=str(uuid.uuid4()), topfform="eckig", topfgroesse="9cm"))
    for form, menge in (("rund", 1200), ("eckig", 3400), ("", 999)):
        aid = auftrag(None, menge=menge)
        db.update_auftrag(aid, {"topfgroesse": "9er", "topfform": form})
    rows = {p["id"]: p for p in bericht(client, meister)}
    assert rows[rund["id"]]["ist"] == 1200
    assert rows[eckig["id"]]["ist"] == 3400


def test_substratnummern_bleiben_beim_mengenupdate_und_export_erhalten(client, meister):
    aid = str(uuid.uuid4())
    datum = main.date.today().isoformat()
    data = {"id": aid, "datum": datum, "kultur": 1, "topfgroesse": "11cm",
            "topfform": "eckig", "substrat_auftr_nr": "000123-A", "substrat_art_nr": "00456"}
    assert client.post("/api/auftrag/new", json=data).status_code == 200
    assert client.post("/api/auftrag/update", json={"auftrag_id": aid, "substrat_art_nr": "000789"}).status_code == 200
    assert client.post("/api/auftrag/update", json={"auftrag_id": aid, "gesamtstueck": 1234}).status_code == 200
    row = db.get_auftrag_by_id(aid)
    assert (row["topfform"], row["substrat_auftr_nr"], row["substrat_art_nr"]) == ("eckig", "000123-A", "000789")
    csv = client.post("/api/export.csv", json={"von": datum, "bis": datum}, headers=meister)
    assert csv.status_code == 200
    assert all(s in csv.text for s in ("Substrate Auftr.Nr.", "Art.Nr.", "000123-A", "000789", "eckig"))


@pytest.mark.parametrize("felder", [{"topfform": "oval"}, {"substrat_auftr_nr": "x" * 101}, {"substrat_art_nr": "x" * 101}])
def test_ungueltige_zusatzfelder_werden_abgewiesen(client, felder):
    r = client.post("/api/auftrag/new", json={"id": str(uuid.uuid4()), "datum": main.date.today().isoformat(), **felder})
    assert r.status_code == 422


def test_alte_topfgroesse_bleibt_beim_planbearbeiten_erhalten(client, meister, payload):
    p = speichern(client, meister, dict(payload, topfgroesse="9er"))
    auftrag(p, menge=1200)
    r = client.post("/api/planung/save", json=dict(payload, topfgroesse="9cm", sollmenge=25000, version=p["updated_ms"]), headers=meister)
    assert r.status_code == 200, r.text
    assert plan_db.get_plan(p["id"])["topfgroesse"] == "9er"
    assert next(p for p in bericht(client, meister) if p["id"] == payload["id"])["ist"] == 1200


def speichern(client, meister, payload):
    r = client.post("/api/planung/save", json=payload, headers=meister)
    assert r.status_code == 200, r.text
    return plan_db.get_plan(payload["id"])


def auftrag(plan=None, datum="2026-10-16", menge=1000, status="abgeschlossen"):
    aid = str(uuid.uuid4())
    data = {"id": aid, "auftragsnr": aid, "datum": datum, "kultur": 1,
            "topfgroesse": "9er", "auftrag_start": "07:00"}
    if plan:
        data.update({k: plan[k] for k in ("kultur", "kultur_frei", "topfgroesse", "arbeit")})
        data["planung_id"] = plan["id"]
    db.create_auftrag(data)
    db.update_auftrag(aid, {"gesamtstueck": menge, "status": status, "auftrag_ende": "08:00" if status == "abgeschlossen" else ""})
    return aid


def bericht(client, meister, von="2026-10-16", bis="2026-10-16"):
    r = client.post("/api/auswertung/planung", json={"von": von, "bis": bis}, headers=meister)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("rolle,erwartet", [(None,403),("auswerter",403),("meister",200),("admin",200)])
def test_nur_meister_und_admin_planen(client,payload,rolle,erwartet):
    r=client.post("/api/planung/save",json=payload,headers=kopf(session=session_token(rolle)) if rolle else {})
    assert r.status_code==erwartet,r.text


def test_vorgaben_auch_ohne_beginn_und_gleiche_kultur_getrennt(client,meister,payload):
    p=speichern(client,meister,payload)
    p2=speichern(client,meister,dict(payload,id=str(uuid.uuid4())))
    aid=auftrag(p,menge=14500)
    for i in range(2):
        db.upsert_mitarbeiter({"id":str(uuid.uuid4()),"auftrag_id":aid,"pnr":str(1234+i),"start":"07:00","ende":"08:00"})
    rows={r["id"]:r for r in bericht(client,meister)}
    assert rows[p["id"]]["ist"]==14500
    assert rows[p["id"]]["fehlt"]==5500
    assert rows[p["id"]]["erfuellt_prozent"]==72.5
    assert rows[p2["id"]]["ist"]==0
    assert rows[p2["id"]]["fehlt"]==20000
    assert rows[p2["id"]]["auftraege"]==[]


def test_mehrtaegig_zeitraum_mehrproduktion_und_vorlaeufig(client,meister,payload):
    p=speichern(client,meister,dict(payload,sollmenge=10000))
    auftrag(p,datum="2026-10-15",menge=6000)
    auftrag(p,menge=4600)
    auftrag(p,menge=900,status="offen")
    auftrag(p,menge=None,status="offen")
    auftrag(None,menge=99999)
    r=bericht(client,meister)[0]
    assert (r["ist"],r["ist_zeitraum"],r["fehlt"],r["mehrproduktion"],r["erfuellt_prozent"])==(10600,4600,0,600,106)
    assert (r["vorlaeufig"],r["laufende_auftraege"],r["laufend_ohne_menge"])==(900,2,1)
    assert r["auftraege_anzahl"]==4


def test_zuordnung_auf_heutigem_auftrag_auch_im_scanbetrieb(client,meister,payload):
    p=speichern(client,meister,payload)
    aid=auftrag(datum=main.date.today().isoformat(),menge=None,status="offen")
    config={k:p[k] for k in ("kultur","kultur_frei","topfgroesse","arbeit")}
    r=client.post("/api/auftrag/update",json=dict(config,auftrag_id=aid,planung_id=p["id"]))
    assert r.status_code==200,r.text
    assert db.get_auftrag_by_id(aid)["planung_id"]==p["id"]
    r=client.post("/api/auftrag/update",json={"auftrag_id":aid,"planung_id":p["id"],"topfgroesse":"3L"})
    assert r.status_code==409
    r=client.post("/api/auftrag/update",json={"auftrag_id":aid,"planung_id":None,"topfgroesse":"3L"})
    assert r.status_code==200
    assert db.get_auftrag_by_id(aid)["planung_id"] is None
    with db.get_conn() as c:
        assert c.execute("SELECT COUNT(*) FROM audit_log WHERE aktion='planung_zuordnen' AND target=?",(aid,)).fetchone()[0]==2


def test_geschlossener_tag_und_altauftrag_brauchen_meister(client,meister,payload):
    p=speichern(client,meister,payload)
    aid=auftrag()
    config={k:p[k] for k in ("kultur","kultur_frei","topfgroesse","arbeit")}
    body=dict(config,auftrag_id=aid,planung_id=p["id"])
    assert client.post("/api/auftrag/update",json=body).status_code==403
    assert client.post("/api/auftrag/update",json=body,headers=meister).status_code==200
    today=main.date.today().isoformat()
    aid=auftrag(datum=today,status="offen")
    with db.get_conn() as c:
        c.execute("INSERT OR REPLACE INTO tagesabschluesse(datum,closed) VALUES (?,1)",(today,));c.commit()
    assert client.post("/api/auftrag/update",json=dict(body,auftrag_id=aid)).status_code==403


def test_veraltete_bearbeitung_und_feste_konfiguration(client,meister,payload):
    p=speichern(client,meister,payload)
    assert client.post("/api/planung/save",json=dict(payload,sollmenge=30000),headers=meister).status_code==409
    auftrag(p)
    assert client.post("/api/planung/save",json=dict(payload,version=p["updated_ms"],topfgroesse="3L"),headers=meister).status_code==409
    r=client.post("/api/planung/save",json=dict(payload,version=p["updated_ms"],sollmenge=30000),headers=meister)
    assert r.status_code==200
    assert plan_db.get_plan(p["id"])["sollmenge"]==30000


def test_archivierte_vorgabe_bleibt_auswertbar(client,meister,payload):
    p=speichern(client,meister,payload)
    aid=auftrag(p,datum=main.date.today().isoformat(),status="offen")
    speichern(client,meister,dict(payload,version=p["updated_ms"],archiviert=True))
    assert client.get("/api/planung/auswahl").json()==[]
    assert bericht(client,meister)[0]["archiviert"]==1
    assert client.post("/api/auftrag/update",json={"auftrag_id":aid,"gesamtstueck":1234}).status_code==200
    today=main.date.today().isoformat()
    cfg={k:p[k] for k in ("kultur","kultur_frei","topfgroesse","arbeit")}
    assert client.post("/api/auftrag/new",json=dict(cfg,id=str(uuid.uuid4()),datum=today,planung_id=p["id"])).status_code==409


@pytest.mark.parametrize("aenderung",[{"sollmenge":0},{"sollmenge":-1},{"termin":"2026-02-30"},{"topfgroesse":"WW"},{"arbeit":" "},{"kultur":23,"kultur_frei":""}])
def test_ungueltige_vorgaben(client,meister,payload,aenderung):
    assert client.post("/api/planung/save",json=dict(payload,**aenderung),headers=meister).status_code in (400,422)


def test_leserechte_und_anonyme_auswahl(client,meister,payload):
    speichern(client,meister,payload)
    assert client.post("/api/auswertung/planung",json={"von":"2026-10-16","bis":"2026-10-16"}).status_code==403
    assert len(bericht(client,kopf(session=session_token("auswerter"))))==1
    p=client.get("/api/planung/auswahl").json()[0]
    assert "changed_by" not in p and "auftraege" not in p
    assert p["sollmenge"]==20000


def test_planung_braucht_csrf_und_protokolliert_bearbeiter(client,payload):
    ohne_csrf=kopf(session=session_token("meister"),mit_csrf_kopf=False)
    assert client.post("/api/planung/save",json=payload,headers=ohne_csrf).status_code==403
    speichern(client,kopf(session=session_token("meister")),payload)
    with db.get_conn() as c:
        row=c.execute("SELECT actor,detail FROM audit_log WHERE target=? AND aktion='planung_anlegen'",(payload["id"],)).fetchone()
    assert row["actor"]=="jens"
    assert '20000' in row["detail"]


def test_neue_vorgabe_mit_kulturwechsel_und_rollback(client,meister,payload):
    p=speichern(client,meister,payload)
    old=auftrag(datum=main.date.today().isoformat(),menge=None,status="offen")
    cfg={k:p[k] for k in ("kultur","kultur_frei","topfgroesse","arbeit")}
    body=dict(cfg,auftrag_id=old,auftrag_ende="08:00",gesamtstueck=1000,
              new_auftrag_id=str(uuid.uuid4()),datum=main.date.today().isoformat(),
              planung_id=p["id"],token=main._auftrag_token(old))
    bad=client.post("/api/auftrag/neue_kultur",json=dict(body,topfgroesse="3L"))
    assert bad.status_code==409,bad.text
    assert db.get_auftrag_by_id(old)["status"]=="offen"
    assert db.get_auftrag_by_id(body["new_auftrag_id"]) is None
    good=client.post("/api/auftrag/neue_kultur",json=body)
    assert good.status_code==200,good.text
    assert db.get_auftrag_by_id(body["new_auftrag_id"])["planung_id"]==p["id"]
    assert db.get_auftrag_by_id(old)["planung_id"] is None
    # Retry darf keinen zweiten Folgeauftrag erzeugen.
    assert client.post("/api/auftrag/neue_kultur",json=body).status_code==200


def test_vorgabe_im_export_und_kulturfilter(client,meister,payload):
    p=speichern(client,meister,payload)
    auftrag(p)
    r=client.post("/api/export.csv",json={"von":"2026-10-16","bis":"2026-10-16"},headers=meister)
    assert r.status_code==200
    assert 'Produktionsvorgabe' in r.text and p["id"] in r.text
    r=client.post("/api/auswertung/planung",json={"von":"2026-10-16","bis":"2026-10-16","kultur_id":2},headers=meister)
    assert r.status_code==200 and r.json()==[]


def test_migration_erhaelt_bestehende_auftraege(tmp_path):
    c=sqlite3.connect(tmp_path/"alt.db")
    c.executescript(db.SCHEMA[db.SCHEMA.index("CREATE TABLE IF NOT EXISTS auftraege"):])
    c.execute("INSERT INTO auftraege(id,auftragsnr,datum,kultur,kultur_frei,gesamtstueck,created_ms,updated_ms) VALUES ('alt','ALT','2025-01-02',23,'Sonderkultur',12345,1,1)")
    before=c.execute("SELECT id,kultur,kultur_frei,gesamtstueck FROM auftraege").fetchall()
    c.commit()
    for _ in range(2):
        c.executescript(db.SCHEMA);db._run_migrations(c)
    assert c.execute("SELECT id,kultur,kultur_frei,gesamtstueck FROM auftraege").fetchall()==before
    assert c.execute("SELECT planung_id FROM auftraege").fetchone()==(None,)
    assert c.execute("SELECT topfform,substrat_auftr_nr,substrat_art_nr FROM auftraege").fetchone()==('', '', '')
    assert "topfform" in {r[1] for r in c.execute("PRAGMA table_info(produktionsplaene)")}
    assert c.execute("PRAGMA integrity_check").fetchone()==('ok',)
    c.close()


# Freie Erfassung: keine Vorgaben-ID und keine Taetigkeitsauswahl erforderlich.
def frei(datum="2026-10-16", menge=1000, topf="12cm", kultur=2, status="abgeschlossen"):
    aid = auftrag(datum=datum, menge=menge, status=status)
    db.update_auftrag(aid, {"kultur": kultur, "topfgroesse": topf, "arbeit": ""})
    return aid


def auto_plan(client, meister, payload, **werte):
    return speichern(client, meister, dict(payload, kultur=2, beginn="2026-10-15", **werte))


def test_automatisch_nach_kultur_topf_und_beginn(client, meister, payload):
    p = auto_plan(client, meister, payload)
    frei("2026-10-15", 6000)
    frei(menge=8500)
    frei("2026-10-14", 99999)
    frei(menge=99999, topf="11er")
    frei(menge=99999, kultur=1)
    r = bericht(client, meister)[0]
    assert (r["ist"],r["ist_zeitraum"],r["fehlt"],r["erfuellt_prozent"]) == (14500,8500,5500,72.5)
    assert all(a["anrechnungsart"] == "automatisch" for a in r["auftraege"])
    assert r["auftraege_anzahl"] == 2
    assert db.get_auftrag_by_id(r["auftraege"][0]["id"])["planung_id"] is None


def test_aufteilen_nach_termin_ohne_doppelzaehlung(client, meister, payload):
    spaeter = auto_plan(client, meister, payload, id="spaeter", sollmenge=10000, termin="2026-10-22", prioritaet=2)
    frueher = auto_plan(client, meister, payload, id="frueher", sollmenge=10000, termin="2026-10-20")
    aid = frei(menge=15000)
    rows = {p["id"]:p for p in bericht(client, meister)}
    assert rows["frueher"]["ist"] == 10000
    assert rows["spaeter"]["ist"] == 5000
    assert rows["spaeter"]["auftraege"][0]["angerechnet"] == 5000
    assert rows["spaeter"]["auftraege"][0]["gesamtstueck"] == 15000
    assert sum(p["ist"] for p in rows.values()) == 15000
    frei(menge=15000)
    rows = {p["id"]:p for p in bericht(client, meister)}
    assert rows["spaeter"]["ist"] == 20000
    assert rows["spaeter"]["mehrproduktion"] == 10000
    assert sum(p["ist"] for p in rows.values()) == 30000
    csv = client.post("/api/export.csv",json={"von":"2026-10-16","bis":"2026-10-16"},headers=meister)
    assert "frueher: 10000" in csv.text and "spaeter: 5000" in csv.text


def test_spaeterer_beginn_verbraucht_keine_fruehere_produktion(client, meister, payload):
    auto_plan(client,meister,payload,id="erste",sollmenge=10000)
    speichern(client,meister,dict(payload,id="zweite",kultur=2,beginn="2026-10-18",sollmenge=10000))
    frei(menge=12000)
    frei("2026-10-18",4000)
    rows={p["id"]:p for p in bericht(client,meister)}
    assert rows["erste"]["ist"] == 12000
    assert rows["zweite"]["ist"] == 4000
    assert rows["zweite"]["ist_zeitraum"] == 0


def test_laufend_reserviert_nicht_die_verbindliche_menge(client, meister, payload):
    auto_plan(client,meister,payload,id="erste",sollmenge=10000,termin="2026-10-20")
    auto_plan(client,meister,payload,id="zweite",sollmenge=10000,termin="2026-10-21")
    frei("2026-10-15",7000,status="offen")
    frei(menge=8000)
    frei(menge=None,status="offen")
    rows={p["id"]:p for p in bericht(client,meister)}
    assert (rows["erste"]["ist"],rows["erste"]["vorlaeufig"]) == (8000,2000)
    assert (rows["zweite"]["ist"],rows["zweite"]["vorlaeufig"]) == (0,5000)
    assert rows["zweite"]["laufend_ohne_menge"] == 1
    assert sum(p["ist"] for p in rows.values()) == 8000


def test_korrektur_neuberechnung_und_zeitraum_veraendern_nicht_die_gesamtverteilung(client, meister, payload):
    auto_plan(client,meister,payload,id="erste",sollmenge=10000,termin="2026-10-20")
    auto_plan(client,meister,payload,id="zweite",sollmenge=10000,termin="2026-10-21")
    aid=frei("2026-10-15",6000)
    frei(menge=9000)
    a=bericht(client,meister)
    b=bericht(client,meister,"2026-10-15","2026-10-15")
    assert [p["ist"] for p in a] == [p["ist"] for p in b] == [10000,5000]
    assert sum(p["ist_zeitraum"] for p in a) == 9000
    assert sum(p["ist_zeitraum"] for p in b) == 6000
    db.update_auftrag(aid,{"gesamtstueck":1000})
    assert [p["ist"] for p in bericht(client,meister)] == [10000,0]


def test_archivierte_automatische_anteile_bleiben_erhalten(client, meister, payload):
    p=auto_plan(client,meister,payload,id="alt",sollmenge=10000)
    aid=frei(menge=8000)
    speichern(client,meister,dict(payload,id="alt",kultur=2,beginn=p["beginn"],sollmenge=10000,version=p["updated_ms"],archiviert=True))
    auto_plan(client,meister,payload,id="neu",sollmenge=10000,termin="2026-10-19")
    frei(menge=3000)
    rows={p["id"]:p for p in bericht(client,meister)}
    assert rows["alt"]["ist"] == 8000
    assert rows["neu"]["ist"] == 3000
    db.update_auftrag(aid,{"gesamtstueck":5000})
    rows={p["id"]:p for p in bericht(client,meister)}
    assert sum(p["ist"] for p in rows.values()) == 8000
    assert rows["alt"]["ist"] == 5000


def test_freie_erfassung_kann_alte_zuordnung_korrigieren(client,meister,payload):
    p=speichern(client,meister,payload)
    aid=auftrag(p,datum=main.date.today().isoformat(),status="offen")
    r=client.post("/api/auftrag/update",json={"auftrag_id":aid,"topfgroesse":"3L"})
    assert r.status_code==200,r.text
    a=db.get_auftrag_by_id(aid)
    assert a["planung_id"] is None and a["topfgroesse"]=="3L"


def test_produktionsbeginn_validieren(client,meister,payload):
    assert client.post("/api/planung/save",json=dict(payload,beginn="2026-10-21"),headers=meister).status_code == 400
    assert client.post("/api/planung/save",json=dict(payload,beginn="2026-02-30"),headers=meister).status_code == 400
    assert client.post("/api/planung/save",json=dict(payload,beginn=""),headers=meister).status_code == 400



def test_gleicher_termin_prioritaet_und_nullmenge(client,meister,payload):
    auto_plan(client,meister,payload,id="normal",sollmenge=10000)
    auto_plan(client,meister,payload,id="hoch",sollmenge=10000,prioritaet=2)
    frei(menge=3000)
    frei(menge=0,status="offen")
    rows={p["id"]:p for p in bericht(client,meister)}
    assert rows["hoch"]["ist"]==3000 and rows["normal"]["ist"]==0
    assert rows["hoch"]["laufende_auftraege"]==1
    assert rows["hoch"]["laufend_ohne_menge"]==0


def test_alte_feste_zuordnung_wird_nicht_nochmal_automatisch_gezaehlt(client,meister,payload):
    p=auto_plan(client,meister,payload,id="erste",sollmenge=20000)
    auto_plan(client,meister,payload,id="zweite",sollmenge=20000,termin="2026-10-21")
    aid=auftrag(p,datum="2026-10-14",menge=6000)
    frei(menge=5000)
    rows={p["id"]:p for p in bericht(client,meister)}
    assert rows["erste"]["ist"]==11000
    assert rows["zweite"]["ist"]==0
    assert db.get_auftrag_by_id(aid)["planung_id"]=="erste"


def test_topf_schreibweisen_und_freitext_nur_eindeutig(client,meister,payload):
    auto_plan(client,meister,payload,topfgroesse="9er")
    frei(menge=1234,topf="9cm")
    assert bericht(client,meister)[0]["ist"]==1234
    plan=speichern(client,meister,dict(payload,id="frei",kultur=23,kultur_frei="Dahlie",beginn="2026-10-15"))
    aid=frei(menge=1000,kultur=23)
    db.update_auftrag(aid,{"kultur_frei":"  DAHLIE  "})
    falsch=frei(menge=9999,kultur=23)
    db.update_auftrag(falsch,{"kultur_frei":"Dahlia"})
    rows={p["id"]:p for p in bericht(client,meister)}
    assert rows["frei"]["ist"]==1000


def test_migration_planbeginn_aus_erstellungstag_in_berlin(tmp_path):
    from datetime import datetime, timezone
    c=sqlite3.connect(tmp_path/"alte_planung.db")
    c.executescript(db.SCHEMA)
    ts=int(datetime(2026,10,15,22,30,tzinfo=timezone.utc).timestamp()*1000)
    c.execute("INSERT INTO produktionsplaene(id,kultur,topfgroesse,arbeit,sollmenge,termin,created_ms,updated_ms) VALUES ('alt',2,'12cm','Topfen',20000,'2026-10-20',?,?)",(ts,ts))
    c.commit()
    db._run_migrations(c)
    db._run_migrations(c)
    assert c.execute("SELECT beginn,created_ms,sollmenge FROM produktionsplaene").fetchone()==('2026-10-16',ts,20000)
    assert c.execute("PRAGMA integrity_check").fetchone()==('ok',)
    c.close()


def test_planung_mit_den_fuenf_sichtbaren_feldern(client,meister):
    body={"id":"fuenf-felder","kultur":2,"topfgroesse":"12cm","beginn":"2026-10-16",
          "termin":"2026-10-20","sollmenge":20000}
    p=speichern(client,meister,body)
    assert p["beginn"]=="2026-10-16" and p["termin"]=="2026-10-20"
    assert p["arbeit"]=="Topfen" and p["prioritaet"]==0
    frei(menge=5000)
    assert bericht(client,meister)[0]["ist"]==5000


def test_fuenf_felder_bearbeiten_erhaelt_alte_zusatzwerte(client,meister,payload):
    p=speichern(client,meister,dict(payload,arbeit="Stecken",terminart="tag",prioritaet=2,bemerkung="Bestehende Notiz"))
    body={"id":p["id"],"version":p["updated_ms"],"kultur":p["kultur"],"topfgroesse":p["topfgroesse"],
          "beginn":p["beginn"],"termin":"2026-10-21","sollmenge":25000}
    neu=speichern(client,meister,body)
    assert (neu["arbeit"],neu["terminart"],neu["prioritaet"],neu["bemerkung"])==("Stecken","tag",2,"Bestehende Notiz")
