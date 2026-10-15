const fs=require('fs'),assert=require('node:assert/strict'),{JSDOM}=require(process.env.L2_JSDOM||'jsdom');
async function boot(user,role,sectors){
 const html=fs.readFileSync('index.html','utf8').replace(/<script[\s\S]*?<\/script>/g,'');
 const dom=new JSDOM(html,{url:'https://l2.test',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window;
 w.structuredClone=structuredClone;w.matchMedia=()=>({matches:false});w.alert=()=>{};w.confirm=()=>true;w.setInterval=()=>0;w.Element.prototype.scrollIntoView=()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};
 const state={user,role,sectors,token:'x'.repeat(40),server:'https://l2.test',team:[user],sellers:[],clients:[{id:'c1',name:'PDV autorizado',owner:'Outro',state:'PA'}],orders:[],pending:[]};
 w.localStorage.setItem('l2one_state_v2',JSON.stringify(state));
 let update,eligible=true,closing=null,closeRequest;
 w.fetch=async(url,options={})=>{
  if(url.endsWith('/office-policy/bonus/close')){closeRequest=JSON.parse(options.body);closing={month:closeRequest.month,closedBy:'Ana Paula',members:[{user:'Erika',eligible:true,bonusCents:400}]};return {ok:true,json:async()=>closing};}
  if(url.includes('/office-policy/bonus/closing?'))return {ok:true,json:async()=>({closing})};
  if(url.includes('/office-policy/bonus/eligibility')){update=JSON.parse(options.body);eligible=update.eligible;return {ok:true,json:async()=>({ok:true})};}
  if(url.includes('/office-policy/bonus?'))return {ok:true,json:async()=>({goalCents:100000,salesCents:100000,billedCents:40000,goalReached:true,basis:'Teste fictício',members:[{user:'Erika',eligible,bonusCents:eligible?400:0}]})};
  return {ok:true,json:async()=>[]};
 };
 w.eval(['commercial_drafts.js','crm_operations.js','goals_dashboard.js','workspace.js','modern_workspace.js','app.js','live_records.js'].map(p=>fs.readFileSync(p,'utf8')).join('\n')+'\nL2Live.pause();window.testState=s;');
 return {w,dom,update:()=>update,closeRequest:()=>closeRequest};
}
(async()=>{
 const promo=await boot('Promo','Promotora',['promotion','routes','finance','admin','commercial']);
 const w=promo.w;
 assert(!w.financeAllowed());assert(!w.sectionAllowed('pedidos'));assert(!w.sectionAllowed('admin'));
 assert(w.document.querySelector('#app').textContent.includes('Meu dia em campo'));
 for(const screen of ['finance','gestao','admin','pedidos','whatsapp']){w.show(screen);assert(w.document.querySelector('#app').textContent.includes('Meu dia em campo'));}
 w.show('clientes');assert(!w.document.querySelector('#clientForm'));assert(!w.document.querySelector('[data-client-order]'));
 w.show('registro');assert(w.document.querySelector('#visitForm'));assert(!w.document.querySelector('#visitForm input[name=photos]'));
 promo.dom.window.close();
 const admin=await boot('Ana Paula','Administradora',['admin','finance','management','commercial','routes','promotion']);
 admin.w.show('admin');
 await new Promise(r=>setTimeout(r,30));
 const form=admin.w.document.getElementById('memberCreateForm');form.elements.role.value='Promotora';admin.w.toggleSellerFields(form);
 assert(form.elements.commissionRate.disabled);
 for(const field of form.querySelectorAll('input[name=sectors]'))if(!['promotion','routes'].includes(field.value)){assert(field.disabled);assert(!field.checked);}
 assert(form.querySelector('input[value=promotion]').checked);
 const bonus=admin.w.document.querySelector('[data-bonus-user=Erika]');assert(bonus);assert(bonus.checked);
 bonus.checked=false;await bonus.onchange();
 assert.equal(admin.update().user,'Erika');assert.equal(admin.update().eligible,false);
 assert.match(admin.update().month,/^\d{4}-\d{2}$/);
 admin.w.document.querySelector('#officeBonusMonth').value='2026-09';
 await admin.w.closeOfficeBonus();
 await new Promise(r=>setTimeout(r,20));
 assert.deepEqual(admin.closeRequest(),{month:'2026-09'});
 assert(admin.w.document.querySelector('#officeBonusClose').disabled);
 assert.match(admin.w.document.querySelector('#officeBonusClosing').textContent,/Apuração encerrada por Ana Paula/);
 admin.dom.window.close();
 console.log('Promotora: restricted mobile UI and visits; admin-only eligibility toggle and profile fields: OK');
})().catch(e=>{console.error(e);process.exit(1)});
