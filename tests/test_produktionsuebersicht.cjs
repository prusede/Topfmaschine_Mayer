const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
function seite(anzahl=1,referenz_anzeigen=false,person=null){
  const cells=Array.from({length:anzahl},()=>({dataset:{},innerHTML:''}));
  const trs=cells.map(cell=>({firstElementChild:cell,hidden:true}));
  const knopf=dataset=>({dataset,offsetLeft:0,attrs:{'aria-expanded':'false'},
    getAttribute(k){return this.attrs[k];},setAttribute(k,v){this.attrs[k]=v;},addEventListener(event,fn){this.click=fn;}});
  const buttons=Array.from({length:anzahl},(_,row)=>[0,1].map(month=>knopf({row:String(row),month:String(month)}))).flat();
  const kulturen=Array.from({length:anzahl},(_,i)=>knopf({row:String(i),culture:String(i)}));
  const kopf=knopf({openMonth:'0'});
  const target={innerHTML:'',contains:()=>true,querySelector(q){if(q==='.prod-scroll')return {clientWidth:1000,scrollLeft:0};if(q==='[data-month]')return buttons[0];return trs[Number(q.split('-').at(-1))];},
    querySelectorAll(q){
      if(q==='[data-month]')return buttons;
      if(q==='[data-culture]')return kulturen;
      if(q==='[data-open-month]')return [kopf];
      const row=q.match(/^\[data-row="(\d+)"\]$/);if(row)return [...buttons,...kulturen].filter(b=>b.dataset.row===row[1]);
      const month=q.match(/^\[data-month="(\d+)"\]$/);if(month)return buttons.filter(b=>b.dataset.month===month[1]);
      return [];
    }};
  const c=vm.createContext({document:{getElementById:()=>({})}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/produktion-uebersicht.js'),'utf8'),c);
  const months=Array.from({length:12},()=>({stueck:0,stunden:0,sph:null,anzahl:0}));
  months[0]={stueck:10,stunden:1,sph:10,anzahl:1};months[1]={stueck:20,stunden:2,sph:10,anzahl:1};
  months[0].referenz={prozent:54,stunden:4.5,bewertungen:1};months[1].referenz={prozent:59.99,bewertungen:2};
  const pending=[];
  c.ProduktionsUebersicht.render(target,{von:'2026-01-13',bis:'2026-02-20',referenz_anzeigen,rows:Array.from({length:anzahl},(_,i)=>({kultur:'Test'+i,jahr:2026,months,total_stueck:30,total_stunden:3,total_sph:10,total_referenz:{prozent:72,bewertungen:2}}))},
    (row,von,bis)=>new Promise((resolve,reject)=>pending.push({row,von,bis,resolve,reject})),rows=>rows[0].label,person);
  return {buttons,kulturen,kopf,pending,cells,trs,target};
}
test('Monatsdetails begrenzen Datum und ignorieren verspaetete Antworten',async()=>{
  const {buttons,pending,cells,trs}=seite(),cell=cells[0],tr=trs[0];
  const jan=buttons[0].click(),feb=buttons[1].click();
  assert.equal(pending[0].von,'2026-01-13');assert.equal(pending[0].bis,'2026-01-31');
  assert.equal(pending[1].von,'2026-02-01');assert.equal(pending[1].bis,'2026-02-20');
  pending[1].resolve([{datum:'2026-02-13',stueck:20,stunden:2,label:'NEU'}]);await feb;
  pending[0].resolve([{datum:'2026-01-13',stueck:10,stunden:1,label:'ALT'}]);await jan;
  assert.ok(cell.innerHTML.includes('NEU'));assert.ok(!cell.innerHTML.includes('ALT'));
  await buttons[1].click();assert.equal(tr.hidden,true);
});
test('Kulturname oeffnet alle Tage der Kultur im gewaehlten Zeitraum',async()=>{
  const {kulturen,pending,cells,trs}=seite();
  const offen=kulturen[0].click();
  assert.equal(pending[0].von,'2026-01-13');assert.equal(pending[0].bis,'2026-02-20');
  pending[0].resolve([{datum:'2026-01-13',stueck:10,stunden:1,label:'JAN'},
                      {datum:'2026-02-13',stueck:20,stunden:2,label:'FEB'}]);await offen;
  assert.ok(cells[0].innerHTML.includes('JAN')&&cells[0].innerHTML.includes('FEB'));
  await kulturen[0].click();assert.equal(trs[0].hidden,true);
});
test('Monatsueberschrift oeffnet und schliesst den Monat aller Kulturen',async()=>{
  const {kopf,pending,cells,trs}=seite(2);
  const offen=kopf.click();assert.equal(pending.length,2);
  pending.forEach((p,i)=>p.resolve([{datum:'2026-01-15',stueck:10,stunden:1,label:'KULTUR'+i}]));
  await offen;assert.ok(cells.every((c,i)=>c.innerHTML.includes('KULTUR'+i)));
  await kopf.click();assert.ok(trs.every(tr=>tr.hidden));
});
test('Ladefehler erscheint in den geoeffneten Details',async()=>{
  const {buttons,pending,cells}=seite();const offen=buttons[0].click();
  pending[0].reject(new Error('Offline'));await offen;
  assert.ok(cells[0].innerHTML.includes('Details konnten nicht geladen werden'));
});
test('Tageszeile zeigt Stk/MA-h ohne Stueck ohne Nettozeit, Kopf nennt Teilzeitraum',async()=>{
  const {buttons,pending,cells}=seite();
  const offen=buttons[0].click();
  pending[0].resolve([{datum:'2026-01-13',stueck:300,stunden:1.5,label:'A'},
                      {datum:'2026-01-13',stueck:5000,stunden:0,label:'B'}]);await offen;
  const html=cells[0].innerHTML;
  assert.ok(html.includes('<b>5.300</b> Stück')&&html.includes('1,50 MA-h')&&html.includes('200 Stk/MA-h'));
  assert.ok(html.includes('<span class="tag-datum">Di 13.01.2026</span>'));
  assert.ok(html.includes('Test0 · Jan 2026 (13.01.2026 – 31.01.2026)'));
});

test('Personalnummernsuche ergaenzt Prozentwert und Durchschnitt in Monat und Gesamt',()=>{
  const normal=seite().target.innerHTML;
  assert.ok(!normal.includes('class="prod-ref'));
  const suche=seite(1,true).target.innerHTML;
  assert.ok(suche.includes('Referenz: 54,0 %'));
  assert.ok(suche.includes('Ø Referenz: &lt;60 %'));
  assert.ok(suche.includes('Ø Referenz: 72,0 %'));
  assert.equal((suche.match(/class="prod-ref gering"/g)||[]).length,2);
});
test('Personensuche: Ueberschrift nennt Nummer und Gesamtreferenz, jeder Tag seine Referenz',async()=>{
  const tage={'2026-01-13':{prozent:45.3,stunden:2,bewertungen:1},'2026-01-14':{prozent:null,stunden:0,bewertungen:0},'2026-01-15':{prozent:80,stunden:1,bewertungen:2}};
  const person={pnr:'1136',tagReferenz:rs=>tage[rs[0].datum]};
  const {buttons,kulturen,pending,cells}=seite(1,true,person);
  const offen=buttons[0].click();
  pending[0].resolve(Object.keys(tage).map(d=>({datum:d,stueck:10,stunden:1,label:d})));await offen;
  const html=cells[0].innerHTML;
  // Gesamtwert = Monatsfeld der Matrix (54 %, 1 Bewertung), nicht neu gerechnet.
  assert.ok(html.includes('Test0 · Jan 2026 (13.01.2026 – 31.01.2026) · Nr. 1136 · <span class="prod-kopf-ref gering">Referenz 54,0 %</span>'));
  assert.ok(html.includes('4,50 bewertete MA-h · 1 Bewertung</small>'));
  assert.equal((html.match(/<summary class="mit-ref">/g)||[]).length,3);
  assert.ok(html.includes('<span class="tag-wert tag-ref gering">Referenz 45,3 %</span>'));
  assert.ok(html.includes('<span class="tag-wert tag-ref leer">Referenz –</span>'));
  assert.ok(html.includes('<span class="tag-wert tag-ref">Ø Referenz 80,0 %</span>'));
  // Kultur geoeffnet: Gesamtwert des Zeitraums
  await buttons[0].click();const k=kulturen[0].click();
  pending[1].resolve([{datum:'2026-01-13',stueck:10,stunden:1,label:'x'}]);await k;
  assert.ok(cells[0].innerHTML.includes('Nr. 1136 · <span class="prod-kopf-ref">Ø Referenz 72,0 %</span>'));
});
test('Ohne Personalnummer bleiben Ueberschrift und Tageszeilen unveraendert',async()=>{
  const {buttons,pending,cells}=seite(1,false,null);
  const offen=buttons[0].click();
  pending[0].resolve([{datum:'2026-01-13',stueck:10,stunden:1,label:'A'}]);await offen;
  assert.ok(!cells[0].innerHTML.includes('Nr. ')&&!cells[0].innerHTML.includes('mit-ref')&&!cells[0].innerHTML.includes('tag-ref'));
});
test('Ueberschrift im Detailbereich klappt Monat und Kultur wieder ein',async()=>{
  const {buttons,kulturen,pending,cells,trs,target}=seite();
  const offen=buttons[1].click();
  pending[0].resolve([{datum:'2026-02-13',stueck:20,stunden:2,label:'A'}]);await offen;
  assert.ok(cells[0].innerHTML.includes('<h2><button type="button" class="prod-detail-zu" data-row="0" title="Einklappen">Test0 · Feb 2026'));
  await target._prodZu('0');
  assert.equal(trs[0].hidden,true);assert.equal(buttons[1].getAttribute('aria-expanded'),'false');
  const k=kulturen[0].click();pending[1].resolve([{datum:'2026-01-13',stueck:10,stunden:1,label:'B'}]);await k;
  assert.equal(trs[0].hidden,false);
  await target._prodZu('0');assert.equal(trs[0].hidden,true);
  // Nichts geoeffnet: kein Fehler, nichts passiert
  assert.equal(target._prodZu('0'),undefined);
});

test('Klick auf eine andere Kultur schliesst die zuvor geoeffnete Zeile',async()=>{
  const {buttons,kulturen,kopf,pending,trs}=seite(2);
  // Zeile 0 ueber den Monatswert, dann Zeile 1 ueber den Monatswert: nur Zeile 1 bleibt offen.
  const a=buttons[0].click();pending[0].resolve([]);await a;
  assert.equal(trs[0].hidden,false);
  const b=buttons[2].click();pending[1].resolve([]);await b;
  assert.equal(trs[0].hidden,true);assert.equal(buttons[0].getAttribute('aria-expanded'),'false');
  assert.equal(trs[1].hidden,false);assert.equal(buttons[2].getAttribute('aria-expanded'),'true');
  // Kulturname schliesst ebenso die andere Zeile
  const c=kulturen[0].click();pending[2].resolve([]);await c;
  assert.equal(trs[1].hidden,true);assert.equal(trs[0].hidden,false);
  // Monatskopf oeffnet weiterhin alle Kulturen des Monats
  const d=kopf.click();pending.slice(3).forEach(p=>p.resolve([]));await d;
  assert.ok(trs.every(tr=>!tr.hidden));
});
