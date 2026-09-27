"""Daily management PDF and scheduled email, sourced from persisted PostgreSQL rows."""
import io
import os
import ssl
import smtplib
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from reportlab.graphics.shapes import Drawing, Rect, String, Line

TZ = ZoneInfo('America/Belem')
NAVY=colors.HexColor('#253743')
GOLD=colors.HexColor('#a5884e')
PALE=colors.HexColor('#f7f1e6')

def amount(value):
    try:
        d=Decimal(str(value or '0'))
        return d if d.is_finite() else Decimal('0')
    except (ValueError, InvalidOperation):
        return Decimal('0')

def brl(value):
    v=f'{amount(value):,.2f}'.replace(',','X').replace('.',',').replace('X','.')
    return 'R$ '+v

def month_back(day, count):
    n=day.year*12+day.month-1-count
    return f'{n//12:04d}-{n%12+1:02d}'

def read_data(con):
    kinds=('order','goal','office_finance','commission_receipt')
    result={kind:[] for kind in kinds}
    for kind,payload in con.execute('SELECT kind,payload FROM entities WHERE kind=ANY(%s)', (list(kinds),)):
        result[kind].append(payload)
    return result

def summarize(data, day):
    month=day.strftime('%Y-%m')
    orders=[o for o in data['order'] if o.get('status') not in ('Cancelado','Orçamento')]
    sales=lambda key:sum((amount(o.get('amount')) for o in orders if str(o.get('date','')).startswith(key)),Decimal('0'))
    daily=[o for o in orders if o.get('date')==day.isoformat()]
    monthly=[o for o in orders if str(o.get('date','')).startswith(month)]
    # Only general team goals, not per-brand subtotals, to avoid double-counting.
    goals=[g for g in data['goal'] if g.get('month')==month and not g.get('brand')]
    target=sum((amount(g.get('amount')) for g in goals),Decimal('0'))
    finance=data['office_finance']
    open_rows=[r for r in finance if r.get('Situação')!='Pago/Recebido']
    receivable=sum((amount(r.get('Valor')) for r in open_rows if r.get('Tipo')=='Receita'),Decimal('0'))
    payable=sum((amount(r.get('Valor')) for r in open_rows if r.get('Tipo')=='Despesa'),Decimal('0'))
    paid=[r for r in finance if r.get('Situação')=='Pago/Recebido' and str(r.get('Liquidação') or r.get('Data','')).startswith(month)]
    income=sum((amount(r.get('Valor')) for r in paid if r.get('Tipo')=='Receita'),Decimal('0'))
    expense=sum((amount(r.get('Valor')) for r in paid if r.get('Tipo')=='Despesa'),Decimal('0'))
    competency=[r for r in finance if str(r.get('Competência') or r.get('Data','')[:7])==month]
    group=lambda name, kind:sum((amount(r.get('Valor')) for r in competency if r.get('Grupo DRE')==name and r.get('Tipo')==kind),Decimal('0'))
    revenue=group('Receita operacional','Receita'); deduction=group('Deduções','Despesa')
    direct=group('Custo direto','Despesa'); operating=group('Despesa operacional','Despesa')
    financial=group('Resultado financeiro','Receita')-group('Resultado financeiro','Despesa')
    taxes=group('Tributos sobre o resultado','Despesa')
    dre=revenue-deduction-direct-operating+financial-taxes
    unclassified=sum(not r.get('Grupo DRE') or r.get('Grupo DRE')=='Não classificado' for r in competency)
    brands={}
    for order in monthly:
        brand=str(order.get('brand') or 'Sem marca')
        brands[brand]=brands.get(brand,Decimal('0'))+amount(order.get('amount'))
    return locals()

