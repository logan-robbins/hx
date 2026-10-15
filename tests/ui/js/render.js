"use strict";
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {buildDocument}=require("./domshim.js");
const [fixtures,staticDir]=process.argv.slice(2);
global.document=buildDocument(fs.readFileSync(path.join(staticDir,"index.html"),"utf8"));
const listeners={};
global.window={addEventListener:(name,fn)=>listeners[name]=fn};
global.location={hash:"#overview"};
global.fetch=async url=>{
  const name=url.startsWith("/api/show/") ? `show-${decodeURIComponent(url.slice(10))}.json` : "board.json";
  return {ok:true,json:async()=>JSON.parse(fs.readFileSync(path.join(fixtures,name),"utf8"))};
};
global.EventSource=class{constructor(){this.onmessage=null;}};
vm.runInThisContext(fs.readFileSync(path.join(staticDir,"app.js"),"utf8"));
const settle=()=>new Promise(resolve=>setTimeout(resolve,10));
(async()=>{
  await settle();
  const views={overview:document.getElementById("content").textContent};
  for(const route of ["runs","delivery","show/orders.work","partner"]){
    location.hash="#"+route;listeners.hashchange();await settle();
    views[route]=document.getElementById("content").textContent;
  }
  process.stdout.write(JSON.stringify(views));
})().catch(error=>{process.stderr.write(String(error));process.exitCode=1;});
