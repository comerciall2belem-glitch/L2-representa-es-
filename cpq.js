/* Quotes and approvals use server calculations; no financial permissions are granted here. */
window.L2CPQ=(()=>{
 let quotes=[], loading=false, proposalActivity, businessActivity, ageTimer, alertSequence=0;
 const actionLabels = {
  ligacao:'Ligação registrada', email_enviado:'E-mail enviado', email_recebido:'E-mail recebido',
  reuniao_agendada:'Reunião agendada', reuniao_realizada:'Reunião realizada',
  proposta_enviada:'Proposta enviada', interacao_comercial:'Interação comercial registrada'
 };
 const channels = {whatsapp:'WhatsApp',email:'E-mail',link_direto:'Link direto',
  telefone:'Telefone',presencial:'Presencial',video:'Vídeo',outro:'Outro'};
 const quoteName = q => q.guest?.name || client(q.clientId).name || q.clientId;
 const quoteStates = ['Rascunho','Enviada','Visualizada','Aprovada','Recusada','Expirada'];
 const status=text=>{const el=document.getElementById('cpqStatus');if(el)el.textContent=text};
 async function request(path,method='GET',body){if(!s.token)throw Error('Entre no sistema.');if(method!=='GET'&&s.pending.length&&!path.startsWith('/alerts/'))throw Error('Sincronize os registros pendentes antes de operar o CPQ.');const response=await fetch(s.server+'/api/cpq'+path,{method,headers:{Authorization:'Bearer '+s.token,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});if(!response.ok){const data=await response.json();throw Error(typeof data.detail==='string'?data.detail:'Revise os campos da proposta.')}return response;}
 function panel(){return `<section class="box" id="cpqWorkspace"><h3>Propostas comerciais</h3><button type="button" onclick="L2CPQ.compose()">Nova proposta</button><p>Preços, frete e pedido mínimo conferidos na nuvem. Até 5%: automático; acima de 5% até 10%: gestor; acima de 10%: diretoria. A aprovação exige outro usuário solicitante.</p>${tab==='pedidos'?`<div class="row"><label>Canal B2B<select id="cpqChannel"><option>B2B</option><option>VAREJO</option><option>REVENDA</option><option>ATACADO</option></select></label><label>Desconto (%)<input id="cpqDiscount" type="number" min="0" max="100" step="0.01" value="0"></label><label>Oportunidade vinculada<select id="cpqOpportunity"><option value="">Sem oportunidade</option>${salesScope().opportunities.filter(o=>o.stage!=='Perdido').map(o=>`<option value="${esc(o.id)}">${esc(client(o.clientId).name)} · ${esc(o.brand)}</option>`).join('')}</select></label></div><details><summary>Opções da proposta</summary><div class="row"><label>Validade (dias)<input id="cpqValidity" type="number" min="1" max="30" value="14"></label><label>Observações<input id="cpqNotes" maxlength="1000"></label></div></details><button type="button" onclick="L2CPQ.create()">Gerar proposta do carrinho</button> <button type="button" class="secondary" onclick="L2CPQ.loadMix()">Sugerir ampliação de mix</button><div id="cpqMix"></div>`:''}<button type="button" class="secondary" onclick="L2CPQ.load()">Atualizar propostas</button>
 <button type="button" class="secondary" onclick="L2CPQ.openRules()">Regras de inatividade</button>
 <label>Situação<select id="cpqFilter" onchange="L2CPQ.filter()"><option value="">Todas</option>${quoteStates.map(state=>`<option>${state}</option>`).join('')}</select></label>
 <label>Inatividade<select id="cpqAgeFilter" onchange="L2CPQ.filter()"><option value="">Todas</option><option>Em dia</option><option>Atenção</option><option>Em risco</option><option>Crítico</option></select></label><p id="cpqStatus" role="status"></p><div id="cpqQuotes"></div><div id="cpqForecast"></div></section>`;}
 function fillSelector(){const form=document.getElementById('crmOpportunityForm'),select=form?.elements.cpqQuoteId;if(!select)return;const old=select.value;select.replaceChildren(new Option('Selecione a proposta CPQ',''));for(const q of quotes.filter(x=>x.clientId===form.elements.clientId.value&&x.sellerResponsible===form.elements.owner.value))select.add(new Option(q.id.slice(0,8)+' · '+money(q.amount)+' · '+q.approvalStatus,q.id));if(old&&![...select.options].some(x=>x.value===old))select.add(new Option(old,old));select.value=old;}
 function renderQuotes(){fillSelector();const el=document.getElementById('cpqQuotes');if(!el)return;el.innerHTML=quotes.map(q=>`<details class="box" data-cpq-quote="${esc(q.id)}" data-cpq-state="${esc(q.proposalStatus)}">
 <summary>${q.number?'#'+q.number+' · ':''}${esc(quoteName(q))} · ${money(q.amount)} · <span data-cpq-status>${esc(q.proposalStatus)}</span></summary>
 <p data-cpq-age></p><p data-cpq-views>${viewLabel(q)}</p><p>Alçada: ${esc(q.approvalStatus)} · ${esc(q.requiredApproval)}</p><p>${esc(q.sellerResponsible)} · desconto ${esc(q.discountPercent)}% · válida até ${esc(q.validUntil)}</p><p>${Object.entries(q.policies).map(([brand,p])=>`${esc(brand)}: frete ${esc(p.freight)}, mínimo ${money(p.minimum)}${p.paymentMethod?' · '+esc(p.paymentMethod):''}`).join(' · ')}</p>${q.approvalStatus==='Pendente'&&['Ana Paula','Euler'].includes(s.user)&&(q.approvalRequestedBy||q.createdBy)!==s.user?`<label>Justificativa da decisão<input id="cpqReason-${esc(q.id)}" maxlength="1000"></label><button type="button" onclick="L2CPQ.decide('${esc(q.id)}',true)">Aprovar alçada</button> <button type="button" class="secondary" onclick="L2CPQ.decide('${esc(q.id)}',false)">Recusar</button>`:''}${q.approvalStatus==='Aprovada'?`<button type="button" onclick="L2CPQ.pdf('${esc(q.id)}')">Emitir PDF</button>
 ${!q.convertedOrderId&&!['Recusada','Expirada'].includes(q.proposalStatus)?`<div class="row">
 <button type="button" class="secondary" onclick="L2CPQ.share('${q.id}','link_direto')">Copiar link rastreado</button>
 <button type="button" class="secondary" onclick="L2CPQ.share('${q.id}','whatsapp')">Enviar por WhatsApp</button>
 <button type="button" class="secondary" onclick="L2CPQ.share('${q.id}','email')">Enviar por e-mail</button></div>`:''}<label>Canal de envio<select id="cpqSend-${esc(q.id)}"><option>WhatsApp</option><option>E-mail</option><option>Link</option></select></label><label>Comprovação do envio / aceite<input id="cpqEvidence-${esc(q.id)}" maxlength="1000" placeholder="Registre destinatário, data e retorno do cliente"></label><button type="button" class="secondary" onclick="L2CPQ.action('${esc(q.id)}','dispatch')">Registrar envio realizado</button> <button type="button" class="secondary" onclick="L2CPQ.action('${esc(q.id)}','accept')">Registrar aceite</button> <button type="button" data-cpq-convert ${q.convertedOrderId||!q.acceptedAt||!q.clientId?'disabled':''} onclick="L2CPQ.action('${esc(q.id)}','convert')">${q.convertedOrderId?'Pedido já gerado':'Converter em pedido'}</button>`:''}<p>${q.documentIssuedAt?'PDF emitido · ':''}${q.sentAt?'Envio registrado · ':''}${q.acceptedAt?'Aceite registrado · ':''}${q.convertedOrderId?'Pedido '+esc(q.convertedOrderId):''}</p><button type="button" class="secondary" onclick="L2CPQ.openContact('propostas','${q.id}')">Registrar contato</button>
 ${!q.clientId?`<label>Cliente para conversão<select id="cpqAttach-${q.id}"><option value="">Selecione</option>${s.clients.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select></label><button type="button" class="secondary" onclick="L2CPQ.attach('${q.id}')">Vincular cliente</button>`:''}
 ${!q.publicCustomer&&!q.documentIssuedAt&&!q.sentAt&&!q.acceptedAt?`<button type="button" class="secondary" onclick="L2CPQ.compose('${q.id}')">Editar rascunho</button> <button type="button" class="secondary" onclick="L2CPQ.removeDraft('${q.id}')">Excluir rascunho</button>`:''}
 </details>`).join('')||'<p>Nenhuma proposta registrada.</p>'; decorate();}
 async function load(){if(loading)return;loading=true;status('Consultando propostas…');try{const results=await Promise.all([request('/activity/propostas'),request('/activity/negocios')]);
 [proposalActivity,businessActivity]=await Promise.all(results.map(response=>response.json()));
 quotes=proposalActivity.quotes;renderQuotes();status('Propostas atualizadas.')}catch(e){status(e.message)}finally{loading=false}}
 async function create(){try{status('Calculando preços e regras…');const draft=orderDraft(),form=document.getElementById('orderForm'),items=pricesForTable(draft.priceTable).map(p=>({brand:p.brand,sku:p.sku,tableId:catalogTableId(p),quantity:Number(draft.quantities[p.brand+'|'+p.sku]||0)})).filter(i=>i.quantity>0);const data={clientId:draft.clientId,sellerResponsible:administrativeOrderEntry()?form.elements.sellerResponsible.value:s.user,priceTable:draft.priceTable,channel:document.getElementById('cpqChannel').value,paymentTerms:form.elements.paymentTerms.value.replace(/\s/g,''),discountPercent:document.getElementById('cpqDiscount').value,opportunityId:document.getElementById('cpqOpportunity').value,items,
 validityDays:Number(document.getElementById('cpqValidity').value),notes:document.getElementById('cpqNotes').value};
 await request('/quotes','POST',data);await load();status('Proposta registrada. Confira a alçada antes de emitir o PDF.')}catch(e){status(e.message)}}
 async function decide(id,approve){try{const reason=document.getElementById('cpqReason-'+id).value;await request('/quotes/'+encodeURIComponent(id)+'/decision','POST',{approve,reason});await load()}catch(e){status(e.message)}}
 async function pdf(id){try{const response=await request('/quotes/'+encodeURIComponent(id)+'/pdf');downloadBlob(await response.blob(),'L2_Proposta_'+id+'.pdf');await load();status('PDF emitido. O envio ao cliente precisa ser realizado e registrado.')}catch(e){status(e.message)}}
 async function action(id,kind){try{const evidence=document.getElementById('cpqEvidence-'+id)?.value||'',data=kind==='convert'?null:kind==='dispatch'?{evidence,channel:document.getElementById('cpqSend-'+id).value}:{evidence};await request('/quotes/'+encodeURIComponent(id)+'/'+kind,'POST',data);if(kind==='convert')await sync();await load();status(kind==='convert'?'Pedido gerado e integrado.':'Registro salvo.')}catch(e){status(e.message)}}
 async function loadMix(){try{const id=orderDraft().clientId;if(!id)throw Error('Selecione um cliente no carrinho.');const out=await (await request('/mix/'+encodeURIComponent(id))).json(),el=document.getElementById('cpqMix');el.innerHTML=`<p>${esc(out.reason)}</p><ul>${out.products.map(x=>`<li>${esc(x.brand)} · ${esc(x.sku)} · ${esc(x.description)} — ${x.quantitySold} unidades faturadas no segmento</li>`).join('')}</ul>`}catch(e){status(e.message)}}
 async function loadForecast(){try{const out=await (await request('/forecast')).json(),el=document.getElementById('cpqForecast');if(!el)return;el.innerHTML='<h4>Forecast · carteira e média global</h4><p>'+esc(out.basis)+'</p><ul>'+Object.entries(out.rates).map(([stage,r])=>`<li>${esc(stage)}: ${r.rate===null?'Sem base suficiente':(100*r.rate).toFixed(1)+'%'} · ${r.sample} resultados · ${esc(r.message)}</li>`).join('')+'</ul>'}catch(e){status(e.message)}}
 function viewLabel(q){
  if(!q.views)return '0 aberturas';
  return `${q.views} ${q.views===1?'abertura':'aberturas'} · ${channels[q.viewedChannel]} · ${new Date(q.viewedAt).toLocaleString('pt-BR')}`;
 }
 function filter(){
  const value=document.getElementById('cpqFilter')?.value;
  const age=document.getElementById('cpqAgeFilter')?.value;
  document.querySelectorAll('[data-cpq-quote]').forEach(card=>{card.hidden=!!((value&&card.dataset.cpqState!==value)||(age&&card.dataset.l2Inactivity!==age));});
 }
 function showAge(card,label,facts,rules,active){
  if(!label||!rules)return;
  card.removeAttribute('data-l2-inactivity');
  label.textContent='';
  const since=Date.parse(facts?.since);
  if(!active||!Number.isFinite(since))return;
  const minutes=Math.max(0,Math.floor((Date.now()-since)/60000)),days=Math.floor(minutes/1440);
  const band=days>=rules.dias_critico?'Crítico':days>=rules.dias_risco?'Em risco':days>=rules.dias_atencao?'Atenção':'Em dia';
  card.dataset.l2Inactivity=band;
  const elapsed=days?days+'d':minutes>=60?Math.floor(minutes/60)+'h':minutes+'min';
  label.textContent=`${band} · ${facts.channel?'Contato':'Em acompanhamento'} há ${elapsed}${facts.channel?' · '+channels[facts.channel]:''}`;
 }
 function decorate(){
  if(proposalActivity)for(const card of document.querySelectorAll('[data-cpq-quote]')){
   const id=card.dataset.cpqQuote;
   showAge(card,card.querySelector('[data-cpq-age]'),proposalActivity.records[id],proposalActivity.rules,
    !['Recusada','Aprovada','Expirada'].includes(card.dataset.cpqState));
  }
  if(businessActivity)for(const card of document.querySelectorAll('[data-cpq-business]')){
   showAge(card,card.querySelector('[data-cpq-age]'),businessActivity.records[card.dataset.cpqBusiness],businessActivity.rules,true);
  }
  filter();
 }
 function dialog(content){
  document.getElementById('cpqDialog')?.remove();
  const el=document.createElement('dialog');
  el.id='cpqDialog';
  el.innerHTML=content;
  document.body.append(el);
  el.addEventListener('close',()=>el.remove());
  el.querySelector('[data-close]').addEventListener('click',()=>el.close());
  el.showModal();
  return el;
 }
 function selectOptions(rows, label='Selecione'){
  return '<option value="">'+esc(label)+'</option>'+rows.map(([value,text])=>`<option value="${esc(value)}">${esc(text)}</option>`).join('');
 }
 function compose(id){
  const original=quotes.find(q=>q.id===id)?.request||{};
  const el=dialog(`<form><h3>${id?'Editar proposta':'Nova proposta'}</h3>
  <div class="row">
   <label>Destinatário<select name="source"><option value="client">Cliente</option><option value="lead">Lead</option><option value="guest">Avulsa</option></select></label>
   <label>Vendedor<select name="seller" required>${sellerOptions(original.sellerResponsible||s.user)}</select></label>
   <label data-source="client">Cliente<select name="clientId">${selectOptions(s.clients.map(c=>[c.id,c.name]))}</select></label>
   <label data-source="lead">Lead<select name="leadId">${selectOptions((s.leads||[]).map(c=>[c.id,c.name]))}</select></label>
  </div>
  <div class="row" data-source="guest">
   <label>Razão social<input name="guestName" maxlength="200"></label><label>CNPJ<input name="guestTaxId" maxlength="20"></label>
   <label>Cidade<input name="guestCity" maxlength="120"></label>
   <label>UF<select name="guestState"><option>PA</option><option>AP</option></select></label>
  </div>
  <p data-customer></p>
  <div class="row">
   <label>Tabela<select name="priceTable"><option>PA</option><option>AP</option></select></label>
   <label>Canal<select name="channel"><option>B2B</option><option>VAREJO</option><option>REVENDA</option><option>ATACADO</option></select></label>
   <label>Pagamento (dias)<input name="terms" pattern="[0-9]+(/[0-9]+)*" placeholder="30/60" required></label>
   <label>Desconto (%)<input name="discount" type="number" min="0" max="100" step=".01" required></label>
   <label>Validade (dias)<input name="validity" type="number" min="1" max="30" required></label>
  </div>
  <div data-items></div><button type="button" class="secondary" data-add>Adicionar item</button>
  <p data-subtotal></p><label>Observações<textarea name="notes" maxlength="1000"></textarea></label>
  <p role="status"></p><button data-save>Salvar proposta</button> <button type="button" class="secondary" data-close>Fechar</button></form>`);
  const form=el.querySelector('form'), fields=form.elements, items=form.querySelector('[data-items]');
  const set=(name,value)=>{fields[name].value=value;};
  set('source',original.leadId?'lead':original.guest?'guest':'client');
  set('clientId',original.clientId||'');set('leadId',original.leadId||'');
  set('guestName',original.guest?.name||'');set('guestTaxId',original.guest?.taxId||'');
  set('guestCity',original.guest?.city||'');set('guestState',original.guest?.state||'PA');
  set('priceTable',original.priceTable||'PA');set('channel',original.channel||'B2B');
  set('terms',original.paymentTerms||'');set('discount',original.discountPercent||0);
  set('validity',original.validityDays||14);set('notes',original.notes||'');
  const catalog=()=> (s.prices||[]).filter(price=>{
   const table=(s.priceTables||[]).find(t=>t.id===catalogTableId(price));
   return price.active!==false&&table?.active&&
    (table.state==='ALL'||table.state===fields.priceTable.value)&&
    (!table.channel||table.channel.toUpperCase()===fields.channel.value);
  });
  const lineValues=()=>[...items.querySelectorAll('[data-line]')].map(row=>{
   const price=row.querySelector('[data-sku]').selectedOptions[0]?.price;
   return {price, quantity:Number(row.querySelector('[data-quantity]').value)};
  });
  function subtotal(){
   const factor=1-Number(fields.discount.value)/100;
   const estimate=lineValues().reduce((total,line)=>total+Math.round(Number(line.price?.price||0)*line.quantity*factor*100),0)/100;
   form.querySelector('[data-subtotal]').textContent='Produtos com desconto · '+money(estimate);
  }
  function addLine(saved={}){
   items.insertAdjacentHTML('beforeend',`<div class="row" data-line>
    <label>Marca<select data-brand required></select></label><label>SKU e tabela<select data-sku required></select></label>
    <label>Quantidade<input data-quantity type="number" min="1" max="100000" value="${saved.quantity||1}" required></label>
    <button type="button" class="secondary" data-remove>Remover item</button></div>`);
   const row=items.lastElementChild;
   const brands=row.querySelector('[data-brand]'), skus=row.querySelector('[data-sku]');
   function choices(brand,sku,tableId){
    const source=catalog();
    brands.innerHTML=selectOptions([...new Set(source.map(p=>p.brand))].sort().map(name=>[name,name]));
    brands.value=brand||'';
    skus.replaceChildren(new Option('Selecione o SKU',''));
    for(const price of source.filter(p=>p.brand===brands.value)){
     const table=(s.priceTables||[]).find(t=>t.id===catalogTableId(price));
     const option=new Option(`${price.sku} · ${price.description||''} · ${table?.title||catalogTableId(price)} · ${money(price.price)}`, String(skus.options.length));
     option.price=price;skus.add(option);
     if(price.sku===sku&&catalogTableId(price)===tableId)skus.value=option.value;
    }
    subtotal();
   }
   row.refresh=()=>{const price=skus.selectedOptions[0]?.price;choices(brands.value,price?.sku,price&&catalogTableId(price));};
   brands.onchange=()=>choices(brands.value);
   row.querySelector('[data-remove]').onclick=()=>{row.remove();subtotal();};
   choices(saved.brand,saved.sku,saved.tableId);
  }
  function customerChanged(resetTable){
   const scope=fields.source.value;
   form.querySelectorAll('[data-source]').forEach(node=>{node.hidden=node.dataset.source!==scope;});
   const customer=scope==='client'?s.clients.find(c=>c.id===fields.clientId.value):
    scope==='lead'?(s.leads||[]).find(c=>c.id===fields.leadId.value):null;
   const purchases=(s.orders||[]).filter(order=>order.clientId===customer?.id&&['Faturado','Entregue'].includes(order.status))
    .sort((a,b)=>String(b.date).localeCompare(String(a.date)));
   const last=purchases[0];
   form.querySelector('[data-customer]').textContent=customer?[customer.taxId,customer.city,stateCode(customer),
    last?'Última compra '+last.date+' · '+money(last.amount):''].filter(Boolean).join(' · '):'';
   if(resetTable){
    set('priceTable',(customer&&stateCode(customer))||(scope==='guest'?fields.guestState.value:'PA'));
    if(!fields.terms.value&&last?.paymentTerms)set('terms',last.paymentTerms);
    refreshItems();
   }
  }
  function refreshItems(){items.querySelectorAll('[data-line]').forEach(row=>row.refresh());}
  for(const name of ['source','clientId','leadId','guestState'])fields[name].onchange=()=>customerChanged(true);
  for(const name of ['priceTable','channel'])fields[name].onchange=refreshItems;
  form.addEventListener('input',subtotal);
  form.querySelector('[data-add]').onclick=()=>addLine();
  customerChanged(false);
  (original.items||[{}]).forEach(addLine);
  form.onsubmit=async event=>{
   event.preventDefault();
   const button=form.querySelector('[data-save]'), out=form.querySelector('[role=status]');
   const selected=lineValues();
   if(!selected.length||selected.some(line=>!line.price)){out.textContent='Selecione pelo menos um SKU e a quantidade.';return;}
   const data={sellerResponsible:fields.seller.value,priceTable:fields.priceTable.value,channel:fields.channel.value,
    paymentTerms:fields.terms.value,discountPercent:fields.discount.value,validityDays:Number(fields.validity.value),notes:fields.notes.value,
    opportunityId:fields.clientId.value===original.clientId?original.opportunityId||'':'',
    items:selected.map(({price,quantity})=>({brand:price.brand,tableId:catalogTableId(price),sku:price.sku,quantity}))};
   if(fields.source.value==='client') data.clientId=fields.clientId.value;
   else if(fields.source.value==='lead') data.leadId=fields.leadId.value;
   else data.guest={name:fields.guestName.value,taxId:fields.guestTaxId.value,city:fields.guestCity.value,state:fields.guestState.value};
   button.disabled=true;
   try{await request(id?'/quotes/'+id:'/quotes',id?'PUT':'POST',data);el.close();await load();}
   catch(e){out.textContent=e.message;}
   finally{button.disabled=false;}
  };
 }
 function removeDraft(id){
  const el=dialog('<h3>Excluir rascunho?</h3><p role="status"></p><button type="button" data-delete>Excluir</button> <button type="button" class="secondary" data-close>Cancelar</button>');
  el.querySelector('[data-delete]').onclick=async()=>{
   try{await request('/quotes/'+id,'DELETE');el.close();await load();}
   catch(e){el.querySelector('[role=status]').textContent=e.message;}
  };
 }
 async function openRules(scope='propostas'){
  try{
   const rules=await (await request('/inactivity/'+scope)).json();
   const el=dialog(`<form><h3>Regras de inatividade</h3>
   <label>Aplicar em<select name="scope"><option value="propostas">Propostas</option><option value="negocios">Negócios</option></select></label>
   <div class="row">${[['atencao','Atenção'],['risco','Em risco'],['critico','Crítico']].map(([key,label])=>`<label>${label} (dias)<input type="number" min="1" max="3650" required name="dias_${key}" value="${rules['dias_'+key]}"></label>`).join('')}</div>
   <fieldset><legend>O que conta como contato de verdade</legend>
   ${Object.entries(actionLabels).map(([key,label])=>`<label><input type="checkbox" name="actions" value="${key}" ${rules.acoes_validas.includes(key)?'checked':''}> ${label}</label>`).join('')}
   </fieldset><p role="status"></p><button>Salvar</button> <button type="button" class="secondary" data-close>Fechar</button></form>`);
   const form=el.querySelector('form');form.elements.scope.value=scope;
   form.elements.scope.onchange=()=>openRules(form.elements.scope.value);
   form.onsubmit=async event=>{
    event.preventDefault();
    const fields=new FormData(form);
    try{
     await request('/inactivity/'+scope,'PUT',{dias_atencao:Number(fields.get('dias_atencao')),
      dias_risco:Number(fields.get('dias_risco')),dias_critico:Number(fields.get('dias_critico')),acoes_validas:fields.getAll('actions')});
     el.close(); await load();
    }catch(e){form.querySelector('[role=status]').textContent=e.message;}
   };
  }catch(e){status(e.message);}
 }
 function openContact(scope,id){
  const el=dialog(`<form><h3>Registrar contato</h3>
  <label>Ação<select name="acao">${Object.entries(actionLabels).map(([key,label])=>`<option value="${key}">${label}</option>`).join('')}</select></label>
  <label>Canal<select name="canal">${Object.entries(channels).map(([key,label])=>`<option value="${key}">${label}</option>`).join('')}</select></label>
  <label>O que aconteceu?<textarea name="evidencia" minlength="3" maxlength="1000" required></textarea></label>
  <p role="status"></p><button>Salvar contato</button> <button type="button" class="secondary" data-close>Fechar</button></form>`);
  const form=el.querySelector('form');
  form.onsubmit=async event=>{
   event.preventDefault();
   try{
    await request('/contact/'+scope+'/'+encodeURIComponent(id),'POST',Object.fromEntries(new FormData(form)));
    el.close(); await load();
   }catch(e){form.querySelector('[role=status]').textContent=e.message;}
  };
 }
 async function attach(id){
  try{
   const clientId=document.getElementById('cpqAttach-'+id).value;
   await request('/quotes/'+id+'/attach','POST',{clientId});
   await load();
  }catch(e){status(e.message);}
 }
 async function share(id,channel){
  try{
   const result=await (await request('/quotes/'+id+'/links/'+channel,'POST')).json();
   const url=new URL(result.path,s.server||location.origin).href;
   const q=quotes.find(record=>record.id===id);
   const message=`Olá! Segue a proposta #${result.number} da L2 Representações para ${quoteName(q)}. Acesse o PDF e responda por aqui: ${url}`;
   if(channel==='link_direto'){
    try{await navigator.clipboard.writeText(url);status('Link rastreado copiado.');return;}catch{}
   }
   const whatsapp='https://wa.me/?text='+encodeURIComponent(message);
   const email='mailto:?subject='+encodeURIComponent('Proposta L2 #'+result.number)+'&body='+encodeURIComponent(message);
   const destination=channel==='whatsapp'?whatsapp:email;
   dialog(`<h3>${channel==='link_direto'?'Link rastreado':'Compartilhar proposta'}</h3>
    <label>Link da proposta<input readonly value="${esc(url)}" onclick="this.select()"></label>
    ${channel==='link_direto'?'':`<p><a href="${esc(destination)}" target="_blank" rel="noopener noreferrer">Abrir ${channels[channel]}</a></p>`}
    <button type="button" class="secondary" data-close>Fechar</button>`);
  }catch(e){status(e.message);}
 }
 async function refreshTracking(){
  if(!document.getElementById('cpqWorkspace'))return;
  try{
   const [proposals,business]=await Promise.all(['propostas','negocios'].map(async scope=>(await request('/activity/'+scope)).json()));
   if(loading)return;
   proposalActivity=proposals;businessActivity=business;
   const latest=new Map(proposals.quotes.map(q=>[q.id,q]));
   for(const card of document.querySelectorAll('[data-cpq-quote]')){
    const q=latest.get(card.dataset.cpqQuote);
    if(!q)continue;
    card.dataset.cpqState=q.proposalStatus;
    card.querySelector('[data-cpq-status]').textContent=q.proposalStatus;
    card.querySelector('[data-cpq-views]').textContent=viewLabel(q);
    if(q.acceptedAt&&q.clientId&&!q.convertedOrderId)card.querySelector('[data-cpq-convert]')?.removeAttribute('disabled');
   }
   decorate();
  }catch{}
 }
 async function loadAlerts(){
  const sequence=++alertSequence, token=s.token;
  let bell=document.getElementById('cpqBell');
  if(!token||s.mustChangePassword){bell?.remove();return;}
  try{
   const pending=await (await request('/alerts')).json();
   if(token!==s.token||sequence!==alertSequence)return;
   if(!bell){
    bell=document.createElement('button');
    bell.id='cpqBell';bell.type='button';bell.className='install-button';bell.setAttribute('aria-label','Avisos de propostas');
    bell.setAttribute('aria-live','polite');
    document.querySelector('.header-actions')?.append(bell);
   }
   bell.textContent=pending.length?'🔔 '+pending.length:'🔔';
   bell.onclick=()=>dialog(`<h3>Aberturas de propostas</h3>${pending.map(a=>`<p>${esc(a.client)} abriu a proposta #${a.number} via ${channels[a.channel]} · ${new Date(a.at).toLocaleString('pt-BR')}
    <button type="button" class="secondary" onclick="L2CPQ.readAlert(${a.id})">Marcar como lido</button></p>`).join('')||'<p>Nenhuma abertura pendente.</p>'}
    <p role="status"></p><button type="button" class="secondary" data-close>Fechar</button>`);
  }catch{}
 }
 async function readAlert(id){
  try{await request('/alerts/read/'+id,'POST');await loadAlerts();document.getElementById('cpqBell')?.click();}
  catch(e){const out=document.querySelector('#cpqDialog [role=status]');if(out)out.textContent=e.message;}
 }
 function bind(){loadAlerts();clearInterval(ageTimer);if(!['crm','pedidos'].includes(tab)||!s.token)return;const app=document.getElementById('app');if(!app||document.getElementById('cpqWorkspace'))return;app.insertAdjacentHTML('beforeend',panel());const form=document.getElementById('crmOpportunityForm');if(form){form.elements.clientId.addEventListener('change',fillSelector);form.elements.owner.addEventListener('change',fillSelector);}load();ageTimer=setInterval(decorate,60000);if(tab==='crm')loadForecast();}
 return {bind,panel,load,create,decide,pdf,action,loadMix,loadForecast,openRules,openContact,filter,decorate,share,attach,loadAlerts,readAlert,refreshTracking,compose,removeDraft};
})();
