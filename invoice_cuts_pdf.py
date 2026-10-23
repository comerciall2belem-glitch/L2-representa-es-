"""PDF of outstanding quantities at original order prices."""
from io import BytesIO
from decimal import Decimal
from html import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


def brl(value):
    return 'R$ '+format(Decimal(str(value or 0)),',.2f').replace(',','_').replace('.',',').replace('_','.')


def build_cuts_pdf(order, customer):
    billing=order['invoiceBilling']
    out=BytesIO()
    doc=SimpleDocTemplate(out,pagesize=landscape(A4),rightMargin=30,leftMargin=30,topMargin=30,bottomMargin=32)
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Cell',fontName='Helvetica',fontSize=9,leading=12))
    styles.add(ParagraphStyle(name='HeadCell',fontName='Helvetica-Bold',fontSize=9,leading=12,textColor=colors.white))
    def p(text,style='Cell'): return Paragraph(escape(str(text)),styles[style])
    rows=[p('L2 REPRESENTAÇÕES | RELATÓRIO DE CORTES','Heading1'),Spacer(1,12),
          p('Pedido '+str(order.get('orderNumber') or order.get('id') or '')+' | '+str(customer.get('name') or 'Cliente')),
          p('Pedido original: '+brl(order.get('originalAmount',order.get('amount')))+' | Faturado confirmado: '+brl(billing['billedAmount'])+' | Cortes: '+brl(billing['cutTotal'])),Spacer(1,14)]
    data=[[p(h,'HeadCell') for h in ['Código / indústria','Descrição','Qtd. pedida','Qtd. faturada','Qtd. cortada','Valor do corte']]]
    for i in billing['cuts']:
        data.append([p(i['sku']+' / '+i['brand']),p(i['description']),p(i['orderedQuantity']),p(i['invoicedQuantity']),p(i['cutQuantity']),p(brl(i['cutValue']))])
    if len(data)>1:
        table=Table(data,colWidths=[144,243,87,87,87,87],repeatRows=1,hAlign='LEFT')
        table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#164b58')),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#f0f5f6'),colors.white]),('LINEBELOW',(0,0),(-1,0),1,colors.HexColor('#164b58'))]))
        rows.append(table)
    else: rows.append(p('Nenhum item cortado. Todos os itens do pedido foram faturados.'))
    rows.extend([Spacer(1,14),p('Quantidades consolidadas de todas as notas confirmadas. Inclui indústrias ainda não faturadas. Valor do corte proporcional ao valor do item no pedido original; não é diferença de preço entre pedido e nota.')])
    def footer(canvas,doc):
        canvas.setFont('Helvetica',8);canvas.setFillColor(colors.HexColor('#50636a'))
        canvas.drawString(30,18,'L2 One | Conferência de pedidos e NF-e')
        canvas.drawRightString(811,18,'Página '+str(doc.page))
    doc.build(rows,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
