const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app.js', 'utf8');
const snippet = source.slice(source.indexOf('function priceCents('), source.indexOf('function orderDraft('));
const helpers = source.slice(source.indexOf('function productMatches('), source.indexOf('function filterOrderProducts('));
const context = {
  window: {orderBrandFilter: ''},
  s: {prices: [],industries:[{name:'Bruna Tavares',active:true},{name:'Ruby Kisses',active:true},{name:'Outra',active:true}],priceTables:[{id:'Bruna Tavares|PA',brand:'Bruna Tavares',state:'PA',active:true},{id:'Ruby Kisses|PA',brand:'Ruby Kisses',state:'PA',active:true},{id:'Outra|AP',brand:'Outra',state:'AP',active:true}]},
  norm: value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(),
  esc: value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[ch])),
  money: value => Number(value).toLocaleString('pt-BR', {style:'currency', currency:'BRL'}),
};
vm.createContext(context);
const total = source.match(/^function orderTotalCents\([^\n]+/m)[0];
vm.runInContext(snippet + helpers + total, context);
const products = [
  {brand:'Bruna Tavares', sku:'BT01', description:'Batom líquido', price:10.50},
  {brand:'Ruby Kisses', sku:'RK01', description:'Base líquida', price:20.25},
  {brand:'Outra', sku:'OT01', description:'Produto AP', price:30},
];
context.s.prices = products.map((p, i) => ({...p, state:i === 2 ? 'AP' : 'PA'}));
const draft = {priceTable:'PA', quantities:{'Bruna Tavares|BT01':2, 'Ruby Kisses|RK01':1}};
const catalog = context.pricesForTable('PA');
assert.equal(catalog.length, 2);
assert.equal(context.orderVisibleProducts(catalog,draft,'').length, 2);
assert.equal(context.orderVisibleProducts(catalog,draft,'ruby').length, 1);
context.window.orderBrandFilter = 'Bruna Tavares';
assert.equal(context.orderVisibleProducts(catalog,draft,'').length, 1);
assert.equal(context.orderTotalCents(catalog,draft), 4125);
assert.match(context.orderCartHTML(catalog,draft), /2 indústria\(s\)/);
const order = {brand:'Multimarcas', items:[
  {brand:'Bruna Tavares', sku:'BT01', quantity:2, unitPrice:'10.50'},
  {brand:'Ruby Kisses', sku:'RK01', quantity:1, unitPrice:'20.25'},
]};
assert.equal(JSON.stringify([...context.orderBrandTotals(order)]), JSON.stringify([['Bruna Tavares',2100], ['Ruby Kisses',2025]]));
assert.equal(context.orderBrands(order).join(','), 'Bruna Tavares,Ruby Kisses');
console.log('Carrinho multimarcas, filtros e totais: OK');
