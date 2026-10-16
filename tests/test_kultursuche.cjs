// node --test tests/test_kulturfilter.cjs tests/test_kultursuche.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const c = vm.createContext({window:{}});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/kulturen.js'),'utf8'), c);
const suche = c.window.TMKulturen;

test('Namensanfang wird schrittweise und ohne Beachtung der Grossschreibung gesucht', ()=>{
  const namen = ['Hedera grün (helix)','Heidelb.','Helmbohne','Hibis syr','Himbeere',
    'Hortensie','Hortensie pan.+arb.','Hedera bunt (var.)','Aronia'];
  assert.equal(namen.filter(n=>suche.passt(n,'H')).length,8);
  assert.deepEqual(namen.filter(n=>suche.passt(n,' ho ')),['Hortensie','Hortensie pan.+arb.']);
  assert.equal(namen.filter(n=>suche.passt(n,'xyz')).length,0);
  assert.equal(namen.filter(n=>suche.passt(n,'')).length,9);
  assert.ok(suche.passt('Hedera grün (helix)','Hedera GRÜ'));
});

test('Alphabetische Anzeige behaelt explizite IDs und veraendert die Quelle nicht', ()=>{
  const cfg = {kultur_optionen:[{id:24,name:'Astilbe'},{id:1,name:'Clematis'},
    {id:15,name:'Polygonum'},{id:3,name:'Aronia'}]};
  const before = JSON.stringify(cfg);
  assert.deepEqual(Array.from(suche.optionen(cfg),x=>x.id),[3,24,1,15]);
  assert.equal(JSON.stringify(cfg),before);
  assert.deepEqual(Array.from(suche.optionen({kulturen:['Clematis','Hortensie']}),x=>x.id),[1,2]);
});

test('Chipsuche blendet nur Treffer ein und behaelt die Auswahl', ()=>{
  const children = ['Hortensie','Astilbe','Freitext'].map(textContent=>({textContent,hidden:false}));
  children[0].selected = true;
  const status = {};
  suche.filterChips({children},'Ho',status);
  assert.deepEqual(children.map(b=>b.hidden),[false,true,true]);
  assert.equal(children[0].selected,true);
  assert.equal(status.textContent,'1 Kultur');
  suche.filterChips({children},'xyz',status);
  assert.equal(status.textContent,'Keine passende Kultur');
  const container = {children};
  suche.filterChips(container,'   ',status);
  assert.ok(children.every(b=>b.hidden));
  assert.equal(container.hidden,true);
  assert.equal(children[0].selected,true);
  assert.equal(status.textContent,'Bitte Namensanfang eingeben oder alle Kulturen anzeigen.');
  suche.filterChips(container,'A',status);
  assert.equal(container.hidden,false);
  assert.deepEqual(children.map(b=>b.hidden),[true,false,true]);
  container.dataset = {alleAnzeigen:'true'};
  suche.filterChips(container,'',status);
  assert.equal(container.hidden,false);
  assert.ok(children.every(b=>!b.hidden));
  assert.equal(status.textContent,'3 Kulturen');
});

test('Beide Seitenskripte sind syntaktisch gueltig', ()=>{
  for(const file of ['index.html','auswertung.html']){
    const source = fs.readFileSync(path.join(__dirname,'../app/static',file),'utf8');
    for(const m of source.matchAll(/<script>([^]*?)<\/script>/g)) new vm.Script(m[1]);
  }
  for(const file of ['planung.js','sync.js']) new vm.Script(fs.readFileSync(path.join(__dirname,'../app/static',file),'utf8'));
});
