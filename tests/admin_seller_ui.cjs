const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app.js','utf8');
const ctx = {save:()=>{},s:{token:'test',user:'Erika',role:'Vendedor',sellers:['Erika','MB','Euler'],sectors:['commercial','office','finance','management','admin','routes']},esc:x=>String(x||''),sellerOptions:x=>'<option>'+x+'</option>'};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function hasSector('),source.indexOf('function show(')),ctx);
for (const sector of ['office','finance','management','admin','catalog']) assert.equal(ctx.hasSector(sector),false);
assert.equal(ctx.sectionAllowed('office'),false);
assert.equal(ctx.sectionAllowed('pedidos'),true);
assert.equal(ctx.sectionAllowed('operations'),true);
assert.match(ctx.orderSellerField({}),/disabled/);
ctx.s.user='Euler';
assert.equal(ctx.hasSector('finance'),true);
assert.equal(ctx.hasSector('management'),true);
ctx.s.user='Laís';ctx.s.role='Administrativo';
assert.equal(ctx.administrativeOrderEntry(),true);
assert.match(ctx.orderSellerField({}),/required/);
assert.doesNotMatch(ctx.orderSellerField({}),/disabled/);
assert.equal(ctx.orderSeller({user:'Laís'}),'');
assert.equal(ctx.orderSeller({user:'Laís',sellerResponsible:'Erika'}),'Erika');
assert.equal(ctx.orderSeller({user:'MB'}),'MB');
console.log('Permissões e atribuição comercial: OK');

ctx.s.industries=[{name:'Test'}];ctx.s.prices=[];ctx.s.commissionRates=[{id:'test',brand:'Test',rate:10}];ctx.s.commissionReceipts=[];
ctx.s.orders=[{brand:'Test',amount:100,user:'MB',date:'2026-10-01',status:'Faturado'},{brand:'Test',amount:200,user:'Laís',sellerResponsible:'Erika',date:'2026-10-02',status:'Faturado'},{brand:'Test',amount:9999,user:'Laís',date:'2026-10-03',status:'Faturado'}];
ctx.financeAllowed=()=>true;ctx.commissionKey=x=>x.toLowerCase();ctx.orderBrands=o=>[o.brand];ctx.orderBrandTotals=o=>new Map([[o.brand,o.amount*100]]);ctx.money=x=>'M'+x;
vm.runInContext(source.slice(source.indexOf('function commissionPanel('),source.indexOf('function done(')),ctx);
const panel=ctx.commissionPanel('2026-10');
assert.match(panel,/>M300</);assert.match(panel,/>M30</);assert.doesNotMatch(panel,/M9999|M10299/);

ctx.s.sellerCommissions=[{user:'MB',rate:'3.00'},{user:'Erika',rate:'2.50'},{user:'Euler',rate:null}];
ctx.s.sellers=['MB','Erika','Euler'];ctx.priceCents=x=>Math.round(Number(x)*100);
vm.runInContext(source.match(/^function sellerOrderRate\([^\n]+/m)[0]+'\n'+source.match(/^function sellerCommissionPanel\([^\n]+/m)[0],ctx);
assert.equal(ctx.sellerOrderRate({user:'MB'}),3);
assert.equal(ctx.sellerOrderRate({user:'Laís',sellerResponsible:'Erika'}),2.5);
assert.equal(ctx.sellerOrderRate({user:'Euler'}),null);
assert.equal(ctx.sellerOrderRate({user:'Erika',status:'Faturado',sellerCommissionRate:'1.50'}),1.5);
assert.match(ctx.sellerCommissionPanel('2026-10'),/>M5</);
assert.match(ctx.sellerCommissionPanel('2026-10'),/>M3</);
assert.match(ctx.sellerCommissionPanel('2026-10'),/Não cadastrado/);

ctx.financeAllowed=()=>ctx.hasSector('finance');ctx.s.user='Marlene';ctx.s.role='Administrativo';ctx.s.sectors=['finance','office','management','admin'];assert.equal(ctx.financeAllowed(),false);assert.equal(ctx.sectionAllowed('finance'),false);assert.equal(ctx.commissionPanel('2026-10'),'');

ctx.eligibleTables=()=>[{brand:'Bella Brazil',id:'r',title:'Revenda',channel:'REVENDA',commissionRate:8}];
vm.runInContext(source.match(/^function orderCommercialTablesHTML\([^\n]+/m)[0],ctx);
assert.doesNotMatch(ctx.orderCommercialTablesHTML({}),/comissão Varejo|Atacado 5%/);
