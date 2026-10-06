/* Shared operational workspace. Data permissions remain in the existing access policy. */
const WORKSPACE_GROUPS=[
 {id:'inicio',label:'Hoje',sections:[['hoje','Prioridades'],['rota','Rota e visitas'],['registro','Registrar atividade']]},
 {id:'vendas',label:'Vendas',sections:[['pedidos','Pedidos'],['marcas','Marcas e preços'],['whatsapp','Mensagens e tabelas']]},
 {id:'clientes',label:'Clientes',sections:[['clientes','Carteira e acompanhamento']]},
 {id:'crescimento',label:'Crescimento',sections:[['crescimento','Desempenho'],['gestao','Metas e gestão']]},
 {id:'escritorio',label:'Escritório',sections:[['office','Processos'],['operations','Operações'],['admin','Equipe'],['finance','Financeiro'],['cash','Caixa'],['personal','Pessoal'],['config','Configurações']]}
];
function workspaceGroup(t){return WORKSPACE_GROUPS.find(g=>g.sections.some(x=>x[0]===t))||WORKSPACE_GROUPS.find(g=>g.id===(t==='crm'?'clientes':'inicio'))}
function workspaceNav(){
 const current=workspaceGroup(tab),root=$('tabs'),sub=$('sectionTabs');if(!root||!sub)return;
 root.replaceChildren();for(const g of WORKSPACE_GROUPS){const sections=g.sections.filter(x=>sectionAllowed(x[0]));if(!sections.length)continue;const b=document.createElement('button');b.textContent=g.label;b.setAttribute('aria-current',current.id===g.id?'page':'false');b.onclick=()=>show(sections[0][0]);root.append(b)}
 sub.replaceChildren();const sections=current.sections.filter(x=>sectionAllowed(x[0]));sub.hidden=sections.length<2;
 for(const [id,label] of sections){const b=document.createElement('button');b.textContent=label;b.setAttribute('aria-current',tab===id?'page':'false');b.onclick=()=>show(id);sub.append(b)}
}
function salesScope(){return {orders:managementAllowed()?s.orders:s.orders.filter(o=>orderSeller(o)===s.user),tasks:s.tasks.filter(t=>managementAllowed()||t.user===s.user),opportunities:(s.opportunities||[]).filter(o=>managementAllowed()||o.owner===s.user)}}
function renderSalesHome(){
 const scope=salesScope(),pending=scope.orders.filter(o=>!['Faturado','Cancelado'].includes(o.status)),due=scope.tasks.filter(t=>t.status!=='Concluída'&&t.date&&t.date<=today()).sort((a,b)=>String(a.date).localeCompare(String(b.date))),follow=scope.opportunities.filter(o=>!['Ganho','Perdido','Pós-venda'].includes(o.stage)&&o.followUp&&o.followUp<=today());
 const month=today().slice(0,7),m=commercialMetrics(s,month,today(),s.user,managementAllowed()),priorities=m.priorities.slice(0,6),chat=['Ana Paula','Euler','Laís','Marlene'].includes(s.user);
 return `<div class="workspace-heading"><div><span class="eyebrow">${esc(s.user)} · ${esc(today().split('-').reverse().join('/'))}</span><h2>Vender e acompanhar</h2><p>Pedidos, retornos e oportunidades da ${managementAllowed()?'equipe':'sua carteira'}.</p></div><button onclick="newOrder()">+ Novo pedido</button></div>
 <div class="workspace-actions">${sectionAllowed('clientes')?'<button class="secondary" onclick="show(\'clientes\')">Buscar cliente</button>':''}${sectionAllowed('marcas')?'<button class="secondary" onclick="show(\'marcas\')">Consultar preços</button>':''}${sectionAllowed('rota')?'<button class="secondary" onclick="show(\'rota\')">Minha rota</button>':''}${chat?'<button class="secondary" onclick="openWorkspaceChat()">Atendimento <span id="workspaceChatBadge"></span></button>':''}${sectionAllowed('crescimento')?'<button class="secondary" onclick="show(\'crescimento\')">Ver crescimento</button>':''}</div>
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
 if(tab==='hoje')loadWorkspaceChatCount();
 if(tab==='crescimento')setTimeout(loadLeadConversion,0);
}
async function loadWorkspaceChatCount(){const badge=$('workspaceChatBadge');if(!badge||!s.token||!navigator.onLine)return;const token=s.token;try{const r=await fetch(s.server+'/api/chat/staff/conversations',{headers:{Authorization:'Bearer '+token},cache:'no-store'});if(!r.ok)return;const rows=await r.json();if(token!==s.token||badge!==$('workspaceChatBadge'))return;const count=rows.filter(r=>r.unread>0).length;badge.textContent=count?'· '+count+' pendente(s)':''}catch(_){}}
function openWorkspaceChat(){
 if(!s.token||!['Ana Paula','Euler','Laís','Marlene'].includes(s.user))return;
 const existing=$('workspaceChatDrawer');if(existing){existing.showModal();return}
 const d=document.createElement('dialog');d.id='workspaceChatDrawer';d.className='workspace-drawer chat-drawer';d.setAttribute('aria-label','Atendimento por link');
 const bar=document.createElement('div');bar.className='drawer-toolbar';const title=document.createElement('strong');title.textContent='Atendimento L2';const close=document.createElement('button');close.className='secondary';close.textContent='Fechar ×';close.onclick=()=>d.close();bar.append(title,close);
 const frame=document.createElement('iframe');frame.src='/atendimento/equipe';frame.title='Painel de atendimento L2';d.append(bar,frame);d.addEventListener('close',()=>{d.remove();loadWorkspaceChatCount()});document.body.append(d);d.showModal();
}
