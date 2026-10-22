/* Einheitliche Bedienung wie Halle 1 (20.10.2026): Aufbau der Seite und die
   Ansichtsumschaltung, die frueher ein Auswahlfeld in der Filterleiste war. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../app/static/auswertung.html'),'utf8');
const bericht=fs.readFileSync(path.join(__dirname,'../app/static/produktionsbericht.js'),'utf8');

test('Seitenaufbau: Zeitwahl-Leiste, Ansicht als Knoepfe, gleich gebaute Karten',()=>{
  assert.ok(html.includes('<div class="quick-bar">')&&html.includes('<div id="zeitSchnellwahl"></div>'));
  assert.ok(html.includes('<input type="hidden" id="fRange" value="" />')&&!html.includes('<select id="fRange">'));
  assert.equal((html.match(/data-ansicht="/g)||[]).length,3);
  // Kultursuche und Navigation bleiben
  assert.ok(html.includes('id="fKulturAnzeige"')&&html.includes('/kulturauswahl.js')&&html.includes('id="navigationKnopf"'));
  // Tagesverlauf entfallen, Kulturvergleich eingeklappt und eindeutig beschriftet
  assert.ok(!html.includes('tagesBody')&&!html.includes('function renderTage'));
  assert.ok(/<div class="card collapsed" id="kulturCard" data-merken="tm_kultur_zu">/.test(html));
  assert.ok(html.includes('Produktion nach Kultur – nur abgeschlossene Aufträge'));
  // Mayer hat keinen Maschinenzaehler: keine Zaehler- und keine Stundenansicht.
  assert.ok(!html.includes('zaehlerCard')&&!html.includes('/api/auswertung/zaehler')&&!html.includes('function renderZaehler'));
  for(const id of ['monatsCard','auftraegeCard','kulturCard'])
    assert.ok(new RegExp(`id="${id}" data-merken="tm_`).test(html),id);
  assert.ok(html.includes('max-width:1600px'));
  assert.ok(html.includes('<details class="legende">')&&html.includes('Erläuterung'));
});

test('Ansichtsknoepfe zeigen den Wert aus fRange',()=>{
  const knoepfe=['','produktion_offen'].map(v=>({dataset:{ansicht:v},attrs:{},setAttribute(k,w){this.attrs[k]=w;}}));
  const el={fRange:{value:'produktion_offen'}};
  const c=vm.createContext({$:id=>el[id],document:{querySelectorAll:()=>knoepfe}});
  vm.runInContext(html.match(/function ansichtKnoepfeSetzen\([^]*?\n}/)[0],c);
  c.ansichtKnoepfeSetzen();
  assert.deepEqual(knoepfe.map(k=>k.attrs['aria-pressed']),['false','true']);
  el.fRange.value='';c.ansichtKnoepfeSetzen();
  assert.deepEqual(knoepfe.map(k=>k.attrs['aria-pressed']),['true','false']);
});

test('Auftragsdetails: Zahlen rechtsbuendig, Mitarbeiter weiter aufklappbar',()=>{
  const c=vm.createContext({SP:{esc:s=>String(s??''),num:(n,d=0)=>n==null?'–':Number(n).toFixed(d)},TMKulturen:{topf:t=>t}});
  vm.runInContext(bericht.match(/function stolzeAuftragsdetails\([^]*?\n}/)[0],c);
  const out=c.stolzeAuftragsdetails([{auftragsnr:'A1',topfgroesse:'12cm',topfform:'rund',gesamtstueck:1000,total_mh:2,stueck_pro_mah:500,status:'abgeschlossen',workers:[{pnr:'1001',rolle:'Topfen',start:'07:00',ende:'09:00',pause:0,netto_h_gesamt:2}]}]);
  assert.ok(out.includes('<th class="num">Stück</th>')&&out.includes('<td class="num">1000</td>')&&out.includes('<td class="num">2.00</td>'));
  assert.ok(out.includes('<details><summary>abgeschlossen')&&out.includes('<th class="num">Netto-MA-h</th>'));
});

test('Loeschknopf in der Auftragstabelle nur fuer Admin und nur an vergangenen Tagen',()=>{
  const c=vm.createContext({});
  vm.runInContext('let _rolle=null;'+html.match(/function isoToday\(\)\{[^\n]*\}/)[0]+'\nconst pad=n=>String(n).padStart(2,"0");'+html.match(/function darfLoeschen\([^\n]*\}/)[0],c);
  const gestern={datum:'2000-01-01'},heute={datum:vm.runInContext('isoToday()',c)};
  assert.equal(c.darfLoeschen(gestern),false);
  vm.runInContext('_rolle="meister"',c);assert.equal(c.darfLoeschen(gestern),false);
  vm.runInContext('_rolle="admin"',c);assert.equal(c.darfLoeschen(gestern),true);assert.equal(c.darfLoeschen(heute),false);
  assert.ok(html.includes('"/api/auswertung/auftrag/loeschen"')&&html.includes('class="btn-zeile-loeschen"'));
});

test('Loeschknopf auch in der Auftragszeile des geoeffneten Tages',()=>{
  const ctx=extra=>{const c=vm.createContext({SP:{esc:s=>String(s??''),num:n=>String(n)},TMKulturen:{topf:t=>t},...extra});
    vm.runInContext(bericht.match(/function stolzeAuftragsdetails\([^]*?\n}/)[0],c);return c;};
  const rows=[{id:'a1',datum:'2026-10-19',auftragsnr:'TM-1',gesamtstueck:2,total_mh:.13,status:'abgeschlossen',workers:[]}];
  assert.ok(!ctx({}).stolzeAuftragsdetails(rows).includes('btn-zeile-loeschen'));
  assert.ok(!ctx({darfLoeschen:()=>false}).stolzeAuftragsdetails(rows).includes('btn-zeile-loeschen'));
  const out=ctx({darfLoeschen:()=>true}).stolzeAuftragsdetails(rows);
  assert.ok(out.includes('data-loeschen="a1"')&&out.includes('<th></th></tr></thead>'));
  assert.ok(html.includes('ev.target.closest("[data-loeschen]")'));
});

test('Detailbereich wie Halle 1: kein eigener Block um Zeile und Detail',()=>{
  assert.ok(!html.includes(':has([aria-expanded=true])')&&!html.includes('100cqw'));
  assert.ok(html.includes('.prod-matrix .prod-detail-zu:hover{background:transparent;'));
  assert.ok(html.includes('.prod-matrix .prod-detail-inhalt td{background:transparent;}'));
  assert.ok(html.includes('.prod-matrix>tbody>tr:not(.prod-detail):hover>td{background:transparent;}'));
});

test('Navigation: alle Anwendungen, Produktionsplanung bei der Ansicht statt im Menue',()=>{
  const nav=html.match(/<div class="navigation-links" id="navigationLinks" hidden>([^]*?)<\/div>/)[1];
  const texte=[...nav.matchAll(/<a [^>]*>([^<]*)<\/a>/g)].map(m=>m[1]);
  const apps=['Halle 1','Stolze','Mayer','Blumenband','Rapido Tisch','Blumenstrauß-Verpackung','Rapido Advent','Fahrradverleih'];
  assert.deepEqual(texte,['Dashboard – Übersicht',...apps.flatMap(a=>[a+' Erfassung',a+' Auswertung'])]);
  assert.ok(!nav.includes('/planung')&&nav.includes('href="https://10.10.0.210:8447/auswertung" aria-current="page"'));
  const ansicht=html.match(/<div class="ansicht-wahl"[^]*?<\/div>/)[0];
  const reihe=[...ansicht.matchAll(/>(Produktion|Produktionsplanung|Offene Produktion)</g)].map(m=>m[1]);
  assert.deepEqual(reihe,['Produktion','Produktionsplanung','Offene Produktion']);
  assert.ok(ansicht.includes('data-ansicht="planung"')&&ansicht.includes('data-ansicht="produktion_offen"')&&!ansicht.includes('href='));
});

test('Produktionsplanung eingebettet: Umschaltung zeigt nur die Planung und sperrt Topf-Filter',()=>{
  const el={};const e=id=>el[id]||(el[id]={style:{},hidden:false,disabled:false});
  const c=vm.createContext({$:e});
  vm.runInContext(html.match(/function zeigeAuswertungsansicht\([^]*?\n}/)[0],c);
  e('fRange').value='planung';c.zeigeAuswertungsansicht();
  assert.equal(e('produktionsAuswertung').style.display,'none');assert.equal(e('auswertungPlanung').style.display,'none');
  assert.equal(e('planungsAnsicht').hidden,false);assert.equal(e('btnExport').style.display,'none');assert.equal(e('fTopf').disabled,true);
  e('fRange').value='';c.zeigeAuswertungsansicht();
  assert.equal(e('planungsAnsicht').hidden,true);assert.equal(e('produktionsAuswertung').style.display,'block');assert.equal(e('fTopf').disabled,false);
  // Formular ist da, "Abbrechen" ist ein Knopf statt Link zur Auswertung, eigener Zeitraum entfallen
  assert.ok(html.includes('id="planForm"')&&html.includes('id="planAbbrechen"')&&!html.includes('id="planVon"')&&!html.includes('Abbrechen und zur Auswertung'));
  assert.ok(html.includes('if(location.hash === "#planung") $("fRange").value = "planung";'));
});

test('Mayer: keine Stundenwerte im Tag, Petrol statt Gruen, eigene Rolle',()=>{
  assert.ok(!html.includes('/api/auswertung/stunden')&&!html.includes('tag-stunden')&&!html.includes('function renderStunden'));
  assert.ok(!bericht.includes('tag-stunden')&&bericht.includes('Mayer_Mengen_'));
  assert.ok(html.includes('const APP_KEY   = "topfmaschine_mayer";')&&html.includes('<title>Topfmaschine Mayer – Auswertung</title>'));
  const css=html.match(/<style>([^]*?)<\/style>/)[1];
  for(const gruen of ['#2f6b3a','#1f4a28','#e4efdf','#c5d9c0','#d7e7d0'])assert.ok(!css.includes(gruen),gruen);
  assert.ok(css.includes('--pu-akzent:#1f6f8a;')&&css.includes('--leaf:#1f6f8a;'));
});
