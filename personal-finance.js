/* Shared household finance. Memory only: never use corporate state, queue or localStorage. */
(() => {
  let data=null, generation=0, loading=false, editId='', namedEdit=null;
  const filters={start:'',end:'',category:'',account:''};
  const colors=['#17665f','#bf9656','#5e9d93','#826f93','#bd7762','#79934d','#6a86a2','#b7899c'];
  function authorized(){return !!s.token&&!s.mustChangePassword&&['Ana Paula','Euler'].includes(s.user)}
  window.personalFinanceClear=()=>{data=null;editId='';namedEdit=null;loading=false;generation++};
  function dates(){if(!filters.start){const d=today();filters.start=d.slice(0,8)+'01';filters.end=d}}
  async function request(path, options={}){
    if(!authorized())throw Error('Acesso restrito aos sócios');
    if(!navigator.onLine)throw Error('Conecte-se à internet para acessar o Financeiro Pessoal');
    const token=s.token,user=s.user;
    const headers={Authorization:'Bearer '+token,...options.headers};
    if(options.body&&!(options.body instanceof FormData))headers['Content-Type']='application/json';
    const response=await fetch(s.server+'/api/personal-finance'+path,{...options,headers,cache:'no-store'});
    if(token!==s.token||user!==s.user||!authorized())throw Error('Acesso alterado; dados pessoais descartados');
    if(!response.ok){const e=await response.json().catch(()=>({}));throw Error(typeof e.detail==='string'?e.detail:'Confira os campos e as datas do lançamento')}
    return response;
  }
  async function load(){
    dates(); const epoch=++generation;loading=true;
    if(tab==='personal')$('app').innerHTML='<div class="box"><h2>Financeiro Pessoal</h2><p>Carregando os registros do casal…</p></div>';
    try{
      const params=new URLSearchParams(filters); const result=await (await request('/overview?'+params)).json();
      if(epoch!==generation||tab!=='personal'||!authorized())return;
      data=result;loading=false;draw();
    }catch(e){if(epoch!==generation||tab!=='personal')return;data=null;loading=false;$('app').innerHTML='<div class="box"><h2>Financeiro Pessoal</h2><p>'+esc(e.message)+'</p><button onclick="personalRefresh()">Tentar novamente</button></div>'}
  }
  window.personalRefresh=()=>{editId='';namedEdit=null;load()};
  window.renderPersonalFinance=()=>{if(!authorized()){window.personalFinanceClear();$('app').textContent='Acesso restrito aos sócios';return}if(!navigator.onLine){window.personalFinanceClear();$('app').innerHTML='<div class="box"><h2>Financeiro Pessoal</h2><p>Disponível somente online, para proteger os dados do casal.</p></div>';return}if(!loading)load()};
  const opt=(rows,value)=>rows.map(x=>'<option value="'+esc(x.id)+'" '+(x.id===value?'selected':'')+'>'+esc(x.name)+(x.active?'':' (inativa)')+'</option>').join('');
  function pie(summary){
    const entries=Object.entries(summary.categories).filter(([,v])=>Number(v)>0),total=entries.reduce((n,[,v])=>n+Number(v),0);let angle=0;
    const gradient=entries.map(([,v],i)=>{const start=angle;angle+=Number(v)/total*100;return colors[i%colors.length]+' '+start+'% '+angle+'%'}).join(',');
    return '<div class="box"><h3>Despesas pagas por categoria</h3>'+(total?'<div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap"><div role="img" aria-label="Distribuição das despesas pessoais por categoria; valores na legenda" style="width:170px;height:170px;border-radius:50%;background:conic-gradient('+gradient+')"></div><div>'+entries.map(([id,value],i)=>'<p><span style="display:inline-block;width:12px;height:12px;background:'+colors[i%colors.length]+'"></span> '+esc(data.categories.find(x=>x.id===id)?.name||'Categoria')+' · <b>'+money(value)+'</b> · '+(Number(value)/total*100).toFixed(1)+'%</p>').join('')+'</div></div>':'<p>Sem despesas pagas neste período.</p>')+'</div>';
  }
  function draw(){
    if(!data||tab!=='personal'||!authorized())return;
    const z=data.summary,entry=data.transactions.find(x=>x.id===editId);
    const categoryOptions=data.categories.filter(x=>x.active||x.id===entry?.category_id),accountOptions=data.accounts.filter(x=>x.active||x.id===entry?.account_id);
    $('app').innerHTML='<div class="page-heading"><div><span class="eyebrow">Ana Paula e Euler</span><h2>Financeiro Pessoal</h2><p>Registros compartilhados do casal · separados do financeiro da empresa</p></div><div><button onclick="personalRefresh()">Atualizar</button> <button class="secondary" onclick="personalTrash()">Lixeira</button></div></div>'+
      '<div class="box"><form id="personalFilters"><div class="row"><label>De<input name="start" type="date" required value="'+esc(filters.start)+'"></label><label>Até<input name="end" type="date" required value="'+esc(filters.end)+'"></label><label>Categoria<select name="category"><option value="">Todas</option>'+opt(data.categories,filters.category)+'</select></label><label>Conta / cartão<select name="account"><option value="">Todas</option>'+opt(data.accounts,filters.account)+'</select></label></div><button>Aplicar filtros</button></form><p class="muted">Pagos por data de pagamento/recebimento; pendentes por vencimento ou data da transação. Retiradas mostram o total empresarial do período, independente dos filtros pessoais.</p></div>'+
      '<div class="cards"><div class="card">Gastos pessoais pagos<strong>'+money(z.expenses)+'</strong></div><div class="card">Receitas recebidas<strong>'+money(z.income)+'</strong></div><div class="card">Despesas pendentes<strong>'+money(z.pending)+'</strong></div><div class="card">Retirado da empresa<strong>'+money(z.withdrawn)+'</strong></div><div class="card">Receitas − gastos no período<strong>'+money(z.net)+'</strong></div><div class="card">Retiradas − gastos pessoais<strong>'+money(z.withdrawal_difference)+'</strong></div></div>'+pie(z)+
      '<div class="box"><h3>'+(entry?'Editar lançamento':'Novo lançamento')+'</h3><form id="personalTransaction"><div class="row">'+
      '<label>Data da transação<input type="date" name="date" required value="'+esc(entry?.date||today())+'"></label><label>Tipo<select name="type">'+options(['Despesa Pessoal','Receita Pessoal'],entry?.type||'Despesa Pessoal')+'</select></label>'+
      '<label>Categoria pessoal<select name="category_id" required><option value="">Selecione</option>'+opt(categoryOptions,entry?.category_id)+'</select></label><label>Conta / cartão de origem<select name="account_id" required><option value="">Selecione</option>'+opt(accountOptions,entry?.account_id)+'</select></label>'+
      '<label>Valor (R$)<input name="amount" inputmode="decimal" placeholder="0,00" required value="'+esc(entry?formatBRLInput(entry.amount):'')+'"></label><label>Status<select name="status">'+options(['Pago','Pendente'],entry?.status||'Pago')+'</select></label>'+
      '<label>Vencimento<input name="due_date" type="date" value="'+esc(entry?.due_date||'')+'"></label><label>Data de pagamento / recebimento<input name="paid_date" type="date" value="'+esc(entry?.paid_date||today())+'"></label><label>Referente a<select name="person">'+options(['Casal','Ana Paula','Euler'],entry?.person||'Casal')+'</select></label></div>'+
      '<label>Vínculo opcional com retirada da empresa<select name="withdrawal_id"><option value="">Sem vínculo</option>'+data.withdrawals.map(x=>'<option value="'+esc(x.id)+'" '+(x.id===entry?.withdrawal_id?'selected':'')+'>'+esc(x.date+' · '+x.kind+' · '+x.description)+' · saldo '+money(x.available)+'</option>').join('')+(entry?.withdrawal_id&&!data.withdrawals.some(x=>x.id===entry.withdrawal_id)?'<option selected value="'+esc(entry.withdrawal_id)+'">Retirada alterada ou estornada; remova ou revise o vínculo</option>':'')+'</select></label><p class="muted">Para conciliar, registre no Caixa diário uma saída na categoria Pró-labore ou Distribuição de Lucros e identifique Ana Paula, Euler ou Casal na descrição. O vínculo não cria outra saída na empresa.</p>'+
      '<label>Observações<textarea name="notes" maxlength="4000">'+esc(entry?.notes||'')+'</textarea></label><label>Anexos · PDF, JPG ou PNG, até 5 MB cada<input name="attachments" type="file" accept=".pdf,.jpg,.jpeg,.png" multiple></label><p class="muted">Em receitas, Pago significa recebido. Registre compras no cartão uma vez; não repita os mesmos gastos no pagamento da fatura.</p><button id="personalSave">Salvar lançamento</button> <button type="button" class="secondary" onclick="personalNew()">Novo / cancelar edição</button><p id="personalNotice" role="status"></p></form></div>'+
      '<div class="box"><h3>Lançamentos do período · '+data.transactions.length+'</h3><div class="scroll"><table><tr><th>Data / pagamento</th><th>Tipo / categoria</th><th>Conta / pessoa</th><th>Valor / status</th><th>Registro / conciliação</th><th>Ações</th></tr>'+data.transactions.map(x=>'<tr><td>'+esc(x.date)+'<br>'+esc(x.paid_date||x.due_date||'Pendente')+'</td><td>'+esc(x.type)+'<br>'+esc(data.categories.find(c=>c.id===x.category_id)?.name)+'</td><td>'+esc(data.accounts.find(c=>c.id===x.account_id)?.name)+'<br>'+esc(x.person)+'</td><td>'+money(x.amount)+'<br>'+esc(x.status)+'</td><td>Por '+esc(x.created_by)+(x.withdrawal_id?'<br>'+(x.reconciliation_warning?'⚠ Revisar retirada vinculada':'Retirada conciliada'):'')+(x.notes?'<br>'+esc(x.notes):'')+x.attachments.map(a=>'<br><button class="secondary" onclick="personalDownload(\''+esc(a.id)+'\')">'+esc(a.name)+'</button>').join('')+'</td><td><button onclick="personalEdit(\''+esc(x.id)+'\')">Editar</button> <button class="secondary" onclick="personalDelete(\''+esc(x.id)+'\')">Excluir</button></td></tr>').join('')+(data.transactions.length?'':'<tr><td colspan="6">Nenhum lançamento neste período.</td></tr>')+'</table></div></div>'+namedPanel();
    $('personalFilters').onsubmit=e=>{e.preventDefault();Object.assign(filters,Object.fromEntries(new FormData(e.target)));editId='';load()};
    $('personalTransaction').onsubmit=saveTransaction;
    const form=$('personalTransaction');form.elements.status.onchange=()=>{form.elements.paid_date.value=form.elements.status.value==='Pago'?today():''};
    form.elements.type.onchange=()=>{if(form.elements.type.value!=='Receita Pessoal')form.elements.withdrawal_id.value=''};
    $('personalNamed').onsubmit=saveNamed;
  }
  function namedPanel(){
    const target=namedEdit?.target||'categories',item=namedEdit?.item;
    return '<div class="box"><h3>Categorias e contas pessoais</h3><form id="personalNamed"><div class="row"><label>Cadastro<select name="target" '+(item?'disabled':'')+'>'+[['categories','Categoria'],['accounts','Conta / cartão']].map(([id,label])=>'<option value="'+id+'" '+(target===id?'selected':'')+'>'+label+'</option>').join('')+'</select></label><label>Nome<input name="name" maxlength="100" required value="'+esc(item?.name||'')+'"></label><label>Tipo de conta<select name="kind">'+options(['Banco','Dinheiro','Cartão'],item?.kind||'Banco')+'</select></label></div><button>'+(item?'Salvar alteração':'Adicionar')+'</button> <button type="button" class="secondary" onclick="personalNamedNew()">Novo</button><p id="personalNamedNotice" role="status"></p></form>'+['categories','accounts'].map(t=>'<h4>'+(t==='categories'?'Categorias':'Contas / cartões')+'</h4><div class="scroll"><table><tr><th>Nome</th><th>Situação</th><th>Ações</th></tr>'+data[t].map(x=>'<tr><td>'+esc(x.name)+(t==='accounts'?' · '+esc(x.kind):'')+'</td><td>'+(x.active?'Ativa':'Inativa')+'</td><td><button onclick="personalNamedEdit(\''+t+'\',\''+esc(x.id)+'\')">Editar</button> <button class="secondary" onclick="personalNamedToggle(\''+t+'\',\''+esc(x.id)+'\')">'+(x.active?'Desativar':'Reativar')+'</button></td></tr>').join('')+'</table></div>').join('')+'</div>';
  }
  window.personalNew=()=>{editId='';draw()};
  window.personalEdit=id=>{editId=id;draw();$('personalTransaction').scrollIntoView({behavior:'smooth',block:'start'})};
  window.personalNamedNew=()=>{namedEdit=null;draw()};
  window.personalNamedEdit=(target,id)=>{namedEdit={target,item:data[target].find(x=>x.id===id)};draw();$('personalNamed').scrollIntoView({behavior:'smooth',block:'start'})};
  window.personalNamedToggle=async(target,id)=>{const item=data[target].find(x=>x.id===id);try{await request('/'+target+'/'+encodeURIComponent(id),{method:'PUT',body:JSON.stringify({...item,active:!item.active})});namedEdit=null;load()}catch(e){alert(e.message)}};
  async function saveNamed(e){e.preventDefault();const f=e.target,body=Object.fromEntries(new FormData(f));const target=namedEdit?.target||body.target;try{await request('/'+target+(namedEdit?'/'+encodeURIComponent(namedEdit.item.id):''),{method:namedEdit?'PUT':'POST',body:JSON.stringify({...body,active:namedEdit?.item.active??true,version:namedEdit?.item.version||0})});namedEdit=null;load()}catch(err){$('personalNamedNotice').textContent=err.message}}
  async function saveTransaction(e){
    e.preventDefault();const form=e.target,body=Object.fromEntries(new FormData(form)),files=[...form.elements.attachments.files],previous=data.transactions.find(x=>x.id===editId);
    const cents=parseBRLCents(body.amount);if(!Number.isSafeInteger(cents)||cents<=0){$('personalNotice').textContent='Informe valor positivo em reais';return}
    if(files.length>5||files.some(f=>f.size>5*1024*1024||!f.size)){ $('personalNotice').textContent='Selecione até cinco anexos de até 5 MB';return}
    body.amount=(cents/100).toFixed(2);body.version=previous?.version||0;for(const k of ['due_date','paid_date','withdrawal_id'])body[k]=body[k]||null;delete body.attachments;
    $('personalSave').disabled=true;
    let saved=null;
    try{saved=await (await request('/transactions'+(editId?'/'+encodeURIComponent(editId):''),{method:editId?'PUT':'POST',body:JSON.stringify(body)})).json();
      for(const file of files){const fd=new FormData();fd.append('file',file);await request('/transactions/'+encodeURIComponent(saved.id)+'/attachments',{method:'POST',body:fd})}
      editId='';await load();
    }catch(err){if(saved){editId='';await load();alert('Lançamento salvo. Anexo pendente: '+err.message+'. Abra Editar para reenviar.')}else if($('personalNotice'))$('personalNotice').textContent=err.message}
    finally{if($('personalSave'))$('personalSave').disabled=false}
  }
  window.personalDelete=async id=>{const row=data.transactions.find(x=>x.id===id);if(!confirm('Excluir este lançamento pessoal? O registro fica preservado para recuperação.'))return;try{await request('/transactions/'+encodeURIComponent(id)+'?version='+row.version,{method:'DELETE'});editId='';load()}catch(e){alert(e.message)}};
  window.personalTrash=async()=>{try{const rows=await (await request('/deleted')).json();if(tab!=='personal')return;$('app').innerHTML='<div class="box"><h2>Lixeira pessoal</h2><button onclick="personalRefresh()">Voltar</button><div class="scroll"><table><tr><th>Data</th><th>Tipo</th><th>Valor</th><th>Ação</th></tr>'+rows.map(x=>'<tr><td>'+esc(x.date)+'</td><td>'+esc(x.type)+'</td><td>'+money(x.amount)+'</td><td><button onclick="personalRestore(\''+esc(x.id)+'\')">Restaurar</button></td></tr>').join('')+(rows.length?'':'<tr><td colspan="4">Lixeira vazia.</td></tr>')+'</table></div></div>'}catch(e){alert(e.message)}};
  window.personalRestore=async id=>{try{await request('/transactions/'+encodeURIComponent(id)+'/restore',{method:'POST'});load()}catch(e){alert(e.message)}};
  window.personalDownload=async(id)=>{const name=data.transactions.flatMap(x=>x.attachments).find(x=>x.id===id)?.name||"comprovante";try{const blob=await (await request('/attachments/'+encodeURIComponent(id))).blob();downloadBlob(blob,name)}catch(e){alert(e.message)}};
  window.addEventListener('offline',()=>{if(tab==='personal')window.renderPersonalFinance()});
  window.addEventListener('pagehide',window.personalFinanceClear);
})();
