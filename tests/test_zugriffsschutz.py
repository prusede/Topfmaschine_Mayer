"""Regressionen der Abnahme: Sperren, fremde IDs und Eingabevalidierung."""
import sqlite3
import time
import uuid
from datetime import timedelta

import pytest

from app import db, main
from conftest import kopf, session_token


@pytest.fixture(autouse=True)
def sauber():
    with db.get_conn() as c:
        for t in ('mitarbeiter_zeiten','auftraege','produktionsplaene','tagesabschluesse'):
            c.execute(f'DELETE FROM {t}')
        c.commit()


def meister():
    return kopf(session=session_token('meister',sub='pruefer'))


def anlegen(day=None):
    aid=str(uuid.uuid4());mid=str(uuid.uuid4())
    db.create_auftrag({'id':aid,'auftragsnr':aid,'datum':day or main.date.today().isoformat(),'kultur':2,'topfgroesse':'9cm','auftrag_start':'07:00'})
    db.upsert_mitarbeiter({'id':mid,'auftrag_id':aid,'pnr':'1234','start':'07:00','ende':'08:00','pause':.25})
    return aid,mid


def test_fremde_id_kann_abgeschlossene_alte_zeile_nicht_ueberschreiben(client):
    day=(main.date.today()-timedelta(days=1)).isoformat()
    alt,mid=anlegen(day);neu,_=anlegen()
    db.close_auftrag(alt,'08:00',1000);db.close_day(day)
    vorher=db.get_mitarbeiter_by_id(mid)
    r=client.post('/api/auftrag/scan',json={'auftrag_id':neu,'mz_id':mid,'pnr':'1234','start':'10:00'})
    assert r.status_code==409
    assert db.get_mitarbeiter_by_id(mid)==vorher


@pytest.mark.parametrize('tag_zu',[False,True])
def test_neuer_scan_in_gesperrtem_auftrag_oder_tag_wird_abgewiesen(client,tag_zu):
    aid,_=anlegen()
    if tag_zu:db.close_day(main.date.today().isoformat())
    else:db.close_auftrag(aid,'08:00',1000)
    r=client.post('/api/auftrag/scan',json={'auftrag_id':aid,'mz_id':'neue-zeile','pnr':'5678'})
    assert r.status_code==400
    assert db.get_mitarbeiter_by_id('neue-zeile') is None


def test_scan_retry_nach_abschluss_veraendert_keine_zeiten(client):
    aid,mid=anlegen();db.close_auftrag(aid,'08:00',1000)
    vorher=db.get_mitarbeiter_by_id(mid)
    r=client.post('/api/auftrag/scan',json={'auftrag_id':aid,'mz_id':mid,'pnr':'1234','start':'07:00'})
    assert r.status_code==200
    assert db.get_mitarbeiter_by_id(mid)==vorher


@pytest.mark.parametrize('ts',[None,1,int(time.time()*1000)-60000])
@pytest.mark.parametrize('tag_zu',[False,True])
def test_clientzeitstempel_hebt_abschlusssperre_nicht_auf(client,ts,tag_zu):
    aid,mid=anlegen()
    if tag_zu:db.close_day(main.date.today().isoformat())
    else:db.close_auftrag(aid,'08:00',1000)
    vorher=db.get_mitarbeiter_by_id(mid)
    body={'mz_id':mid,'start':'06:00'}
    if ts is not None:body['updated_ms']=ts
    r=client.post('/api/worker/update',json=body)
    assert r.status_code==400
    assert db.get_mitarbeiter_by_id(mid)==vorher
    assert client.post('/api/worker/update',json=body,headers=meister()).status_code==200


def test_verlorene_erfolgsantwort_darf_nach_abschluss_wiederholt_werden(client):
    aid,mid=anlegen()
    body={'mz_id':mid,'start':'06:00','updated_ms':1}
    assert client.post('/api/worker/update',json=body).status_code==200
    db.close_auftrag(aid,'08:00',1000)
    vorher=db.get_mitarbeiter_by_id(mid)
    assert client.post('/api/worker/update',json=body).status_code==200
    assert db.get_mitarbeiter_by_id(mid)==vorher


