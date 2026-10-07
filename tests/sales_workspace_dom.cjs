const fs=require('fs'),assert=require('node:assert/strict');
const {JSDOM}=require(process.env.L2_JSDOM||'jsdom');
(async()=>{for(const user of ['Ana Paula','Euler','Laís','Marlene','Erika']){
 const html=fs.readFileSync('index.html','utf8').replace(/<script[\s\S]*?<\/script>/g,'');const dom=new JSDOM(html,{url:'https://l2.test',runScripts:'outside-only',pretendToBeVisual:true});const w=dom.window;
 w.structuredClone=structuredClone;w.matchMedia=()=>({matches:false});w.alert=()=>{};w.confirm=()=>true;w.setInterval=()=>0;w.fetch=async()=>({ok:true,json:async()=>[]});w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'))};
 const sectors=['commercial','routes','office','management','finance','admin','catalog'];w.localStorage.setItem('l2one_state_v2',JSON.stringify({user,role:user==='Erika'?'Vendedor':'Administrativo',token:'x'.repeat(40),server:'https://l2.test',sectors,sellers:['Ana Paula','Erika'],clients:[{id:'c1',name:'Cliente Teste',owner:user,state:'PA',city:'Belém'}],orders:[{id:'o1',clientId:'c1',sellerResponsible:user,status:'Pendente',amount:100,date:'2026-10-05'}],tasks:[],opportunities:[],pending:[]}));
 w.eval(fs.readFileSync('commercial_drafts.js','utf8')+'\n'+fs.readFileSync('crm_operations.js','utf8')+'\n'+fs.readFileSync('goals_dashboard.js','utf8')+'\n'+fs.readFileSync('workspace.js','utf8')+'\n'+fs.readFileSync('app.js','utf8'));
 assert(w.document.querySelector('#app').textContent.includes('Vender e acompanhar'));
 for(const id of ['crm','rota','crescimento','operations','pedidos','marcas'])assert(w.document.querySelector('#tabs button[data-tab='+id+']'));assert(w.document.querySelector('#sectionTabs').hidden);
 w.show('crm');assert(w.document.querySelector('#tabs button[data-tab=crm]'));assert(w.document.querySelector('#app').textContent.includes('Visão 360'));assert.equal(w.document.querySelector('#clientWorkspaceDrawer'),null);
 const activity=w.commercialActivityHTML([{user:'Marlene',kind:'task',action:'upsert',clientName:'Cliente <teste>',at:'2026-10-07T13:23:00+00:00'}]);assert(activity.includes('Tarefa atualizada'));assert(activity.includes('10:23'));assert(!activity.includes('upsert'));assert(activity.includes('&lt;teste&gt;'));
 assert(w.commercialActivityHTML(Array.from({length:11},()=>({kind:'route',at:'bad'}))).includes('Ver mais 1 atividades'));

 w.show('clientes');w.openClient360('c1');assert(w.document.querySelector('#clientWorkspaceDrawer').open);assert(w.document.querySelector('#app').textContent.includes('Cliente Teste'));w.closeClientWorkspace();assert.equal(w.document.querySelector('#clientWorkspaceDrawer'),null);
 w.show('crescimento');assert(w.document.querySelector('#app').textContent.includes('Crescimento da carteira'));
 w.newOrder();assert(w.document.querySelector('#orderForm'));
 w.show('hoje');w.openWorkspaceChat();assert.equal(!!w.document.querySelector('#workspaceChatDrawer'),user!=='Erika');w.document.querySelector('#workspaceChatDrawer')?.close();
 w.show('config');if(['Marlene','Erika'].includes(user)){w.show('finance');assert(!w.document.querySelector('#app').textContent.includes('Contas e lançamentos'))}
 if(user==='Erika'){w.show('admin');assert(w.document.querySelector('#app').textContent.includes('Vender e acompanhar'))}
 await new Promise(r=>setTimeout(r,10));dom.window.close();
 }console.log('5 profiles passed: grouped navigation, client drawer, growth, new order, chat and restricted finance/admin');
})().catch(e=>{console.error(e);process.exit(1)});
