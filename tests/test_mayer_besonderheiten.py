"""Mayer bleibt eine eigenstaendige Anwendung mit manueller Mengenerfassung."""
from pathlib import Path

from app import db, main
from conftest import kopf, session_token


def test_keine_maschinenrouten_oder_messtabellen(client):
    routen = {r.path for r in main.app.routes}
    assert not any(p.startswith('/api/zaehler') for p in routen)
    assert '/api/auswertung/zaehler' not in routen
    assert '/api/auswertung/stunden' not in routen
    with db.get_conn() as c:
        tabellen = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'logo_status' not in tabellen
    assert 'zaehlerstaende' not in tabellen


def test_mayer_ui_verwendet_eigene_rolle_links_und_manuelle_menge(client):
    index = client.get('/').text
    assert 'Topfmaschine Mayer' in index
    assert 'const APP_KEY   = "topfmaschine_mayer"' in index
    assert 'Produzierte Menge bitte manuell eintragen.' in index
    assert 'id="logoTile"' not in index
    assert 'pollLogoStatus' not in index
    assert 'https://10.10.0.210:8446/" title="Zur Stolze-Erfassung wechseln"' in index
    plan_js = client.get('/planung.js').text
    assert '(s.roles || {}).topfmaschine_mayer)' in plan_js
    assert 'topfmaschine_mayer_mayer' not in plan_js
    assert not (Path(main.BASE) / 'logo_poller.py').exists()


def test_mayer_planung_verlangt_mayer_rolle(client):
    # Eine Sitzung fuer Stolze berechtigt nicht automatisch zu Mayer.
    h = kopf(session=session_token('meister', app_key='topfmaschine'))
    r = client.post('/api/auswertung/planung', json={'von':'2026-10-16','bis':'2026-10-16'}, headers=h)
    assert r.status_code == 403
