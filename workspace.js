function industryLabel(value){const row=(s.industries||[]).find(x=>x.id===value||x.name===value);return row?.displayName||row?.name||value||''}
/* Shared operational workspace. Data permissions remain in the existing access policy. */
const WORKSPACE_GROUPS=[
 {id:'inicio',label:'Hoje',sections:[['hoje','Prioridades'],['routine','Rotina estratégica'],['rota','Rota e visitas'],['registro','Registrar atividade']]},
 {id:'vendas',label:'Vendas',sections:[['pedidos','Pedidos'],['marcas','Marcas e preços'],['industries','Indústrias'],['whatsapp','Chat e tabelas']]},
 {id:'clientes',label:'Clientes',sections:[['clientes','Carteira e acompanhamento'],['crm','Visão 360°']]},
 {id:'crescimento',label:'Crescimento',sections:[['crescimento','Desempenho'],['gestao','Metas e gestão']]},
 {id:'escritorio',label:'Escritório',sections:[['office','Processos'],['operations','Operações'],['estoque','Estoque'],['admin','Equipe'],['finance','Financeiro'],['cash','Caixa'],['personal','Pessoal'],['config','Configurações']]}
];
function workspaceGroup(t){return WORKSPACE_GROUPS.find(g=>g.sections.some(x=>x[0]===t))||WORKSPACE_GROUPS.find(g=>g.id===(t==='crm'?'clientes':'inicio'))}
const WORKSPACE_TABS=[['hoje','Hoje'],['clientes','Clientes'],['crm','Visão 360°'],['marcas','Marcas e preços'],['industries','Indústrias'],['rota','Atuação / Minha rota'],['registro','Registrar'],['routine','Rotina estratégica'],['pedidos','Pedidos'],['crescimento','Acompanhamento de vendas'],['whatsapp','Chat'],['gestao','Gestão'],['office','Escritório'],['operations','Operações'],['estoque','Estoque'],['admin','Administração'],['finance','Financeiro'],['personal','Financeiro Pessoal'],['cash','Caixa diário'],['config','Configurações']];
function workspaceNav(){
 const root=$('tabs'),sub=$('sectionTabs');if(!root)return;
 root.replaceChildren();for(const [id,label] of WORKSPACE_TABS){if(!sectionAllowed(id))continue;const b=document.createElement('button');b.textContent=label;b.dataset.tab=id;b.setAttribute('aria-current',tab===id?'page':'false');b.onclick=()=>{if(id==='crm')window.crmClientId='';show(id)};root.append(b)}
 if(sub){sub.replaceChildren();sub.hidden=true}
}
function salesScope(){return {orders:managementAllowed()?s.orders:s.orders.filter(o=>orderSeller(o)===s.user),tasks:s.tasks.filter(t=>managementAllowed()||t.user===s.user),opportunities:(s.opportunities||[]).filter(o=>managementAllowed()||o.owner===s.user)}}
function dailySalesSummary(orders, visits, sellers, day){
 const bySeller=new Map(sellers.filter(Boolean).map(name=>[name,new Set()]));
 const byBrand=new Map(), buyers=new Set();
 let sold=0, pending=0, total=0;
 const unassignedBrand='Marca não identificada';
 for(const order of new Map(orders.map(order=>[order.id,order])).values()){
  if(order.date!==day)continue;
  if(order.status==='Pendente')pending++;
  if(!['Confirmado','Faturado','Entregue'].includes(order.status))continue;
  sold++;
  const cents=priceCents(order.amount);
  total+=cents;
  const seller=orderSeller(order)||'Sem vendedor informado';
  if(!bySeller.has(seller))bySeller.set(seller,new Set());
  if(order.clientId){
   buyers.add(order.clientId);
   bySeller.get(seller).add(order.clientId);
  }
  const parts=[...orderBrandTotals(order)].filter(([,value])=>Number.isSafeInteger(value)&&value>0);
  const identified=parts.reduce((sum,[,value])=>sum+value,0);
  // Keep legacy orders with missing items or inconsistent splits in the commercial total.
  const attributed=identified<=cents?identified:0;
  if(attributed){
   for(const [brand,value] of parts)byBrand.set(brand,(byBrand.get(brand)||0)+value);
  }
  if(attributed<cents)byBrand.set(unassignedBrand,(byBrand.get(unassignedBrand)||0)+cents-attributed);
 }
 const revisit=new Set(visits.filter(v=>v.date===day&&v.clientId&&v.result==='Visitado - sem pedido'&&!buyers.has(v.clientId)).map(v=>v.clientId));
 const rank=entries=>entries.sort(([a,x],[b,y])=>y-x||a.localeCompare(b,'pt-BR'));
 return {sold,pending,total,buyers:buyers.size,revisit:[...revisit],
  sellers:rank([...bySeller].map(([name,clients])=>[name,clients.size])),
  brands:rank([...byBrand])};
}

