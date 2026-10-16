"""Produktionsvorgaben, eindeutige Zuordnung und Mengenfortschritt."""
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app import db
from app.kulturen import KULTUR_FREITEXT


class PlanConflict(ValueError):
    pass


def _audit(c, aktion, actor, target, detail):
    c.execute("INSERT INTO audit_log (ts,aktion,actor,target,detail) VALUES (?,?,?,?,?)",
              (int(time.time() * 1000), aktion, actor, target,
               json.dumps(detail, ensure_ascii=False, sort_keys=True)))


def get_plan(pid):
    with db.get_conn() as c:
        row = c.execute("SELECT * FROM produktionsplaene WHERE id=?", (pid,)).fetchone()
        return dict(row) if row else None


def save_plan(data, version, actor):
    with db.get_conn() as c:
        # Versionspruefung und Aenderung gehoeren in dieselbe Schreibtransaktion.
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM produktionsplaene WHERE id=?", (data["id"],)).fetchone()
        old = dict(row) if row else None
        data = dict(data)
        data.setdefault("topfform", old.get("topfform", "") if old else "")
        if old and _kultur_topf(old) == _kultur_topf(data):
            data["topfgroesse"] = old["topfgroesse"]
        data.setdefault("beginn", old["beginn"] if old else
                        datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat())
        if (old and version != old["updated_ms"]) or (not old and version is not None):
            raise PlanConflict("Die Vorgabe wurde zwischenzeitlich geändert. Bitte neu laden.")
        linked = c.execute("SELECT 1 FROM auftraege WHERE planung_id=? LIMIT 1", (data["id"],)).fetchone()
        angerechnet = next((p["auftraege_anzahl"] for p in _berechne(c) if p["id"] == data["id"]), 0) if old else 0
        if old and (linked or angerechnet) and any(old[k] != data[k] for k in ("kultur", "kultur_frei", "topfgroesse", "arbeit", "topfform")):
            raise PlanConflict("Kultur, Topfgröße, Topfform und Tätigkeit sind nach der ersten Anrechnung fest. Bitte eine neue Vorgabe anlegen.")
        if old and not old["archiviert"] and data["archiviert"]:
            vorher = next(p for p in _berechne(c) if p["id"] == data["id"])
            c.execute("DELETE FROM plan_archiv_anrechnungen WHERE planung_id=?", (data["id"],))
            for a in vorher["auftraege"]:
                if a["status"] == "abgeschlossen" and a["angerechnet"]:
                    c.execute("INSERT INTO plan_archiv_anrechnungen VALUES (?,?,?)",
                              (data["id"], a["id"], a["angerechnet"]))
        now = max(int(time.time() * 1000), (old["updated_ms"] + 1) if old else 0)
        values = dict(data, created_ms=old["created_ms"] if old else now,
                      updated_ms=now, changed_by=actor)
        if old:
            keys = [k for k in values if k not in ("id", "created_ms")]
            c.execute("UPDATE produktionsplaene SET " + ",".join(f"{k}=:{k}" for k in keys) + " WHERE id=:id", values)
        else:
            keys = list(values)
            c.execute("INSERT INTO produktionsplaene (" + ",".join(keys) + ") VALUES (" +
                      ",".join(":" + k for k in keys) + ")", values)
        _audit(c, "planung_aendern" if old else "planung_anlegen", actor, data["id"],
               {"vorher": old, "nachher": values})
        c.commit()
        return values


def konfiguration(data, existing=None):
    """Prueft die Zuordnung auch fuer alte Clients und spaet eintreffende Offline-Auftraege."""
    merged = dict(existing or {}, **data)
    pid = merged.get("planung_id")
    if not pid:
        return data
    plan = get_plan(pid)
    if not plan:
        raise PlanConflict("Produktionsvorgabe nicht gefunden.")
    if plan["archiviert"] and (existing or {}).get("planung_id") != pid:
        raise PlanConflict("Diese Produktionsvorgabe ist archiviert. Bitte eine aktive Vorgabe wählen.")
    for key in ("kultur", "kultur_frei", "topfgroesse", "arbeit"):
        if merged.get(key, "") != plan[key]:
            raise PlanConflict("Kultur, Topfgröße oder Tätigkeit stimmen nicht mit der Vorgabe überein. Vorgabe neu wählen oder Zuordnung lösen.")
    if plan.get("topfform") and merged.get("topfform", "") != plan["topfform"]:
        raise PlanConflict("Topfform stimmt nicht mit der Vorgabe überein.")
    return data


