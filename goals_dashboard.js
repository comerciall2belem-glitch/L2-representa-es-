/* Shared sales goals. Monetary arithmetic uses integer cents; no financial commissions. */
(function(root){
 const key=x=>String(x||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').trim().toLowerCase();
 const cents=x=>{const n=Number(x);return Number.isFinite(n)&&n>=0?Math.round(n*100):0};
 const validDay=d=>/^\d{4}-\d{2}-\d{2}$/.test(d||'')&&Number.isFinite(Date.parse(d+'T12:00:00Z'))&&new Date(d+'T12:00:00Z').toISOString().slice(0,10)===d;
 const dayAfter=(day,n)=>new Date(Date.parse(day+'T12:00:00Z')+n*864e5).toISOString().slice(0,10);
 function businessDays(month){const [y,m]=month.split('-').map(Number),out=[];for(let i=1;i<=31;i++){const d=new Date(Date.UTC(y,m-1,i,12));if(d.getUTCMonth()!==m-1)break;if(d.getUTCDay()>0&&d.getUTCDay()<6)out.push(d.toISOString().slice(0,10))}return out}
 function orderAmounts(order){
  const raw=new Map();for(const item of order.items||[]){const brand=item.brand||order.brand||'Indústria não informada',amount=item.subtotal!==undefined?cents(item.subtotal):cents(item.unitPrice)*Number(item.quantity||0);raw.set(brand,(raw.get(brand)||0)+amount)}
  if(!raw.size)raw.set(order.brand&&order.brand!=='Multimarcas'?order.brand:'Indústria não informada',cents(order.amount));
  const total=cents(order.amount),gross=[...raw.values()].reduce((n,v)=>n+v,0);
  if(!gross)return new Map([[order.brand&&order.brand!=='Multimarcas'?order.brand:'Indústria não informada',total]]);
  const allocations=[...raw].map(([brand,value])=>({brand,value:Math.floor(total*value/gross),fraction:total*value/gross%1}));
  let remainder=total-allocations.reduce((n,x)=>n+x.value,0);for(const x of allocations.slice().sort((a,b)=>b.fraction-a.fraction||a.brand.localeCompare(b.brand)))if(remainder-->0)x.value++;
  return new Map(allocations.map(x=>[x.brand,x.value]));
 }
 function weighted(total,rows){
  const values=rows.map(r=>Math.floor(total*r.weight/10000));let rest=total-values.reduce((a,b)=>a+b,0);
  rows.map((r,i)=>({i,f:total*r.weight%10000})).sort((a,b)=>b.f-a.f||a.i-b.i).slice(0,rest).forEach(r=>values[r.i]++);return values;
 }
 function expandGoals(goals){
  const plans=new Map();for(const g of goals)if(g.planVersion===1&&!g.sourcePlan){const k=g.month+'|'+g.brand;if(!plans.has(k)||String(g.updatedAt||'')>=String(plans.get(k).updatedAt||''))plans.set(k,g)}
  const result=goals.filter(g=>!g.sourcePlan&&!plans.has(g.month+'|'+g.brand));
  // A seller-scoped response may contain projections without the parent.
  result.push(...goals.filter(g=>g.sourcePlan&&!plans.has(g.month+'|'+g.brand)));
  for(const g of plans.values()){result.push(g);const amounts=weighted(cents(g.amount),g.allocations);g.allocations.forEach((r,i)=>result.push({id:g.id+'-member-'+i,month:g.month,brand:g.brand,user:r.user,amount:amounts[i]/100,sourcePlan:g.id,workDates:g.workDates,updatedAt:g.updatedAt}))}return result;
 }
 function schedule(goal){
  const dates=(goal.workDates||businessDays(goal.month)).slice().sort(),values=Array(dates.length).fill(0),parts=goal.planVersion===1?weighted(cents(goal.amount),goal.allocations):[cents(goal.amount)];
  for(const part of parts)splitEqual(part,dates.length).forEach((v,i)=>values[i]+=v);
  return dates.map((date,i)=>({date,amount:values[i]}));
 }
 function periodGoal(goal,day,start,end){const rows=schedule(goal);return {daily:rows.find(x=>x.date===day)?.amount||0,weekly:rows.filter(x=>x.date>=start&&x.date<=end).reduce((n,x)=>n+x.amount,0),expected:rows.filter(x=>x.date<=day).reduce((n,x)=>n+x.amount,0)};}
 function compute(state,month,today,basis='registered'){
  if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(month))throw Error('Mês inválido');
  const [y,m]=month.split('-').map(Number),last=new Date(Date.UTC(y,m,0,12)).toISOString().slice(0,10),first=month+'-01',asOf=today>last?last:today,days=businessDays(month);
  const weekDay=new Date(asOf+'T12:00:00Z').getUTCDay(),monday=dayAfter(asOf,-(weekDay===0?6:weekDay-1)),sunday=dayAfter(monday,6),weekStart=monday<first?first:monday,weekEnd=sunday>last?last:sunday;
  const weekDays=days.filter(d=>d>=weekStart&&d<=weekEnd),elapsed=days.filter(d=>d<=asOf).length;
  const industries=new Map(),addIndustry=(name,registered=false)=>{if(!name)return;const id=key(name);if(!industries.has(id))industries.set(id,{brand:name,registered,month:0,day:0,week:0,target:null});else if(registered){industries.get(id).brand=name;industries.get(id).registered=true}};
  for(const industry of state.industries||[])addIndustry(industry.name||industry.id,true);
  const goals=expandGoals(state.goals||[]).filter(g=>g.month===month),latest=new Map();for(const g of goals){const id=key(g.brand)+'|'+key(g.user);const old=latest.get(id);if(!old||String(g.updatedAt||'')>=String(old.updatedAt||''))latest.set(id,g);if(g.brand)addIndustry(g.brand)}
  const orders=(state.orders||[]).filter(o=>o.status!=='Cancelado'),orderIds=new Set(orders.map(o=>o.id)),fulfillments=new Map((state.fulfillments||[]).map(f=>[f.orderId,f]));
  const docs=(state.billedDocuments||[]).filter(d=>orderIds.has(d.orderId)),billedIds=new Set(docs.map(d=>d.orderId));
  const hasStage=(o,stages)=>stages.includes(o.status)||stages.includes(fulfillments.get(o.id)?.stage)||[...(o.stageHistory||[]),...(fulfillments.get(o.id)?.stageHistory||[])].some(h=>stages.includes(h.stage));
  const accepted=o=>billedIds.has(o.id)||hasStage(o,['Confirmado','Confirmado pela indústria','Faturado','Em transporte','Entregue','Pós-venda concluído']);
  const billed=o=>hasStage(o,['Faturado','Em transporte','Entregue','Pós-venda concluído']);
  const records=[];let invalidDates=0;
  if(basis==='invoices')for(const d of docs){if(!validDay(d.billedDate)){invalidDates++;continue}records.push({date:d.billedDate,brand:d.brand||'Indústria não informada',amount:cents(d.billed)})}
  else for(const o of orders.filter(basis==='billed'?billed:accepted)){if(!validDay(o.date)){invalidDates++;continue}for(const [brand,amount] of orderAmounts(o))records.push({date:o.date,brand,amount})}
  for(const row of records){if(!row.date.startsWith(month)||row.date>asOf)continue;addIndustry(row.brand);const industry=industries.get(key(row.brand));industry.month+=row.amount;if(row.date===asOf)industry.day+=row.amount;if(row.date>=weekStart&&row.date<=weekEnd)industry.week+=row.amount}
  for(const industry of industries.values()){
   const clients=new Set();for(const order of orders){if(!order.clientId||!accepted(order)||!validDay(order.date)||!order.date.startsWith(month)||order.date>asOf)continue;if([...orderAmounts(order)].some(([brand,amount])=>key(brand)===key(industry.brand)&&amount>0))clients.add(order.clientId)}
   industry.positiveClients=clients.size;
  }
  let targeted=0;for(const industry of industries.values()){
   const assigned=[...latest.values()].filter(g=>key(g.brand)===key(industry.brand)),team=assigned.find(g=>key(g.user)==='equipe');
   if(team){industry.target=cents(team.amount);industry.source='Meta da equipe'}else if(assigned.length){industry.target=assigned.reduce((n,g)=>n+cents(g.amount),0);industry.source='Soma das metas individuais'}
   industry.clientTarget=team&&team.clientTarget!==undefined&&team.clientTarget!==''?Number(team.clientTarget):null;industry.clientsRemaining=industry.clientTarget===null?null:Math.max(0,industry.clientTarget-industry.positiveClients);
   if(industry.target!==null)targeted++;
   industry.daily=industry.target===null?null:Math.round(industry.target/days.length);industry.weekly=industry.target===null?null:Math.round(industry.target*weekDays.length/days.length);
   industry.pct=industry.target>0?100*industry.month/industry.target:null;industry.remaining=industry.target===null?null:Math.max(0,industry.target-industry.month);industry.remainingPct=industry.target>0?100*industry.remaining/industry.target:null;
   industry.expected=industry.target===null?null:Math.round(industry.target*elapsed/days.length);
   const planned=team?.planVersion===1?team:assigned.find(g=>g.sourcePlan);
   if(planned){const period=periodGoal(planned,asOf,weekStart,weekEnd);industry.daily=period.daily;industry.weekly=period.weekly;industry.expected=period.expected;industry.workDates=planned.workDates;industry.plan=planned;}
   industry.pace=industry.target===null?'missing':industry.target===0?'zero':industry.month>=industry.target?'done':industry.month>=industry.expected?'ideal':industry.month>=industry.expected*.8?'attention':'focus';
  }
  const rows=[...industries.values()].sort((a,b)=>a.brand.localeCompare(b.brand,'pt-BR')),sum=field=>rows.reduce((n,x)=>n+(x[field]||0),0),target=targeted?sum('target'):null;
  const generalGoals=[...latest.values()].filter(g=>!g.brand),generalTeam=generalGoals.find(g=>key(g.user)==='equipe'),generalReference=goals.some(g=>g.month===month&&(g.planVersion===1||g.sourcePlan))?null:generalTeam?cents(generalTeam.amount):generalGoals.length?generalGoals.reduce((n,g)=>n+cents(g.amount),0):null;
  return {month,asOf,weekStart:weekStart<=weekEnd?weekStart:null,weekEnd:weekStart<=weekEnd?weekEnd:null,basis,rows,target,targeted,missing:rows.filter(x=>x.target===null).length,total:sum('month'),day:sum('day'),week:sum('week'),daily:target===null?null:sum('daily'),weekly:target===null?null:sum('weekly'),pct:target>0&&rows.every(x=>x.target!==null)?100*sum('month')/target:null,remaining:target===null?null:Math.max(0,target-sum('month')),remainingPct:target>0&&rows.every(x=>x.target!==null)?100*Math.max(0,target-sum('month'))/target:null,expected:target===null?null:sum('expected'),elapsed,workdays:days.length,invalidDates,generalReference};
 }
 function splitEqual(total,n){if(total===null)return Array(n).fill(null);if(!n)return [];const base=Math.floor(total/n),extra=total-base*n;return Array.from({length:n},(_,i)=>base+(i<extra?1:0))}
 function separated(state,month,today,industryBasis='invoices'){
  const commercial=compute(state,month,today,'registered'),industry=compute(state,month,today,industryBasis),sellers=[...new Set([...([...(state.sellers||[]),...((state.team||[]).includes('Ana Paula')?['Ana Paula']:[])].filter(x=>x&&key(x)!=='lais')),...expandGoals(state.goals||[]).filter(g=>g.month===month&&g.sourcePlan).map(g=>g.user)])].sort((a,b)=>a.localeCompare(b,'pt-BR'));
  const days=businessDays(month),remainingDays=days.filter(d=>d>=today).length,weekDays=days.filter(d=>d>=commercial.weekStart&&d<=commercial.weekEnd).length;
  const projected=expandGoals(state.goals||[]).filter(g=>g.month===month&&g.sourcePlan);
  const allocations=[];for(const row of commercial.rows){
   const plans=projected.filter(g=>key(g.brand)===key(row.brand));
   if(plans.length){for(const g of plans){const period=periodGoal(g,commercial.asOf,commercial.weekStart,commercial.weekEnd);allocations.push({seller:g.user,brand:row.brand,target:cents(g.amount),daily:period.daily,weekly:period.weekly,workDates:g.workDates})}continue;}
   const monthly=splitEqual(row.target,sellers.length),daily=splitEqual(row.daily,sellers.length),weekly=splitEqual(row.weekly,sellers.length);sellers.forEach((seller,i)=>allocations.push({seller,brand:row.brand,target:monthly[i],daily:daily[i],weekly:weekly[i]}));}

  const members=sellers.map(seller=>{const orders=(state.orders||[]).filter(o=>(o.sellerResponsible||o.commissionSeller||o.user)===seller),metrics=compute({...state,orders,goals:[]},month,today,'registered'),plan=allocations.filter(x=>x.seller===seller);for(const x of plan){const actual=metrics.rows.find(r=>key(r.brand)===key(x.brand));x.actual=actual?.month||0;x.remaining=x.target===null?null:Math.max(0,x.target-x.actual);const left=(x.workDates||days).filter(d=>d>=today).length;x.requiredDaily=x.remaining===null||!left?null:Math.ceil(x.remaining/left);}
   const sum=k=>plan.some(x=>x[k]!==null)?plan.reduce((n,x)=>n+(x[k]||0),0):null;
   return {seller,target:sum('target'),daily:sum('daily'),weekly:sum('weekly'),actual:metrics.total,day:metrics.day,week:metrics.week,remaining:sum('remaining'),requiredDaily:sum('requiredDaily'),orders:orders.filter(o=>validDay(o.date)&&o.date.startsWith(month)&&o.date<=commercial.asOf&&['Confirmado','Faturado','Entregue'].includes(o.status)).length};});
  const hasPlan=projected.length>0,teamTarget=hasPlan?commercial.target:commercial.generalReference!==null?commercial.generalReference:commercial.target;
  return {commercial,industry,sellers,members,allocations,remainingDays,teamTarget,teamTargetSource:!hasPlan&&commercial.generalReference!==null?'Meta comercial geral cadastrada':'Plano comercial: soma das metas por indústria',teamDaily:hasPlan?commercial.daily:teamTarget===null?null:Math.round(teamTarget/days.length),teamWeekly:hasPlan?commercial.weekly:teamTarget===null?null:Math.round(teamTarget*weekDays/days.length)};
 }
 root.L2Goals={compute,orderAmounts,businessDays,separated,weighted,expandGoals,schedule,periodGoal};if(typeof module!=='undefined')module.exports=root.L2Goals;
})(typeof window==='undefined'?globalThis:window);
