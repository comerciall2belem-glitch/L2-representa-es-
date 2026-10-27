const fs=require('fs'),assert=require('assert'),{JSDOM}=require(process.env.L2_JSDOM);
(async()=>{
 const dom=new JSDOM('<!doctype html><body></body>',{url:'https://l2.test',runScripts:'outside-only'}),w=dom.window;
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'))};
 w.eval("var s={user:'Erika',token:'secret',server:'https://l2.test',mustChangePassword:false}; var esc=x=>String(x??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));");
 let acknowledged=false,interval;
 w.setInterval=fn=>(interval=fn,1);w.clearInterval=()=>{};
 w.fetch=async(url,options={})=>({ok:true,json:async()=>url.includes('/status')?{date:'2026-10-27',acknowledged}: {date:'2026-10-27',status:'em_risco',acknowledged:false,rows:[{name:'Loja <teste>',code:'22',potential:'123.45',suggestion:'Conferir BT'}],unavailable:[{name:'Loja 2',code:'23',message:'Cliente sem histórico suficiente ou meta cadastrada para cálculo de potencial.'}],basis:'Estimativa'}});
 const source=fs.readFileSync('app.js','utf8');w.eval(source.slice(source.indexOf('// Recomendação calculada no servidor')));
 await w.showDailyRecommendation();let modal=w.document.getElementById('dailyRecommendation');assert(modal);assert(modal.textContent.includes('Loja <teste>'));assert(!modal.querySelector('teste'));assert(modal.textContent.includes('123,45'));assert(modal.textContent.includes('Cliente sem histórico suficiente'));
 acknowledged=true;interval();await new Promise(resolve=>setTimeout(resolve,10));assert(!w.document.getElementById('dailyRecommendation'));
 dom.window.close();console.log('Recomendação diária: exibição, proteção de texto e confirmação em outro dispositivo: OK');
})().catch(e=>{console.error(e);process.exit(1)});
