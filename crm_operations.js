/* Derived commercial indicators. No commission, cash or cost data. */
(function(root){
 const closed=x=>['Concluída','Concluído','Concluido','Concluida','Cancelado','Cancelada','Resolvido','Resolvida','Ganho','Perdido','Pós-venda','Entregue','Faturado','Pedido confirmado'].includes(x);
 const attendedVisit=v=>['Visitado - pedido','Visitado - sem pedido','Contato remoto'].includes(v.result);
 const date=x=>{if(typeof x!=='string')return '';const d=x.trim().slice(0,10);if(!/^\d{4}-\d{2}-\d{2}$/.test(d))return '';const parsed=new Date(d+'T12:00:00Z');return Number.isFinite(parsed.getTime())&&parsed.toISOString().slice(0,10)===d?d:'';};
 const dayNumber=x=>Date.parse(date(x)+'T12:00:00Z')/86400000;
 const businessDays=month=>{const result=[];const [y,m]=month.split('-').map(Number);for(let d=1;d<=31;d++){const x=new Date(Date.UTC(y,m-1,d,12));if(x.getUTCMonth()!==m-1)break;if(x.getUTCDay()>0&&x.getUTCDay()<6)result.push(x.toISOString().slice(0,10));}return result;};
 function overview(state,day){
  const ids=new Set(state.clients.map(c=>c.id)),pending=new Map(),events=[];
  const add=(key,x)=>{if(!pending.has(key))pending.set(key,x)};
  for(const x of state.tasks||[])if(!closed(x.status)&&(!x.clientId||ids.has(x.clientId)))add(x.sourceVisitId?'visit:'+x.sourceVisitId:x.sourceOpportunityId?'opportunity:'+x.sourceOpportunityId:'task:'+x.id,{...x,kind:'task',due:date(x.date),title:x.text});
  for(const x of state.visits||[])if(!(state.tasks||[]).some(t=>t.sourceVisitId===x.id&&closed(t.status))&&ids.has(x.clientId)&&x.next&&x.returnDate&&!closed(x.nextStatus))add('visit:'+x.id,{...x,kind:'visit',due:date(x.returnDate),title:x.next});
  for(const x of state.opportunities||[])if(ids.has(x.clientId)&&x.stage!=='Perdido')add('opportunity:'+x.id,{...x,kind:'opportunity',due:date(x.followUp),title:x.next||'Retornar negociação '+x.brand});
  for(const x of state.routes||[])if(ids.has(x.clientId)&&!closed(x.status)&&!(state.visits||[]).some(v=>v.clientId===x.clientId&&v.date===x.date&&attendedVisit(v)))add('route:'+x.id,{...x,kind:'route',due:date(x.date),title:x.objective||'Atendimento agendado'});
  for(const collection of ['officeActions','officeCommercial','officeAdministrative'])for(const x of state[collection]||[])if(!closed(x.Status)&&!(/financeiro/i.test(x['Área']||'')))add(collection+':'+x.id,{...x,kind:'office',collection,due:date(x.Prazo||x['Data retorno']),title:x['Demanda ou problema']||x['Próxima ação']||x.Demanda||x.Atividade||'Pendência do escritório',user:x.Responsável});
  for(const x of state.fulfillments||[])if(!['Entregue','Pós-venda concluído'].includes(x.stage)&&(!x.clientId||ids.has(x.clientId)))add('fulfillment:'+x.id,{...x,kind:'fulfillment',due:date(x.due),title:'Pedido · '+(x.stage||'Conferência'),user:x.owner});
  const rows=[...pending.values()].sort((a,b)=>(a.due||'9999').localeCompare(b.due||'9999'));
  const clients=state.clients.map(c=>{
   const orders=(state.orders||[]).filter(o=>o.clientId===c.id&&['Confirmado','Faturado'].includes(o.status)&&o.date<=day).sort((a,b)=>a.date.localeCompare(b.date));
   const dates=[...new Set(orders.map(o=>o.date))],gaps=dates.slice(1).map((d,i)=>dayNumber(d)-dayNumber(dates[i])).filter(n=>n>0).sort((a,b)=>a-b);
   const cadence=gaps.length>=2?gaps.length%2?gaps[Math.floor(gaps.length/2)]:(gaps[gaps.length/2-1]+gaps[gaps.length/2])/2:null;
   const last=dates.at(-1),reorderDate=cadence!==null?new Date((dayNumber(last)+Math.round(cadence))*86400000).toISOString().slice(0,10):null;
   const attended=[...(state.visits||[]).filter(v=>attendedVisit(v)),...(state.interactions||[])].some(v=>v.clientId===c.id&&date(v.date||v.at).startsWith(day.slice(0,7))&&date(v.date||v.at)<=day);
   const ticket=orders.length?orders.reduce((n,o)=>n+Number(o.amount||0),0)/orders.length:null;
   const open=(state.opportunities||[]).filter(o=>o.clientId===c.id&&!closed(o.stage)),amount=open.reduce((n,o)=>n+Number(o.amount||0),0);
   return {client:c,attended,cadence,reorderDate,ticket,potential:open.length?amount:reorderDate&&reorderDate<=day?ticket:null,basis:open.length?'Oportunidades abertas':reorderDate&&reorderDate<=day?'Ticket histórico · recompra prevista':'Sem base suficiente'};
  });
  return {pending:rows,overdue:rows.filter(x=>x.due&&x.due<day).length,withoutService:clients.filter(x=>!x.attended),reorders:clients.filter(x=>x.reorderDate&&x.reorderDate<=day),clients,potential:clients.reduce((n,x)=>n+(x.potential||0),0),unknownPotential:clients.filter(x=>x.potential===null).length};
 }
 function targets(state,day,user,all,brandTotals,seller){
  const month=day.slice(0,7),days=businessDays(month),weekday=new Date(day+'T12:00:00Z').getUTCDay(),monday=dayNumber(day)-(weekday===0?6:weekday-1),week=days.filter(d=>dayNumber(d)>=monday&&dayNumber(d)<monday+7);
  return (state.goals||[]).filter(g=>g.month===month&&(all||g.user===user||g.user==='Equipe')).map(g=>{
   const orders=(state.orders||[]).filter(o=>['Confirmado','Faturado'].includes(o.status)&&o.date<=day&&(g.user==='Equipe'||seller(o)===g.user));
   const sum=dates=>orders.filter(o=>dates.includes(o.date)).reduce((n,o)=>n+(g.brand?(brandTotals(o).get(g.brand)||0)/100:Number(o.amount||0)),0);
   const planned=(state.routes||[]).filter(r=>r.date===day&&(g.user==='Equipe'||r.user===g.user)&&(!g.brand||r.brand===g.brand||String(state.clients.find(c=>c.id===r.clientId)?.brands||'').split(/[,;|]/).map(b=>b.trim()).includes(g.brand))).length;
   const daily=days.includes(day)?Number(g.amount||0)/days.length:0;
   return {...g,daily,weekly:Number(g.amount||0)*week.length/days.length,dailySales:sum([day]),weeklySales:sum(week),planned,perAttendance:planned?daily/planned:null};
  });
 }
 function filterPending(rows,day,filter='all',owner=''){return rows.filter(x=>(!owner||(x.owner||x.user||'Não definido')===owner)&&(filter==='all'||filter==='action'&&x.due&&x.due<=day||filter==='overdue'&&x.due&&x.due<day||filter==='today'&&x.due===day||filter==='future'&&x.due>day||filter==='undated'&&!x.due));}
 function historicalForecast(opportunities,orders,day,minimum=5){
  const byId=new Map(orders.map(o=>[o.id,o])),rates={},closedRows=opportunities.filter(o=>o.stage==='Perdido'||byId.has(o.orderId)&&['Confirmado','Faturado','Entregue'].includes(byId.get(o.orderId).status)&&date(byId.get(o.orderId).date)&&date(byId.get(o.orderId).date)<=day);
  for(const stage of ['Prospectado','Qualificado','Visita agendada','Proposta enviada','Negociação','Ganho']){
   const seenOrders=new Set(),cohort=closedRows.filter(o=>{
    const linked=byId.get(o.orderId),end=o.stage==='Perdido'?(o.stageHistory||[]).filter(h=>h.stage==='Perdido'&&date(h.at)&&date(h.at)<=day).at(-1)?.at:linked?.date;
    if(!end||!(o.stageHistory||[]).some(h=>h.stage===stage&&date(h.at)&&date(h.at)<=date(end)))return false;
    if(o.stage!=='Perdido'){if(linked.clientId!==o.clientId||seenOrders.has(o.orderId))return false;seenOrders.add(o.orderId)}return true;
   });
   const wins=cohort.filter(o=>o.stage!=='Perdido').length;rates[stage]={sample:cohort.length,wins,rate:cohort.length>=minimum?wins/cohort.length:null};
  }
  const open=opportunities.filter(o=>!o.orderId&&rates[o.stage]),eligible=open.filter(o=>rates[o.stage].rate!==null);
  return {rates,open,eligible,unknown:open.length-eligible.length,expected:eligible.reduce((n,o)=>n+Math.round(Number(o.amount||0)*100*rates[o.stage].rate),0)/100};
 }
 root.CRMOperations={historicalForecast,attendedVisit,overview,targets,businessDays,closed,filterPending};
 if(typeof module!=='undefined')module.exports=root.CRMOperations;
})(typeof window==='undefined'?globalThis:window);
