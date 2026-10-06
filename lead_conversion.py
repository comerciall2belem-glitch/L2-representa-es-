"""Conversão por coorte mensal de empresas, com vínculo explícito ou CNPJ."""
import re

def conversion_metrics(leads, clients, orders, month):
    digits=lambda v: re.sub(r'\D','',str(v or ''))
    groups={}
    for lead in sorted(leads,key=lambda x:str(x.get('createdAt') or '')):
        key=digits(lead.get('taxId')) or str(lead['id'])
        groups.setdefault(key,[]).append(lead)
    by_tax={digits(c.get('taxId')):c for c in clients if digits(c.get('taxId'))}
    converted=won=qualified=total=undated=0
    for key,records in groups.items():
        first=records[0];created=str(first.get('createdAt') or '')
        if not created:undated+=1;continue
        if not created.startswith(month):continue
        total+=1
        linked=next((c for c in clients if any(c.get('id')==l.get('clientId') or c.get('sourceLeadId')==l['id'] for l in records)),None) or by_tax.get(key)
        if any(l.get('firstResponseAt') or l.get('status') in ('Qualificado','Convertido','Em Atendimento') for l in records):qualified+=1
        if linked:
            converted+=1
            if any(o.get('clientId')==linked['id'] and o.get('status') in ('Confirmado','Faturado') and str(o.get('date') or '')>=created[:10] for o in orders):won+=1
    return {'leads':total,'contacted':qualified,'clients':converted,'buyers':won,'conversion':100*won/total if total else None,'undated':undated,'month':month}
