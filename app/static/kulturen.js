/* Gemeinsame Namenssuche: Die Suche veraendert niemals eine gespeicherte ID. */
window.TMKulturen = {
  topf(value){return ({'9er':'9cm','11er':'11cm'})[value] || value || '';},
  optionen(cfg){
    return (cfg.kultur_optionen || (cfg.kulturen || []).map((name,i)=>({id:i+1,name})))
      .slice().sort((a,b)=>a.name.localeCompare(b.name, "de"));
  },
  passt(name, suche){
    return String(name).toLocaleLowerCase("de").startsWith(suche.trim().toLocaleLowerCase("de"));
  },
  filterChips(container, suche, status){
    const hatSuche = Boolean(suche.trim());
    const alle = container.dataset?.alleAnzeigen === "true";
    let treffer = 0;
    [...container.children].forEach(b=>{
      b.hidden = !(hatSuche || alle) || !this.passt(b.textContent, suche);
      if(!b.hidden) treffer++;
    });
    container.hidden = !(hatSuche || alle);
    status.textContent = !(hatSuche || alle) ? "Bitte Namensanfang eingeben oder alle Kulturen anzeigen." :
      treffer ? `${treffer} ${treffer === 1 ? "Kultur" : "Kulturen"}` : "Keine passende Kultur";
  },
};
