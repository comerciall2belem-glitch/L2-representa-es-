/* Server-authorized BI. No browser persistence or offline copies of financial data. */
window.L2BI = (() => {
 'use strict';
 const e = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const money = x => x == null ? 'Indisponível' : Number(x).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
 const pct = x => x == null ? 'N/A' : Number(x).toLocaleString('pt-BR',{maximumFractionDigits:1})+'%';
 let controller, sequence=0, last=null, context='', timer, selected='executive';
 const node = id => document.getElementById(id);
 function panel(){return `<section id="bi-shell" class="box l2-bi" aria-labelledby="bi-title"><div class="bi-heading"><div><span class="eyebrow">Decisões e acompanhamento</span><h2 id="bi-title">BI estratégico L2</h2><p>Faturamentos registrados, metas e carteira autorizada.</p></div><button type="button" id="bi-refresh">Atualizar números</button></div><form id="bi-filters" class="bi-filters"><label>De<input name="start" type="date" required></label><label>Até (inclusive)<input name="through" type="date" required></label>${[['seller','Vendedor'],['brand','Marca'],['state','UF'],['city','Cidade'],['segment','Segmento']].map(([n,t])=>`<label>${t}<input name="${n}" placeholder="Todos" maxlength="180"></label>`).join('')}<button>Aplicar filtros</button></form><p id="bi-status" role="status" aria-live="polite">Consultando dados…</p><div class="bi-tabs" role="group" aria-label="Visões do BI">${[['executive','Executiva'],['commercial','Comercial'],['brands','Marcas'],['clients','Clientes e território']].map(([id,label])=>`<button type="button" class="secondary" data-bi-view="${id}">${label}</button>`).join('')}</div><div id="bi-data"></div></section>`;}
 function card(title,value,description=''){return `<article class="bi-card"><span>${e(title)}</span><strong>${e(value)}</strong><small>${e(description)}</small></article>`;}
 function table(title,headers,rows){return `<details open><summary>${e(title)}</summary><div class="bi-scroll"><table><thead><tr>${headers.map(h=>`<th scope="col">${e(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(c=>`<td>${e(c)}</td>`).join('')}</tr>`).join('')||`<tr><td colspan="${headers.length}">Sem dados neste recorte.</td></tr>`}</tbody></table></div></details>`;}
 function bars(title,rows){const max=Math.max(...rows.map(x=>Number(x.amount)),1);return `<section class="bi-chart"><h3>${e(title)}</h3>${rows.slice(0,15).map(x=>`<div class="bi-bar"><span>${e(x.label||'Não informado')}</span><meter min="0" max="${max}" value="${Math.max(0,Number(x.amount))}" aria-label="${e(x.label)}"></meter><b>${e(money(x.amount))}</b></div>`).join('')||'<p>Sem faturamento registrado neste recorte.</p>'}</section>`;}
 function draw(){
  if(!last||context!==s.token+'|'+s.server||!node('bi-data'))return;
  const k=last.kpis, f=last.financial;
  let html='';
  if(selected==='executive'){
   html=`<div class="bi-cards">${card('Vendas representadas',money(k.sales),'NF registradas no período')}${card('Meta mensal',money(k.target),'Disponível para mês completo e escopo compatível')}${card('Atingimento',pct(k.attainment))}${card('Ticket por pedido',money(k.ticket_order))}</div>`;
   if(f)html+=`<div class="bi-cards">${card('Comissão prevista',money(f.expected_commission),'Dos faturamentos selecionados')}${card('Comissão recebida',money(f.received_on_selected_invoices),'Recebido dos documentos, não fluxo de caixa do período')}${card('Comissão aberta hoje',money(f.open_commission_today),'Posição de '+f.position_date)}${card('Comissão vencida hoje',money(f.overdue_commission_today),'Posição atual, independente do período')}</div><p>Lucro e margem líquidos: indisponíveis até conciliar tributos, repasses e despesas. ${e(f.missing)} registro(s) financeiros incompletos.</p>`;
   html+=`<div class="bi-grid">${bars('Faturamento por marca',last.brands)}${table('Evolução diária',['Dia','Faturado'],last.trend.map(x=>[x.label,money(x.amount)]))}</div>`;
  }
  if(selected==='commercial')html=`<div class="bi-cards">${card('Clientes compradores',k.buyers)}${card('Carteira no recorte',k.portfolio)}${card('Positivação',pct(k.positivation),'Carteira atual; indisponível por marca')}${card('Ticket por cliente',money(k.ticket_client))}</div>${bars('Vendas por vendedor responsável',last.sellers)}${table('Funil atual da carteira',['Etapa','Oportunidades'],Object.entries(last.funnel_snapshot))}<p>Funil mostra posição atual; conversão histórica exige eventos de fechamento conciliados.</p>`;
  if(selected==='brands')html=bars('Faturamento por representada',last.brands)+table('Marcas no período',['Marca','Faturamento'],last.brands.map(x=>[x.label,money(x.amount)]))+'<p>ABC de SKU e giro aguardam itens de faturamento e estoque conciliados. Não se utiliza quantidade pedida como quantidade vendida.</p>';
  if(selected==='clients')html=`${table('Curva ABC de clientes no período',['Cliente','Faturamento','Classe'],last.abc_clients.map(x=>[x.name,money(x.amount),x.abc]))}${table('Tempo desde a última NF registrada',['Cliente','Cidade','Dias','Faixa'],last.clients.map(x=>[x.name,x.city,x.days??'Sem histórico',x.band]))}${bars('Cobertura por cidade',last.geography)}<p>Visão geográfica agregada; mapa e churn dependem de localização e histórico completos.</p>`;
  html+=table('Composição dos valores e documentos',['NF','Data','Cliente','Pedido','Marca','Vendedor','Faturado'],last.documents.map(x=>[x.invoice,x.date,x.client,x.order_id,x.brand,x.seller,money(x.amount)]));
  html+=`<details><summary>Fontes e limites para decisão</summary><ul>${last.warnings.map(x=>`<li>${e(x)}</li>`).join('')}</ul><p>Versão das fórmulas: ${e(last.formula_version)}</p></details>`;
  node('bi-data').innerHTML=html;
  document.querySelectorAll('[data-bi-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.biView===selected)));
 }
 async function load(){
  const form=node('bi-filters');if(!form||!s.token)return;
  const token=s.token,server=s.server,current=++sequence;context=token+'|'+server;last=null;
  controller?.abort();controller=new AbortController();node('bi-data').replaceChildren();node('bi-status').textContent='Consultando dados…';
  const values=Object.fromEntries(new FormData(form)),dt=new Date(values.through+'T12:00:00Z');
  if(!Number.isFinite(dt.valueOf())){node('bi-status').textContent='Informe uma data final válida.';return;}
  dt.setUTCDate(dt.getUTCDate()+1);values.end=dt.toISOString().slice(0,10);delete values.through;
  try{
   const response=await fetch(server+'/api/bi/dashboard?'+new URLSearchParams(values),{headers:{Authorization:'Bearer '+token},cache:'no-store',signal:controller.signal});
   if(!response.ok){let error={};try{error=await response.json();}catch{}throw Error(typeof error.detail==='string'?error.detail:'Não foi possível consultar. Tente atualizar os números.');}
   const data=await response.json();
   if(current!==sequence||token!==s.token||server!==s.server||!node('bi-data'))return;
   last=data;node('bi-status').textContent='Atualizado em '+new Date(data.generated_at).toLocaleString('pt-BR',{timeZone:'America/Belem'})+' · '+data.period.start+' a '+values.through+' · filtros aplicados';draw();
  }catch(error){if(error.name!=='AbortError'&&current===sequence&&token===s.token&&node('bi-status'))node('bi-status').textContent=error.message;}
 }
 function bind(){
  clearInterval(timer);controller?.abort();++sequence;last=null;
  const form=node('bi-filters');if(!form)return;
  const today=new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Belem',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  form.elements.start.value=today.slice(0,7)+'-01';form.elements.through.value=today;
  form.onsubmit=ev=>{ev.preventDefault();load();};node('bi-refresh').onclick=load;
  document.querySelectorAll('[data-bi-view]').forEach(b=>b.onclick=()=>{selected=b.dataset.biView;draw();});
  load();timer=setInterval(()=>{if(!document.hidden&&node('bi-filters')&&!form.contains(document.activeElement))load();},60000);
 }
 return {panel,bind};
})();
