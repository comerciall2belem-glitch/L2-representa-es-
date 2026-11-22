const fs=require('node:fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.L2_JSDOM||'/tmp/l2-bi-dom/node_modules/jsdom');
const wait=()=>new Promise(r=>setTimeout(r,25));
const result={kpis:{sales:'1200',target:null,attainment:null,orders:1,buyers:1,ticket_order:'1200',ticket_client:'1200',portfolio:2,positivation:50},financial:null,brands:[{label:'<img src=x onerror=alert(1)>',amount:'1200'}],trend:[],sellers:[],abc_clients:[],clients:[],geography:[],documents:[],funnel_snapshot:{},warnings:['Base parcial'],generated_at:'2026-11-06T12:00:00-03:00',formula_version:'test',period:{start:'2026-11-01'}};
(async()=>{
 const dom=new JSDOM('<div id="app"></div>',{url:'https://test.local',runScripts:'outside-only'}),w=dom.window;
 w.s={token:'valid',server:''};const calls=[];
 w.fetch=async(url,init)=>{calls.push({url,init});return{ok:true,json:async()=>result}};
 w.eval(fs.readFileSync('bi_dashboard.js','utf8'));w.document.getElementById('app').innerHTML=w.L2BI.panel();w.L2BI.bind();await wait();
 assert.match(w.document.getElementById('bi-data').textContent,/Vendas por NF/);
 assert.equal(w.document.querySelector('#bi-data img'),null);
 assert.doesNotMatch(w.document.getElementById('bi-data').textContent,/Comissão prevista/);
 const form=w.document.getElementById('bi-filters');form.elements.city.value='Belém';form.dispatchEvent(new w.Event('submit',{cancelable:true}));await wait();
 assert.match(calls.at(-1).url,/city=Bel/);assert.equal(calls.at(-1).init.headers.Authorization,'Bearer valid');
 assert.equal(form,w.document.getElementById('bi-filters'));assert.equal(form.elements.city.value,'Belém');
 const original=form.elements.start.value;w.document.querySelector('[data-bi-period="-1"]').click();await wait();assert.notEqual(form.elements.start.value,original);assert.equal(form.elements.city.value,'Belém');assert.match(calls.at(-1).url,/city=Bel/);w.document.querySelector('[data-bi-period="0"]').click();await wait();assert.equal(form.elements.start.value,original);
 w.document.querySelector('[data-bi-view="clients"]').click();assert.match(w.document.getElementById('bi-data').textContent,/Curva ABC/);
 w.fetch=async()=>({ok:false,json:async()=>({detail:'Sem acesso'})});w.document.getElementById('bi-refresh').click();await wait();
 assert.match(w.document.getElementById('bi-status').textContent,/Sem acesso/);assert.equal(w.document.getElementById('bi-data').textContent,'');
 // Response in flight after a change of account must never render.
 let finish;w.fetch=()=>new Promise(r=>finish=r);w.document.getElementById('bi-refresh').click();w.s.token='another';finish({ok:true,json:async()=>result});await wait();
 assert.equal(w.document.getElementById('bi-data').textContent,'');dom.window.close();console.log('BI DOM passed: filters, XSS, financial hiding, stale-session guard and errors');
})().catch(e=>{console.error(e);process.exitCode=1});
