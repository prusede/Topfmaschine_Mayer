const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/auswertung.html'),'utf8');
function seite(){
  const el={fRange:{value:''},fVon:{value:'2026-10-12'},fBis:{value:'2026-10-16'},btnExport:{style:{}},datenLadeStatus:{textContent:''},produktionsAuswertung:{style:{}}};
  const req=[],anzeigen=[];
  el.auswertungPlanung={style:{}};
  const c=vm.createContext({$:id=>el[id],_auswertungAnfrage:0,_planBerichtAnfrage:0,_letzteSummaries:[],
    _fetchTimeout:(url,opts)=>new Promise(resolve=>req.push({url,body:JSON.parse(opts.body),resolve})),
    _farbenSetzen(){},renderZaehlerKPIs(){},renderZaehlerTage(){},
    renderAuswahl(){anzeigen.push(c._letzteSummaries);},ladePlanbericht:async()=>{},
  });
  for(const name of ['zeitraumGueltig','zeigeAuswertungsansicht'])vm.runInContext(source.match(new RegExp('function '+name+'\\([^]*?\\n}'))[0],c);
  vm.runInContext(source.match(/async function loadData\([^]*?\n}/)[0],c);
  return {c,el,req,anzeigen};
}
test('Ungueltige oder unvollstaendige Datumsbereiche starten keinen Abruf',async()=>{
  const s=seite();s.el.fVon.value='';await s.c.loadData();
  s.el.fVon.value='2026-10-17';await s.c.loadData();
  assert.equal(s.req.length,0);assert.match(s.el.datenLadeStatus.textContent,/gültigen Zeitraum/);
  assert.equal(s.el.btnExport.disabled,true);
});
test('Spaet eintreffender alter Abruf ueberschreibt neuere Auswahl nicht',async()=>{
  const s=seite();const alt=s.c.loadData();assert.match(s.el.datenLadeStatus.textContent,/Wird geladen/);
  s.el.fVon.value='2026-10-16';const neu=s.c.loadData();
  for(const r of s.req.slice(1))r.resolve({ok:true,json:async()=>r.url.endsWith('/summary')?[{id:'neu'}]:[]});
  await neu;
  for(const r of s.req.slice(0,1))r.resolve({ok:true,json:async()=>r.url.endsWith('/summary')?[{id:'alt'}]:[]});
  await alt;assert.equal(s.anzeigen.length,1);assert.equal(s.anzeigen[0][0].id,'neu');
  assert.equal(s.el.datenLadeStatus.textContent,'');
});
test('Produktion offen laedt ausschliesslich den Planbericht',async()=>{
  const s=seite();let pl=0;s.c.ladePlanbericht=async()=>pl++;s.el.fRange.value='produktion_offen';
  await s.c.loadData();assert.equal(pl,1);assert.equal(s.req.length,0);
  assert.equal(s.el.produktionsAuswertung.style.display,'none');
});
