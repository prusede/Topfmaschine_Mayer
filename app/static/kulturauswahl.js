/* Ein einziges bedienbares Feld: Namensanfang tippen und Treffer auswaehlen. */
window.TMKulturCombo = {
  install(input, select, list){
    let offen=false, aktiv=-1, treffer=[];
    const name=()=>select.options[select.selectedIndex]?.textContent || "Alle";
    function markieren(){
      [...list.children].forEach((b,i)=>{
        b.classList.toggle("aktiv",i===aktiv);
        b.setAttribute("aria-selected",String(i===aktiv));
      });
      if(aktiv>=0){
        input.setAttribute("aria-activedescendant",list.children[aktiv].id);
        list.children[aktiv].scrollIntoView({block:"nearest"});
      }else input.removeAttribute("aria-activedescendant");
    }
    function schliessen(){
      offen=false;list.hidden=true;aktiv=-1;
      input.value=name();input.setAttribute("aria-expanded","false");
      input.removeAttribute("aria-activedescendant");
    }
    function waehlen(i){
      if(!treffer[i]) return;
      select.value=treffer[i].value;
      schliessen();
      select.dispatchEvent(new Event("change",{bubbles:true}));
    }
    function zeigen(suche=""){
      offen=true;aktiv=-1;list.replaceChildren();
      input.setAttribute("aria-expanded","true");list.hidden=false;
      const q=suche.trim().toLocaleLowerCase("de");
      treffer=[...select.options].filter(o=>!q || (o.value && TMKulturen.passt(o.textContent,q)) || (!o.value && q==="alle"));
      if(!treffer.length){
        const text=document.createElement("div");text.className="kultur-kein-treffer";
        text.textContent="Keine passende Kultur";list.appendChild(text);return;
      }
      treffer.forEach((o,i)=>{
        const b=document.createElement("button");b.type="button";
        b.id=`${list.id}-${i}`;b.setAttribute("role","option");
        b.setAttribute("aria-selected","false");b.tabIndex=-1;b.textContent=o.textContent;
        b.addEventListener("pointerdown",e=>e.preventDefault());
        b.onclick=()=>waehlen(i);list.appendChild(b);
      });
      input.removeAttribute("aria-activedescendant");
    }
    input.addEventListener("focus",()=>{zeigen();input.select();});
    input.addEventListener("click",()=>{if(!offen){zeigen();input.select();}});
    input.addEventListener("input",()=>zeigen(input.value));
    input.addEventListener("blur",schliessen);
    input.addEventListener("keydown",e=>{
      if(e.key==="Escape"){e.preventDefault();schliessen();return;}
      if(e.key==="ArrowDown" || e.key==="ArrowUp"){
        e.preventDefault();if(!offen) zeigen();
        if(!treffer.length) return;
        aktiv=aktiv<0 ? (e.key==="ArrowDown"?0:treffer.length-1) :
          (aktiv+(e.key==="ArrowDown"?1:-1)+treffer.length)%treffer.length;
        markieren();
      }else if(e.key==="Enter" && offen){e.preventDefault();waehlen(aktiv<0?0:aktiv);}
    });
    schliessen();
    return {aktualisieren:schliessen};
  },
};
