// Ausfuehren mit: node --test tests/test_kulturfilter.cjs
// Die echten Seitenfunktionen laufen mit einer kleinen DOM-Attrappe, damit
// die Kulturwechsel auch ohne Browser und laufenden Auth-Dienst pruefbar sind.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/auswertung.html'), 'utf8');

function seite(){
  const elemente = {fKultur:{value:''},fRange:{value:''},fTopf:{value:''},fForm:{value:''},produktionsAuswertung:{style:{}},btnExport:{style:{}},btnMengenExport:{style:{}},datenLadeStatus:{textContent:''}};
  const aufrufe = {};
  elemente.auswertungPlanung={style:{}};
  const c = vm.createContext({
    $:id=>elemente[id] ||= {},
    fmtNum:(n, dec=1)=>n == null ? '—' : n.toLocaleString('de-DE', {
      minimumFractionDigits:dec, maximumFractionDigits:dec,
    }),
    _letzteSummaries:[
      {id:'h', kultur_id:2, kultur:'Hortensie', status:'abgeschlossen', gesamtstueck:12300, total_mh:22.5},
      {id:'p', kultur_id:9, kultur:'Passiflora rot', status:'abgeschlossen', gesamtstueck:10092, total_mh:18.33},
      {id:'l', kultur_id:13, kultur:'Lonicera', status:'abgeschlossen', gesamtstueck:4866, total_mh:8.83},
      {id:'f', kultur_id:23, kultur:'Sonderkultur', status:'offen', gesamtstueck:null, total_mh:0},
    ],
    _auswertungAnfrage:0,_planBerichtAnfrage:0,ladePlanbericht:async()=>{},
  });
  for(const name of ['_filterKultur','renderKPIs','renderAuswahl','zeitraumGueltig','zeigeAuswertungsansicht']){
    vm.runInContext(source.match(new RegExp('function '+name+'\\([^]*?\\n}'))[0], c);
  }
  for(const name of ['renderAuftraege','renderMonatsUebersicht','renderKulturAgg']){
    c[name] = rows=>{ aufrufe[name] = rows; };
  }
  return {c, elemente, aufrufe};
}

test('Kulturwechsel aktualisiert Kennzahlen und alle auftragsbezogenen Ansichten', ()=>{
  const {c,elemente,aufrufe} = seite();
  elemente.fKultur.value = '2';
  c.renderAuswahl();
  assert.equal(elemente.kpiAuftraege.textContent, 1);
  assert.equal(elemente.kpiStueck.textContent, '12.300');
  assert.equal(elemente.kpiMah.textContent, '22,5');
  assert.equal(elemente.kpiLeistung.textContent, '547');
  for(const name of ['renderAuftraege','renderMonatsUebersicht']){
    assert.deepEqual(Array.from(aufrufe[name], r=>r.id), ['h']);
  }
  assert.equal(aufrufe.renderKulturAgg.length, 1);
  assert.equal(aufrufe.renderKulturAgg[0].kultur, 'Hortensie');
  elemente.fKultur.value = '';
  c.renderAuswahl();
  assert.equal(elemente.kpiAuftraege.textContent, 4);
  assert.equal(elemente.kpiStueck.textContent, '27.258');
  assert.equal(elemente.kpiMah.textContent, '49,7');
  assert.equal(elemente.kpiLeistung.textContent, '549');
});

test('Keine Treffer und Freitext werden ueber die Kultur-ID behandelt', ()=>{
  const {c,elemente,aufrufe} = seite();
  elemente.fKultur.value = '1'; c.renderAuswahl();
  assert.equal(elemente.kpiAuftraege.textContent, 0);
  assert.equal(elemente.kpiStueck.textContent, '0');
  assert.equal(elemente.kpiMah.textContent, '0,0');
  assert.equal(elemente.kpiLeistung.textContent, '—');
  assert.equal(aufrufe.renderKulturAgg.length, 0);
  elemente.fKultur.value = '23'; c.renderAuswahl();
  assert.equal(elemente.kpiAuftraege.textContent, 1);
  assert.equal(aufrufe.renderAuftraege[0].kultur, 'Sonderkultur');
  assert.equal(aufrufe.renderKulturAgg.length, 0); // Nur abgeschlossene Auftraege
});

test('Gesamtes Seitenskript ist syntaktisch gueltig', ()=>{
  for(const match of source.matchAll(/<script(?:\s[^>]*)?>([^]*?)<\/script>/g)){
    new vm.Script(match[1]);
  }
});

test('Laden verwendet die aktuelle Kulturauswahl fuer alle Ansichten', async ()=>{
  const {c,elemente,aufrufe} = seite();
  elemente.fVon = {value:'2026-10-13'};
  elemente.fBis = {value:'2026-10-13'};
  elemente.fKultur.value = '2';
  c._farbenSetzen = ()=>{};
  c._fetchTimeout = async url=>({ok:true, json:async ()=>
    url.endsWith('/summary') ? c._letzteSummaries : []});
  vm.runInContext(source.match(/async function loadData\([^]*?\n}/)[0], c);
  await c.loadData();
  assert.equal(elemente.kpiAuftraege.textContent, 1);
  assert.equal(elemente.kpiStueck.textContent, '12.300');
  assert.equal(aufrufe.renderMonatsUebersicht[0].id, 'h');
  let handler;
  elemente.fKultur.addEventListener = (name, callback)=>{ handler = callback; };
  vm.runInContext(source.match(/\$\("fKultur"\)\.addEventListener\("change", renderAuswahl\);/)[0], c);
  elemente.fKultur.value = '';
  handler();
  assert.equal(elemente.kpiAuftraege.textContent, 4);
});

test('CSV-Schaltflaeche uebertraegt die Kultur-ID und Alle als null', async ()=>{
  const {c,elemente} = seite();
  elemente.fVon = {value:'2026-10-13'};
  elemente.fBis = {value:'2026-10-13'};
  let request;
  c._fetchTimeout = async (url, options)=>{
    request = JSON.parse(options.body);
    return {ok:true, blob:async ()=>({})};
  };
  c.URL = {createObjectURL:()=> 'blob:test'};
  c.document = {createElement:()=>({click(){}, remove(){}}), body:{appendChild(){}}};
  c.alert = message=>{ throw new Error(message); };
  vm.runInContext(source.match(/\$\("btnExport"\)\.onclick = async \(\) => \{[^]*?\n};/)[0], c);
  elemente.fKultur.value = '2';
  await elemente.btnExport.onclick();
  assert.deepEqual(request, {von:'2026-10-13', bis:'2026-10-13', kultur_id:2});
  elemente.fKultur.value = '';
  await elemente.btnExport.onclick();
  assert.equal(request.kultur_id, null);
});
