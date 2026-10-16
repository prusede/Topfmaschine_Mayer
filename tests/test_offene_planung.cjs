const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/auswertung.html'),'utf8');

function seite(){
  const elemente={fRange:{value:'produktion_offen'},fKultur:{value:'2'},fVon:{value:'2026-10-16'},fBis:{value:'2026-10-16'},auswertungPlanung:{textContent:''}};
  elemente.produktionsAuswertung={style:{}};
  elemente.auswertungPlanung.style={};
  elemente.btnExport={style:{}};
  const rows=[
    {id:'offen',archiviert:0,fehlt:500},
    {id:'noch-nicht-begonnen',archiviert:0,fehlt:1000},
    {id:'erfuellt',archiviert:0,fehlt:0},
    {id:'archiviert',archiviert:1,fehlt:700},
  ];
  let gezeigt,anfrage;
  const c=vm.createContext({$:id=>elemente[id],_planBerichtAnfrage:0,TMPlan:{
    api:async(url,data)=>{anfrage={url,data};return rows;},
    render:(target,werte)=>{gezeigt=werte;},
  }});
  vm.runInContext(source.match(/function zeigeAuswertungsansicht\([^]*?\n}/)[0],c);
  vm.runInContext(source.match(/function zeitraumGueltig\([^]*?\n}/)[0],c);
  vm.runInContext(source.match(/async function ladePlanbericht\([^]*?\n}/)[0],c);
  return {c,elemente,rows,get gezeigt(){return gezeigt;},get anfrage(){return anfrage;}};
}

test('Produktion offen zeigt nur aktive unerfuellte Vorgaben einschliesslich noch nicht begonnener',async()=>{
  const s=seite();await s.c.ladePlanbericht();
  assert.deepEqual(Array.from(s.gezeigt,p=>p.id),['offen','noch-nicht-begonnen']);
  assert.equal(s.anfrage.data.kultur_id,2);
  assert.equal(s.anfrage.data.von,'2026-10-16');
  assert.equal(s.elemente.produktionsAuswertung.style.display,'none');
  assert.equal(s.elemente.btnExport.style.display,'none');
});

test('Wechsel zur Datumsschnellwahl hebt den Filter fuer offene Vorgaben auf',async()=>{
  const s=seite();await s.c.ladePlanbericht();
  s.elemente.fRange.value='today';await s.c.ladePlanbericht();
  assert.equal(s.gezeigt.length,4);
  assert.equal(s.elemente.produktionsAuswertung.style.display,'block');
  assert.equal(s.elemente.btnExport.style.display,'');
});

test('Leere offene Auswahl wird eindeutig angezeigt',async()=>{
  const s=seite();s.rows.splice(0,2);await s.c.ladePlanbericht();
  assert.match(s.elemente.auswertungPlanung.textContent,/Keine offenen Produktionsvorgaben/);
});
