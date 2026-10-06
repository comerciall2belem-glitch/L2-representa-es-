const assert=require('node:assert/strict');
const crm=require('../crm_operations.js');
const state={clients:[{id:'a',name:'A',brands:'BT'},{id:'b',name:'B'}],tasks:[{id:'t',clientId:'a',sourceVisitId:'v',text:'Retorno',date:'2026-10-01',status:'Aberta'}],visits:[{id:'v',clientId:'a',date:'2026-10-02',result:'Contato remoto',next:'Retorno',returnDate:'2026-10-01'}],interactions:[],routes:[],officeActions:[{id:'f','Área':'Financeiro',Status:'Aberta',Prazo:'2026-10-01'},{id:'o','Área':'Comercial',Status:'Aberta',Prazo:'2026-10-03'}],opportunities:[],orders:[{clientId:'a',date:'2026-07-01',status:'Faturado',amount:100},{clientId:'a',date:'2026-08-01',status:'Faturado',amount:200},{clientId:'a',date:'2026-09-01',status:'Faturado',amount:300},{clientId:'a',date:'2026-11-01',status:'Faturado',amount:99999}],goals:[]};
let m=crm.overview(state,'2026-10-06');assert.equal(m.pending.length,2);assert.equal(m.withoutService.length,1);assert.equal(m.reorders.length,1);assert.equal(m.potential,200);assert.equal(m.unknownPotential,1);
state.tasks[0].status='Concluída';assert.equal(crm.overview(state,'2026-10-06').pending.length,1);
state.opportunities.push({id:'op',clientId:'a',stage:'Negociação',amount:900,followUp:'2026-10-07'});assert.equal(crm.overview(state,'2026-10-06').potential,900);
state.goals=[{month:'2026-10',user:'Euler',brand:'BT',amount:2200}];state.routes=[{id:'r',date:'2026-10-06',user:'Euler',clientId:'a'}];state.orders.push({clientId:'a',date:'2026-10-06',user:'Euler',brand:'BT',amount:50,status:'Confirmado'});
let g=crm.targets(state,'2026-10-06','Euler',false,o=>new Map([[o.brand,o.amount*100]]),o=>o.user)[0];assert.equal(g.daily,100);assert.equal(g.weekly,500);assert.equal(g.dailySales,50);assert.equal(g.perAttendance,100);
assert.equal(crm.targets(state,'2026-10-06','Marlene',false,()=>new Map(),o=>o.user).length,0);
assert.equal(crm.targets(state,'2026-10-04','Euler',false,()=>new Map(),o=>o.user)[0].daily,0);
assert.equal(crm.overview({...state,clients:state.clients.filter(c=>c.id==='b')},'2026-10-06').reorders.length,0);
console.log('Pendências, deduplicação, escopo, recompra e metas: OK');

{const state={clients:[{id:'c'}],routes:[{id:'bad',clientId:'c',date:'[object HTMLInputElement]'},{id:'impossible',clientId:'c',date:'2026-02-30'},{id:'valid',clientId:'c',date:'2026-10-05'}]};const result=require('../crm_operations.js').overview(state,'2026-10-06');assert.equal(result.overdue,1);assert.equal(result.pending.find(x=>x.id==='bad').due,'');assert.equal(result.pending.find(x=>x.id==='impossible').due,'');}

{const rows=[{due:'2026-10-05',user:'Euler'},{due:'2026-10-06',owner:'Ana Paula'},{due:'2026-10-07',user:'Marlene'},{due:'',user:'Euler'}];assert.equal(crm.filterPending(rows,'2026-10-06','overdue').length,1);assert.equal(crm.filterPending(rows,'2026-10-06','today').length,1);assert.equal(crm.filterPending(rows,'2026-10-06','future','Marlene').length,1);assert.equal(crm.filterPending(rows,'2026-10-06','undated').length,1);assert.equal(crm.filterPending(rows,'2026-10-06','all','Euler').length,2);}

assert.equal(crm.filterPending([{due:'2026-10-05'},{due:'2026-10-06'},{due:'2026-10-07'},{due:''}],'2026-10-06','action').length,2);