def build_pdf(data, day):
    stats=summarize(data,day)
    buffer=io.BytesIO()
    doc=SimpleDocTemplate(buffer,pagesize=(210*mm,297*mm),leftMargin=16*mm,rightMargin=16*mm,topMargin=11*mm,bottomMargin=11*mm)
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='L2Title',parent=styles['Heading1'],fontSize=19,textColor=NAVY,spaceAfter=12))
    styles.add(ParagraphStyle(name='L2Section',parent=styles['Heading2'],fontSize=10,textColor=NAVY,spaceBefore=7,spaceAfter=4))
    styles.add(ParagraphStyle(name='L2Note',parent=styles['BodyText'],fontSize=8,textColor=colors.HexColor('#555555'),leading=11))
    normal=ParagraphStyle(name='L2Cell',parent=styles['BodyText'],fontSize=8,leading=10)
    story=[Paragraph('L2 ONE  |  RELATÓRIO DIÁRIO',styles['L2Title']),Paragraph('Gestão comercial e financeira · '+day.strftime('%d/%m/%Y')+' · Horário de Belém',normal),Spacer(1,3*mm)]
    def table(title,headers,rows,widths=None):
        story.append(Paragraph(title,styles['L2Section']))
        values=[[Paragraph(escape(str(c)),normal) for c in headers]]+[[Paragraph(escape(str(c)),normal) for c in row] for row in rows]
        t=Table(values,colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),PALE),('TEXTCOLOR',(0,0),(-1,0),NAVY),('GRID',(0,0),(-1,-1),.35,colors.HexColor('#d9d1c3')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
        story.append(t)
    sales=stats['sales'](stats['month'])
    target=stats['target']
    pct=f'{(sales/target*100):.1f}%' if target else 'Sem meta geral definida'
    table('Resumo do dia e mês',['Indicador','Valor'],[
        ('Pedidos hoje',str(len(stats['daily']))),('Vendas registradas hoje',brl(stats['sales'](day.isoformat()))),
        ('Vendas registradas no mês',brl(sales)),('Metas gerais da equipe',brl(target)),('Atingimento das metas gerais',pct),
        ('Entradas liquidadas no mês',brl(stats['income'])),('Saídas liquidadas no mês',brl(stats['expense'])),
        ('Contas a receber',brl(stats['receivable'])),('Contas a pagar',brl(stats['payable']))],[92*mm,86*mm])
    series=[(month_back(day,i),stats['sales'](month_back(day,i))) for i in range(5,-1,-1)]
    peak=max([v for _,v in series]+[Decimal('1')])
    story.append(Paragraph('Evolução das vendas registradas · 6 meses',styles['L2Section']))
    chart=Drawing(178*mm,39*mm)
    for i,(key,total) in enumerate(series):
        x=13*mm+i*28*mm
        chart.add(Rect(x,9*mm,13*mm,float(total/peak)*19*mm,fillColor=GOLD,strokeColor=None))
        chart.add(String(x,7*mm,key[5:],fontSize=8,fillColor=NAVY))
        chart.add(String(x,31*mm,brl(total),fontSize=6.5,fillColor=NAVY))
    story.append(chart)
    table('Vendas do mês por indústria',['Indústria','Pedidos'],[(brand,brl(total)) for brand,total in sorted(stats['brands'].items(),key=lambda x:-x[1])][:20] or [('Sem pedidos','—')],[92*mm,86*mm])
    table('DRE gerencial preliminar · competência '+stats['month'],['Grupo','Valor'],[
      ('Receita operacional',brl(stats['revenue'])),('(-) Deduções',brl(-stats['deduction'])),
      ('(-) Custos diretos',brl(-stats['direct'])),('(-) Despesas operacionais',brl(-stats['operating'])),
      ('Resultado financeiro',brl(stats['financial'])),('(-) Tributos sobre resultado',brl(-stats['taxes'])),
      ('Resultado gerencial estimado',brl(stats['dre']))],[92*mm,86*mm])
    story.append(Spacer(1,4*mm))
    story.append(Paragraph(f"{stats['unclassified']} lançamentos do mês sem classificação DRE. Vendas são pedidos das indústrias e não receita automática da L2. Os valores dependem de classificação e conciliação contábil.",styles['L2Note']))
    doc.build(story)
    return buffer.getvalue()

def send_daily(con, report_day=None):
    day=report_day or datetime.now(TZ).date()
    con.execute('CREATE TABLE IF NOT EXISTS report_delivery (report_date DATE PRIMARY KEY, delivered_at TIMESTAMPTZ NOT NULL DEFAULT now(), recipient TEXT NOT NULL)')
    con.execute('SELECT pg_advisory_xact_lock(%s)', (82719026,))
    if con.execute('SELECT 1 FROM report_delivery WHERE report_date=%s',(day,)).fetchone():
        return 0
    smtp_host=os.getenv('L2_SMTP_HOST')
    smtp_user=os.getenv('L2_SMTP_USER')
    smtp_password=os.getenv('L2_SMTP_PASSWORD')
    if not (smtp_host and smtp_user and smtp_password):
        raise RuntimeError('Configure L2_SMTP_HOST, L2_SMTP_USER e L2_SMTP_PASSWORD no serviço de relatório.')
    pdf=build_pdf(read_data(con),day)
    msg=EmailMessage()
    msg['Subject']='L2 ONE | Relatório diário '+day.strftime('%d/%m/%Y')
    msg['From']=os.getenv('L2_SMTP_FROM',smtp_user)
    msg['To']='comerciall2belem@gmail.com'
    msg.set_content('Relatório diário L2 ONE em anexo. Dados financeiros e comerciais referentes a '+day.strftime('%d/%m/%Y')+'.')
    msg.add_attachment(pdf,maintype='application',subtype='pdf',filename='L2_ONE_relatorio_'+day.isoformat()+'.pdf')
    port=int(os.getenv('L2_SMTP_PORT','465'))
    with smtplib.SMTP_SSL(smtp_host,port,context=ssl.create_default_context(),timeout=30) as smtp:
        smtp.login(smtp_user,smtp_password)
        smtp.send_message(msg)
    con.execute('INSERT INTO report_delivery(report_date,recipient) VALUES(%s,%s)',(day,'comerciall2belem@gmail.com'))
    return len(pdf)

if __name__=='__main__':
    import psycopg
    with psycopg.connect(os.environ['DATABASE_URL']) as connection:
        count=send_daily(connection)
        print(f'Relatório entregue via SMTP ({count} bytes)')
