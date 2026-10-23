const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {jsPDF}=require('../jspdf.umd.min.js');
const {build}=require('../invoice_pdf.js');
const report={invoiceNumber:'TESTE',orderNumber:62,brand:'Teste',orderTotal:1000,invoiceTotal:800,items:[
 {sku:'CORTE-TOTAL',description:'Produto com corte total',orderedQuantity:10,invoicedQuantity:0,cutQuantity:10,cutValue:123.45},
 {sku:'CORTE-PARCIAL',description:'Produto com corte parcial',orderedQuantity:5,invoicedQuantity:3,cutQuantity:2,cutValue:20},
 {sku:'COMPLETO',description:'Produto faturado integralmente',orderedQuantity:5,invoicedQuantity:5,cutQuantity:0,cutValue:0}
]};
function make(data,options){const doc=new jsPDF(),texts=[],original=doc.text.bind(doc);doc.text=(text,...args)=>{texts.push(...(Array.isArray(text)?text:[text]));return original(text,...args)};build(doc,data,options);return {doc,text:texts.join('\n'),texts};}
const result=make(report);
assert.equal(result.doc.getNumberOfPages(),1);
assert.match(result.text,/ITENS NÃO FATURADOS/);assert.match(result.text.replace(/\n/g,''),/CORTE-PARCIAL/);assert.doesNotMatch(result.text,/COMPLETO/);
assert.match(result.text,/R\$ 143,45/);assert.match(result.text,/2 produtos \| 12 unidades cortadas/);
const full=make(report,{cutsOnly:false});assert.match(full.text,/COMPLETO/);assert.match(full.text,/ESPELHO DO CONFRONTO/);
const many=make({...report,items:Array.from({length:100},(_,i)=>({...report.items[0],sku:'ITEM-'+i,description:'Descrição longa de produto para verificar alinhamento e quebra de linhas '.repeat(5)}))});
assert(many.doc.getNumberOfPages()>1);assert.equal(many.texts.filter(t=>t==='CÓDIGO').length,many.doc.getNumberOfPages());assert.match(many.text,/ITEM-99/);assert.match(many.text,/R\$ 12\.345,00/);
const empty=make({...report,items:[]});assert.match(empty.text,/Nenhum item cortado/);assert.match(empty.text,/0 produtos/);
const legacy=make({...report,items:undefined,differences:[{sku:'ANTIGO',pedidoQuantidade:5,notaQuantidade:3,pedidoValor:50,notaValor:30}]});assert.match(legacy.text,/R\$ 20,00/);
const dir=fs.mkdtempSync(path.join(os.tmpdir(),'l2-invoice-pdf-'));
fs.writeFileSync(path.join(dir,'cuts.pdf'),Buffer.from(result.doc.output('arraybuffer')));
fs.writeFileSync(path.join(dir,'multipage.pdf'),Buffer.from(many.doc.output('arraybuffer')));
console.log('Espelho PDF: cortes, BRL, filtro, totais, legado e cabeçalhos multipágina: OK');
console.log(dir);
