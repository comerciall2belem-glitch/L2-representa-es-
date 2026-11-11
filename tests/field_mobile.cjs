const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
function setup(mobile,token='yes'){
 const listeners={},query={matches:mobile,addEventListener(){}};
 const c={window:{},matchMedia:()=>query,s:{token,user:'Erika',pending:[],syncAt:'',mustChangePassword:false},navigator:{onLine:true},renderSalesHome:()=>'<p>DESKTOP ORIGINAL</p>',workspaceNav:()=>{},render:()=>{},salesScope:()=>({orders:[],tasks:[{clientId:'a',date:'2026-11-10',text:'<script>',status:'Aberta'},{date:'2026-11-11',status:'Concluída'}],opportunities:[{clientId:'a',followUp:'2026-11-12',stage:'Negociação'},{followUp:'2026-11-11',stage:'Ganho'}]}),today:()=> '2026-11-11',esc:x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),client:()=>({name:'Cliente',phone:'(91) 99999-1111'}),sectionAllowed:x=>x!=='registro',document:{addEventListener:(k,f)=>listeners[k]=f}};vm.createContext(c);vm.runInContext(fs.readFileSync('field_mobile.js','utf8'),c);return c;
}
let c=setup(false);assert.equal(c.renderSalesHome(),'<p>DESKTOP ORIGINAL</p>');
c=setup(true,'');assert.equal(c.renderSalesHome(),'<p>DESKTOP ORIGINAL</p>');
c=setup(true);const h=c.renderSalesHome();assert.match(h,/1 retornos/);assert.match(h,/https:\/\/wa.me\/5591999991111/);assert.match(h,/&lt;script>/);assert.doesNotMatch(h,/<script>/);assert.doesNotMatch(h,/data-field-visit/);assert.equal(c.window.L2Field.rows().length,2);assert.match(h,/DESKTOP ORIGINAL/);console.log('PASS: desktop and login preserved; due records, escaping, phone and permission checks.');
