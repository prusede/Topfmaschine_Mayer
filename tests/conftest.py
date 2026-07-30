"""Testumgebung fuer die Topfmaschine Mayer.

Wichtig: die Umgebungsvariablen muessen stehen, BEVOR app.main importiert wird.
Das Modul prueft beim Import das Schluesselverzeichnis, legt die Datenbank an und
baut die AuthConfig. Deshalb passiert das hier auf Modulebene und nicht in einer
Fixture -- und deshalb wird das Schluesselpaar geschrieben, bevor app.main
importiert wird: ohne einen Schluessel bricht die Anwendung beim Start ab.

Die Tokens stellen die Tests selbst aus -- mit einem eigenen Schluesselpaar,
genau so, wie der Auth-Dienst es im Betrieb tut. Der private Schluessel verlaesst
den Testlauf nicht.
"""
from __future__ import annotations

import os
import tempfile
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

APP_KEY = "topfmaschine_mayer"
ORIGIN = "https://testserver"
CSRF_VALUE = "PruefwertFuerDenTest_0123456789"

_TMP = tempfile.mkdtemp(prefix="topfmaschine-mayer-tests-")
_KEYDIR = os.path.join(_TMP, "public")
os.makedirs(_KEYDIR, exist_ok=True)

os.environ["TM_DB_PATH"] = os.path.join(_TMP, "topfmaschine_mayer.db")
os.environ["TM_ORIGIN"] = ORIGIN
os.environ["AUTH_PUBLIC_KEY_DIR"] = _KEYDIR

# Schluesselpaar anlegen, bevor die Anwendung den Store baut.
from erfassung_auth.jws import sign  # noqa: E402
from erfassung_auth.keys import kid_for  # noqa: E402
from erfassung_auth.tokens import csrf_hash  # noqa: E402

_PRIVATE = Ed25519PrivateKey.generate()
KID = kid_for(_PRIVATE.public_key())
with open(os.path.join(_KEYDIR, f"{KID}.pem"), "wb") as fh:
    fh.write(
        _PRIVATE.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402


# ── Tokens ausstellen wie der Auth-Dienst ────────────────────────────────────


def session_token(role="meister", app_key=APP_KEY, sub="jens", ttl=3600, total=36000,
                  epoch=0, csrf=CSRF_VALUE, roles=None):
    now = int(time.time())
    if roles is None:
        roles = {app_key: role} if role else {}
    return sign(
        {
            "iss": "erfassung-auth",
            "sub": sub,
            "name": "Jens Pruss",
            "typ": "session",
            "roles": roles,
            "epoch": epoch,
            "csh": csrf_hash(csrf),
            "iat": now,
            "exp": now + ttl,
            "sxp": now + total,
        },
        _PRIVATE,
        KID,
    )


def stepup_token(sub="jens", ttl=900, epoch=0):
    now = int(time.time())
    return sign(
        {
            "iss": "erfassung-auth",
            "sub": sub,
            "typ": "stepup",
            "epoch": epoch,
            "iat": now,
            "exp": now + ttl,
        },
        _PRIVATE,
        KID,
    )


def kopf(session=None, stepup=None, csrf=CSRF_VALUE, origin=ORIGIN, mit_csrf_kopf=True):
    """Cookies und Koepfe einer Anfrage.

    Bewusst als Cookie-Kopf und nicht ueber den Cookie-Jar des Testclients: die
    Namen tragen den Praefix __Host-, den manche Jars eigenwillig behandeln.
    """
    kekse = []
    if session:
        kekse.append(f"__Host-erf_session={session}")
    if stepup:
        kekse.append(f"__Host-erf_stepup={stepup}")
    if csrf:
        kekse.append(f"__Host-erf_csrf={csrf}")

    h = {}
    if kekse:
        h["Cookie"] = "; ".join(kekse)
    if origin:
        h["Origin"] = origin
    if csrf and mit_csrf_kopf:
        h["X-CSRF-Token"] = csrf
    return h


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def auftrag(client):
    """Ein offener Auftrag mit einer Mitarbeiterzeile.

    Angelegt ueber die offenen Endpunkte, nicht direkt in der Datenbank -- damit
    prueft schon die Vorbereitung, dass der Bestandsschutz haelt.
    """
    aid = f"test-a-{int(time.time() * 1000000)}"
    mz = f"test-m-{int(time.time() * 1000000)}"
    heute = time.strftime("%Y-%m-%d")

    antwort = client.post("/api/auftrag/new", json={
        "id": aid, "datum": heute, "auftrag_start": "07:00", "kultur": 1,
        "topfgroesse": "9er",
    })
    assert antwort.status_code == 200, antwort.text
    antwort = client.post("/api/auftrag/scan", json={
        "auftrag_id": aid, "mz_id": mz, "pnr": "1234", "rolle": "aufsicht",
        "start": "07:00",
    })
    assert antwort.status_code == 200, antwort.text
    return {"id": aid, "mz_id": mz, "datum": heute}