function renderDailySalesDashboard(){
 if(!s.token||!sectionAllowed('pedidos'))return '';
 const currentDay=new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Belem'}).format(new Date());
 const day=window.dailySalesDay||currentDay, team=managementAllowed();
 const {orders}=salesScope();
 const visits=s.visits.filter(v=>team||v.user===s.user);
 const sellers=team?s.sellers.filter(name=>name!=='Laís'):[s.user];
 const summary=dailySalesSummary(orders,visits,sellers,day);
 const cash=cents=>money(cents/100);
 const revisitNames=summary.revisit.map(id=>client(id).name).filter(Boolean).slice(0,3);
 const chart=(title,rows,format,empty,brandChart=false)=>{
  const max=Math.max(1,...rows.map(([,value])=>value));
  return `<div class="daily-sales-chart"><h3>${title}</h3>${rows.length?`<ul>${rows.map(([name,value])=>`
   <li><div><span>${esc(brandChart?industryLabel(name):name)}</span><strong>${format(value)}</strong></div>
   <span class="daily-sales-track" aria-hidden="true"><span style="width:${value/max*100}%"></span></span></li>`).join('')}</ul>`:`<p>${empty}</p>`}</div>`;
 };
 return `<section class="box daily-sales" aria-labelledby="dailySalesTitle">
  <div class="workspace-section-heading"><div><h2 id="dailySalesTitle">Vendas do dia</h2><p>${team?'Equipe comercial':'Minha carteira'} · Horário de Belém</p></div>
  <label>Data dos pedidos<input type="date" value="${esc(day)}" max="${currentDay}" onchange="if(this.validity.valid){window.dailySalesDay=this.value;window.L2Live?.refresh()}"></label></div>
  <div id="dailySalesData">${!s.syncAt&&!orders.length?'<p>Aguardando a primeira sincronização dos pedidos.</p>':`
   <dl class="daily-sales-totals"><div><dt>Pedidos confirmados, faturados ou entregues</dt><dd>${summary.sold}</dd></div>
   <div><dt>Pedidos pendentes</dt><dd>${summary.pending}</dd></div>
   <div><dt>Valor vendido em pedidos</dt><dd>${cash(summary.total)}</dd></div>
   <div><dt>Clientes positivados</dt><dd>${summary.buyers}</dd></div></dl>
   <p>A positivação conta cada cliente com compra uma vez por vendedor. O total do dia também elimina repetições entre vendedores.</p>
   <div class="daily-sales-charts">
    ${chart('Positivação por vendedor',summary.sellers,value=>value.toLocaleString('pt-BR'),'Nenhum vendedor na base do dia.')}
    ${chart('Valor vendido por marca',summary.brands,cash,'Nenhuma venda por marca nesta data.',true)}
   </div>
   <div class="daily-sales-opportunities"><h3>Oportunidades do dia</h3>
    ${summary.pending?`<p><strong>${summary.pending}</strong> pedido(s) pendente(s). Confira o que falta para confirmar. <button type="button" class="secondary" onclick="window.orderStatusFilter='Pendente';show('pedidos');document.getElementById('orderRegister')?.scrollIntoView()">Abrir lista de pendentes</button></p>`:''}
    ${summary.revisit.length?`<p><strong>${summary.revisit.length}</strong> cliente(s) com visita sem pedido e sem compra registrada no dia. ${revisitNames.length?`Retomar com ${esc(revisitNames.join(', '))}.`:''} ${sectionAllowed('registro')?'<button type="button" class="secondary" onclick="show(&#39;registro&#39;)">Ver atendimentos</button>':''}</p>`:''}
    ${!summary.pending&&!summary.revisit.length?'<p>Sem pedidos pendentes ou visitas sem compra identificadas nesta data.</p>':''}
   </div>`}</div>
 </section>`;
}

