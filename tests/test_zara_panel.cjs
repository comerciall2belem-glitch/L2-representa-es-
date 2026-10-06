const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
 constructor(){this.children=[];this.textContent='';this.disabled=false;this.value='';this.hidden=false;this.scrollHeight=0}
 append(...items){this.children.push(...items)}
 replaceChildren(...items){this.children=items}
}
const elements=Object.fromEntries(['#status','#list','#title','#thread','#actions','#takeover','#resume','#send','#reply','#refresh','main'].map(k=>[k,new Element()]));
let state='graph_accepted',linked=false,confirmed=false,timer;
const paths=[];
const context={console,encodeURIComponent,localStorage:{getItem:()=>JSON.stringify({token:'test',user:'Ana Paula'})},
 document:{hidden:false,querySelector:k=>elements[k],createElement:()=>new Element(),createTextNode:t=>t},
 setInterval:(fn,ms)=>{assert.equal(ms,5000);timer=fn},
 fetch:async(path,opts)=>{
  paths.push([path,opts.method]);
  if(path.endsWith('/confirm-e2e')){assert.equal(opts.method,'POST');confirmed=true;return {ok:true,json:async()=>({ok:true})}}
  const value=path.endsWith('/conversations')?[{phone:'5591999999999',name:'Cliente Teste',mode:'human',lastMessage:'Retorno do teste',deliveryStatus:state}]:[
   {id:'wamid.out',direction:'out',body:'Mensagem de teste',deliveryStatus:state,e2eStatus:confirmed?'confirmed':'pending',e2eReady:linked&&['delivered','read'].includes(state)},
   ...(linked?[{id:'wamid.in',direction:'in',body:'Recebi no aparelho',deliveryStatus:'received'}]:[])];
  return {ok:true,json:async()=>value};
 }};
const script=fs.readFileSync('zara.html','utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
vm.createContext(context);vm.runInContext(script,context);
const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{
 await flush();assert.equal(elements['#list'].children.length,1);
 await elements['#list'].children[0].onclick();
 const bubble=()=>elements['#thread'].children[0];
 assert.match(bubble().children[0].textContent,/aguardando entrega/);
 assert.equal(bubble().children.filter(x=>x.onclick).length,0);
 elements['#reply'].value='Rascunho preservado';state='delivered';linked=true;await timer();await flush();
 assert.equal(elements['#reply'].value,'Rascunho preservado');
 assert.equal(elements['#thread'].children.length,2);
 assert.match(elements['#thread'].children[1].textContent,/Recebi no aparelho/);
 const button=bubble().children.find(x=>x.onclick);assert.ok(button);
 await button.onclick();assert.ok(confirmed);
 assert.match(bubble().children[1].textContent,/E2E confirmada/);
 assert.ok(paths.some(([p,m])=>p.endsWith('/confirm-e2e')&&m==='POST'));
 console.log('Painel Zara: listagem, atualização, retorno, rascunho e confirmação E2E: OK');
})().catch(e=>{console.error(e);process.exitCode=1});

