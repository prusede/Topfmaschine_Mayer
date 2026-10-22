const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/planung.js'),'utf8');
function seite(fetch){
  const c=vm.createContext({window:{},TMKulturen:{topf:v=>({'9er':'9cm','11er':'11cm'})[v] || v},document:{readyState:'loading',addEventListener(){},cookie:'__Host-erf_csrf=Test%20Token'},fetch,AbortController,setTimeout,clearTimeout});
  vm.runInContext(source,c);return c.window.TMPlan;
}

test('Planungsaufrufe senden Sitzung und CSRF-Kopf und zeigen Serverfehler',async()=>{
  let request;
  const plan=seite(async(url,opts)=>{request={url,opts};return {ok:true,json:async()=>({ok:true})};});
  await plan.api('/api/planung/save',{sollmenge:20000});
  assert.equal(request.opts.credentials,'include');
  assert.equal(request.opts.headers['X-CSRF-Token'],'Test Token');
  assert.equal(JSON.parse(request.opts.body).sollmenge,20000);
  assert.ok(request.opts.signal);
  const denied=seite(async()=>({ok:false,json:async()=>({detail:'Keine Meisterrechte'})}));
  await assert.rejects(()=>denied.api('/api/planung/save',{}),/Keine Meisterrechte/);
});

test('Bericht trennt Gesamtfortschritt, Zeitraum und vorlaeufige Mengen',()=>{
  const target={innerHTML:''};
  const plan=seite();
  plan.render(target,[{id:'plan-test',kultur_name:'<script>Test</script>',topfgroesse:'12cm',arbeit:'Topfen',termin:'2026-10-20',terminart:'bis',bemerkung:'A & B',archiviert:0,ist:10600,sollmenge:10000,fehlt:0,mehrproduktion:600,erfuellt_prozent:106,ist_zeitraum:4600,vorlaeufig:900,vorlaeufig_zeitraum:900,laufende_auftraege:2,laufend_ohne_menge:1,auftraege_anzahl:3,auftraege:[]}]);
  for(const text of ['10.600','4.600','106 %','600','900 vorläufig','ohne Menge','Produktion im Zeitraum']) assert.ok(target.innerHTML.includes(text),text);
  assert.ok(target.innerHTML.includes('width:100%'));
  assert.ok(target.innerHTML.includes('&lt;script&gt;'));
  assert.ok(!target.innerHTML.includes('<script>'));
});

test('Noch wartende Konfiguration wird vollstaendig gegen Server-Reload geschuetzt',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../app/static/index.html'),'utf8');
  const c=vm.createContext({});
  vm.runInContext(html.match(/function schuetzeKonfig\([^]*?\n}/)[0],c);
  const order={planung_id:'alt',kultur:1,kultur_frei:'Alt',topfgroesse:'9er',arbeit:'Alt',gesamtstueck:600};
  c.schuetzeKonfig(order,{planung_id:null,kultur:null,kultur_frei:'',topfgroesse:'',arbeit:''});
  assert.deepEqual(order,{planung_id:null,kultur:null,kultur_frei:'',topfgroesse:'',arbeit:'',gesamtstueck:600});
});

test('Fertig verlangt Topfform und beide Substratnummern, Notiz bleibt optional',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../app/static/index.html'),'utf8');
  const c=vm.createContext({KULTUR_FREITEXT:23,_konfigEditTopf:'9cm'});
  vm.runInContext(html.match(/function fehlendeKonfig\([^]*?\n}/)[0],c);
  const a={kultur:2,topfgroesse:'',topfform:'rund',substrat_auftr_nr:'000123',substrat_art_nr:'000456',sonst:''};
  assert.deepEqual(Array.from(c.fehlendeKonfig(a,true)),[]);
  assert.deepEqual(Array.from(c.fehlendeKonfig(a)),['Topfgröße']);
  for(const [key,label] of [['topfform','Topfform'],['substrat_auftr_nr','Substrate Auftr.Nr.'],['substrat_art_nr','Art.Nr.']]){
    assert.deepEqual(Array.from(c.fehlendeKonfig({...a,[key]:'   '},true)),[label]);
  }
  assert.ok(Array.from(c.fehlendeKonfig({...a,kultur:23,kultur_frei:' '},true)).includes('Freitext-Kultur'));
});