def test_abschluss_zwischen_vorpruefung_und_schreiben_bleibt_gesperrt(client,monkeypatch):
    aid,mid=anlegen()
    original=db.update_mitarbeiter_sicher
    def gleichzeitig(mz_id,fields,berechtigt):
        db.close_auftrag(aid,'08:00',1000)
        return original(mz_id,fields,berechtigt)
    monkeypatch.setattr(db,'update_mitarbeiter_sicher',gleichzeitig)
    r=client.post('/api/worker/update',json={'mz_id':mid,'start':'06:00'})
    assert r.status_code==400
    assert db.get_mitarbeiter_by_id(mid)['start']=='07:00'


def test_heutige_leseablaufe_bleiben_offen_historische_brauchen_rolle(client):
    heute=main.date.today().isoformat();alt=(main.date.today()-timedelta(days=1)).isoformat()
    aid,_=anlegen(alt);neu,_=anlegen(heute)
    for url in [f'/api/auftraege?von={alt}&bis={alt}',f'/api/auftrag/{aid}',f'/api/day/status?datum={alt}']:
        assert client.get(url).status_code==403
        assert client.get(url,headers=kopf(session=session_token('auswerter'))).status_code==200
    for url in [f'/api/auftraege?von={heute}&bis={heute}',f'/api/auftrag/{neu}',f'/api/day/status?datum={heute}','/api/auftrag/active']:
        assert client.get(url).status_code==200


@pytest.mark.parametrize('fields',[{'status':'beliebig'},{'gesamtstueck':-1},{'topfgroesse':'unbekannt'},{'auftrag_start':'25:00'},{'auftrag_ende':'08:99'}])
def test_admin_korrekturen_pruefen_gueltige_eingaben(client,fields):
    aid,_=anlegen();vorher=db.get_auftrag_by_id(aid)
    r=client.post('/api/admin/auftrag/edit',json={'auftrag_id':aid,**fields},headers=meister())
    assert r.status_code==400
    assert db.get_auftrag_by_id(aid)==vorher


def test_offene_eingaben_pruefen_zeiten(client):
    aid,_=anlegen()
    assert client.post('/api/auftrag/update',json={'auftrag_id':aid,'auftrag_start':'99:99'}).status_code==400
    assert client.post('/api/auftrag/scan',json={'auftrag_id':aid,'mz_id':'neu','pnr':'5678','start':'99:99'}).status_code==400


def test_wiederoeffnen_schreibt_benutzer_bei_unveraenderten_meisterrechten(client):
    today=main.date.today().isoformat();db.close_day(today,'vorher')
    assert client.post('/api/admin/day/reopen',json={'datum':today},headers=meister()).status_code==200
    assert db.get_day_close(today)['changed_by']=='pruefer'


@pytest.mark.parametrize('von,bis',[('ungueltig','2026-10-16'),('20261016','2026-10-16'),('2026-10-17','2026-10-16')])
def test_auswertungen_lehnen_ungueltige_zeitraeume_ab(client,von,bis):
    paths=['summary','kulturen','planung']
    if hasattr(main,'auswertung_zaehler'):paths+=['zaehler','stunden']
    for name in paths:
        assert client.post('/api/auswertung/'+name,json={'von':von,'bis':bis},headers=meister()).status_code==400
    assert client.post('/api/export.csv',json={'von':von,'bis':bis},headers=meister()).status_code==400


def test_migrationsfehler_werden_nicht_verschluckt(tmp_path,monkeypatch):
    c=sqlite3.connect(tmp_path/'migration.db')
    monkeypatch.setattr(db,'_MIGRATIONS',['ALTER TABLE fehlende_tabelle ADD COLUMN wert TEXT'])
    with pytest.raises(sqlite3.OperationalError):db._run_migrations(c)
    c.close()
