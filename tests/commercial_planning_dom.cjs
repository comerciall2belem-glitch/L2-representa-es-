const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require(process.env.L2_JSDOM||'/tmp/l2-planning-dom/node_modules/jsdom');
const source=fs.readFileSync('commercial_planning.js','utf8');
const report={metrics:{active_12m:2,productivity_12m:100,activity_percent:50,upa_12m:10,average_order:200,average_unit_price:10,starts_12m:null,restarts_12m:null,ceased_12m:null,sell_through:200,sell_through_12m:200},retention:[],regional:[],portfolio:[],baselines:[],coverage:{history_complete:false,sell_out:'Não informado'},generated_at:'2026-11-02T10:00:00-03:00',strategic_potential_share_percent:null};
const tick=()=>new Promise(resolve=>setTimeout(resolve,15));
(async()=>{
 const dom=new JSDOM('<!doctype html><div id="app"></div>',{url:'https://l2.test/',runScripts:'outside-only'});
 const w=dom.window;w.s={token:'session',server:''};let calls=[],saved=null;
 w.fetch=async(url,init)=>{calls.push({url,init});let data={};
 if(url.endsWith('/status'))data={enabled:true,manage:false,finance:false};
 else if(url.includes('/indicators?'))data=report;
 else if(url.includes('/ports?'))data={total:1,ports:[{id:'a',name:'<img src=x onerror=alert(1)>',tax_id:'11222333000181',profile:null,version:0}]};
 else if(url.endsWith('/ports/a/profile')){saved=JSON.parse(init.body);data={version:1};}
 else if(url.endsWith('/interactions/a'))data=[];
 else if(url.endsWith('/interactions'))data={id:'saved'};
 return {ok:true,json:async()=>data};};
 w.eval(source);
 assert.equal(w.L2Commercial.panel('config'),'');
 w.document.getElementById('app').innerHTML=w.L2Commercial.panel('crescimento');
 w.L2Commercial.bind();w.document.getElementById('cp-shell').open=true;await tick();await tick();
 assert.match(w.document.getElementById('cp-indicators').textContent,/Produtividade 12m/);
 assert.equal(w.document.getElementById('cp-mfa'),null,'Finance hidden for commercial role');
 assert.equal(w.document.getElementById('cp-import'),null,'Import restricted to management');
 const form=w.document.getElementById('cp-search');form.elements.q.value='Salão';form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
 assert.equal(w.document.querySelector('#cp-ports img'),null,'Client names cannot inject markup');
 w.document.querySelector('[data-port]').click();await tick();
 const profile=w.document.getElementById('cp-profile-form');profile.elements.needs.value='Prioridade do parceiro';profile.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
 assert.equal(saved.expected_version,0);assert.equal(saved.started_on,null);assert.equal(saved.needs,'Prioridade do parceiro');
 assert.ok(calls.every(x=>x.init.headers.Authorization==='Bearer session'));
 assert.match(calls.find(x=>x.url.includes('/ports?')).url,/q=Sal/);
 // Automatic value refresh does not replace the account form or typed notes.
 const before=w.document.getElementById('cp-profile-form');
 w.document.getElementById('cp-filter').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
 assert.equal(w.document.getElementById('cp-profile-form'),before);
 assert.equal(before.elements.needs.value,'Prioridade do parceiro');
 dom.window.close();console.log('commercial planning DOM: 12 assertions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
