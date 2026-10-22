const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/produktionsbericht.js'),'utf8');
test('Monatsmengen zaehlen Auftraege einmal, trennen Jahre und Freitextkulturen',()=>{
  const c=vm.createContext({});
  vm.runInContext(source.match(/function stolzeMonate\([^]*?\n}/)[0],c);
  const rows=[{datum:'2026-10-13',kultur_id:23,kultur:'Sonderkultur A',gesamtstueck:1040,total_mh:2,workers:[{},{}]},
              {datum:'2026-10-14',kultur_id:23,kultur:'Sonderkultur A',gesamtstueck:500,total_mh:1,status:'offen'},
              {datum:'2026-10-13',kultur_id:23,kultur:'Sonderkultur B',gesamtstueck:10,total_mh:1},
              {datum:'2027-10-13',kultur_id:23,kultur:'Sonderkultur A',gesamtstueck:20,total_mh:1}];
  const result=c.stolzeMonate(rows);
  assert.equal(result.length,3);
  const a=result.find(r=>r.jahr===2026&&r.kultur==='Sonderkultur A');
  assert.equal(a.total_stueck,1540);assert.equal(a.total_stunden,3);
  assert.equal(a.months[9].anzahl,2);assert.equal(a.total_sph,1540/3);
  assert.equal(rows[0].gesamtstueck,1040);
});
test('Neue Seitenskripte sind syntaktisch gueltig',()=>{
  new vm.Script(source);
  new vm.Script(fs.readFileSync(path.join(__dirname,'../app/static/produktion-uebersicht.js'),'utf8'));
});
