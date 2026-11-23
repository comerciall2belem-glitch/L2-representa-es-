const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/../app.js','utf8');
const begin=source.indexOf('function confirmedOrderSyncConflict(');
const end=source.indexOf('function pendingTableRepairsHTML(',begin);
assert(begin>=0&&end>begin,'Funções de reparo de conflitos ausentes');
const fragment=source.slice(begin,end);
const confirmed={type:'order',changeId:'conflict',data:{id:'order-90',orderNumber:90}};
const retryable={type:'client',changeId:'other',data:{id:'client-1'}};
const state={pending:[confirmed,retryable],syncFaults:{conflict:'Pedido confirmado: condições comerciais imutáveis',other:'Corrigir inscrição estadual'}};
let synced=0,downloads=0;
const ctx={s:state,esc:x=>String(x),auditActionLabel:()=> 'Pedido',save:()=>{},sync:()=>{synced++},
 confirm:()=>true,window:{currentOrder:{id:'order-90'}},
 Blob:class{constructor(content){this.content=content}},
 URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}},
 document:{body:{appendChild:()=>{}},createElement:()=>({click:()=>downloads++,remove:()=>{}})},
 setTimeout:()=>{},Date,Set,JSON,String,Object};
vm.createContext(ctx);vm.runInContext(fragment,ctx);
assert.equal(ctx.confirmedOrderSyncConflict(confirmed),true);
assert.equal(ctx.confirmedOrderSyncConflict(retryable),false);
assert.match(ctx.syncFaultsHTML(),/Baixar tentativa de alteração/);
assert.match(ctx.syncFaultsHTML(),/Manter pedido confirmado na nuvem/);
ctx.retrySyncFaults();
assert.equal(state.syncFaults.conflict,'Pedido confirmado: condições comerciais imutáveis');
assert.equal(state.syncFaults.other,undefined);
assert.equal(synced,1);
ctx.resolveOrderSyncConflict('conflict');
assert.equal(downloads,1);
assert.equal(state.pending.length,1);
assert.equal(state.pending[0].changeId,'other');
assert.equal(state.syncFaults.conflict,undefined);
assert.equal(ctx.window.currentOrder,null);
assert.equal(synced,2);
console.log('Conflito 409: cópia, proteção e resolução explícita OK');
