/* =====================================================================
   sync.js – Topfmaschine Mayer – Offline-fähige Synchronisation.
   ---------------------------------------------------------------------
   Strategie:
   - Jede Operation wird in eine IndexedDB-Outbox geschrieben.
   - Ein Sync-Loop sendet die Outbox als einzelne API-Calls an den Server.
   - Beim Start und alle 30 Sekunden wird der aktive Auftrag vom Server geladen.

   Robustheit (übernommen aus Blumenband_Produktionserfassung/app/static/sync.js,
   dort nach dem Vorfall "100 offline, nur Reload hilft" ergänzt – siehe
   Analyse_Sync_Offline_Problem.md dort für die volle Herleitung):
   Die fruehere Fassung dieser Datei plante den Sync-Loop ueber eine einzige,
   sich selbst neu planende setTimeout-Kette. Blieb dieser eine geplante
   Aufruf aus (Tab im Hintergrund, Android friert die Seite ein, eine
   Exception im Aufrufer), stand die gesamte Sync-Schleife fuer immer still -
   nur ein manueller Reload half. Gegenmassnahmen hier, analog zu Blumenband:
   - Watchdog bei visibilitychange/pageshow (erzwingt sofort einen Sync-
     Versuch, sobald die Seite wieder sichtbar wird / aus dem bfcache
     zurueckkehrt).
   - Der periodische Lese-Reload (alle 30s) stoesst zusaetzlich die Schreib-
     Sync-Schleife an - eine haengengebliebene Outbox laeuft dadurch
     spaetestens nach 30s von selbst wieder an, unabhaengig vom Zustand der
     eigentlichen Sync-Kette.
   - Stale-Timer-Erkennung: ein laenger als 5s haengender Debounce-Timer wird
     verworfen statt endlos zu blockieren.
   - Die IndexedDB-Verbindung wird bei einem Fehler verworfen (idb = null),
     damit der naechste Zugriff eine frische Verbindung oeffnet statt
     dauerhaft gegen eine durch Speicherdruck/Standby ungueltig gewordene
     Verbindung zu laufen.
   ===================================================================== */