test('Plan speichern verlangt sechs gueltige Angaben und erlaubt unveraenderte alte Formzuordnung',()=>{
  const el={pKultur:{value:'Hortensie'},pFrei:{value:''},pTopf:{value:'9cm'},pSoll:{value:'2000',validity:{valid:true}},pBeginn:{value:'2026-10-16',validity:{valid:true}},pEnde:{value:'2026-10-20',validity:{valid:true}}};
  const c=vm.createContext({$:id=>el[id],canEdit:true,cfg:{kultur_freitext:23,topfgroessen:['9cm']},topfform:'rund',kulturId:()=>2,bestandOhneForm:()=>false});
  vm.runInContext(source.match(/const gueltig=\(\)=>\{[^]*?\n    };/)[0].replace('const gueltig','globalThis.gueltig'),c);
  assert.equal(c.gueltig(),true);
  for(const [id,value] of [['pSoll',''],['pSoll','0'],['pSoll','2.5'],['pTopf',''],['pBeginn',''],['pEnde','2026-10-15']]){
    const alt=el[id].value;el[id].value=value;assert.equal(c.gueltig(),false,id);el[id].value=alt;
  }
  c.topfform='';assert.equal(c.gueltig(),false);
  c.bestandOhneForm=()=>true;assert.equal(c.gueltig(),true);
  c.kulturId=()=>23;el.pFrei.value=' ';assert.equal(c.gueltig(),false);
  el.pFrei.value='Sonderkultur';assert.equal(c.gueltig(),true);
  c.canEdit=false;assert.equal(c.gueltig(),false);
});

test('Eingebettet: Start erst beim Oeffnen, Zeitraum und Kultur aus der Auswertung',async()=>{
  const calls=[];
  // Platzhalter fuer alle Formularfelder; planVon fehlt bewusst (= eingebettet).
  const felder={fVon:{value:'2026-10-01'},fBis:{value:'2026-10-22'},fKultur:{value:'5'},planVon:null};
  const el=id=>id in felder?felder[id]:(felder[id]={value:'',checked:false,validity:{valid:true},classList:{toggle(){}},addEventListener(){},appendChild(){},replaceChildren(){},reset(){}});
  const doc={readyState:'complete',cookie:'',addEventListener(){},getElementById:el,querySelectorAll:()=>[],createElement:()=>({})};
  const fetch=async(url,opts)=>{calls.push({url,body:opts.body&&JSON.parse(opts.body)});
    if(url==='/api/config')return {ok:true,json:async()=>({kultur_optionen:[],topfgroessen:[],kultur_freitext:23})};
    if(url.endsWith('/api/session/me'))return {ok:true,json:async()=>({roles:{topfmaschine_mayer:'meister'}})};
    return {ok:true,json:async()=>[]};};
  const c=vm.createContext({window:{},TMKulturen:{optionen:()=>[],topf:v=>v},document:doc,fetch,AbortController,setTimeout,clearTimeout,crypto:{randomUUID:()=>'u'},Date});
  vm.runInContext(source,c);
  assert.equal(calls.length,0); // ohne planVon kein Selbststart
  await c.window.TMPlan.planungLaden();
  const berichte=calls.filter(x=>x.url==='/api/auswertung/planung');
  assert.equal(berichte.length,1);
  assert.deepEqual(berichte[0].body,{von:'2026-10-01',bis:'2026-10-22',kultur_id:5});
  await c.window.TMPlan.planungLaden();
  assert.equal(calls.filter(x=>x.url==='/api/config').length,1); // nur einmal gestartet
  assert.equal(calls.filter(x=>x.url==='/api/auswertung/planung').length,2);
});
