const assert=require('assert'),vm=require('vm'),fs=require('fs');
const clients=[{id:'c1',name:'Cliente Belém',city:'Belém',state:'PA',last_purchase:'2026-10-01'},{id:'c2',name:'Cliente inativo',city:'Macapá',state:'AP',last_purchase:'2026-01-01'},{id:'c3',name:'Sem histórico'}];
const state={clients,orders:[{id:'o1',clientId:'c1',orderNumber:1,date:'2026-11-05',status:'Faturado',amount:100,sellerResponsible:'Ana Paula',proposalSentAt:'2026-11-01',items:[{brand:'RK',sku:'SKU1',quantity:2,unitPrice:50,subtotal:100}]},{id:'cancel',clientId:'c2',date:'2026-11-02',status:'Cancelado',amount:900},{id:'future',clientId:'c1',date:'2026-11-30',status:'Faturado',amount:10000},{id:'old',clientId:'c2',date:'2026-10-01',status:'Faturado',amount:500}],opportunities:[{id:'op1',closeDate:'2026-11-08',stage:'Perdido',lossReason:'Preço',amount:100}],interactions:[{clientId:'c1',at:'2026-11-09T10:00:00Z',owner:'Ana Paula',text:'Ligação'}],visits:[{clientId:'c1',date:'2026-11-05',user:'Ana Paula',result:'Realizado'}],goals:[{month:'2026-11',user:'Equipe',amount:200}],tasks:Array.from({length:12},(_,i)=>({id:String(i),date:'2026-11-01',text:'Atividade '+i,status:'Aberta'}))};
let finance=true;
const box={innerHTML:''};const c={window:{reportMonth:'2026-11'},s:state,today:()=> '2026-11-09',client:id=>clients.find(c=>c.id===id)||{},financeAllowed:()=>finance,CRM_STAGES:['Prospectado','Proposta enviada','Negociação','Pedido confirmado','Pós-venda','Perdido'],L2Goals:{expandGoals:g=>g},orderSeller:o=>o.sellerResponsible||'',sellerOrderRate:()=>5,orderLabel:v=>String(v).padStart(3,'0'),orderBrandTotals:o=>new Map([['RK',o.amount*100]]),daysSince:v=>v?Math.floor((Date.parse('2026-11-09')-Date.parse(v))/864e5):null,esc:v=>String(v??'').replace(/</g,'&lt;'),$:()=>box,show:()=>{}};
vm.createContext(c);vm.runInContext(fs.readFileSync(require.resolve('../sales_reports.js'),'utf8'),c);
const reports=c.window.L2SalesReports,payloads=[];
for(const kind of ['revenue','commissions','history','abc','pipeline','conversion','loss','activity','clients','goals']){
 reports.select(kind);const r=reports.report();payloads.push(JSON.parse(JSON.stringify(r)));assert(r.rows.every(x=>x.length===r.headers.length),kind);assert(box.innerHTML.includes('Exportar PDF')&&box.innerHTML.includes('Exportar Excel'));
 if(kind==='revenue')assert.equal(r.rows[0][3],100);
 if(kind==='conversion')assert.equal(r.rows[0][2],100);
 if(kind==='activity')assert.equal(r.rows.length,2);
 if(kind==='loss')assert.equal(r.rows[0][0],'Preço');
 if(kind==='goals')assert.equal(r.rows[0][4],50);
 if(kind==='abc')assert.equal(r.rows[0][4],'A');
 if(kind==='clients'){assert.equal(r.rows.find(x=>x[0]==='Sem histórico')[2],'Sem histórico');assert.equal(r.rows[0][2],'Positivado');}
}
reports.select('commissions');finance=false;reports.render();assert(!box.innerHTML.includes('value="commissions"'));assert.equal(reports.report().title,'Faturamento por cliente e região');
assert.equal((reports.tasks().match(/>Editar<\/button>/g)||[]).length,5);assert.equal(state.tasks.length,12);
reports.select('revenue');state.orders=Array.from({length:21},(_,i)=>({clientId:'client'+i,date:'2026-11-01',status:'Faturado',amount:1}));reports.move(2);state.orders=[];reports.render();assert(box.innerHTML.includes('página 1'));
if(process.argv[2])fs.writeFileSync(process.argv[2],JSON.stringify(payloads));
console.log('10 relatórios: período, cancelamentos, cálculos, permissões, paginação e preservação de atividades aprovados.');