def update_config(aid, fields, actor):
    if not fields:
        return
    with db.get_conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM auftraege WHERE id=?", (aid,)).fetchone()
        if not row:
            raise PlanConflict("Auftrag nicht gefunden.")
        old = dict(row)
        # Neue freie Erfassung hat keine Planungsauswahl mehr. Eine bewusste
        # Konfigurationskorrektur loest eine alte feste Zuordnung; die aktuelle
        # Kultur-/Topfkombination wird anschliessend automatisch ausgewertet.
        if "planung_id" not in fields and any(k in fields and fields[k] != old[k]
                for k in ("kultur", "kultur_frei", "topfgroesse", "topfform")):
            fields = dict(fields, planung_id=None)
        konfiguration(fields, old)
        c.execute("UPDATE auftraege SET " + ",".join(f"{k}=?" for k in fields) +
                  ", updated_ms=?, changed_by=? WHERE id=?",
                  [*fields.values(), int(time.time() * 1000), actor, aid])
        if "planung_id" in fields and old.get("planung_id") != fields["planung_id"]:
            _audit(c, "planung_zuordnen", actor, aid,
                   {"vorher": old.get("planung_id"), "nachher": fields["planung_id"]})
        c.commit()


def _kultur_topf(row):
    # Schreibweisen von cm/er und Liter vereinheitlichen, ohne Auftraege zu aendern.
    topf = (row.get("topfgroesse") or "").strip().casefold().replace(" ", "").replace(",", ".")
    topf = {"9er": "9cm", "11er": "11cm"}.get(topf, topf)
    kultur = row.get("kultur")
    frei = (row.get("kultur_frei") or "").strip().casefold() if kultur == KULTUR_FREITEXT else ""
    return kultur, frei, topf


