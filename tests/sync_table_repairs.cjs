const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('app.js','utf8');
const code=source.slice(source.indexOf('function auditActionLabel('),source.indexOf('function usageDate('));
const context={s:{pending:[],priceTables:[],prices:[],orders:[]},esc:x=>String(x),uid:()=> 'fresh-'+Math.random(),pendingOrderSellerIssues:()=>[],pendingClientRegistrationIssues:()=>[],sync:()=>{},save:()=>{},show:()=>{},alert:()=>{},FormData:class{constructor(form){this.form=form}get(k){return this.form[k]}}};vm.createContext(context);vm.runInContext(code,context);
const s=context.s;s.priceTables=[{id:'normal',brand:'BT',state:'AP',active:true},{id:'simple',brand:'BT',state:'AP',active:true},{id:'old',brand:'BT',state:'AP',active:false}];
s.pending=[{changeId:'one',type:'price',data:{id:'old|sku',brand:'BT',state:'AP',sku:'sku',tableId:'old',price:'12.00'}}];s.prices=[{...s.pending[0].data}];
assert.equal(context.pendingTableIssues().length,1);
assert(context.pendingTableRepairsHTML().includes("repairPendingTable(event,'one',0)"));
context.repairPendingTable({preventDefault(){},target:{tableId:'normal'}},'one',0);
assert.equal(s.pending.length,1);assert.equal(s.pending[0].data.price,'12.00');assert.equal(s.pending[0].data.id,'normal|sku');assert.equal(context.pendingTableIssues().length,0);
assert(!context.auditActionLabel({kind:'price_table',action:'upsert'}).includes('upsert'));
assert(context.auditActionLabel({kind:'route',action:'delete'}).includes('Exclusão'));
console.log('Vínculos corrigidos sem perda de fila; seleção explícita; histórico legível: OK');

// A bad order must not block visits, nor allow an older version or its fulfillment through.
s.pending=[{changeId:'bad',type:'order',data:{id:'o',brand:'BT',priceTable:'AP',items:[{sku:'sku',tableId:'old'}]}},{changeId:'older',type:'order',data:{id:'o',brand:'BT',priceTable:'AP',items:[{sku:'sku',tableId:'normal'}]}},{changeId:'dependent',type:'fulfillment',data:{id:'f',orderId:'o'}},{changeId:'visit',type:'visit',data:{id:'v'}}];
assert.deepEqual(Array.from(context.syncReadyBatch(),x=>x.changeId),['visit']);assert.equal(s.pending.length,4);
s.pending[0].data.items[0].tableId='normal';assert.equal(context.syncReadyBatch().length,4);
s.pending=[{changeId:'all',type:'order',data:{id:'o2',brand:'Bella Brazil',priceTable:'PA',items:[{sku:'sku',brand:'Bella Brazil',tableId:'Bella Brazil|VAREJO'}]}}];s.priceTables.push({id:'Bella Brazil|VAREJO',brand:'Bella Brazil',state:'ALL',active:true});assert.equal(context.pendingTableIssues().length,0);

s.pending=[{changeId:'bad-record',type:'visit',data:{id:'bad'}},{changeId:'valid-record',type:'visit',data:{id:'good'}}];s.syncFaults={'bad-record':'Data inválida'};assert.deepEqual(Array.from(context.syncReadyBatch(),x=>x.changeId),['valid-record']);assert(context.syncFaultsHTML().includes('Data inválida'));assert.equal(s.pending.length,2);

s.priceTables=[{id:'Bruna Tavares|PA',brand:'Bruna Tavares',state:'PA',active:false}];s.prices=[{brand:'Bruna Tavares',state:'PA',sku:'BMM10B'}];s.pending=[{changeId:'restored',type:'order',data:{id:'o',brand:'Bruna Tavares',priceTable:'PA',items:[{sku:'BMM10B'}]}}];s.syncFaults={restored:'Selecione uma tabela comercial ativa da indústria'};context.releaseRestoredTableFaults();assert(s.syncFaults.restored);s.priceTables[0].active=true;context.releaseRestoredTableFaults();assert(!s.syncFaults.restored);assert.equal(context.syncReadyBatch().length,1);assert.equal(s.pending.length,1);
