const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app.js','utf8');
const panel={textContent:'',innerHTML:'',scrollIntoView(){}},notice={textContent:''},calls=[];
const state={token:'token',server:'',pending:[],orders:[{id:'o1',orderNumber:1,brand:'BT',amount:200,items:[{brand:'BT'}]}]};
const report={id:'n1',brand:'BT',invoiceNumber:'123',orderTotal:'200',invoiceTotal:'80',items:[{sku:'B1',description:'Produto',orderedQuantity:10,invoicedQuantity:4,cutQuantity:6,cutValue:120,classification:'Corte Parcial'}]};
const context={s:state,syncing:false,FormData:class{constructor(form){this.form=form}},$:id=>id==='orderInvoices'?panel:notice,esc:x=>String(x??''),industryLabel:x=>x,orderLabel:x=>x,money:x=>'R$ '+Number(x).toFixed(2),alert:message=>{throw Error(message)},confirm:()=>true,save(){},show(){},console,
fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>options?.method==='POST'?(url.endsWith('/confirm')?{report:{...report,confirmed:true},order:{...state.orders[0],amount:80,billedAmount:80,originalAmount:200,invoiceBilling:{},billedByBrand:{BT:80}}}:report):[report]}}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('async function manageOrderInvoices('),source.indexOf('\nfunction ',source.indexOf('async function downloadOrderCutsPDF('))),context);
(async()=>{
 await context.manageOrderInvoices('o1');
 assert.match(panel.innerHTML,/\.pdf,\.xml/);assert.doesNotMatch(panel.innerHTML,/<textarea[^>]*required/);assert.doesNotMatch(panel.innerHTML,/<input name="file"[^>]*required/);
 const button={disabled:false},form={dataset:{order:'o1'},elements:{file:{files:[{name:'nota.pdf'}]},invoiceText:{value:''},brand:{value:'BT'}},querySelector:()=>button};
 await context.uploadOrderInvoice({preventDefault(){},target:form});
 assert.equal(calls.find(x=>x.options?.method==='POST').url,'/api/orders/o1/invoices');
 form.elements.file.files=[];form.elements.invoiceText.value='NF: 123';
 await context.uploadOrderInvoice({preventDefault(){},target:form});
 assert(calls.some(x=>x.url.endsWith('/text')));
 const before=calls.length;state.pending=[{type:'order',data:{id:'o1'}}];
 await context.uploadOrderInvoice({preventDefault(){},target:form});assert.equal(calls.length,before);
 state.pending=[];await context.confirmOrderInvoice('o1','n1',button);
 assert.equal(state.orders[0].amount,80);assert.equal(state.orders[0].originalAmount,200);
 assert.match(panel.innerHTML,/Baixar PDF de cortes/);
 console.log('NF-e: anexo sem texto, alternativa de texto, pendências, confirmação e atualização local: OK');
})().catch(e=>{console.error(e);process.exit(1)});
