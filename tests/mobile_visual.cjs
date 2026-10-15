const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const engines=require(process.env.L2_PLAYWRIGHT||'playwright');
const engine=process.env.L2_BROWSER||'chromium';
assert(['chromium','webkit'].includes(engine));
(async()=>{
 const out='artifacts/mobile-visual/'+engine;fs.mkdirSync(out,{recursive:true});
 let html=fs.readFileSync('index.html','utf8').replace(/<script[\s\S]*?<\/script>/g,'').replace(/<link\b[^>]*>/g,'');
 const styles=['strategy_ui.css','modern_workspace.css'].map(f=>fs.readFileSync(f,'utf8')).join('\n');
 html=html.replace('</head>','<style>'+styles+'</style></head>');
 const scripts=['commercial_drafts.js','crm_operations.js','goals_dashboard.js','workspace.js','modern_workspace.js','app.js','live_records.js'].map(f=>fs.readFileSync(f,'utf8')).join('\n');
 const browser=await engines[engine].launch({headless:true});const results=[];let failed=false;
 try{for(const [width,height] of [[360,800],[390,844],[768,1024],[1024,768]]){
  const page=await browser.newPage({viewport:{width,height},hasTouch:true});
  await page.route('**/*',route=>route.abort());
  // A non-opaque origin enables localStorage while every network request stays blocked.
  await page.route('http://l2.test/',route=>route.fulfill({contentType:'text/html',body:html}));
  await page.goto('http://l2.test/');
  await page.evaluate(()=>{
   window.alert=()=>{};window.confirm=()=>true;window.setInterval=()=>0;
   window.fetch=async()=>({ok:true,json:async()=>[]});
   localStorage.setItem('l2one_state_v2',JSON.stringify({user:'Ana Paula',role:'Administrativo',token:'x'.repeat(40),server:'https://l2.test',sectors:['commercial','routes','office','management','finance','admin','catalog'],sellers:['Ana Paula'],clients:[{id:'qa-visual',name:'Cliente fictício visual',owner:'Ana Paula',state:'PA',city:'Belém'}],orders:[],tasks:[],opportunities:[],pending:[]}));
  });
  await page.addScriptTag({content:scripts+'\nL2Live.pause();'});
  for(const screen of ['hoje','clientes','pedidos','marcas']){
   await page.evaluate(id=>{show(id);L2Live.pause();},screen);
   if(screen==='clientes'){
    const input=page.locator('#clientForm input[name="name"]');
    if(await input.count()){await page.locator('details').filter({has:input}).first().evaluate(e=>{e.open=true;});await input.fill('Rascunho fictício por toque');await assert.equal(await input.inputValue(),'Rascunho fictício por toque');}
   }
   const menu=page.locator('#l2MenuButton');
   if(await menu.isVisible()){await menu.click();assert.equal(await menu.getAttribute('aria-expanded'),'true');await page.locator('#l2MenuVeil').click({position:{x:width-10,y:10}});assert.equal(await menu.getAttribute('aria-expanded'),'false');}
   await page.screenshot({path:path.join(out,width+'-'+screen+'.png'),fullPage:true});
   const metrics=await page.evaluate(()=>{
    const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
    const inputs=[...document.querySelectorAll('#app input:not([type=checkbox]):not([type=radio]):not([type=hidden]),#app select,#app textarea')].filter(visible).map(e=>({id:e.id,height:e.getBoundingClientRect().height,font:parseFloat(getComputedStyle(e).fontSize)}));
    const buttons=[...document.querySelectorAll('#l2BottomNav button,header button')].filter(visible).map(e=>({text:e.textContent,height:e.getBoundingClientRect().height}));
    return {viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,inputs,buttons};
   });
   const errors=[];if(metrics.documentWidth>width+1)errors.push('horizontal page overflow');
   for(const x of metrics.inputs)if(x.font<16||x.height<47.5)errors.push('input too small: '+x.id);
   for(const x of metrics.buttons)if(x.height<47.5)errors.push('button too small: '+x.text);
   if(errors.length)failed=true;results.push({width,height,screen,metrics,errors});
  }await page.close();
 }}finally{await browser.close();fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({physicalDevice:false,engine,results},null,2));}
 assert(!failed,'Responsive checks failed; inspect results.json and screenshots');
 console.log('16 screen/viewport checks passed. Browser-engine simulation; physical Android/iOS and Safari pending.');
})().catch(e=>{console.error(e);process.exitCode=1});
