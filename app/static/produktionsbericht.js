/* Auftragsmengen werden einmal gezaehlt; Mitarbeiterstunden beschreiben das Team. */
const SP=ProduktionsUebersicht;
let stolzeZeitwahl;
function stolzeZeitraumStart(){
  if(stolzeZeitwahl)return;
  stolzeZeitwahl=SP.zeitwahl({target:$("zeitSchnellwahl"),von:$("fVon"),bis:$("fBis"),onChange(){
    $("fRange").value="";loadData();
  }});
  _fetchTimeout("/api/auswertung/jahre",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"})
    .then(r=>{if(!r.ok)throw new Error();return r.json();}).then(j=>stolzeZeitwahl.jahre(j)).catch(()=>{});
}
function stolzeFilter(){
  const b={von:$("fVon").value,bis:$("fBis").value,kultur_id:$("fKultur").value?Number($("fKultur").value):null};
  if($("fTopf").value)b.topfgroesse=$("fTopf").value;
  if($("fForm").value)b.topfform=$("fForm").value;
  return b;
}
function stolzeMonate(rows){
  const groups=new Map();
  rows.forEach(r=>{
    const jahr=Number(r.datum.slice(0,4)),key=JSON.stringify([jahr,r.kultur_id,r.kultur]);
    if(!groups.has(key))groups.set(key,{jahr,kultur:r.kultur||"(ohne Kultur)",kultur_id:r.kultur_id,months:Array.from({length:12},()=>({stueck:0,stunden:0,anzahl:0}))});
    const cell=groups.get(key).months[Number(r.datum.slice(5,7))-1];
    cell.stueck+=r.gesamtstueck||0;cell.stunden+=r.total_mh||0;cell.anzahl++;
  });
  return [...groups.values()].sort((a,b)=>a.kultur.localeCompare(b.kultur,"de")||a.jahr-b.jahr).map(r=>{
    r.months.forEach(m=>m.sph=m.stunden?m.stueck/m.stunden:null);
    r.total_stueck=r.months.reduce((s,m)=>s+m.stueck,0);r.total_stunden=r.months.reduce((s,m)=>s+m.stunden,0);
    r.total_sph=r.total_stunden?r.total_stueck/r.total_stunden:null;return r;
  });
}
// Zahlenspalten rechtsbuendig wie in Halle 1, damit sich die Auftraege eines Tages vergleichen lassen.
// Loeschen wie in der Auftragstabelle: darfLoeschen() aus auswertung.html (nur Admin, vergangene Tage).
function stolzeAuftragsdetails(rows){
  const loeschen=r=>typeof darfLoeschen==="function"&&darfLoeschen(r);
  const spalte=rows.some(loeschen);
  return '<table class="auftrag-details"><thead><tr><th>Auftrag</th><th>Topf / Form</th><th class="num">Stück</th><th class="num">MA-h</th><th class="num">Stück/MA-h (Team)</th><th>Status / Mitarbeiterdetails</th>'+(spalte?'<th></th>':'')+'</tr></thead><tbody>'+rows.map(r=>`<tr><td>${SP.esc(r.auftragsnr)}</td><td>${SP.esc(TMKulturen.topf(r.topfgroesse))} · ${SP.esc(r.topfform||"–")}</td><td class="num">${r.gesamtstueck==null?'offen':SP.num(r.gesamtstueck)}</td><td class="num">${SP.num(r.total_mh,2)}</td><td class="num">${SP.num(r.stueck_pro_mah,1)}</td><td><details><summary>${SP.esc(r.status)}${r.status!=='abgeschlossen'?' · Menge vorläufig':''}${r.has_missing_data?' · Angaben fehlen':''} · Mitarbeiter öffnen</summary>`+
      '<table><thead><tr><th>Person / Gruppe</th><th>Rolle</th><th>Start</th><th>Ende</th><th class="num">Pause h</th><th class="num">Netto-MA-h</th></tr></thead><tbody>'+(r.workers||[]).map(w=>`<tr><td>${SP.esc(w.sammel?`${w.anzahl} Personen`:w.pnr)}</td><td>${SP.esc(w.rolle)}</td><td>${SP.esc(w.start||"–")}</td><td>${SP.esc(w.ende||"läuft / fehlt")}</td><td class="num">${SP.num(w.pause,2)}</td><td class="num">${SP.num(w.netto_h_gesamt,2)}</td></tr>`).join("")+'</tbody></table></details></td>'+(spalte?`<td class="zelle-edit">${loeschen(r)?`<button type="button" class="btn-zeile-loeschen" data-loeschen="${SP.esc(r.id)}" title="Auftrag löschen (nur Admin)">🗑</button>`:''}</td>`:'')+'</tr>').join("")+'</tbody></table>';
}
function renderMonatsUebersicht(rows){
  SP.render($("monatsResult"),{von:$("fVon").value,bis:$("fBis").value,rows:stolzeMonate(rows)},async(row,von,bis)=>
    rows.filter(r=>r.datum>=von&&r.datum<=bis&&r.kultur_id===row.kultur_id&&(r.kultur||"(ohne Kultur)")===row.kultur).map(r=>({...r,stueck:r.gesamtstueck||0,stunden:r.total_mh||0})),stolzeAuftragsdetails);
  const offen=rows.filter(r=>r.status!=="abgeschlossen"),bestaetigt=rows.filter(r=>r.status==="abgeschlossen").reduce((s,r)=>s+(r.gesamtstueck||0),0);
  // Nur die Zahlen dieser Auswahl; die allgemeinen Erklaerungen stehen in der Erlaeuterung der Matrix.
  $("mengenHinweis").textContent=`${SP.num(bestaetigt)} Stück aus abgeschlossenen Aufträgen`+
    (offen.length===1?' · 1 laufender/offener Auftrag mit vorläufiger Menge ist in „Stück gesamt“ enthalten.':
     offen.length?` · ${offen.length} laufende/offene Aufträge mit vorläufigen Mengen sind in „Stück gesamt“ enthalten.`:'.');
}
async function stolzeMengenExport(){
  if(!zeitraumGueltig())return;
  const btn=$("btnMengenExport");btn.disabled=true;
  try{
    const r=await _fetchTimeout("/api/export.csv",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...stolzeFilter(),ansicht:"mengen"})},30000);
    if(!r.ok)throw new Error();
    const a=document.createElement("a");a.href=URL.createObjectURL(await r.blob());a.download=`Mayer_Mengen_${$("fVon").value}_${$("fBis").value}.csv`;
    document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
  }catch(e){alert("Mengenexport fehlgeschlagen. Bitte Auswahl erneut laden.");}finally{btn.disabled=false;}
}

function stolzeDatenLeeren(){
  _letzteSummaries=[];
  for(const id of ["kpiAuftraege","kpiStueck","kpiMah","kpiLeistung"])$(id).textContent="–";
  for(const id of ["kulturAgg","monatsResult"])$(id).innerHTML='<p class="prod-hinweis">Daten werden geladen …</p>';
  $("auftraegeBody").innerHTML='<tr><td colspan="12">Daten werden geladen …</td></tr>';
  $("mengenHinweis").textContent="";
}