function renderSalesHome(){
 const scope=salesScope(),pending=scope.orders.filter(o=>!['Faturado','Cancelado'].includes(o.status)),due=scope.tasks.filter(t=>t.status!=='Concluída'&&t.date&&t.date<=today()).sort((a,b)=>String(a.date).localeCompare(String(b.date))),follow=scope.opportunities.filter(o=>!['Ganho','Perdido','Pós-venda'].includes(o.stage)&&o.followUp&&o.followUp<=today());
 const month=today().slice(0,7),m=commercialMetrics(s,month,today(),s.user,managementAllowed()),priorities=m.priorities.slice(0,6),chat=['Ana Paula','Euler','Laís','Marlene'].includes(s.user);
 return `<div class="workspace-heading"><div><span class="eyebrow">${esc(s.user)} · ${esc(today().split('-').reverse().join('/'))}</span><h2>Dashboard</h2><p>Vender e acompanhar · Pedidos, retornos e oportunidades da ${managementAllowed()?'equipe':'sua carteira'}.</p></div><button onclick="newOrder()">+ Novo pedido</button></div>
 ${window.L2Strategy?.dashboardTop()||''}
 ${window.L2Strategy?.launchpad()||''}
 <div class="workspace-actions">${sectionAllowed('clientes')?'<button class="secondary" onclick="show(\'clientes\')">Buscar cliente</button>':''}${sectionAllowed('marcas')?'<button class="secondary" onclick="show(\'marcas\')">Consultar preços</button>':''}${sectionAllowed('rota')?'<button class="secondary" onclick="show(\'rota\')">Minha rota</button>':''}${chat?'<button class="secondary" onclick="openWorkspaceChat()">Atendimento <span id="workspaceChatBadge"></span></button>':''}${sectionAllowed('crescimento')?'<button class="secondary" onclick="show(\'crescimento\')">BI estratégico e crescimento</button>':''}</div>
 <div class="workspace-stats"><span>Vendas no mês <b>${money(m.total)}</b></span><span>Pedidos em andamento <b>${pending.length}</b></span><span>Retornos pendentes <b>${due.length+follow.length}</b></span><span>Clientes inativos <b>${m.inactive}</b></span></div>
 <div class="workspace-grid"><section class="box"><div class="workspace-section-heading"><h3>Acompanhamento de hoje</h3><button class="secondary" onclick="window.crmClientId='';show('crm')">Ver acompanhamento</button></div>
 ${due.slice(0,4).map(t=>`<div class="workspace-item"><div><b>${esc(client(t.clientId).name||'Atividade comercial')}</b><p>${esc(t.text||'Retorno')} · ${esc(t.date)}</p></div>${t.clientId?`<button class="secondary" onclick="openClient360('${esc(t.clientId)}')">Acompanhar</button>`:''}</div>`).join('')}
 ${follow.slice(0,4).map(o=>`<div class="workspace-item"><div><b>${esc(client(o.clientId).name||'Negociação')}</b><p>${esc(o.brand||'')} · ${esc(o.stage)} · retorno ${esc(o.followUp)}</p></div><button class="secondary" onclick="openClient360('${esc(o.clientId)}')">Retomar</button></div>`).join('')}
 ${!due.length&&!follow.length?'<p class="muted">Nenhum retorno vencido ou previsto para hoje. Aproveite para prospectar ou reativar clientes.</p>':''}
 </section><section class="box"><div class="workspace-section-heading"><h3>Pedidos em andamento</h3><button class="secondary" onclick="show('pedidos')">Ver todos</button></div>
 ${pending.slice().sort((a,b)=>String(b.date).localeCompare(String(a.date))).slice(0,6).map(o=>`<div class="workspace-item"><div><b>${esc(client(o.clientId).name||'Cliente não informado')}</b><p>${o.orderNumber?'Pedido '+esc(orderLabel(o.orderNumber)):'Pedido local'} · ${esc(o.status)} · ${money(o.amount)}</p></div><button class="secondary" onclick="editOrder('${esc(o.id)}')">Abrir</button></div>`).join('')||'<p class="muted">Nenhum pedido em andamento.</p>'}</section></div>
 ${priorities.length?`<section class="box"><div class="workspace-section-heading"><h3>Oportunidades para vender mais</h3><button class="secondary" onclick="show('clientes')">Abrir carteira</button></div><div class="workspace-priorities">${priorities.map(x=>`<article><b>${esc(x.client.name)}</b><p>${esc(x.reasons.join(' · '))}</p><small>${esc(locationLabel(x.client))}</small><div><button onclick="startClientOrder('${esc(x.client.id)}')">Vender</button> <button class="secondary" onclick="openClient360('${esc(x.client.id)}')">Acompanhar</button></div></article>`).join('')}</div></section>`:''}
 <p class="muted workspace-sync">${s.pending.length?s.pending.length+' alterações aguardando sincronização':'Dados sincronizados'} · ${esc(s.syncAt||'Sincronize para atualizar a carteira')}</p>`;
}
function renderClientWorkspace(){return `<div class="workspace-actions"><button class="secondary" onclick="window.crmClientId='';show('crm')">Funil e retornos da carteira</button></div>`+renderClients()}
function renderClientDrawer(){return renderClientWorkspace()+`<dialog id="clientWorkspaceDrawer" class="workspace-drawer" aria-label="Acompanhamento do cliente"><div class="drawer-toolbar"><strong>Cliente · Histórico e acompanhamento</strong><button class="secondary" onclick="closeClientWorkspace()" aria-label="Fechar acompanhamento">Fechar ×</button></div>${renderCRM()}</dialog>`}
function closeClientWorkspace(){show('clientes')}
function bindWorkspace(){
 workspaceNav();const drawer=$('clientWorkspaceDrawer');if(drawer){drawer.showModal();drawer.addEventListener('cancel',e=>{e.preventDefault();closeClientWorkspace()})}


}
async function loadWorkspaceChatCount(){const badge=$('workspaceChatBadge');if(!badge||!s.token||!navigator.onLine)return;const token=s.token;try{const r=await fetch(s.server+'/api/chat/staff/conversations',{headers:{Authorization:'Bearer '+token},cache:'no-store'});if(!r.ok)return;const rows=await r.json();if(token!==s.token||badge!==$('workspaceChatBadge'))return;const count=rows.filter(r=>r.unread>0).length;badge.textContent=count?'· '+count+' pendente(s)':''}catch(_){}}
function openWorkspaceChat(){
 if(!s.token||!['Ana Paula','Euler','Laís','Marlene'].includes(s.user))return;
 const existing=$('workspaceChatDrawer');if(existing){existing.showModal();return}
 const d=document.createElement('dialog');d.id='workspaceChatDrawer';d.className='workspace-drawer chat-drawer';d.setAttribute('aria-label','Atendimento por link');
 const bar=document.createElement('div');bar.className='drawer-toolbar';const title=document.createElement('strong');title.textContent='Atendimento L2';const close=document.createElement('button');close.className='secondary';close.textContent='Fechar ×';close.onclick=()=>d.close();bar.append(title,close);
 const frame=document.createElement('iframe');frame.src='/atendimento/equipe';frame.title='Painel de atendimento L2';d.append(bar,frame);d.addEventListener('close',()=>{d.remove();loadWorkspaceChatCount()});document.body.append(d);d.showModal();
}
