
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const today=()=>new Date().toLocaleDateString('sv-SE'), money=n=>Number(n||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
const key='l2one_state_v2', defaults={clients:[],visits:[],orders:[],tasks:[],goals:[],routes:[],user:'Ana Paula',server:'',token:'',syncAt:'',pending:[]};
let s=Object.assign({},defaults,JSON.parse(localStorage.getItem(key)||'{}')),tab='hoje',syncing=false;
const norm=v=>String(v||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim();
const stateCode=c=>{let uf=norm(c.state).replace(/\s/g,'');if(uf==='pa'||uf==='para')return'PA';if(uf==='ap'||uf==='amapa')return'AP';return uf?'FORA':''};
const regionClient=c=>stateCode(c)!=='FORA';
const daysSince=value=>{if(!value)return null;let d=new Date(value+'T12:00:00');return Number.isNaN(d.getTime())?null:Math.max(0,Math.floor((Date.now()-d.getTime())/864e5))};
function lastVisitDays(id){let dates=s.visits.filter(v=>v.clientId===id&&v.result!=='Não visitado').map(v=>v.date).filter(Boolean).sort();return dates.length?daysSince(dates[dates.length-1]):null}
function priority(c){let purchase=daysSince(c.last_purchase),visit=lastVisitDays(c.id),score=0,reasons=[];if(purchase===null){score+=260;reasons.push('sem histórico de compra')}else if(purchase>=120){score+=520;reasons.push(purchase+' dias sem compra')}else if(purchase>=90){score+=430;reasons.push(purchase+' dias sem compra')}else if(purchase>=60){score+=330;reasons.push(purchase+' dias sem compra')}else if(purchase>=30){score+=220;reasons.push(purchase+' dias sem compra')}else{score+=80;reasons.push('compra recente')}if(visit===null){score+=180;reasons.push('sem visita registrada')}else if(visit>=60){score+=160;reasons.push(visit+' dias sem visita')}else if(visit>=30){score+=100;reasons.push(visit+' dias sem visita')}if(c.address)score+=20;return{score,reason:reasons.join(' · ')}}
function locationLabel(c){return[c.address,c.district,c.city,stateCode(c)||c.state].filter(Boolean).join(' · ')}
function addressTokens(c){return new Set(norm(c.address).split(' ').filter(x=>x.length>2&&!['rua','avenida','travessa','rodovia'].includes(x)))}
function suggestedClients(day,limit=8){
 const attended=new Set(s.visits.filter(v=>v.date===day&&v.result!=='Não visitado').map(v=>v.clientId));
 const scheduled=new Set(s.routes.filter(r=>r.date===day).map(r=>r.clientId));
 let pool=s.clients.filter(c=>regionClient(c)&&!attended.has(c.id)&&!scheduled.has(c.id));
 if(!pool.length)return[];
 const clusters={};
 for(const c of pool){let key=norm(c.city)+'|'+norm(c.district);(clusters[key]||(clusters[key]=[])).push(c)}
 const best=Object.values(clusters).sort((a,b)=>b.reduce((t,c)=>t+priority(c).score,0)-a.reduce((t,c)=>t+priority(c).score,0)||b.length-a.length)[0];
 const seed=best.slice().sort((a,b)=>priority(b).score-priority(a).score)[0],seedTokens=addressTokens(seed),seedCity=norm(seed.city),seedDistrict=norm(seed.district);
 return pool.sort((a,b)=>{
   const proximity=c=>{let points=norm(c.city)===seedCity?2000:0;points+=norm(c.district)===seedDistrict?4000:0;for(const token of addressTokens(c))if(seedTokens.has(token))points+=100;return points+priority(c).score};
   return proximity(b)-proximity(a)||String(a.name).localeCompare(String(b.name),'pt-BR');
 }).slice(0,limit);
}
function save(){localStorage.setItem(key,JSON.stringify(s));network()}
function network(){let n=$('network');if(n)n.textContent=(navigator.onLine?'● Online':'● Offline')+(s.server?' · '+s.pending.length+' alterações pendentes':' · somente neste aparelho')}
function uid(){return crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random()}
function options(arr,val){return arr.map(v=>`<option ${v===val?'selected':''}>${esc(v)}</option>`).join('')}
function client(id){return s.clients.find(c=>c.id===id)||{}}
function show(t){tab=t;document.querySelectorAll('nav button').forEach(b=>b.style.opacity=b.dataset.tab===t?'1':'.6');render()}
function render(){let h='';if(tab==='hoje'){let v=s.visits.filter(x=>x.date===today()),o=s.orders.filter(x=>x.date===today()),due=s.tasks.filter(x=>x.status!=='Concluída'&&x.date<=today()),r=s.routes.filter(x=>x.date===today());h=`<h2>Meu dia · ${today()}</h2><div class="cards"><div class="card">Visitas realizadas<strong>${v.filter(x=>x.result!=='Não visitado').length}</strong></div><div class="card">Pedidos registrados<strong>${o.length}</strong></div><div class="card">Valor de pedidos<strong>${money(o.reduce((a,x)=>a+Number(x.amount||0),0))}</strong></div><div class="card">Pendências vencidas / hoje<strong>${due.length}</strong></div></div><div class="box"><h3>Rota de hoje</h3>${r.length?r.sort((a,b)=>a.order-b.order).map(x=>{let c=client(x.clientId);return `<p><b>${x.order}. ${esc(c.name)}</b> · ${esc(c.district)} · ${esc(c.city)} <button onclick="prefill('${esc(c.id)}')">Atender</button> <a target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent([c.address,c.district,c.city,c.state].join(' '))}">Mapa</a></p>`}).join(''):'Nenhum cliente na rota. Abra “Minha rota” para planejar.'}</div><div class="box"><h3>Próximas ações</h3>${due.slice(0,12).map(x=>`<p>${esc(x.date)} · ${esc(client(x.clientId).name)} · ${esc(x.text)} <button onclick="done('${x.id}')">Concluir</button></p>`).join('')||'Nenhuma pendência vencida.'}</div>`}
if(tab==='clientes'){let q=window.clientQuery||'';let list=s.clients.filter(c=>[c.name,c.city,c.district,c.phone,c.brands].join(' ').toLowerCase().includes(q.toLowerCase()));h=`<h2>Carteira de clientes (${s.clients.length})</h2><input id="q" placeholder="Buscar nome, bairro, cidade, marca ou contato" value="${esc(q)}" oninput="window.clientQuery=this.value;show('clientes')"><p class="muted">Exibindo ${Math.min(list.length,100)} de ${list.length}. Para editar, abra o cadastro.</p><div class="scroll"><table><tr><th>Cliente</th><th>Bairro / Cidade</th><th>Última compra</th><th>Ações</th></tr>${list.slice(0,100).map(c=>`<tr><td><b>${esc(c.name)}</b><br><small>${esc(c.brands)}</small></td><td>${esc(c.district)} / ${esc(c.city)}</td><td>${esc(c.last_purchase||'Não informada')}</td><td><button onclick="editClient('${esc(c.id)}')">Editar</button> <button onclick="prefill('${esc(c.id)}')">Atender</button></td></tr>`).join('')}</table></div><div class="box"><h3 id="clientFormTitle">Cadastrar novo cliente</h3><p class="muted">Informe a UF do cadastro para aplicar a tabela de preços correta nos pedidos.</p><form id="clientForm"><input type="hidden" name="id" id="cid"><div class="row"><label>Razão social / Nome<input name="name" id="c_name" required maxlength="180"></label><label>Nome fantasia<input name="tradeName" id="c_tradeName" maxlength="180"></label><label>CNPJ / CPF<input name="taxId" id="c_taxId" inputmode="numeric" maxlength="18"></label><label>Contato<input name="contact" id="c_contact"></label><label>WhatsApp<input name="phone" id="c_phone" type="tel"></label><label>E-mail<input name="email" id="c_email" type="email"></label><label>Endereço<input name="address" id="c_address"></label><label>Bairro<input name="district" id="c_district"></label><label>Cidade<input name="city" id="c_city" required></label><label>UF<select name="state" id="c_state" required><option value="">Selecione</option><option value="PA">Pará (PA)</option><option value="AP">Amapá (AP)</option></select></label><label>Canal<input name="channel" id="c_channel" placeholder="Ex.: Farma"></label><label>Marcas<input name="brands" id="c_brands"></label><label>Última compra<input name="last_purchase" id="c_last_purchase" type="date"></label></div><button type="submit">Salvar cliente</button> <button type="button" class="secondary" onclick="newClient()">Limpar / novo cadastro</button></form></div>`}
if(tab==='rota'){let day=window.routeDay||today(),route=s.routes.filter(x=>x.date===day).sort((a,b)=>a.order-b.order),missing=s.clients.filter(c=>!c.state).length;if(!route.length&&!window.routeBuilding&&window.routeAttemptedDay!==day){window.routeBuilding=true;setTimeout(()=>autoRoute(day),0)}let first=route.length?client(route[0].clientId):null;h=`<h2>Rota inteligente</h2><p class="muted">O L2 ONE escolhe automaticamente 8 atendimentos por proximidade e prioridade: cidade → bairro → endereço → tempo sem compra → tempo sem visita.</p><div class="row"><label>Data<input type="date" id="routeDay" value="${day}" onchange="window.routeDay=this.value;window.routeBuilding=false;window.routeAttemptedDay='';show('rota')"></label><div><button onclick="autoRoute('${day}',true)">Atualizar sugestão automática</button></div></div>${missing?`<p class="warn">${missing} cliente(s) estão sem UF e foram preservados para conferência. Cadastros com UF fora de PA/AP são excluídos da carteira.</p>`:''}<div class="box"><h3>Localidade sugerida</h3>${first?`<p><b>${esc(first.district||'Bairro não informado')} · ${esc(first.city||'Cidade não informada')} / ${esc(stateCode(first)||first.state||'UF pendente')}</b></p><p class="muted">A sequência abaixo prioriza clientes próximos desta localidade.</p>`:window.routeBuilding?'Calculando a melhor localidade e os atendimentos...':'Nenhum cliente disponível para esta data.'}</div><div class="box"><h3>Atendimentos sugeridos (${route.length}/8)</h3>${route.map(x=>{let c=client(x.clientId),p=priority(c),query=[c.address,c.district,c.city,stateCode(c)||c.state].filter(Boolean).join(' ');return `<div class="card"><b>${x.order}. ${esc(c.name)}</b><p>${esc(locationLabel(c)||'Endereço pendente')}</p><p class="muted">Prioridade: ${esc(p.reason)}</p><button onclick="prefill('${esc(c.id)}')">Iniciar atendimento</button> <a target="_blank" rel="noopener" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}">Ver localização no mapa</a></div>`}).join('')||'A sugestão será criada automaticamente quando a carteira estiver sincronizada.'}</div>`}
if(tab==='registro'){let id=window.prefillId||'';h=`<h2>Registrar atendimento</h2><form id="visitForm" class="box"><div class="row"><label>Data<input name="date" type="date" value="${today()}" required></label><label>Cliente<select name="clientId" required><option value="">Selecione</option>${s.clients.map(c=>`<option value="${esc(c.id)}" ${c.id===id?'selected':''}>${esc(c.name)}</option>`).join('')}</select></label><label>Marca<input name="brand" placeholder="Ex.: Bruna Tavares"></label><label>Resultado<select name="result">${options(['Visitado - pedido','Visitado - sem pedido','Não visitado','Reagendado','Contato remoto'],'Visitado - pedido')}</select></label><label>Valor do pedido (R$)<input name="amount" type="number" step="0.01" min="0" placeholder="0,00"></label><label>Status do pedido<select name="orderStatus">${options(['Pendente','Confirmado','Faturado'],'Pendente')}</select></label><label>Próxima ação<input name="next" placeholder="Ex.: Enviar proposta"></label><label>Data de retorno<input name="returnDate" type="date"></label></div><label>Observações<textarea name="notes" rows="3"></textarea></label><p class="muted">O atendimento será registrado. A criação de novos pedidos por valor total está suspensa até a disponibilização do formulário de itens e preços por UF.</p><button>Salvar atendimento</button></form><div class="box"><h3>Últimos atendimentos</h3>${s.visits.slice(-12).reverse().map(v=>`<p>${esc(v.date)} · ${esc(client(v.clientId).name)} · ${esc(v.result)} · ${esc(v.user)}</p>`).join('')}</div>`}
if(tab==='pedidos'){let orders=s.orders.slice().reverse();h=`<h2>Pedidos</h2><div class="cards"><div class="card">Pedidos<strong>${orders.length}</strong></div><div class="card">Valor total registrado<strong>${money(orders.reduce((a,x)=>a+Number(x.amount),0))}</strong></div><div class="card">Faturado<strong>${money(orders.filter(x=>x.status==='Faturado').reduce((a,x)=>a+Number(x.amount),0))}</strong></div></div><div class="scroll"><table><tr><th>Data</th><th>Cliente</th><th>Marca</th><th>Valor</th><th>Status</th><th>Usuário</th></tr>${orders.slice(0,250).map(x=>`<tr><td>${esc(x.date)}</td><td>${esc(client(x.clientId).name)}</td><td>${esc(x.brand)}</td><td>${money(x.amount)}</td><td><select onchange="changeOrder('${x.id}',this.value)">${options(['Pendente','Confirmado','Faturado','Cancelado'],x.status)}</select></td><td>${esc(x.user)}</td></tr>`).join('')}</table></div>`}
if(tab==='gestao'){let month=window.reportMonth||today().slice(0,7),o=s.orders.filter(x=>x.date.startsWith(month)&&x.status!=='Cancelado'),vis=s.visits.filter(x=>x.date.startsWith(month)),byBrand={};o.forEach(x=>byBrand[x.brand||'Sem marca']=(byBrand[x.brand||'Sem marca']||0)+Number(x.amount));let total=o.reduce((a,x)=>a+Number(x.amount),0),goal=s.goals.find(x=>x.month===month&&x.user===s.user),pct=goal&&goal.amount?Math.min(100,total/Number(goal.amount)*100):0;let opportunities=s.clients.filter(c=>c.last_purchase&&((Date.now()-new Date(c.last_purchase).getTime())/864e5)>=30).sort((a,b)=>a.last_purchase.localeCompare(b.last_purchase));h=`<h2>Gestão e oportunidades</h2><label>Mês<input type="month" value="${month}" onchange="window.reportMonth=this.value;show('gestao')"></label><div class="cards"><div class="card">Pedidos no mês<strong>${o.length}</strong></div><div class="card">Valor registrado<strong>${money(total)}</strong></div><div class="card">Meta de ${esc(s.user)}<strong>${goal?money(goal.amount):'Não definida'}</strong><small>${goal?pct.toFixed(1)+'% alcançada':''}</small></div><div class="card">Atendimentos<strong>${vis.length}</strong></div><div class="card">Clientes com compra há 30+ dias<strong>${opportunities.length}</strong></div></div><div class="box"><h3>Definir meta mensal</h3><form id="goalForm"><div class="row"><label>Mês<input name="month" type="month" value="${month}" required></label><label>Responsável<select name="user">${options(['Ana Paula','Euler','Laís','Marlene'],s.user)}</select></label><label>Meta (R$)<input name="amount" type="number" min="0" step="0.01" value="${goal?esc(goal.amount):''}" required></label></div><button>Salvar meta</button></form></div><div class="box"><h3>Pedidos por marca</h3>${Object.entries(byBrand).map(([k,v])=>`<p>${esc(k)}: <b>${money(v)}</b></p>`).join('')||'Sem pedidos no período.'}</div><div class="box"><h3>Oportunidades de reposição</h3><p class="muted">Somente clientes com última compra informada; datas ausentes não são consideradas atraso.</p>${opportunities.slice(0,60).map(c=>`<p>${esc(c.name)} · ${esc(c.city)} · última compra ${esc(c.last_purchase)} <button onclick="prefill('${esc(c.id)}')">Atender</button></p>`).join('')||'Nenhum histórico de compra com atraso identificado.'}</div><div class="box"><h3>Nova pendência</h3><form id="taskForm"><div class="row"><label>Cliente<select name="clientId"><option value="">Geral</option>${s.clients.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select></label><label>Data<input name="date" type="date" value="${today()}"></label><label>Responsável<select name="user">${options(['Ana Paula','Euler','Laís','Marlene'],s.user)}</select></label><label>Ação<input name="text" required></label></div><button>Salvar pendência</button></form></div>`}
if(tab==='config'){h=`<h2>Configuração e sincronização</h2><div class="box"><p><b>Modo atual:</b> ${s.token?'Conectado como '+esc(s.user):(s.server?'Servidor configurado':'Local neste aparelho')}.</p>${s.token?`<div class="card"><b>✓ Acesso salvo neste aparelho</b><p class="muted">O L2 ONE continuará conectado automaticamente. A senha não fica exposta nem gravada em texto.</p><p><button onclick="sync()">Sincronizar agora</button> <button class="secondary" onclick="logout()">Sair / trocar usuário</button></p></div>`:`<label>Usuário ativo<select id="who" autocomplete="username">${options(['Ana Paula','Euler','Laís','Marlene'],s.user)}</select></label><label>URL do servidor (HTTPS)<input id="server" placeholder="https://seu-servidor.exemplo.com" value="${esc(s.server)}"></label><label>Senha individual<input id="password" type="password" autocomplete="current-password"></label><p><button onclick="connect()">Entrar e manter acesso</button></p><p class="muted">Após o primeiro login, este aparelho permanecerá conectado automaticamente. Use somente em aparelho pessoal ou da equipe.</p>`}<p>Alterações pendentes: ${s.pending.length} · Última sincronização: ${esc(s.syncAt||'Nunca')}</p><button class="secondary" onclick="exportData()">Exportar backup JSON</button> <label>Restaurar backup JSON<input type="file" accept=".json" onchange="importData(this.files[0])"></label></div>`}
$('app').innerHTML=h;let f=$('clientForm');if(f)f.onsubmit=saveClient;f=$('visitForm');if(f)f.onsubmit=saveVisit;f=$('taskForm');if(f)f.onsubmit=saveTask;f=$('goalForm');if(f)f.onsubmit=saveGoal;network()}
function queue(type,data){s.pending.push({type,data:structuredClone(data),changeId:uid()});save();if(s.server&&navigator.onLine)sync()}
function newClient(){show('clientes');$('clientForm').reset();$('cid').value='';$('clientFormTitle').textContent='Cadastrar novo cliente';$('c_name').focus()}
function saveClient(e){
 e.preventDefault();
 const d=Object.fromEntries(new FormData(e.target));
 d.name=d.name.trim();d.city=d.city.trim();d.state=stateCode(d);
 if(!d.name||!d.city||!['PA','AP'].includes(d.state)){alert('Informe nome, cidade e UF válida (PA ou AP).');return}
 const old=s.clients.findIndex(c=>c.id===d.id);
 const taxId=(d.taxId||'').replace(/\\D/g,'');
 if(taxId&&s.clients.some(c=>c.id!==d.id&&(c.taxId||'').replace(/\\D/g,'')===taxId)){alert('Já existe um cliente com este CNPJ/CPF. Confira o cadastro antes de salvar.');return}
 d.id=d.id||uid();
 const merged=old<0?d:{...s.clients[old],...d};
 if(old<0)s.clients.push(merged);else s.clients[old]=merged;
 queue('client',merged);show('clientes');
}
function editClient(id){
 show('clientes');let c=client(id);$('cid').value=id;
 for(let k of ['name','tradeName','taxId','contact','phone','email','address','district','city','state','channel','brands','last_purchase'])$('c_'+k).value=c[k]||'';
 $('c_state').value=stateCode(c)==='FORA'?'':stateCode(c);
 $('clientFormTitle').textContent='Editar cadastro do cliente';
 $('clientForm').scrollIntoView({behavior:'smooth',block:'start'});
}
function prefill(id){window.prefillId=id;show('registro')}
function saveVisit(e){e.preventDefault();let d=Object.fromEntries(new FormData(e.target));d.id=uid();d.user=s.user;d.amount=Number(d.amount||0);s.visits.push(d);queue('visit',d);if(d.amount>0){alert('Atendimento registrado. O pedido por valor total não foi criado: novos pedidos exigem itens e preços da tabela do estado do cliente.')}if(d.next){let t={id:uid(),clientId:d.clientId,text:d.next,date:d.returnDate||d.date,user:s.user,status:'Aberta'};s.tasks.push(t);queue('task',t)}window.prefillId='';show('hoje')}
function saveTask(e){e.preventDefault();let d=Object.fromEntries(new FormData(e.target));d.id=uid();d.status='Aberta';s.tasks.push(d);queue('task',d);show('hoje')}
function saveGoal(e){e.preventDefault();let d=Object.fromEntries(new FormData(e.target));d.amount=Number(d.amount||0);d.id=d.month+'-'+d.user.toLowerCase().replace(/[^a-z0-9]+/g,'-');let i=s.goals.findIndex(x=>x.id===d.id);if(i<0)s.goals.push(d);else s.goals[i]=d;queue('goal',d);window.reportMonth=d.month;show('gestao')}
function done(id){let t=s.tasks.find(x=>x.id===id);t.status='Concluída';queue('task',t);show('hoje')}
function addRoute(id){let d=window.routeDay||today(),n=s.routes.filter(x=>x.date===d).length;if(n>=10){alert('A rota já tem 10 clientes. Retire um para adicionar outro.');return}let r={id:uid(),date:d,clientId:id,order:n+1,user:s.user};s.routes.push(r);queue('route',r);show('rota')}
function removeRoute(id){s.routes=s.routes.filter(x=>x.id!==id);queue('delete_route',{id});show('rota')}
function autoRoute(day=today(),replace=false){
 window.routeAttemptedDay=day;
 if(!['Ana Paula','Euler'].includes(s.user)){alert('A roteirização é restrita aos representantes Ana Paula e Euler.');window.routeBuilding=false;return}
 const existing=s.routes.filter(r=>r.date===day);
 if(replace){for(const r of existing){s.routes=s.routes.filter(x=>x.id!==r.id);s.pending.push({type:'delete_route',data:{id:r.id},changeId:uid()})}}
 const picks=suggestedClients(day,8);
 let order=s.routes.filter(r=>r.date===day).length;
 for(const c of picks){let r={id:uid(),date:day,clientId:c.id,order:++order,user:s.user,suggested:true,location:locationLabel(c),reason:priority(c).reason};s.routes.push(r);s.pending.push({type:'route',data:structuredClone(r),changeId:uid()})}
 window.routeBuilding=false;save();if(s.server&&navigator.onLine)sync();show('rota');
}
function changeOrder(id,status){
 const o=s.orders.find(x=>x.id===id);
 if(!o)return;
 if(!Array.isArray(o.items)||!o.items.length){alert('Pedido antigo: alteração de status indisponível até a migração para pedidos por itens.');show('pedidos');return}
 o.status=status;queue('order',o);show('pedidos')
}
function exportData(){let b=new Blob([JSON.stringify(s,null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='L2_ONE_backup_'+today()+'.json';a.click();URL.revokeObjectURL(a.href)}
async function importData(f){if(!f)return;try{let d=JSON.parse(await f.text());if(!Array.isArray(d.clients))throw Error('Arquivo inválido');if(confirm('Substituir os dados locais pelo backup?')){s={...defaults,...d,token:'',server:'',pending:[]};save();show('hoje')}}catch(e){alert(e.message)}}
async function connect(){
 const user=$('who').value, server=$('server').value.replace(/\/$/,'');
 if(!server || (location.protocol==='https:' && !server.startsWith('https://'))){alert('Informe uma URL HTTPS válida.');return}
 if(s.pending.length && (user!==s.user || server!==s.server)){alert('Sincronize ou exporte o backup das alterações pendentes antes de trocar usuário ou servidor.');return}
 try{
  const r=await fetch(server+'/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user,password:$('password').value})});
  if(!r.ok)throw Error('Login não autorizado');
  const token=(await r.json()).token;
  if(user!==s.user || server!==s.server){s={...defaults,user,server};}
  s.user=user;s.server=server;s.token=token;save();await sync();show('config');
 }catch(e){alert('Não foi possível conectar: '+e.message)}
}
function logout(){
 if(!confirm('Sair deste aparelho? A senha será solicitada no próximo acesso.'))return;
 s.token='';s.syncAt='';save();show('config');
}
async function sync(){
 if(syncing||!s.server||!s.token||!navigator.onLine)return;
 syncing=true;
 const batch=s.pending.slice(0,500), user=s.user, server=s.server, token=s.token;
 try{
  const r=await fetch(server+'/api/sync',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify({changes:batch})});
  if(!r.ok){if(r.status===401){s.token='';save();alert('Sua sessão expirou. Entre novamente em Nuvem.')}throw Error('Sincronização recusada: '+r.status)}
  const data=await r.json();
  if(user!==s.user||server!==s.server||token!==s.token)return;
  const acknowledged=new Set(batch.map(x=>x.changeId));
  s.pending=s.pending.filter(x=>!acknowledged.has(x.changeId));
  for(const name of ['clients','visits','orders','tasks','routes','goals'])if(Array.isArray(data[name]))s[name]=data[name];
  // Reaplica alterações criadas enquanto a solicitação estava em andamento.
  for(const change of s.pending){const name={client:'clients',visit:'visits',order:'orders',task:'tasks',route:'routes',delete_route:'routes'}[change.type];if(!name)continue;s[name]=s[name].filter(x=>x.id!==change.data.id);if(change.type!=='delete_route')s[name].push(change.data)}
  s.syncAt=new Date().toLocaleString('pt-BR');window.routeAttemptedDay='';save();show(tab);
 }catch(e){console.warn(e);network()}finally{syncing=false;if(s.pending.length&&s.server&&s.token&&navigator.onLine)setTimeout(sync,3000)}
}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>show(b.dataset.tab));
window.addEventListener('online',()=>{network();sync()});window.addEventListener('offline',network);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)sync()});setInterval(sync,30000);
(async()=>{if('serviceWorker'in navigator&&location.protocol!=='file:')navigator.serviceWorker.register('./sw.js').catch(()=>{});if(!s.server&&location.protocol.startsWith('http'))s.server=location.origin;save();show('hoje');sync()})();
