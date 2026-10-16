/* Gemeinsamer Soll-Ist-Bericht fuer Planung und Auswertung. Keine Fremdbibliotheken. */
window.TMPlan = (()=>{
  const esc = value=>String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const num = n=>Number(n || 0).toLocaleString("de-DE",{maximumFractionDigits:1});
  const datum = s=>/^\d{4}-\d{2}-\d{2}$/.test(s || "") ? s.split("-").reverse().join(".") : "—";
  async function api(url, data){
    const ctrl = new AbortController();
    const timer = setTimeout(()=>ctrl.abort(),12000);
    const csrf = document.cookie.match(/(?:^|;\s*)__Host-erf_csrf=([^;]+)/);
    const opts = {credentials:"include",signal:ctrl.signal,headers:{}};
    if(csrf) opts.headers["X-CSRF-Token"] = decodeURIComponent(csrf[1]);
    if(data!==undefined){opts.method="POST";opts.headers["Content-Type"]="application/json";opts.body=JSON.stringify(data);}
    try {
      const r = await fetch(url, opts);
      const result = await r.json();
      if(!r.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Eingaben prüfen oder Sitzung erneuern.");
      return result;
    }finally{clearTimeout(timer);}
  }
  function render(target, rows, edit){
    if(!rows.length){target.innerHTML='<p class="plan-hinweis">Keine Produktionsvorgaben vorhanden.</p>';return;}
    target.innerHTML=`<div class="plan-tabelle"><table><thead><tr><th>Vorgabe / Produktionszeitraum</th><th>Soll</th><th>Ist gesamt</th><th>Fehlt</th><th>Erfüllt</th><th>Mehr</th><th>Produktion im Zeitraum</th>${edit?'<th></th>':''}</tr></thead><tbody>${rows.map(p=>`<tr>
      <td><div class="plan-kultur">${esc(p.kultur_name)} · ${esc(TMKulturen.topf(p.topfgroesse))}${p.topfform?' · '+esc(p.topfform):''}</div><div class="plan-meta">Produktion ${datum(p.beginn)} – ${datum(p.termin)}</div><div class="plan-meta">Vorgabe ${esc(p.id.slice(-8))}${p.bemerkung?' · '+esc(p.bemerkung):''}</div>
      ${p.archiviert?'<span class="plan-status">Archiviert</span>':p.ist>=p.sollmenge?'<span class="plan-status">Erfüllt</span>':''}
      <details><summary>${num(p.auftraege_anzahl)} zugeordnete Aufträge</summary><ul>${p.auftraege.map(a=>`<li>${esc(a.auftragsnr)} · ${datum(a.datum)} · ${a.angerechnet==null?'Menge offen':num(a.angerechnet)+' Stück angerechnet'}${a.gesamtstueck!=null && a.angerechnet!==a.gesamtstueck?' von '+num(a.gesamtstueck)+' Stück im Auftrag':''}${a.status!=='abgeschlossen'?' (vorläufig)':''}</li>`).join('') || '<li>Noch nicht begonnen</li>'}</ul></details></td>
      <td class="plan-zahl" data-label="Soll">${num(p.sollmenge)}</td><td class="plan-zahl" data-label="Ist gesamt"><strong>${num(p.ist)}</strong>${p.laufende_auftraege?`<div class="plan-meta">+ ${num(p.vorlaeufig)} vorläufig<br>${num(p.laufende_auftraege)} laufend${p.laufend_ohne_menge?', davon '+num(p.laufend_ohne_menge)+' ohne Menge':''}</div>`:''}</td>
      <td class="plan-zahl" data-label="Fehlt">${num(p.fehlt)}</td><td class="plan-zahl" data-label="Erfüllt">${num(p.erfuellt_prozent)} %<div class="plan-balken"><span style="width:${Math.max(0,Math.min(100,p.erfuellt_prozent))}%"></span></div></td><td class="plan-zahl" data-label="Mehrproduktion">${num(p.mehrproduktion)}</td>
      <td class="plan-zahl" data-label="Produktion im Zeitraum">${num(p.ist_zeitraum)}${p.vorlaeufig_zeitraum?`<div class="plan-meta">+ ${num(p.vorlaeufig_zeitraum)} vorläufig</div>`:''}</td>
      ${edit?`<td class="plan-edit-cell"><button type="button" class="sekundaer" data-plan-edit="${esc(p.id)}">Bearbeiten</button></td>`:''}</tr>`).join('')}</tbody></table></div>`;
    if(edit) target.querySelectorAll('[data-plan-edit]').forEach(b=>b.onclick=()=>edit(rows.find(p=>p.id===b.dataset.planEdit)));
  }
  async function loadReport(target, von, bis, kultur_id=null){
    try {render(target, await api('/api/auswertung/planung',{von,bis,kultur_id}));}
    catch(e){target.textContent=e.message;target.classList.add('plan-fehler');}
  }
  async function init(){
    const $=id=>document.getElementById(id);
    if(!$('planForm')) return;
    let topfform="",speichert=false;
    let cfg, kulturen, rows=[], current=null, canEdit=false, heute="", draftId=crypto.randomUUID();
    const meldung=(text,fehler=false)=>{$('planMeldung').textContent=text;$('planMeldung').classList.toggle('plan-fehler',fehler);};
    const kulturId=()=>$('pKultur').value==='Freitext'?cfg?.kultur_freitext:kulturen?.find(k=>k.name===$('pKultur').value)?.id;
    const bestandOhneForm=()=>Boolean(current && !current.topfform && current.auftraege_anzahl>0);
    const gueltig=()=>{
      const kultur=kulturId(),soll=Number($('pSoll').value),beginn=$('pBeginn').value,ende=$('pEnde').value;
      return Boolean(canEdit && kultur && (kultur!==cfg.kultur_freitext || $('pFrei').value.trim()) &&
        cfg.topfgroessen.includes($('pTopf').value) && (['rund','eckig'].includes(topfform) || bestandOhneForm()) &&
        Number.isInteger(soll) && soll>0 && soll<=1000000000 &&
        /^\d{4}-\d{2}-\d{2}$/.test(beginn) && /^\d{4}-\d{2}-\d{2}$/.test(ende) && beginn<=ende &&
        $('pSoll').validity.valid && $('pBeginn').validity.valid && $('pEnde').validity.valid);
    };
    const freigabe=()=>{$('planSpeichern').disabled=speichert || !gueltig();};
    const formKacheln=()=>document.querySelectorAll('[data-topfform]').forEach(b=>{
      b.setAttribute('aria-pressed',String(b.dataset.topfform===topfform));
      b.disabled=Boolean(current?.auftraege_anzahl);
    });
    document.querySelectorAll('[data-topfform]').forEach(b=>b.onclick=()=>{
      topfform=topfform===b.dataset.topfform?'':b.dataset.topfform;formKacheln();freigabe();
    });
    const reset=()=>{
      topfform="";
      current=null;draftId=crypto.randomUUID();$('planForm').reset();$('pBeginn').value=heute;$('planFormTitel').textContent='Neue Produktionsvorgabe';
      for(const id of ['pKultur','pFrei','pTopf']) $(id).disabled=false;
      $('pFest').hidden=true;$('pFreiGruppe').hidden=true;$('pFrei').required=false;$('pFormBestand').hidden=true;formKacheln();freigabe();
    };
    const edit=p=>{
      current=p;topfform=p.topfform || '';formKacheln();$('planFormTitel').textContent='Produktionsvorgabe bearbeiten';
      $('pKultur').value=p.kultur===cfg.kultur_freitext?'Freitext':p.kultur_name;
      $('pFrei').value=p.kultur_frei;$('pFreiGruppe').hidden=p.kultur!==cfg.kultur_freitext;$('pFrei').required=p.kultur===cfg.kultur_freitext;
      for(const [id,key] of [['pTopf','topfgroesse'],['pSoll','sollmenge'],['pEnde','termin'],['pBeginn','beginn']]) $(id).value=key==='topfgroesse'?TMKulturen.topf(p[key]):p[key];
      $('pArchiv').checked=Boolean(p.archiviert);
      for(const id of ['pKultur','pFrei','pTopf']) $(id).disabled=p.auftraege_anzahl>0;
      $('pFest').hidden=!p.auftraege_anzahl;
      $('pFormBestand').hidden=!bestandOhneForm();freigabe();
      $('planEditor').scrollIntoView({behavior:'smooth',block:'start'});
    };
    function anzeigen(){render($('planBericht'),rows.filter(p=>$('planArchivZeigen').checked || !p.archiviert),canEdit?edit:null);}
    async function laden(){
      try {rows=await api('/api/auswertung/planung',{von:$('planVon').value,bis:$('planBis').value});anzeigen();}
      catch(e){meldung(e.message,true);}
    }
    try {
      cfg=await api('/api/config');kulturen=TMKulturen.optionen(cfg);
      $('planKulturen').replaceChildren(...[...kulturen.map(k=>k.name),'Freitext'].map(name=>{const o=document.createElement('option');o.value=name;return o;}));
      cfg.topfgroessen.forEach(t=>{const o=document.createElement('option');o.value=t;o.textContent=t;$('pTopf').appendChild(o);});
      try {const s=await api('https://10.10.0.210:8451/api/session/me');canEdit=['meister','admin'].includes((s.roles || {}).topfmaschine_mayer);$('planBenutzer').textContent=s.display_name || s.username || '';}
      catch(e){meldung('Sitzung konnte nicht geladen werden. Vorgaben können weiterhin gelesen werden.',true);}
      $('planEditor').hidden=!canEdit;
      // Lokales Kalenderdatum statt UTC-Datum beim Tageswechsel.
      const now=new Date();heute=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
      $('planVon').value=heute;$('planBis').value=heute;$('pBeginn').value=heute;
      $('planLaden').onclick=laden;$('planArchivZeigen').onchange=anzeigen;
      $('pKultur').oninput=()=>{
        const frei=$('pKultur').value==='Freitext';$('pFreiGruppe').hidden=!frei;$('pFrei').required=frei;freigabe();
      };
      $('planForm').addEventListener('input',freigabe);
      $('planForm').addEventListener('change',freigabe);
      freigabe();
      $('planForm').onsubmit=async e=>{
        e.preventDefault();
        if(speichert || !gueltig()){freigabe();meldung('Bitte alle Pflichtfelder vollständig und gültig ausfüllen.',true);return;}
        const kultur=kulturId();
        const body={id:current?.id || draftId,version:current?.updated_ms ?? null,kultur,kultur_frei:$('pFrei').value,topfgroesse:$('pTopf').value,topfform,sollmenge:Number($('pSoll').value),termin:$('pEnde').value,beginn:$('pBeginn').value,archiviert:$('pArchiv').checked};
        speichert=true;freigabe();
        try {await api('/api/planung/save',body);reset();meldung('Produktionsvorgabe gespeichert.');await laden();}
        catch(e){meldung(e.message,true);}finally{speichert=false;freigabe();}
      };
      await laden();
    }catch(e){meldung(e.message,true);}
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init);else init();
  return {api,render,loadReport};
})();
