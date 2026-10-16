const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function feld(){
  function element(){
    return {children:[],hidden:false,value:'',textContent:'',attributes:{},listeners:{},
      classList:{toggle(){}},select(){},scrollIntoView(){},
      setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},
      appendChild(b){this.children.push(b);},replaceChildren(){this.children=[];},
      addEventListener(name,fn){this.listeners[name]=fn;},
      dispatchEvent(e){this.listeners[e.type]?.(e);return true;}};
  }
  const c=vm.createContext({window:{},document:{createElement:element},Event});
  for(const file of ['kulturen.js','kulturauswahl.js']) vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static',file),'utf8'),c);
  c.TMKulturen=c.window.TMKulturen;
  const input=element(),list=element(),select=element();list.id='kulturListe';
  select.options=[{value:'',textContent:'Alle'},{value:'2',textContent:'Hortensie'},
    {value:'28',textContent:'Hortensie pan.+arb.'},{value:'24',textContent:'Astilbe'}];
  Object.defineProperty(select,'selectedIndex',{get:()=>select.options.findIndex(o=>o.value===select.value)});
  let changes=0;select.addEventListener('change',()=>changes++);
  c.window.TMKulturCombo.install(input,select,list);
  const focus=()=>input.dispatchEvent(new Event('focus'));
  const tippen=value=>{input.value=value;input.dispatchEvent(new Event('input'));};
  const key=key=>input.listeners.keydown({key,preventDefault(){}});
  return {input,list,select,focus,tippen,key,changes:()=>changes};
}

test('Direkt im Auswahlfeld mehrere Buchstaben suchen, erst Auswahl aendert die ID',()=>{
  const f=feld();f.focus();assert.equal(f.list.children.length,4);
  f.tippen('Ho');assert.deepEqual(f.list.children.map(b=>b.textContent),['Hortensie','Hortensie pan.+arb.']);
  f.tippen('Hor');assert.equal(f.list.children.length,2);
  assert.equal(f.select.value,'');assert.equal(f.changes(),0);
  f.list.children[1].onclick();
  assert.equal(f.select.value,'28');assert.equal(f.input.value,'Hortensie pan.+arb.');
  assert.equal(f.changes(),1);assert.equal(f.input.attributes['aria-expanded'],'false');
});

test('Pfeiltasten und Enter waehlen, Escape und Fokusverlust verwerfen die Suche',()=>{
  const f=feld();f.focus();f.tippen('Ho');f.key('ArrowDown');f.key('Enter');
  assert.equal(f.select.value,'2');
  f.focus();f.tippen('xyz');assert.equal(f.list.children[0].textContent,'Keine passende Kultur');
  f.key('Enter');assert.equal(f.select.value,'2');
  f.key('Escape');assert.equal(f.input.value,'Hortensie');assert.equal(f.select.value,'2');
  f.focus();f.tippen('Ast');f.input.dispatchEvent(new Event('blur'));
  assert.equal(f.input.value,'Hortensie');assert.equal(f.changes(),1);
  f.focus();f.tippen('Ho');f.key('ArrowUp');f.key('Enter');assert.equal(f.select.value,'28');
});

test('Alle bleibt waehlbar und entfernt den Kulturfilter',()=>{
  const f=feld();f.focus();f.tippen('Ast');f.key('Enter');assert.equal(f.select.value,'24');
  f.focus();f.tippen('');f.list.children[0].onclick();
  assert.equal(f.select.value,'');assert.equal(f.input.value,'Alle');
  assert.equal(f.changes(),2);
});
