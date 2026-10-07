/* Update facts and derived panels without navigating or replacing editing forms. */
(function(root){
 let timer,stream,retryTimer,failures=0,reading=false,needsRead=false,refreshing=false;
 const activities=()=>{
  const remote=window.commercialActivityOwner===s.user?window.commercialActivityEvents||[]:[];
  const local=s.pending.filter(x=>['client','visit','task','route','order','interaction','opportunity','fulfillment','office_action','office_commercial','office_administrative','price_table','price'].includes(x.type)).map(x=>({entityId:x.data.id,user:x.data.updatedBy||s.user,kind:x.type,action:'upsert',at:x.data.updatedAt,clientName:client(x.data.clientId).name||x.data['Demanda ou problema']||x.data.Demanda||x.data.title||x.data.brand||'',local:true}));
  return [...local,...remote].sort((a,b)=>String(b.at||'').localeCompare(String(a.at||''))).slice(0,100);
 };
 function refresh(){
  if(!sectionAllowed(tab)){const app=$('app');if(app)app.textContent='Seu acesso a este setor foi alterado. Escolha uma aba permitida.';return;}
  if(refreshing||!document.getElementById('app')||tab==='personal'||s.mustChangePassword)return;
  refreshing=true;
  try{
   const html=render(true);if(!html)return;
   const preview=document.createElement('div');preview.innerHTML=html;const app=$('app');
   // Replace only read-only metrics and record tables. Form nodes, files, selection,
   // focus, scroll position and unsaved field contents are never recreated.
   for(const selector of ['.cards','.workspace-stats','.workspace-sync','.scroll','#clientTimeline']){
    const current=[...app.querySelectorAll(selector)],next=[...preview.querySelectorAll(selector)];
    if(current.length!==next.length)continue;
    current.forEach((node,index)=>{if(node.closest('form')||node.querySelector('input,textarea,select,form,[contenteditable="true"]')||next[index].querySelector('input,textarea,select,form'))return;
     if(node.innerHTML!==next[index].innerHTML){const top=node.scrollTop,left=node.scrollLeft;node.innerHTML=next[index].innerHTML;node.scrollTop=top;node.scrollLeft=left;}
    });
   }
   for(const select of app.querySelectorAll('select[name="sellerResponsible"],select[name="user"],select[name="owner"],select[name="Responsável"]')){const names=select.name==='sellerResponsible'?s.sellers:teamNames();for(const name of names||[])if(![...select.options].some(o=>o.value===name)){const option=document.createElement('option');option.value=name;option.textContent=name;select.append(option);}}
   const feed=$('commercialActivity');if(feed)feed.innerHTML=commercialActivityHTML(activities());network();
  }finally{window.liveRenderOnly=false;refreshing=false;}
 }
 function schedule(){clearTimeout(timer);timer=setTimeout(()=>{refresh();if(s.token&&navigator.onLine&&!syncing&&!s.mustChangePassword)sync({automatic:true});},700);}
 function saved(){refresh();schedule();}
 function settled(){refresh();if(tab==='admin')loadAdminUsage();if(tab==='finance'){window.loadFinancialAnalysis?.();window.loadFinancialInputs?.();}if(needsRead){needsRead=false;requestRead();}else if(s.pending.length&&!window.lastSyncError)schedule();}
 async function requestRead(){
  if(reading||syncing){needsRead=true;return;}if(!s.token||!navigator.onLine||document.hidden)return;
  reading=true;try{await sync({automatic:true});}finally{reading=false;if(needsRead){needsRead=false;setTimeout(requestRead,100);}}
 }
 function rendered(){
  const form=$('memberCreateForm');if(form){toggleSellerFields(form);if(!form.dataset.validationBound){form.dataset.validationBound='1';form.addEventListener('invalid',event=>{const out=$('adminTeamNotice');if(out)out.textContent='Confira o campo '+(event.target.closest('label')?.textContent.trim()||event.target.name)+'.';},true);}}
  if($('leadConversionPanel'))loadLeadConversion();
  if($('commercialActivity')&&window.commercialActivityOwner!==s.user)loadCommercialActivity();
  connected();
 }
 async function connected(){
  if(stream||!s.token||!navigator.onLine||document.hidden||s.mustChangePassword)return;
  const token=s.token,server=s.server,controller=new AbortController();stream=controller;
  try{
   const response=await fetch(server+'/api/records/events',{headers:{Authorization:'Bearer '+token},cache:'no-store',signal:controller.signal});
   if(!response.ok||!response.body?.getReader)throw Error('Atualização compartilhada indisponível');
   failures=0;const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
   while(!controller.signal.aborted){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let end;
    while((end=buffer.indexOf('\n\n'))>=0){const message=buffer.slice(0,end);buffer=buffer.slice(end+2);if(token!==s.token||server!==s.server){controller.abort();break;}if(message.includes('event: records')||message.includes('event: ready'))requestRead();}
   }
  }catch(error){if(!controller.signal.aborted)failures++;}finally{if(stream===controller)stream=null;if(!controller.signal.aborted&&token===s.token&&failures<4&&!document.hidden)retryTimer=setTimeout(connected,Math.min(30000,3000*Math.max(1,failures)));}
 }
 function pause(){clearTimeout(retryTimer);stream?.abort();stream=null;}
 document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();else{failures=0;connected();}});
 root.addEventListener('online',()=>{failures=0;connected();if(s.pending.length)schedule();});
 root.addEventListener('offline',pause);root.addEventListener('pagehide',pause);
 root.L2Live={activities,refresh,saved,settled,rendered,connected,pause};rendered();
})(window);
