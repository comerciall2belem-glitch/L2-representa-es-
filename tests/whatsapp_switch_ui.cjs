const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app.js', 'utf8');
const capability = source.slice(source.indexOf('async function loadWhatsappCapabilities('), source.indexOf('function previewWhatsappMedia('));
const send = source.slice(source.indexOf('async function sendWhatsappMedia('), source.indexOf('function sendWhatsappOrder('));
const button = {disabled:true};
const status = {textContent:''};
const form = {querySelector:()=>button, elements:{file:{files:[]}}};
let requests=[];
const context={
  window:{}, s:{token:'test-session',server:'https://example.test'},
  navigator:{onLine:true},
  $:id=>({'whatsappMediaForm':form,'whatsappMediaStatus':status,'whatsappClient':{value:'client-1'}}[id]),
  fetch:async (url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({mediaWithCaption:false})}},
};
vm.createContext(context);
vm.runInContext(capability+send,context);
(async()=>{
  await context.loadWhatsappCapabilities();
  assert.equal(button.disabled,true);
  assert.match(status.textContent,/indisponível/);
  await context.sendWhatsappMedia({preventDefault(){},target:form});
  assert.equal(requests.length,1); // Capability GET only; no media POST.
  context.fetch=async (url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({mediaWithCaption:true})}};
  await context.loadWhatsappCapabilities();
  assert.equal(button.disabled,false);
  assert.equal(context.window.whatsappMediaReady,true);
  assert.equal(requests[1].url,'https://example.test/api/whatsapp/capabilities');
  console.log('Switch de envio por API: OK');
})().catch(error=>{console.error(error);process.exitCode=1});
