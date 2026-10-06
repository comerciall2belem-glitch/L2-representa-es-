// Runs isolated commercial rules from the shipped app.js without a browser or customer data.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/../app.js','utf8');
function fragment(begin,end){const a=source.indexOf(begin),b=source.indexOf(end,a);assert(a>=0&&b>a,`Missing ${begin}`);return source.slice(a,b)}
const state={opportunities:[
 {clientId:'1',brand:'BT',stage:'Negociação',owner:'Ana Paula',amount:'1000.00',closeDate:'2026-09-30',followUp:'2026-09-29'},
 {clientId:'2',brand:'KNY',stage:'Proposta enviada',owner:'Euler',amount:'2000.00',closeDate:'2026-09-29'},
 {clientId:'3',brand:'RK',stage:'Perdido',owner:'Ana Paula',amount:'9000.00',closeDate:'2026-09-28'},
 {clientId:'4',brand:'RK',stage:'Negociação',owner:'Ana Paula',amount:'1000.00',closeDate:'2026-10-03'}]};
const ctx={s:state,window:{crmForecastMonth:'2026-09',crmForecastOwner:'*'},today:()=> '2026-09-28',teamNames:()=>['Ana Paula','Euler'],esc:x=>String(x??''),money:n=>'R$ '+Number(n).toFixed(2),client:()=>({name:'Cliente'}),console};
vm.createContext(ctx);
vm.runInContext(fragment('function csvRows(','function leadPanel(')+fragment('function opportunityAdvice(','function renderCRMBase('),ctx);
const csv=ctx.csvRows('nome;cnpj;uf;cidade\r\n"Loja; Norte";11222333000181;PA;Belém\r\n"Loja ""Sul""";22333444000155;AP;Macapá');
assert.equal(csv.length,3);assert.equal(csv[1][0],'Loja; Norte');assert.equal(csv[2][0],'Loja "Sul"');
assert.throws(()=>ctx.csvRows('nome;cnpj\n"incompleto;123'),/aspas/);
const all=ctx.forecastPanel();assert.match(all,/R\$ 3000\.00/);assert.match(all,/R\$ 1700\.00/);assert.doesNotMatch(all,/9000\.00/);
ctx.window.crmForecastOwner='Euler';const onlyEuler=ctx.forecastPanel();assert.match(onlyEuler,/R\$ 1000\.00/);assert.doesNotMatch(onlyEuler,/R\$ 1700\.00/);
console.log('CSV e previsão comercial filtrada: OK');
