"""Read-only strategic reporting. No writes, inferred invoices or generated leads."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import csv
import io

CATALOG = {
 'commercial': [('pipeline','Funil de vendas'),('brands','Vendas por marca e categoria'),('regions','Vendas por região'),('commissions','Comissões')],
 'financial': [('aging','Contas a receber e atrasos'),('billing','Faturamento e metas'),('cashflow','Fluxo de caixa projetado')],
 'operations': [('deliveries','Pedidos e entregas'),('abc_clients','Curva ABC de clientes'),('abc_products','Curva ABC de produtos')],
 'productivity': [('activities','Atividades da equipe'),('prospecting','Conversão de prospecção'),('inactive','Clientes inativos')],
}
FINANCIAL = {'aging','billing','cashflow','commissions'}
KINDS = ('client','order','opportunity','interaction','visit','lead','fulfillment','goal','settlement','office_finance','financial_input')
def number(value):
    try:
        n=Decimal(str(value or 0))
        return n if n.is_finite() else Decimal(0)
    except (InvalidOperation, ValueError): return Decimal(0)
def day(value):
    try: return date.fromisoformat(str(value)[:10])
    except ValueError: return None
def seller(row): return row.get('sellerResponsible') or row.get('owner') or row.get('user') or 'Não informado'
def abc(values):
    total=sum(values.values(),Decimal(0)); running=Decimal(0); rows=[]
    for label,value in sorted(values.items(),key=lambda x:(-x[1],x[0])):
        before=running/total if total else Decimal(1);running+=value
        rows.append([label,float(value),float(value/total*100) if total else 0,'A' if before<Decimal('.8') else 'B' if before<Decimal('.95') else 'C'])
    return rows

def build(data, report, start, end, asof, inactive_days=60):
    notes=[];rows=[]; columns=[]
    clients={c['id']:c for c in data.get('client',[]) if c.get('id')}
    def within(value):
        d=day(value);return bool(d and start<=d<=end)
    orders=[o for o in data.get('order',[]) if within(o.get('date')) and o.get('status') in ('Confirmado','Faturado','Entregue')]
    def client_name(cid):return clients.get(cid,{}).get('name') or cid or 'Não informado'
    if report.startswith('abc_'):orders=[o for o in orders if o.get('status') in ('Faturado','Entregue')]
    if report=='deliveries':orders=[o for o in data.get('order',[]) if within(o.get('date')) and o.get('status')!='Cancelado']
    if report=='pipeline':
        columns=['Responsável','Etapa','Quantidade','Valor R$','Conversão encerrados %']
        groups=defaultdict(list)
        for o in data.get('opportunity',[]):
            if within(o.get('createdAt')): groups[seller(o)].append(o)
        for owner,items in sorted(groups.items()):
            closed=[o for o in items if o.get('stage') in ('Ganho','Perdido')]
            pct=100*sum(o.get('stage')=='Ganho' for o in closed)/len(closed) if closed else None
            for stage in sorted({o.get('stage') or 'Não informado' for o in items}):
                selected=[o for o in items if (o.get('stage') or 'Não informado')==stage]
                rows.append([owner,stage,len(selected),float(sum((number(o.get('amount')) for o in selected),Decimal(0))),pct])
        all_closed=[o for items in groups.values() for o in items if o.get('stage') in ('Ganho','Perdido')]
        rows.append(['Total','Encerrados',len(all_closed),None,100*sum(o.get('stage')=='Ganho' for o in all_closed)/len(all_closed) if all_closed else None])
        notes.append('Coorte por criação; conversão = ganhos / (ganhos + perdas). Sem datas ou encerramentos: sem base.')
    elif report in ('brands','regions','abc_clients','abc_products'):
        values=defaultdict(Decimal);qty=defaultdict(Decimal)
        for o in orders:
            if report in ('regions','abc_clients'):
                c=clients.get(o.get('clientId'),{})
                label=' / '.join(str(c.get(k) or 'Não informado') for k in ('state','city','district')) if report=='regions' else client_name(o.get('clientId'))
                values[label]+=number(o.get('amount'));qty[label]+=1
            else:
                for item in o.get('items',[]):
                    label=' / '.join(str(x or 'Não informado') for x in ((item.get('brand') or o.get('brand')),item.get('category'))) if report=='brands' else ' / '.join(str(x or 'Não informado') for x in (item.get('brand') or o.get('brand'),item.get('sku'),item.get('description')))
                    q=number(item.get('quantity'));values[label]+=number(item.get('unitPrice'))*q;qty[label]+=q
        if report.startswith('abc_'):
            columns=['Cliente' if report=='abc_clients' else 'Marca / SKU / Produto','Valor R$','Participação %','Curva'];rows=abc(values)
            notes.append('ABC: A até cruzar 80%, B até cruzar 95%, C restante; pedido faturado ou entregue, pela data do pedido; não substitui conciliação de NF.')
        else:
            columns=['Marca / Categoria' if report=='brands' else 'UF / Município / Bairro','Quantidade' if report=='brands' else 'Pedidos','Valor R$']
            rows=[[k,float(qty[k]),float(v)] for k,v in sorted(values.items())]
        notes.append('Volume comercial de pedidos, não receita própria da L2. Categoria/bairro ausentes são identificados.')
    elif report in ('billing','commissions'):
        docs=[r for r in data.get('settlement',[]) if r.get('invoiceNumber') and within(r.get('billedDate'))]
        total=sum((number(r.get('billed')) for r in docs),Decimal(0))
        if report=='billing':
            columns=['Período','Faturamento documentado R$','Meta R$','Atingimento %']
            for month in sorted({start.strftime('%Y-%m'),end.strftime('%Y-%m')}|{str(r['billedDate'])[:7] for r in docs}|{g.get('month','') for g in data.get('goal',[]) if start.strftime('%Y-%m')<=g.get('month','')<=end.strftime('%Y-%m')}):
                goals=[g for g in data.get('goal',[]) if g.get('month')==month and g.get('user')=='Equipe' and not g.get('brand')]
                target=number(goals[0].get('amount')) if len(goals)==1 else None
                billed=sum((number(r.get('billed')) for r in docs if str(r['billedDate']).startswith(month)),Decimal(0))
                rows.append([month,float(billed),float(target) if target is not None else None,float(billed/target*100) if target else None])
            notes.append('Faturamento usa liquidações com número de NF e data de faturamento; metas são mensais integrais, inclusive em período parcial.')
        else:
            columns=['Pedido / Regra','Vendedor','Base faturada R$','Percentual histórico %','Comissão R$','Bônus coletivo R$']
            by_order=defaultdict(Decimal)
            for r in docs:by_order[r.get('orderId')]+=number(r.get('billed'))
            for o in data.get('order',[]):
                if o.get('id') not in by_order:continue
                rate=o.get('sellerCommissionRate');base=by_order[o['id']]
                rows.append([o['id'],seller(o),float(base),float(number(rate)) if rate is not None else None,round(float(base*number(rate)/100),2) if rate is not None else None,None])
            from calendar import monthrange
            for month in sorted({str(r['billedDate'])[:7] for r in docs}):
                first=date.fromisoformat(month+'-01');last=date(first.year,first.month,monthrange(first.year,first.month)[1])
                goals=[g for g in data.get('goal',[]) if g.get('month')==month and g.get('user')=='Equipe' and not g.get('brand')]
                target=number(goals[0].get('amount')) if len(goals)==1 else None
                base=sum((number(r.get('billed')) for r in docs if str(r['billedDate']).startswith(month)),Decimal(0))
                contracts=[r for r in data.get('financial_input',[]) if r.get('kind')=='seller_contract' and day(r.get('effectiveDate')) and day(r['effectiveDate'])<=last]
                for owner in sorted({r.get('name','') for r in contracts}):
                    versions=[r for r in contracts if r.get('name')==owner]
                    rule=max(versions,key=lambda r:r['effectiveDate']);rate=rule.get('bonusRate');bonus=None
                    eligible=(start<=first and end>=last and asof>=last and day(rule['effectiveDate'])<=first and rule.get('basis')=='Faturado' and rule.get('contractReference') and rate is not None and target and target>0)
                    if eligible:bonus=round(float(base*number(rate)/100),2) if base>=target*number(rule.get('targetPercent') or 100)/100 else 0
                    rows.append(['Bônus '+month,owner,float(base),float(number(rate)) if rate is not None else None,None,bonus])
            notes.append('Percentual histórico preservado. Bônus separado: exige mês completo encerrado, meta Equipe única e contrato vigente desde o início do mês com base Faturado. Sem regra validada: Sem base; não gera pagamentos.')
    elif report in ('aging','cashflow'):
        columns=['Cliente / Contraparte','Vencimento','Situação','Dias de atraso','Valor R$'] if report=='aging' else ['Data prevista','Entrada R$','Saída R$','Saldo acumulado sem abertura R$']
        flows=defaultdict(lambda:[Decimal(0),Decimal(0)])
        for r in data.get('office_finance',[]):
            due=day(r.get('Vencimento'));liquidation=day(r.get('Liquidação'));paid=r.get('Situação')=='Pago/Recebido' and (liquidation is None or liquidation<=asof);income=r.get('Tipo')=='Receita'
            if day(r.get('Data')) and day(r['Data'])>asof:continue
            if report=='aging' and income:
                if due is None: notes.append('Há títulos sem vencimento; não classificados por atraso.');continue
                delay=max(0,((liquidation if paid and liquidation else asof)-due).days) if not paid or liquidation else None
                rows.append([client_name(r.get('clientId')) if r.get('clientId') else r.get('Contraparte','Não informado'),due.isoformat(),'Liquidado' if paid else 'A vencer' if due>=asof else '1-30' if delay<=30 else '31-60' if delay<=60 else '61-90' if delay<=90 else '90+',delay,float(number(r.get('Valor')))])
            elif report=='cashflow' and not paid and due and start<=due<=end:
                flows[due.isoformat()][0 if income else 1]+=number(r.get('Valor'))
        if report=='cashflow':
            balance=Decimal(0)
            for d,(inc,out) in sorted(flows.items()):balance+=inc-out;rows.append([d,float(inc),float(out),float(balance)])
            notes.append('Variação projetada dos títulos abertos cadastrados, sem saldo bancário inicial. Pedidos não são receita própria; somente recebíveis registrados entram no caixa.')
        else:notes.append('Posição dos títulos cadastrados na data de referência, independente do período de emissão. Histórico depende da data de liquidação registrada.')
    elif report=='deliveries':
        columns=['Pedido','Cliente','Status pedido','Etapa logística','Previsão','Alerta']
        for o in orders:
            events=[f for f in data.get('fulfillment',[]) if f.get('orderId')==o.get('id')]
            f=max(events,key=lambda x:str(x.get('at','')),default={});due=day(f.get('due'));stage=f.get('stage','Sem registro')
            closed=stage in ('Entregue','Pós-venda concluído') or o.get('status')=='Entregue'
            occurrence=(str(stage)+' '+str(f.get('notes') or '')).lower()
            retention=any(term in occurrence for term in ('retido','retida','retenção fiscal','retencao fiscal','aguardando fiscalização','aguardando fiscalizacao'))
            alert='Retenção fiscal' if retention and not closed else 'Atrasado' if due and due<asof and not closed else ''
            rows.append([o.get('id'),client_name(o.get('clientId')),o.get('status'),stage,f.get('due'),alert])
    elif report=='activities':
        columns=['Responsável','Canal','Interações registradas'];counts=defaultdict(int)
        for kind in ('visit','interaction'):
            for r in data.get(kind,[]):
                if within(r.get('date') or r.get('at') or r.get('createdAt')):counts[(seller(r),'Visita' if kind=='visit' else r.get('channel') or r.get('type') or 'Não informado')]+=1
        rows=[[a,b,n] for (a,b),n in sorted(counts.items())]
        notes.append('Somente atividades persistidas; chamadas e WhatsApp sem eventos integrados não são estimados.')
    elif report=='prospecting':
        columns=['Origem','Leads','Convertidos em cliente','Conversão %'];groups=defaultdict(list)
        for r in data.get('lead',[]):
            if within(r.get('createdAt')):groups[r.get('source') or 'Não informado'].append(r)
        for source,items in sorted(groups.items()):
            n=sum(bool(r.get('clientId') in clients or any(c.get('sourceLeadId')==r.get('id') for c in clients.values())) for r in items);rows.append([source,len(items),n,100*n/len(items)])
        notes.append('Coorte pela data de captação; exige vínculo clientId ou sourceLeadId. Ausência do vínculo não comprova conversão.')
    elif report=='inactive':
        columns=['Cliente','Responsável','Último pedido','Dias sem pedido','Situação'];last={}
        for o in data.get('order',[]):
            d=day(o.get('date'))
            if d and d<=asof and o.get('status') in ('Confirmado','Faturado','Entregue'):last[o.get('clientId')]=max(d,last.get(o.get('clientId'),date.min))
        for cid,c in clients.items():
            d=max(filter(None,[last.get(cid),day(c.get('last_purchase')) if day(c.get('last_purchase')) and day(c.get('last_purchase'))<=asof else None]),default=None)
            if not d or (asof-d).days>=inactive_days:rows.append([c.get('name'),c.get('owner'),d.isoformat() if d else None,(asof-d).days if d else None,'Inativo' if d else 'Sem histórico'])
        notes.append('Clientes sem histórico separados de inativos; referência na data consultada.')
    return {'report':report,'start':str(start),'end':str(end),'asOf':str(asof),'columns':columns,'rows':rows,'notes':sorted(set(notes)),'count':len(rows)}

def export_csv(result):
    out=io.StringIO(newline='');writer=csv.writer(out,delimiter=';')
    def safe(v):
        if v is None:return 'Sem base'
        if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')):return "'"+v
        return v
    writer.writerow(result['columns']);writer.writerows([[safe(v) for v in row] for row in result['rows']])
    return ('\ufeff'+out.getvalue()).encode('utf-8')

def export_pdf(result):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, LongTable, TableStyle, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4, landscape
    from xml.sax.saxutils import escape
    out=io.BytesIO();styles=getSampleStyleSheet();doc=SimpleDocTemplate(out,pagesize=landscape(A4),leftMargin=28,rightMargin=28,topMargin=28,bottomMargin=28)
    label=next(title for group in CATALOG.values() for name,title in group if name==result['report'])
    story=[Paragraph('L2 ONE | '+escape(label),styles['Title']),Paragraph(f"{result['start']} a {result['end']} | Referência: {result['asOf']}",styles['Normal']),Spacer(1,12)]
    for note in result['notes']:story.extend([Paragraph(escape(note),styles['Normal']),Spacer(1,6)])
    def display(v,i):
        if v is None:return 'Sem base'
        if isinstance(v,(int,float)):
            text=f'{v:,.2f}'.replace(',','_').replace('.',',').replace('_','.')
            return 'R$ '+text if 'R$' in result['columns'][i] else text.rstrip('0').rstrip(',')
        return str(v)
    cells=[[Paragraph(escape(str(v)),styles['Normal']) for v in result['columns']]]
    cells.extend([[Paragraph(escape(display(v,i)),styles['Normal']) for i,v in enumerate(row)] for row in result['rows']])
    table=LongTable(cells,repeatRows=1,colWidths=[doc.width/len(result['columns'])]*len(result['columns']))
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dcebed')),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,0),.6,colors.HexColor('#164955')),('BOTTOMPADDING',(0,0),(-1,-1),7)]));story.append(table)
    doc.build(story);return out.getvalue()
