/* Shared on-screen/download invoice mirror. All amounts come from the reconciliation. */
(function(root){
 'use strict';
 function build(doc,report,options={}){
  const cutsOnly=options.cutsOnly!==false,margin=12,width=doc.internal.pageSize.getWidth(),height=doc.internal.pageSize.getHeight(),usable=width-margin*2;
  const ink=[18,63,74],pale=[234,241,243],muted=[86,101,112],grid=[213,224,228];
  const numeric=v=>Number.isFinite(Number(v))?Number(v):0;
  const money=v=>'R$ '+numeric(v).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
  const quantity=v=>numeric(v).toLocaleString('pt-BR',{maximumFractionDigits:4});
  const clean=v=>String(v??'').replace(/[\r\n]+/g,' ').trim();
  let items=Array.isArray(report.items)?report.items:Array.isArray(report.cuts)?report.cuts:[];
  if(!items.length&&Array.isArray(report.differences))items=report.differences.map(d=>{
   const ordered=numeric(d.pedidoQuantidade),invoiced=numeric(d.notaQuantidade),cut=Math.max(0,ordered-invoiced);
   return {sku:d.sku,description:d.description||d.sku,orderedQuantity:ordered,invoicedQuantity:invoiced,cutQuantity:cut,cutValue:ordered?numeric(d.pedidoValor)*cut/ordered:0};
  });
  if(cutsOnly)items=items.filter(item=>numeric(item.cutQuantity)>0);
  const totalCents=items.reduce((sum,item)=>sum+Math.round(numeric(item.cutValue)*100),0),totalQuantity=items.reduce((sum,item)=>sum+numeric(item.cutQuantity),0);
  const columns=cutsOnly?[
   {title:'CÓDIGO',width:25,key:'sku'}, {title:'DESCRIÇÃO DO PRODUTO',width:usable-81,key:'description'},
   {title:'QTD. CORTADA',width:20,key:'cutQuantity',right:true}, {title:'VALOR DO CORTE',width:36,key:'cutValue',right:true}
  ]:[
   {title:'CÓDIGO',width:23,key:'sku'}, {title:'DESCRIÇÃO DO PRODUTO',width:usable-104,key:'description'},
   {title:'PEDIDO',width:16,key:'orderedQuantity',right:true}, {title:'FATURADO',width:19,key:'invoicedQuantity',right:true},
   {title:'CORTE',width:16,key:'cutQuantity',right:true},{title:'VALOR DO CORTE',width:30,key:'cutValue',right:true}
  ];
  let y=0;
  function setText(size,bold=false,color=ink){doc.setFont('helvetica',bold?'bold':'normal');doc.setFontSize(size);doc.setTextColor(...color);}
  function drawFooter(){
   const page=doc.internal.getCurrentPageInfo().pageNumber;
   doc.setDrawColor(...ink);doc.setLineWidth(.2);doc.line(margin,height-12,width-margin,height-12);
   setText(7,false,muted);doc.text('L2 REPRESENTAÇÕES | CONFERÊNCIA COMERCIAL',margin,height-7);doc.text(String(page),width-margin,height-7,{align:'right'});
  }
  function title(continued=false){
   if(options.logo){try{doc.addImage(options.logo,'JPEG',margin,12,39,12);}catch(_){setText(12,true);doc.text('L2 REPRESENTAÇÕES',margin,19);}}
   else {setText(12,true);doc.text('L2 REPRESENTAÇÕES',margin,19);}
   setText(14,true);doc.text(cutsOnly?'ITENS NÃO FATURADOS':'ESPELHO DO CONFRONTO',width-margin,18,{align:'right'});
   setText(9,true);doc.text('Pedido L2 '+clean(options.orderLabel||report.orderNumber||'não informado')+' | NF '+clean(report.invoiceNumber),width-margin,26,{align:'right'});
   y=continued?35:38;
  }
  function tableHeader(){
   doc.setFillColor(...ink);doc.rect(margin,y,usable,13,'F');setText(7.5,true,[255,255,255]);let x=margin;
   for(const col of columns){const lines=doc.splitTextToSize(col.title,col.width-4);doc.text(lines,col.right?x+col.width-2:x+2,y+5,{align:col.right?'right':'left'});x+=col.width;}
   y+=13;
  }
  function newPage(withTable=true){drawFooter();doc.addPage();title(true);if(withTable)tableHeader();}
  function textBlock(text,size=9,color=ink){
   setText(size,false,color);const lines=doc.splitTextToSize(clean(text),usable);
   for(const line of lines){if(y+5>height-19)newPage(false);setText(size,false,color);doc.text(line,margin,y);y+=4.5;}
  }
  title();
  if(options.clientName||report.clientName)textBlock('Cliente: '+(options.clientName||report.clientName));
  textBlock('Marca: '+(options.brandLabel||report.brand||'Não informada'));
  if(report.recipientCnpj)textBlock('CNPJ do destinatário: '+report.recipientCnpj);
  y+=4;
  const summary=[['PEDIDO EMITIDO',money(report.orderTotal)],['NOTA FATURADA',money(report.invoiceTotal)],['TOTAL DOS CORTES',money(totalCents/100)]];
  doc.setFillColor(...pale);doc.rect(margin,y,usable,19,'F');
  summary.forEach((pair,i)=>{const x=margin+i*usable/3+3;setText(7.5);doc.text(pair[0],x,y+6);setText(10,true);doc.text(pair[1],x,y+13);});y+=26;
  tableHeader();
  for(const item of items){
   setText(8.5);const cells=columns.map(col=>{
    const value=col.key==='cutValue'?money(item[col.key]):col.right?quantity(item[col.key]):clean(item[col.key]||item.sku);
    return doc.splitTextToSize(value,col.width-4);
   });
   let offset=0;const maxLines=Math.max(...cells.map(lines=>lines.length));
   while(offset<maxLines){
    const capacity=Math.floor((height-21-y-5)/4.1);
    if(capacity<1){newPage();continue;}
    const take=Math.min(maxLines-offset,capacity),rowHeight=Math.max(11,take*4.1+5);let x=margin;
    setText(8.5);
    cells.forEach((lines,i)=>{const part=lines.slice(offset,offset+take);if(part.length)doc.text(part,columns[i].right?x+columns[i].width-2:x+2,y+5,{align:columns[i].right?'right':'left'});x+=columns[i].width;});
    y+=rowHeight;doc.setDrawColor(...grid);doc.setLineWidth(.15);doc.line(margin,y,width-margin,y);offset+=take;
    if(offset<maxLines)newPage();
   }
  }
  if(!items.length){y+=8;textBlock(cutsOnly?'Nenhum item cortado no confronto.':'Nenhum item disponível no confronto.');y+=4;}
  if(y+15>height-19)newPage();
  doc.setFillColor(...pale);doc.rect(margin,y,usable,12,'F');setText(9,true);doc.text('TOTAL',margin+2,y+7.5);
  const cutIndex=columns.findIndex(col=>col.key==='cutQuantity'),cutX=margin+columns.slice(0,cutIndex+1).reduce((sum,col)=>sum+col.width,0)-2;
  doc.text(quantity(totalQuantity),cutX,y+7.5,{align:'right'});doc.text(money(totalCents/100),width-margin-2,y+7.5,{align:'right'});y+=19;
  textBlock(items.length+' produtos | '+quantity(totalQuantity)+' unidades cortadas',8,muted);
  textBlock('Valores e quantidades conforme o confronto. O total dos cortes considera somente as unidades não faturadas.',8,muted);
  for(const warning of report.warnings||[])textBlock('Conferir: '+warning,8,muted);
  drawFooter();return doc;
 }
 root.L2InvoicePDF={build};
 if(typeof module==='object'&&module.exports)module.exports={build};
})(typeof window!=='undefined'?window:globalThis);