def _berechne(c, von="0001-01-01", bis="9999-12-31"):
    plaene = [dict(r) for r in c.execute("""SELECT * FROM produktionsplaene
        ORDER BY termin, prioritaet DESC, created_ms, id""")]
    if not plaene:
        return []
    by_id = {p["id"]: p for p in plaene}
    rang = {p["id"]: i for i, p in enumerate(plaene)}
    start = min(p["beginn"] for p in plaene)
    orders = [dict(r) for r in c.execute("""SELECT id,auftragsnr,datum,auftrag_start,
        status,gesamtstueck,kultur,kultur_frei,topfgroesse,topfform,planung_id,created_ms
        FROM auftraege WHERE datum>=? OR planung_id IS NOT NULL
          OR id IN (SELECT auftrag_id FROM plan_archiv_anrechnungen)
        ORDER BY datum,auftrag_start,created_ms,id""", (start,))]
    frozen = {}
    for r in c.execute("""SELECT x.* FROM plan_archiv_anrechnungen x
        JOIN produktionsplaene p ON p.id=x.planung_id WHERE p.archiviert=1"""):
        frozen.setdefault(r["auftrag_id"], []).append(dict(r))
    active = {}
    for p in plaene:
        p.update(ist=0, ist_zeitraum=0, vorlaeufig=0, vorlaeufig_zeitraum=0)
        if not p["archiviert"]:
            active.setdefault(_kultur_topf(p), []).append(p)
    records = {p["id"]: {} for p in plaene}
    residual = {}

    def buchen(p, a, menge, art):
        geschlossen = a["status"] == "abgeschlossen"
        feld = "ist" if geschlossen else "vorlaeufig"
        p[feld] += menge or 0
        if von <= a["datum"] <= bis:
            p[feld + "_zeitraum"] += menge or 0
        eintrag = records[p["id"]].get(a["id"])
        if eintrag:
            eintrag["angerechnet"] = (eintrag["angerechnet"] or 0) + (menge or 0)
        else:
            records[p["id"]][a["id"]] = {k: a[k] for k in
                ("id", "auftragsnr", "datum", "status", "gesamtstueck")}
            records[p["id"]][a["id"]].update(angerechnet=menge, anrechnungsart=art)

    # Erst verbindliche Mengen verteilen, danach laufende Mengen als Vorschau.
    # So verdraengt ein offener Auftrag niemals eine bereits fertige Produktion.
    for geschlossen in (True, False):
        gruppe = [a for a in orders if (a["status"] == "abgeschlossen") == geschlossen]
        for a in gruppe:
            menge = max(a["gesamtstueck"] or 0, 0)
            pid = a.get("planung_id")
            if pid in by_id:
                # Historische ausdrueckliche Zuordnungen haben Vorrang. Die ganze
                # Menge ist damit verbraucht und wird nicht nochmals automatisch verteilt.
                buchen(by_id[pid], a, a["gesamtstueck"], "bestehend")
                residual[a["id"]] = 0
                continue
            rest = menge
            for f in sorted(frozen.get(a["id"], []), key=lambda f: rang[f["planung_id"]]):
                n = min(rest, f["menge"])
                if n:
                    buchen(by_id[f["planung_id"]], a, n, "archiviert")
                    rest -= n
            residual[a["id"]] = rest
        for a in gruppe:
            if a.get("planung_id") in by_id:
                continue
            kandidaten = [p for p in active.get(_kultur_topf(a), []) if p["beginn"] <= a["datum"]
                          and (not p["topfform"] or p["topfform"] == a["topfform"])]
            if not kandidaten:
                continue
            rest = residual[a["id"]]
            if a["gesamtstueck"] is None:
                p = next((p for p in kandidaten if p["ist"] + p["vorlaeufig"] < p["sollmenge"]), kandidaten[-1])
                buchen(p, a, None, "automatisch")
                continue
            if a["gesamtstueck"] == 0:
                p = next((p for p in kandidaten if p["ist"] + p["vorlaeufig"] < p["sollmenge"]), kandidaten[-1])
                buchen(p, a, 0, "automatisch")
                continue
            for p in kandidaten:
                platz = max(p["sollmenge"] - p["ist"] - p["vorlaeufig"], 0)
                n = min(rest, platz)
                if n:
                    buchen(p, a, n, "automatisch")
                    rest -= n
                if not rest:
                    break
            if rest:
                # Wenn alle passenden Vorgaben erfuellt sind, bleibt der Ueberhang
                # bei der letzten Vorgabe als sichtbare Mehrproduktion erhalten.
                buchen(kandidaten[-1], a, rest, "automatisch")
    for p in plaene:
        p["auftraege"] = list(records[p["id"]].values())
        p["auftraege_anzahl"] = len(p["auftraege"])
        laufend = [a for a in p["auftraege"] if a["status"] != "abgeschlossen"]
        p["laufende_auftraege"] = len(laufend)
        p["laufend_ohne_menge"] = sum(a["gesamtstueck"] is None for a in laufend)
        p["fehlt"] = max(p["sollmenge"] - p["ist"], 0)
        p["mehrproduktion"] = max(p["ist"] - p["sollmenge"], 0)
        p["erfuellt_prozent"] = p["ist"] / p["sollmenge"] * 100
    return plaene


def fortschritt(von, bis):
    with db.get_conn() as c:
        # Alle Quellen in einem konsistenten Lesesnapshot, auch bei parallelem
        # Auftragsabschluss. Die Berechnung selbst schreibt keine Produktionsdaten.
        c.execute("BEGIN")
        result = _berechne(c, von, bis)
        c.commit()
        return result


def auswahl():
    return [p for p in fortschritt("0001-01-01", "9999-12-31") if not p["archiviert"]]