(function (global) {
  "use strict";

  const DB_NAME  = "tm_outbox";
  const STORE    = "ops";
  let   idb      = null;
  let   online   = navigator.onLine;
  let   syncing  = false;
  let   _pendingCount = 0;
  let   _onStatusCb   = null;
  let   _lastOkMs = 0;
  let   _blockedOps = [];

  /* Statuscodes, bei denen ein erneuter Versuch NIE erfolgreich sein kann:
     der Server hat die Operation inhaltlich abgelehnt (fehlende Berechtigung,
     Zielobjekt weg, Regelverstoss). Solche Eintraege werden nach dem ersten
     Fehlversuch geparkt statt endlos wiederholt - sie wuerden sonst alle
     nachfolgenden, gueltigen Eintraege blockieren. 429 (Sperre nach zu vielen
     Fehlversuchen) und 5xx gehoeren bewusst NICHT dazu. */
  const _ENDGUELTIG = new Set([400, 403, 404, 409, 410, 422]);
  const _MAX_VERSUCHE = 3;

  /* ── IndexedDB ──
     Jede Operation faengt Fehler ab und verwirft die gecachte Verbindung
     (idb = null) statt sie dauerhaft weiterzuverwenden - siehe Erlaeuterung
     oben. */
  function openIDB() {
    return new Promise((res, rej) => {
      const r = indexedDB.open(DB_NAME, 1);
      r.onupgradeneeded = () => {
        const d = r.result;
        if (!d.objectStoreNames.contains(STORE))
          d.createObjectStore(STORE, { keyPath: "opid" });
      };
      r.onsuccess = () => res(r.result);
      r.onerror   = () => rej(r.error);
    });
  }

  async function putOp(op) {
    if (!idb) idb = await openIDB();
    try {
      return await new Promise((res, rej) => {
        const tx = idb.transaction(STORE, "readwrite");
        tx.objectStore(STORE).put(op);
        tx.oncomplete = res;
        tx.onerror    = () => rej(tx.error);
      });
    } catch (e) { idb = null; throw e; }
  }

  async function allOps() {
    if (!idb) idb = await openIDB();
    try {
      return await new Promise((res, rej) => {
        const tx = idb.transaction(STORE, "readonly");
        const rq = tx.objectStore(STORE).getAll();
        rq.onsuccess = () => res(rq.result || []);
        rq.onerror   = () => rej(rq.error);
      });
    } catch (e) { idb = null; throw e; }
  }

  async function delOp(opid) {
    if (!idb) idb = await openIDB();
    try {
      return await new Promise((res, rej) => {
        const tx = idb.transaction(STORE, "readwrite");
        tx.objectStore(STORE).delete(opid);
        tx.oncomplete = res;
        tx.onerror    = () => rej(tx.error);
      });
    } catch (e) { idb = null; throw e; }
  }

  let _opSeq = 0;
  function newOpId() {
    return Date.now().toString(36) + "-" + (++_opSeq).toString(36) +
           "-" + Math.random().toString(36).slice(2, 7);
  }

  /* ── Fetch mit Timeout ──
     Verhindert, dass ein haengender Request (instabiles WLAN) den
     Sync-Loop blockiert: ohne Timeout bleibt "syncing" dauerhaft true. */
  function _fetchTimeout(url, opts, timeoutMs) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs || 12000);
    return fetch(url, Object.assign({}, opts, { signal: ctrl.signal }))
      .finally(() => clearTimeout(t));
  }

  /* ── API-Hilfsfunktion ──
     Der HTTP-Status wird am Error-Objekt mitgegeben (e.status), damit
     runSync() zwischen "nie wieder erfolgreich" (404 - Ziel existiert nicht
     mehr) und "vielleicht spaeter erfolgreich" (Netzwerkfehler, 5xx)
     unterscheiden kann. */
  async function apiPost(path, body) {
    const r = await _fetchTimeout(path, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(body),
    });
    if (!r.ok) {
      const txt = await r.text().catch(() => "");
      const err = new Error(`HTTP ${r.status}: ${txt}`);
      err.status = r.status;
      throw err;
    }
    return r.json();
  }

  /* ── Abschluss-Tokens ──
     auftrag/close, neue_kultur und day_close sind serverseitig an einen
     nicht erratbaren Token gebunden (siehe main.py _auftrag_token/_day_token).
     Der Token wird beim ohnehin bereits stattfindenden Laden des Auftrags/
     Tagesstatus mitgeliefert - hier vor dem eigentlichen Senden aus dem
     Sync-Loop heraus abgerufen, damit sich am Aufruf-Fluss in index.html
     nichts aendert. */
  async function _fetchAuftragToken(auftrag_id) {
    try {
      const r = await _fetchTimeout(`/api/auftrag/${encodeURIComponent(auftrag_id)}`);
      if (!r.ok) return "";
      const j = await r.json();
      return j.close_token || "";
    } catch (e) { return ""; }
  }
  async function _fetchDayToken(datum) {
    try {
      const r = await _fetchTimeout(`/api/day/status?datum=${encodeURIComponent(datum)}`);
      if (!r.ok) return "";
      const j = await r.json();
      return j.close_token || "";
    } catch (e) { return ""; }
  }

  /* ── Pending-Status ──
     Blockierte Eintraege werden getrennt gezaehlt: sie warten nicht auf
     Netz, sondern wurden vom Server inhaltlich abgelehnt und brauchen eine
     Entscheidung. Die UI kann beides dadurch unterscheidbar anzeigen. */
  function _setPending(n, blocked) {
    _pendingCount  = n;
    _blockedOps    = blocked || [];
    if (_onStatusCb) _onStatusCb(n, _lastOkMs, _blockedOps);
  }

  async function _countPending() {
    const ops = await allOps().catch(() => []);
    const blocked = ops.filter(o => o.blocked);
    _setPending(ops.length, blocked);
    return ops.length - blocked.length;   // nur das, was noch von selbst laufen kann
  }

  /* ── Outbox-Queue ─────────────────────────────────────────────────── */

  // Serialisierungskette damit keine Race-Conditions in der Outbox entstehen
  let _chain = Promise.resolve();
  function _enqueue(fn) {
    const r = _chain.then(fn, fn);
    _chain  = r.catch(() => {});
    return r;
  }

  /* qms/qseq: Einreihungszeitpunkt der Operation.
     ---------------------------------------------------------------------
     WICHTIG: IndexedDB getAll() liefert die Eintraege in Schluessel-
     Reihenfolge zurueck - also alphabetisch nach opid. Die opid beginnt mit
     einem Typ-Praefix ("acls-", "anew-", "dcls-", "scan-", "wupd-" ...),
     wodurch die Warteschlange bisher NACH TYP statt nach Entstehungszeit
     abgearbeitet wurde. Konkret lief "acls-" (Auftrag abschliessen) immer vor
     "wupd-" (Mitarbeiter-Zeit aendern), auch wenn die Zeitaenderung zuerst
     erfolgt war. Der Server lehnte die Aenderung dann mit
     "Auftrag/Tag abgeschlossen - Aenderung nur im Admin-Modus" (HTTP 400) ab
     und blockierte damit die gesamte restliche Warteschlange.
     Seitdem wird vor dem Senden nach qms/qseq sortiert, also in der
     Reihenfolge, in der die Aktionen tatsaechlich passiert sind. Altbestand
     ohne diese Felder sortiert nach vorn (er ist ohnehin aelter). */
  function queueOp(op) {
    op.qms  = Date.now();
    op.qseq = ++_opSeq;
    return _enqueue(async () => {
      await putOp(op);
      await _countPending();
      scheduleSync();
    });
  }

  /* ── Öffentliche Queue-Funktionen ─────────────────────────────────── */

  function queueAuftragNew(data) {
    return queueOp({ opid: "anew-" + data.id, type: "auftrag_new",    data });
  }
  function queueAuftragUpdate(data) {
    return queueOp({ opid: "aupd-" + data.auftrag_id, type: "auftrag_update", data });
  }
  function queueScan(data) {
    return queueOp({ opid: "scan-" + data.mz_id, type: "worker_scan", data });
  }
  function queueWorkerUpdate(data) {
    return queueOp({ opid: "wupd-" + data.mz_id, type: "worker_update", data });
  }
  function queueAuftragClose(data) {
    return queueOp({ opid: "acls-" + data.auftrag_id, type: "auftrag_close", data });
  }
  function queueNeueKultur(data) {
    return queueOp({ opid: "anew-" + data.new_auftrag_id, type: "neue_kultur", data });
  }
  function queueAuftragDelete(data) {
    return queueOp({ opid: "adel-" + data.auftrag_id, type: "auftrag_delete", data });
  }
  function queueDayClose(data) {
    return queueOp({ opid: "dcls-" + data.datum, type: "day_close", data });
  }
  function queueWorkerDelete(data) {
    return queueOp({ opid: "wdel-" + data.mz_id, type: "worker_delete", data });
  }

  /* Serverantwort auf eine lesbare Kurzfassung eindampfen - der Rohtext ist
     JSON wie {"detail":"Auftrag/Tag abgeschlossen – ..."}. */
  function _kurzGrund(msg) {
    const m = String(msg || "");
    const j = m.match(/\{.*\}$/);
    if (j) {
      try {
        const o = JSON.parse(j[0]);
        const d = o.detail;
        if (typeof d === "string") return d;
        if (d && d.message) return d.message;
      } catch (e) {}
    }
    return m.slice(0, 120);
  }

  /* ── Geparkte Eintraege ───────────────────────────────────────────── */
  async function getBlockedOps() {
    const ops = await allOps().catch(() => []);
    return ops.filter(o => o.blocked).map(o => ({
      opid:   o.opid,
      type:   o.type,
      grund:  _kurzGrund(o.lastError),
      zeit:   o.qms || o.blockedMs || 0,
      data:   o.data,
    }));
  }

  /* Einen geparkten Eintrag erneut freigeben (z. B. nachdem der Tag wieder
     geoeffnet oder der Auftrag korrigiert wurde). */
  async function retryOp(opid) {
    const ops = await allOps().catch(() => []);
    const op  = ops.find(o => o.opid === opid);
    if (!op) return false;
    delete op.blocked; delete op.lastError; delete op.blockedMs;
    op.fails = 0;
    await putOp(op);
    await _countPending();
    forceSync();
    return true;
  }

  async function retryAllBlocked() {
    const ops = await allOps().catch(() => []);
    for (const op of ops.filter(o => o.blocked)) {
      delete op.blocked; delete op.lastError; delete op.blockedMs;
      op.fails = 0;
      await putOp(op).catch(() => {});
    }
    await _countPending();
    return forceSync();
  }

  /* Endgueltig verwerfen - bewusst nur ueber die UI mit Bestaetigung
     erreichbar, weil damit eine erfasste Aenderung verloren geht. */
  async function discardOp(opid) {
    await delOp(opid);
    await _countPending();
    return true;
  }

  /* ── Sync-Loop ────────────────────────────────────────────────────── */
  async function runSync() {
    if (syncing || !online) return;
    let ops = await allOps().catch(() => []);
    if (!ops.length) { _setPending(0, []); return; }

    // Reihenfolge = Entstehungsreihenfolge, NICHT alphabetisch nach opid
    // (siehe ausfuehrliche Begruendung bei queueOp).
    ops.sort((a, b) => (a.qms || 0) - (b.qms || 0) || (a.qseq || 0) - (b.qseq || 0));

    syncing = true;
    try {
      for (const op of ops) {
        // Geparkte Eintraege ueberspringen - sie warten auf eine Entscheidung
        // des Nutzers und duerfen die uebrigen nicht aufhalten.
        if (op.blocked) continue;
        try {
          switch (op.type) {
            case "auftrag_new":
              await apiPost("/api/auftrag/new",         op.data); break;
            case "auftrag_update":
              await apiPost("/api/auftrag/update",      op.data); break;
            case "worker_scan":
              await apiPost("/api/auftrag/scan",        op.data); break;
            case "worker_update":
              await apiPost("/api/worker/update",       op.data); break;
            case "auftrag_close": {
              const token = await _fetchAuftragToken(op.data.auftrag_id);
              await apiPost("/api/auftrag/close", Object.assign({}, op.data, { token }));
              break;
            }
            case "neue_kultur": {
              const token = await _fetchAuftragToken(op.data.auftrag_id);
              await apiPost("/api/auftrag/neue_kultur", Object.assign({}, op.data, { token }));
              break;
            }
            case "worker_delete":
              await apiPost("/api/worker/delete",       op.data); break;
            case "auftrag_delete":
              await apiPost("/api/admin/auftrag/delete", op.data); break;
            case "day_close": {
              const token = await _fetchDayToken(op.data.datum);
              await apiPost("/api/day/close", Object.assign({}, op.data, { token }));
              break;
            }
            default:
              break;
          }
          await delOp(op.opid);
          online = true;
          _lastOkMs = Date.now();
        } catch (e) {
          const status = e && e.status;
          console.warn("Sync-Fehler:", op.type, e && e.message);
          op.fails = (op.fails || 0) + 1;

          // Inhaltliche Ablehnung durch den Server oder wiederholt
          // gescheitert: Eintrag PARKEN statt loeschen. Er bleibt mit Grund
          // erhalten und sichtbar, blockiert aber die uebrigen nicht mehr.
          if (_ENDGUELTIG.has(status) || op.fails >= _MAX_VERSUCHE) {
            op.blocked   = true;
            op.lastError = (e && e.message) || "Unbekannter Fehler";
            op.blockedMs = Date.now();
            await putOp(op).catch(() => {});
            console.error(`Sync geparkt: ${op.type} ${op.opid} – ${op.lastError}`);
            if (typeof toast === "function") {
              toast(`Sync blockiert: ${op.type} – ${_kurzGrund(op.lastError)}`, true);
            }
            await _countPending();
            continue; // naechsten Eintrag in dieser Runde weiterverarbeiten
          }

          await putOp(op).catch(() => {});
          // Nur echte Verbindungsprobleme gelten als "offline". Ein 4xx/5xx
          // bedeutet, dass der Server erreichbar ist - ihn hier als offline zu
          // markieren hat den Sync-Loop zusaetzlich schlafen gelegt.
          if (!status || status >= 500) online = false;
          break; // Reihenfolge wahren, spaeter erneut versuchen
        }
      }
    } finally {
      syncing = false;
      const remaining = await _countPending();   // ohne geparkte Eintraege
      if (remaining) {
        // Selbst-Reschedule bei verbleibendem Rueckstand - abgesichert durch
        // die Watchdogs unten, falls dieser eine geplante Aufruf doch einmal
        // ausbleiben sollte.
        setTimeout(scheduleSync, online ? 1000 : 5000);
      }
    }
  }

  /* ── Watchdog-Infrastruktur ──
     scheduleSync() debounced ueber `timer` (400ms). Ist dieser Timer laenger
     als STALE_TIMER_MS gesetzt, gilt er als haengengeblieben (z. B. weil das
     Geraet zwischenzeitlich im Hintergrund/Standby war) und wird verworfen
     statt die Schleife fuer immer zu blockieren. */
  let timer = null;
  let _timerSetAt = 0;
  const STALE_TIMER_MS = 5000;

  function scheduleSync() {
    if (timer) {
      if (Date.now() - _timerSetAt < STALE_TIMER_MS) return;
      clearTimeout(timer);
      timer = null;
    }
    _timerSetAt = Date.now();
    timer = setTimeout(() => { timer = null; runSync(); }, 400);
  }

  /* Erzwingt einen sofortigen Sync-Versuch, unabhaengig vom Debounce-Status -
     fuer die Watchdogs unten sowie als optionaler manueller "Jetzt
     synchronisieren"-Klick in der UI (siehe index.html, Klick auf
     #syncBadge). */
  function forceSync() {
    if (timer) { clearTimeout(timer); timer = null; }
    online = navigator.onLine;
    return runSync();
  }

  // Watchdog 1: Sobald die Seite wieder sichtbar wird (Tab-Wechsel,
  // Benachrichtigung, Standby/Wake, App-Wechsel auf Android), Sync-Status neu
  // pruefen und die Schleife notfalls direkt entklemmen, statt auf den
  // naechsten Scan zu warten.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") forceSync();
  });
  // Watchdog 2: bfcache-Restore (z.B. nach Vor-/Zurueck-Navigation) faengt
  // pageshow, nicht immer visibilitychange.
  window.addEventListener("pageshow", (ev) => { if (ev.persisted) forceSync(); });

  /* ── Periodisches Nachladen ───────────────────────────────────────── */
  let _reloadTimer = null;
  function startPeriodicReload(cb, intervalMs) {
    if (_reloadTimer) clearInterval(_reloadTimer);
    _reloadTimer = setInterval(async () => {
      // Watchdog 3: dieser Takt laeuft unabhaengig von der Schreib-Sync-
      // Schleife (setInterval statt der selbst-planenden setTimeout-Kette).
      // Ein Stoss auf scheduleSync() hier sorgt dafuer, dass eine
      // haengengebliebene Outbox spaetestens nach `intervalMs` (Standard 30s)
      // von selbst wieder anlaeuft, auch ohne visibilitychange-Event.
      scheduleSync();
      if (!online) return;
      try {
        const data = await _fetchTimeout("/api/auftrag/active").then(r => r.json());
        if (cb) cb(data);
      } catch (e) {}
    }, intervalMs || 30000);
  }

  async function loadActiveAuftrag() {
    const r = await _fetchTimeout("/api/auftrag/active");
    if (!r.ok) throw new Error("Ladefehler");
    return r.json();
  }

  async function adminVerify(pw) {
    try {
      const r = await apiPost("/api/admin/verify", { pw });
      return !!r.ok;
    } catch { return false; }
  }

  async function auswertungVerify(pw) {
    try {
      const r = await apiPost("/api/auswertung/verify", { pw });
      return !!r.ok;
    } catch { return false; }
  }

  /* ── Online-Status ─────────────────────────────────────────────────── */
  window.addEventListener("online",  () => { online = true;  forceSync(); });
  window.addEventListener("offline", () => { online = false; });

  /* ── Öffentliche API ──────────────────────────────────────────────── */
  global.TMSync = {
    newOpId,
    queueAuftragNew,
    queueAuftragUpdate,
    queueScan,
    queueWorkerUpdate,
    queueAuftragClose,
    queueNeueKultur,
    queueAuftragDelete,
    queueWorkerDelete,
    queueDayClose,
    scheduleSync,
    forceSync,
    startPeriodicReload,
    loadActiveAuftrag,
    adminVerify,
    auswertungVerify,
    getPendingCount: () => _pendingCount,
    getBlockedCount: () => _blockedOps.length,
    getLastSyncOk: () => _lastOkMs,
    getBlockedOps,
    retryOp,
    retryAllBlocked,
    discardOp,
    onStatus: (cb) => { _onStatusCb = cb; },
    runSync,
  };
})(window);
