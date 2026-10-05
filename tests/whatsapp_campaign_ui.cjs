const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const src=fs.readFileSync('app.js','utf8');
const code=src.slice(src.indexOf('function previewWhatsappCampaignMedia('),src.indexOf('function whatsappMediaLimit('));
const panels={
  whatsappClient:{value:'c1'},whatsappBrand:{value:'Bruna Tavares'},
  whatsappCampaignMedia:{files:[{name:'produto.jpg',type:'image/jpeg',size:1200}],value:'x'},
  whatsappCampaignStatus:{textContent:''},whatsappCampaignPreview:{textContent:''},
  whatsappCampaignManual:{innerHTML:'',replaceChildren(){this.innerHTML=''}},
  whatsappCampaignSend:{disabled:false},
};
const opened=[];let posts=0;
const ctx={$:id=>panels[id],s:{clients:[{id:'c1',name:'Cliente',phone:'91999999999'}],whatsappTemplates:[{id:'t1',message:'Olá {cliente}, veja {marca}'}],token:'token',server:'https://example.test'},window:{whatsappTemplateId:'t1',whatsappMediaReady:false,open:(...args)=>opened.push(args)},navigator:{onLine:true},whatsappPhone:v=>'55'+v,whatsappMediaLimit:f=>f.type==='image/jpeg'&&f.size<5e6,esc:v=>v,alert:()=>{},postWhatsappMedia:async()=>{posts++;return{messageId:'wamid.test'}}};
vm.createContext(ctx);vm.runInContext(code,ctx);
(async()=>{
  await ctx.sendWhatsappMessage();
  assert.equal(posts,0);assert.equal(opened.length,0);
  assert.match(panels.whatsappCampaignManual.innerHTML,/anexe-o na conversa/);
  assert.match(panels.whatsappCampaignManual.innerHTML,/wa.me\/5591999999999/);
  ctx.window.whatsappMediaReady=true;
  await ctx.sendWhatsappMessage();
  assert.equal(posts,1);assert.match(panels.whatsappCampaignStatus.textContent,/única mensagem/);
  panels.whatsappCampaignMedia.files=[];
  await ctx.sendWhatsappMessage();
  assert.equal(opened.length,1);assert.match(opened[0][0],/\?text=/);
  console.log('Campanha com anexo: sem envio isolado no fallback; envio único pela API: OK');
})().catch(error=>{console.error(error);process.exitCode=1});
