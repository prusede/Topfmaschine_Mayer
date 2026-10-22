/* Halle 1: Monatswerte, Monatsueberschriften und Kulturen oeffnen Details direkt. */
(function(root){
  "use strict";
  const monate=["Jan","Feb","Mär","Apr","Mai","Jun","Jul","Aug","Sep","Okt","Nov","Dez"];
  const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const num=(n,d=0)=>n==null?"–":Number(n).toLocaleString("de-DE",{maximumFractionDigits:d,minimumFractionDigits:d});
  const iso=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
  const datum=s=>String(s||"").split("-").reverse().join(".");
  const wochentag=s=>["So","Mo","Di","Mi","Do","Fr","Sa"][new Date(s+"T00:00:00").getDay()]||"";
  let folge=0;
  // Farben als CSS-Variablen; die Vorgaben sind die gruenen Werte von Halle 1 und Stolze.
  // Mayer setzt die --pu-*-Variablen in seiner auswertung.html auf Petrol. So bleibt diese
  // Datei in allen Apps byte-identisch (22.10.2026).
  function style(){
    if(document.getElementById("produktion-stil")) return;
    const el=document.createElement("style"); el.id="produktion-stil";
    el.textContent=`.produktion-zeit{display:flex;flex-wrap:wrap;align-items:center;gap:7px}.produktion-zeit button,.produktion-zeit select{font:inherit;font-size:.84rem;padding:6px 10px;border:1px solid #d8d7ca;border-radius:7px;background:#fff;color:var(--pu-text,#254d2c)}.produktion-zeit button{cursor:pointer}.produktion-zeit button[aria-pressed=true]{background:var(--pu-akzent,#2f6b3a);color:white}.produktion-zeit label{font-size:.8rem;margin-left:8px}.produktion-monate{background:white;border:1px solid #e3ded2;border-radius:12px;margin:14px 0;overflow:hidden}.produktion-monate>summary{padding:13px 15px;cursor:pointer;font-weight:700;color:var(--pu-text,#244c2c)}.produktion-monate .prod-inhalt{padding:0 12px 12px}.prod-scroll{overflow-x:auto}.prod-matrix{overflow:visible;min-width:1480px;width:100%;border-collapse:collapse;font-size:1rem}.prod-matrix td,.prod-matrix th{padding:10px 8px;border-bottom:1px solid #e3ded2;vertical-align:top}.prod-matrix th{font-size:.875rem;letter-spacing:0;background:#f7f5ef;color:var(--pu-kopf-text,#344e36)}.prod-matrix>thead>tr>th:first-child,.prod-matrix>tbody>tr>th:first-child{position:sticky;left:0;z-index:1;background:#f7f5ef}.prod-matrix .prod-zahl{display:block;font-size:1.125rem;white-space:nowrap}.prod-matrix small{display:block;font-size:.875rem;line-height:1.5;color:#5c6157;font-weight:400}.prod-monat{border:1px solid var(--pu-feld-rand,#d4dfd0);border-radius:6px;background:var(--pu-feld,#eff5ec);padding:7px 6px;width:100%;text-align:right;cursor:pointer;font:inherit;color:var(--pu-text,#244c2c)}.prod-kultur,.prod-kopf-monat{border:0;background:transparent;color:var(--pu-text,#244c2c);font:inherit;font-weight:700;text-align:left;padding:2px;cursor:pointer;text-decoration:underline;text-decoration-style:dotted;text-underline-offset:4px}.prod-kopf-monat:disabled{color:#777;cursor:default;text-decoration:none}.prod-kultur:focus-visible,.prod-kopf-monat:focus-visible,.prod-monat:focus-visible{outline:2px solid var(--pu-akzent,#2f6b3a);outline-offset:2px}.prod-monat:hover{background:var(--pu-block,#e4efdf);border-color:var(--pu-feld-hover-rand,#9fbf9f)}.prod-monat[aria-expanded=true]{background:var(--pu-akzent,#2f6b3a);border-color:var(--pu-akzent-dunkel,#1f4a28);color:#fff;border-bottom-left-radius:0;border-bottom-right-radius:0}.prod-monat[aria-expanded=true] small{color:var(--pu-hell,#e7f0e6)}.prod-kultur[aria-expanded=true]{background:var(--pu-akzent,#2f6b3a);color:#fff;border-radius:6px;padding:2px 6px;text-decoration:none}.prod-kultur[aria-expanded=true] small{color:var(--pu-hell,#e7f0e6)}tr:has([aria-expanded=true])+tr.prod-detail>td{background:var(--pu-block,#e4efdf);border:2px solid var(--pu-akzent,#2f6b3a);border-top-width:4px}.prod-detail{background:#faf9f4}.prod-detail details{margin:4px 0;border:1px solid #e3ded2;border-radius:7px;background:white}.prod-detail summary{cursor:pointer;padding:8px 14px;font-size:1rem;list-style:none;display:grid;grid-template-columns:minmax(170px,1fr) repeat(3,minmax(110px,160px));gap:12px;align-items:baseline}.prod-detail summary::-webkit-details-marker{display:none}.prod-detail summary:hover{background:var(--pu-tag-hover,#f6f9f4)}.tag-datum::before{content:"▸";display:inline-block;width:1.2em;text-align:center;color:var(--pu-akzent,#2f6b3a);transition:transform .15s}.prod-detail details[open]>summary .tag-datum::before{transform:rotate(90deg)}.tag-wert{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;color:#5c6157}.prod-detail summary.mit-ref{grid-template-columns:minmax(170px,1fr) repeat(4,minmax(110px,160px))}.tag-ref{font-weight:700;color:var(--pu-akzent-dunkel,#1f4a28)}.tag-ref.gering{color:#a8341f}.tag-ref.leer,.prod-kopf-ref.leer{color:#5c6157;font-weight:400}.prod-kopf-ref{color:var(--pu-akzent-dunkel,#1f4a28)}.prod-kopf-ref.gering{color:#a8341f}.prod-detail-inhalt>h2 small{display:block;font-size:.875rem;font-weight:400;color:#5c6157;margin-top:3px}.tag-wert b{color:#21251f}.prod-detail details[open]>summary{border-bottom:1px solid #e3ded2;margin-bottom:12px}.prod-detail-inhalt{position:sticky;left:0;width:calc(100vw - 88px);max-width:1500px}.prod-detail-inhalt>h2{font-size:1.125rem;margin:6px 0 12px}.prod-detail-zu{border:0;background:transparent;padding:2px 4px;margin:-2px -4px;border-radius:6px;font:inherit;color:inherit;text-align:left;cursor:pointer}.prod-detail-zu:hover{background:var(--pu-zu-hover,#d4e6cc)}.prod-detail-zu:focus-visible{outline:2px solid var(--pu-akzent,#2f6b3a);outline-offset:2px}.prod-zu-pfeil{font-size:.8em;color:var(--pu-akzent,#2f6b3a);margin-left:6px}.prod-detail .leistungsgruppe{margin:0 12px 14px}.prod-kultur{min-width:135px}.prod-detail table{width:100%;font-size:1rem}.prod-detail td,.prod-detail th{padding:7px}.prod-hinweis{font-size:.875rem;line-height:1.5;padding:0 12px;color:#5c6157;margin:8px 0}.prod-fehler{color:#a8341f}.prod-badge{display:inline-block;background:#f8eed5;border-radius:4px;padding:2px 5px;font-size:.875rem}`;
    document.head.appendChild(el);
  }
  function zeitwahl({target,von,bis,onChange}){
    style(); let jahre=[],jahr=new Date().getFullYear();
    target.classList.add("produktion-zeit");
    target.innerHTML=[['today','Heute'],['week','Woche'],['month','Monat'],['year','Jahr']].map(([id,label])=>`<button type="button" data-range="${id}" aria-pressed="${id==='today'}">${label}</button>`).join("")+
      '<label>Jahr <select aria-label="Produktionsjahr" data-jahr></select></label><label>Monat <select aria-label="Produktionsmonat" data-monat><option value="">wählen</option>'+monate.map((m,i)=>`<option value="${i}">${m}</option>`).join("")+'</select></label>';
    const ys=target.querySelector("[data-jahr]"),ms=target.querySelector("[data-monat]");
    function jahreSetzen(values){
      jahre=[...new Set([...values,jahr,new Date().getFullYear(),new Date().getFullYear()-1])].sort((a,b)=>b-a);
      ys.innerHTML=jahre.map(y=>`<option value="${y}">${y}</option>`).join("");ys.value=String(jahr);
    }
    function set(a,b,mode){
      von.value=iso(a);bis.value=iso(b);jahr=a.getFullYear();jahreSetzen(jahre);
      target.querySelectorAll("[data-range]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.range===mode)));
      onChange();
    }
    target.querySelectorAll("[data-range]").forEach(b=>b.addEventListener("click",()=>{
      const t=new Date();ms.value="";
      if(b.dataset.range==="today") set(t,t,"today");
      if(b.dataset.range==="week"){const a=new Date(t);a.setDate(a.getDate()-(a.getDay()+6)%7);set(a,t,"week");}
      if(b.dataset.range==="month") set(new Date(t.getFullYear(),t.getMonth(),1),t,"month");
      if(b.dataset.range==="year") set(new Date(jahr,0,1),jahr===t.getFullYear()?t:new Date(jahr,11,31),"year");
    }));
    ys.addEventListener("change",()=>{jahr=Number(ys.value);ms.value="";const t=new Date();set(new Date(jahr,0,1),jahr===t.getFullYear()?t:new Date(jahr,11,31),"year");});
    ms.addEventListener("change",()=>{if(ms.value==="")return;const m=Number(ms.value),t=new Date();set(new Date(jahr,m,1),jahr===t.getFullYear()&&m===t.getMonth()?t:new Date(jahr,m+1,0),"");});
    jahreSetzen([]);
    return {jahre:jahreSetzen,manuell(){const y=Number(von.value.slice(0,4));if(y)jahr=y;jahreSetzen(jahre);ms.value="";target.querySelectorAll("[data-range]").forEach(b=>b.setAttribute("aria-pressed","false"));}};
  }
  // person (optional): {pnr, tagReferenz(rows)} -- nur bei Personalnummernsuche. Dann nennen
  // Ueberschrift und Tageszeilen die Person und ihre Referenz; ohne bleibt alles wie bisher.
  function render(target,data,loadMonth,renderEntries,person=null){
    style();const id=++folge;
    if(target._prodResize)target._prodResize.disconnect();
    if(!(data.rows||[]).length){target.innerHTML='<div class="prod-hinweis">Keine Produktionsdaten im gewählten Zeitraum.</div>';return;}
    const kopf='<tr><th>Kultur / Jahr</th>'+monate.map((m,j)=>`<th><button type="button" class="prod-kopf-monat" data-open-month="${j}" aria-expanded="false" aria-label="${m}: Details aller Kulturen" ${data.rows.some(r=>r.months[j].anzahl)?'':'disabled'}>${m} ▾</button></th>`).join("")+'<th>Gesamt</th></tr>';
    const refAnzeige=p=>p==null?'–':p<60&&Math.round(p*10)>=600?'&lt;60 %':num(p,1)+' %';
    const refLabel=ref=>(ref.bewertungen||0)>1?'Ø Referenz':'Referenz';
    const gering=ref=>ref.prozent==null?' leer':ref.prozent<60?' gering':'';
    const wert=m=>{
      const ref=m.referenz||{},prozent=ref.prozent;
      const label=refLabel(ref);
      const anzeige=refAnzeige(prozent);
      return `<span class="prod-zahl"><b>${num(m.stueck)}</b></span><small>${num(m.stunden,2)} MA-h</small><small>${num(m.sph,0)} Stk/MA-h</small>`+
        (data.referenz_anzeigen?`<small class="prod-ref${prozent!=null&&prozent<60?' gering':''}" title="Nach bewertbaren Netto-Arbeitsstunden gewichtet; Aufsicht/Abladen und andere Erfassungen ohne Ranking zählen nicht mit.">${label}: ${anzeige}</small>`:'');
    };
    target.innerHTML=`<p class="prod-hinweis">${datum(data.von)} – ${datum(data.bis)} · Kultur oder Monat anklicken, um die Tage aufzuklappen.</p><div class="prod-scroll"><table class="prod-matrix"><thead>${kopf}</thead><tbody>`+
      // r.art (optional) kennzeichnet besondere Zeilen fuer die App-Gestaltung, z. B. Halle 1 "schneiden".
      data.rows.map((r,i)=>`<tr${r.art?` data-art="${esc(r.art)}"`:''}><th><button type="button" class="prod-kultur" data-culture="${i}" data-row="${i}" aria-label="${esc(r.kultur)}, ${r.jahr}: alle Details im Zeitraum" aria-expanded="false" aria-controls="prod-${id}-${i}">${esc(r.kultur)}<small>${r.jahr} · Details ▾</small></button></th>`+r.months.map((m,j)=>`<td>${m.anzahl?`<button type="button" class="prod-monat" data-row="${i}" data-month="${j}" aria-label="${esc(r.kultur)}, ${monate[j]} ${r.jahr}: Details" aria-expanded="false" aria-controls="prod-${id}-${i}">${wert(m)}</button>`:'–'}</td>`).join("")+`<td>${wert({stueck:r.total_stueck,stunden:r.total_stunden,sph:r.total_sph,referenz:r.total_referenz})}</td></tr><tr class="prod-detail" id="prod-${id}-${i}" hidden><td colspan="14"></td></tr>`).join("")+'</tbody></table></div>';
    const scroll=target.querySelector(".prod-scroll"),erster=target.querySelector("[data-month]");
    let bedient=false;
    function ausrichten(){
      if(!erster||bedient)return;
      const left=erster.getBoundingClientRect?erster.getBoundingClientRect().left-scroll.getBoundingClientRect().left+scroll.scrollLeft:erster.offsetLeft;
      scroll.scrollLeft=Math.max(0,left-scroll.clientWidth/2);
    }
    ausrichten();
    if(typeof ResizeObserver!=="undefined"){
      target._prodResize=new ResizeObserver(ausrichten);target._prodResize.observe(scroll);
      for(const event of ["pointerdown","wheel"])scroll.addEventListener(event,()=>{bedient=true;},{once:true,passive:true});
    }
    async function oeffnen(button,m,erzwingen=false){
      const row=data.rows[Number(button.dataset.row)];
      const tr=target.querySelector(`#prod-${id}-${button.dataset.row}`),cell=tr.firstElementChild;
      const open=button.getAttribute("aria-expanded")==="true";
      if(open&&erzwingen)return;
      target.querySelectorAll(`[data-row="${button.dataset.row}"]`).forEach(b=>b.setAttribute("aria-expanded","false"));
      if(open){cell.dataset.request=String(++folge);tr.hidden=true;return;}
      button.setAttribute("aria-expanded","true");tr.hidden=false;
      const request=String(++folge);cell.dataset.request=request;
      const a=iso(new Date(row.jahr,m<0?0:m,1)),b=iso(new Date(row.jahr,m<0?12:m+1,0));
      const von=a<data.von?data.von:a,bis=b>data.bis?data.bis:b;
      cell.innerHTML='<p role="status">Details werden geladen …</p>';
      try{
        const rows=await loadMonth(row,von,bis);
        if(cell.dataset.request!==request||!target.contains(cell))return;
        const tage=new Map();rows.forEach(r=>{if(!tage.has(r.datum))tage.set(r.datum,[]);tage.get(r.datum).push(r);});
        const teil=von!==a||bis!==b,titel=m<0?`${datum(von)} – ${datum(bis)}`:`${monate[m]} ${row.jahr}${teil?` (${datum(von)} – ${datum(bis)})`:''}`;
        // Gesamtwert aus der Matrix uebernehmen statt neu rechnen: Ueberschrift und Monatsfeld stimmen so immer ueberein.
        const gesamt=person?(m<0?row.total_referenz:row.months[m].referenz)||{}:null;
        const pfeil='<span class="prod-zu-pfeil" aria-hidden="true">▴ einklappen</span>';
        const kopfPerson=gesamt?` · Nr. ${esc(person.pnr)} · <span class="prod-kopf-ref${gering(gesamt)}">${refLabel(gesamt)} ${refAnzeige(gesamt.prozent)}</span>${pfeil}<small>${num(gesamt.stunden||0,2)} bewertete MA-h · ${gesamt.bewertungen||0} Bewertung${gesamt.bewertungen===1?'':'en'}</small>`:'';
        // Die Ueberschrift klappt den Bereich wieder ein -- wie ein zweiter Klick auf Monat oder Kultur.
        cell.innerHTML=`<div class="prod-detail-inhalt"><h2><button type="button" class="prod-detail-zu" data-row="${button.dataset.row}" title="Einklappen">${esc(row.kultur)} · ${titel}${kopfPerson||pfeil}</button></h2>`+[...tage].sort(([a],[b])=>a.localeCompare(b)).map(([d,rs])=>{
          const st=rs.reduce((s,r)=>s+r.stueck,0),h=rs.reduce((s,r)=>s+r.stunden,0);
          // Wie in der Matrix: Stueck ohne Nettozeit (laufend/unvollstaendig) zaehlen zur Menge, nicht zur Leistung.
          const stZeit=rs.reduce((s,r)=>s+(r.stunden>0?r.stueck:0),0),sph=h>0?stZeit/h:null;
          // Werte als rechtsbuendige Spalten: die Tage lassen sich untereinander vergleichen.
          // Tag ohne Bewertung (nur Aufsicht/Abladen, manuelle Menge ...) zeigt "–", nicht 0 %.
          const tagRef=person?person.tagReferenz(rs):null;
          const refSpalte=tagRef?`<span class="tag-wert tag-ref${gering(tagRef)}">${refLabel(tagRef)} ${refAnzeige(tagRef.prozent)}</span>`:'';
          return `<details><summary${tagRef?' class="mit-ref"':''}><span class="tag-datum">${wochentag(d)} ${datum(d)}</span><span class="tag-wert"><b>${num(st)}</b> Stück</span><span class="tag-wert">${num(h,2)} MA-h</span><span class="tag-wert">${num(sph,0)} Stk/MA-h</span>${refSpalte}</summary><div class="prod-scroll">${renderEntries(rs)}</div></details>`;
        }).join("")+(tage.size?'':'<p>Keine Einträge.</p>')+'</div>';
      }catch(e){if(cell.dataset.request===request&&target.contains(cell))cell.innerHTML='<p class="prod-fehler" role="alert">Details konnten nicht geladen werden. Zum erneuten Laden schließen und öffnen.</p>';}
    }
    target.querySelectorAll("[data-month]").forEach(button=>button.addEventListener("click",()=>oeffnen(button,Number(button.dataset.month))));
    target.querySelectorAll("[data-culture]").forEach(button=>button.addEventListener("click",()=>oeffnen(button,-1)));
    // Schliessen ueber die Ueberschrift: der geoeffnete Knopf der Zeile wird erneut ausgeloest.
    // Der Listener haengt einmal am Container, der beim Neuzeichnen erhalten bleibt; _prodZu
    // zeigt immer auf die aktuelle Zeichnung.
    target._prodZu=zeile=>{
      const offen=Array.from(target.querySelectorAll(`[data-row="${zeile}"]`)).find(b=>b.getAttribute("aria-expanded")==="true");
      if(offen)return oeffnen(offen,offen.dataset.culture!=null?-1:Number(offen.dataset.month));
    };
    if(target.addEventListener&&!target._prodZuListener){
      target._prodZuListener=true;
      target.addEventListener("click",ev=>{const k=ev.target.closest&&ev.target.closest(".prod-detail-zu");if(k)target._prodZu(k.dataset.row);});
    }
    target.querySelectorAll("[data-open-month]").forEach(button=>button.addEventListener("click",async()=>{
      const cells=Array.from(target.querySelectorAll(`[data-month="${button.dataset.openMonth}"]`));
      const alleOffen=cells.length>0&&cells.every(b=>b.getAttribute("aria-expanded")==="true");
      button.setAttribute("aria-expanded",String(!alleOffen));
      await Promise.all(cells.map(b=>oeffnen(b,Number(b.dataset.month),!alleOffen)));
    }));
  }
  root.ProduktionsUebersicht={zeitwahl,render,esc,num,datum};
})(typeof window!=="undefined"?window:globalThis);
