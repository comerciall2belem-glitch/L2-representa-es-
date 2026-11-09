const fs=require('fs'),assert=require('node:assert/strict'),{JSDOM}=require(process.env.L2_JSDOM||'jsdom');
(async()=>{for(const user of ['Ana Paula','Euler','Laís','Marlene','Erika']){
 const source=fs.readFileSync('index.html','utf8'),dom=new JSDOM(source.replace(/<script[\s\S]*?<\/script>/g,''),{url:'https://l2.test',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window;
 w.structuredClone=structuredClone;w.matchMedia=()=>({matches:false});w.alert=()=>{};w.confirm=()=>false;w.setTimeout=()=>0;w.setInterval=()=>0;w.Element.prototype.scrollIntoView=()=>{};w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};w.URL.createObjectURL=()=> 'blob:test';w.URL.revokeObjectURL=()=>{};w.fetch=async()=>({ok:true,json:async()=>[],blob:async()=>new w.Blob(['test'])});
 w.localStorage.setItem('l2one_state_v2',JSON.stringify({user,role:user==='Erika'?'Vendedor':'Administrativo',token:'x'.repeat(40),server:'https://l2.test',sectors:['commercial','routes','office','management','finance','admin','catalog','clients_edit'],sellers:['Euler','Erika'],team:['Ana Paula','Euler','Erika','Laís','Marlene'],industries:[{id:'BT',name:'BT',active:true}],priceTables:[{id:'BT|PA',brand:'BT',state:'PA',title:'BT PA',active:true}],prices:[{id:'BT|PA|A1',tableId:'BT|PA',brand:'BT',state:'PA',sku:'A1',description:'Batom matte',price:10}],clients:[{id:'c1',name:'Loja de Teste',owner:user,state:'PA',city:'Belém',phone:'91999999999'}],orders:[{id:'o1',clientId:'c1',sellerResponsible:user,user,status:'Confirmado',date:'2026-10-12',amount:10,orderNumber:1,items:[{brand:'BT',sku:'A1',quantity:1,unitPrice:10,subtotal:10}]}],pending:[]}));
 const scripts=[...source.matchAll(/<script src="([^"?]+)[^"]*"><\/script>/g)].map(m=>m[1]);w.jspdf=require('../jspdf.umd.min.js');w.eval(scripts.filter(f=>f!=='jspdf.umd.min.js').map(f=>fs.readFileSync(f,'utf8')).join('\n')+'\nwindow.auditState=s;L2Live.pause();window.auditTabs=WORKSPACE_TABS;window.auditResolve=name=>eval("typeof "+name);');

 w.show('hoje');assert.equal(w.document.querySelector('#strategyConsentForm'),null);
 w.show('clientes');assert.equal(w.document.querySelector('#strategyDiscoverForm'),null);
 const button=w.document.querySelector('[data-strategy-tools=clientes]');assert(button);
 w.eval(button.getAttribute('onclick'));assert(w.document.querySelector('#strategyDiscoverForm'));
 assert.equal(w.document.querySelector('[data-strategy-tools=clientes]').getAttribute('aria-expanded'),'true');
 w.eval(w.document.querySelector('[data-strategy-tools=clientes]').getAttribute('onclick'));
 assert.equal(w.document.querySelector('#strategyDiscoverForm'),null);
 w.L2Strategy.openDashboard('tdc');assert(w.document.querySelector('#strategyDiscoverForm'));
 w.L2Strategy.openDashboard('cnpj');assert(w.document.querySelector('#strategyQualification').open);
 w.L2Strategy.openDashboard('agenda');assert(w.document.querySelector('#strategyAgendaDay'));
 w.L2Strategy.openDashboard('mcr');assert(w.document.querySelector('#strategyOverview'));
 w.auditState.user='Sem acesso';w.auditState.sectors=[];assert.equal(w.L2Strategy.screen('clientes'),'');
 w.strategyToolsTab=null;w.L2Strategy.openTools('clientes');assert.equal(w.strategyToolsTab,null);
 dom.window.close();
}console.log('Secondary strategic tools: five profiles, real script order, toggles, shortcuts and sector guards passed');})().catch(e=>{console.error(e);process.exit(1)});
