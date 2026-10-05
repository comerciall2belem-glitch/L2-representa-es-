"""Captura transacional de leads e distribuição comercial inicial."""
import re
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb


class LeadIntake(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    email: str | None = Field(default=None, max_length=180)
    phone: str | None = Field(default=None, max_length=30)
    company: str | None = Field(default=None, max_length=180)
    taxId: str | None = Field(default=None, max_length=18)
    state: str | None = Field(default=None, max_length=2)
    city: str | None = Field(default=None, max_length=120)
    origin: str = Field(default='Formulário', max_length=120)
    campaign: str | None = Field(default=None, max_length=120)
    landingPage: str | None = Field(default=None, max_length=500)
    utmSource: str | None = Field(default=None, max_length=120)
    utmMedium: str | None = Field(default=None, max_length=120)
    utmCampaign: str | None = Field(default=None, max_length=120)
    utmContent: str | None = Field(default=None, max_length=120)
    utmTerm: str | None = Field(default=None, max_length=120)
    customFields: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


def normalize_intake(data: LeadIntake, valid_cnpj):
    name=' '.join(data.name.split())
    email=(data.email or '').strip().lower()
    phone=re.sub(r'\D','',data.phone or '')
    if len(phone) in (12,13) and phone.startswith('55'):
        phone=phone[2:]
    tax_id=re.sub(r'\D','',data.taxId or '')
    state=(data.state or '').strip().upper()
    if not name or not (email or phone):
        raise HTTPException(422,'Informe nome e e-mail ou telefone')
    if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
        raise HTTPException(422,'E-mail inválido')
    if phone and len(phone) not in (10,11):
        raise HTTPException(422,'Telefone inválido; informe DDD')
    if tax_id and not valid_cnpj(tax_id):
        raise HTTPException(422,'CNPJ inválido')
    if state and state not in ('PA','AP'):
        raise HTTPException(422,'UF deve ser PA ou AP')
    if len(data.customFields)>20 or any(len(k)>80 or len(str(v))>500 for k,v in data.customFields.items()):
        raise HTTPException(422,'Campos adicionais excedem o limite')
    if data.landingPage and not data.landingPage.startswith('https://'):
        raise HTTPException(422,'Landing page deve usar HTTPS')
    return {'name':name,'email':email or None,'phone':phone or None,'taxId':tax_id or None,
            'company':' '.join((data.company or '').split()) or None,'state':state or None,
            'city':' '.join((data.city or '').split()) or None,
            'origin':data.origin.strip() or 'Formulário','campaign':data.campaign,
            'landingPage':data.landingPage,'utmSource':data.utmSource,'utmMedium':data.utmMedium,
            'utmCampaign':data.utmCampaign,'utmContent':data.utmContent,'utmTerm':data.utmTerm,
            'customFields':data.customFields}


def next_seller(active, last):
    """Stable ordering lets the database cursor survive team changes."""
    names=sorted(set(active),key=str.casefold)
    if not names:return None
    return next((name for name in names if last and name.casefold()>last.casefold()),names[0])


def ingest_lead(con, item, source='webhook'):
    """Call inside one DB transaction. Serialize intake for dedup and round robin."""
    con.execute('SELECT pg_advisory_xact_lock(%s)',(8239017,))
    matches=con.execute('''SELECT id FROM capture_leads WHERE
          (%s::text IS NOT NULL AND email_norm=%s) OR
          (%s::text IS NOT NULL AND phone_norm=%s)
          ORDER BY created_at LIMIT 2''',(item['email'],item['email'],item['phone'],item['phone'])).fetchall()
    if matches:
        if len({row[0] for row in matches})>1:
            raise HTTPException(409,'Contatos coincidem com leads diferentes; revise os cadastros')
        lead_id=matches[0][0]
        con.execute('INSERT INTO capture_lead_events(lead_id,event_type,details) VALUES(%s,%s,%s)',
                    (lead_id,'duplicate_received',Jsonb({'source':source})))
        return {'id':lead_id,'created':False,'duplicate':True}
    account_id=None
    if item['company'] or item['taxId']:
        if item['taxId']:
            row=con.execute('SELECT id FROM capture_accounts WHERE tax_id=%s',(item['taxId'],)).fetchone()
            account_id=row[0] if row else None
        if account_id is None:
            account_id=str(uuid.uuid4())
            con.execute('''INSERT INTO capture_accounts(id,name,tax_id,state,city)
              VALUES(%s,%s,%s,%s,%s)''',(account_id,item['company'] or item['name'],item['taxId'],item['state'],item['city']))
    contact_id=str(uuid.uuid4())
    con.execute('''INSERT INTO capture_contacts(id,account_id,name,email_norm,phone_norm)
      VALUES(%s,%s,%s,%s,%s)''',(contact_id,account_id,item['name'],item['email'],item['phone']))
    lead_id=str(uuid.uuid4())
    con.execute('''INSERT INTO capture_leads(id,account_id,contact_id,status,origin,campaign,landing_page,
      utm_source,utm_medium,utm_campaign,utm_content,utm_term,custom_fields,email_norm,phone_norm)
      VALUES(%s,%s,%s,'Novo / Não Distribuído',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
      (lead_id,account_id,contact_id,item['origin'],item['campaign'],item['landingPage'],
       item['utmSource'],item['utmMedium'],item['utmCampaign'],item['utmContent'],item['utmTerm'],
       Jsonb(item['customFields']),item['email'],item['phone']))
    con.execute('INSERT INTO capture_lead_events(lead_id,event_type,details) VALUES(%s,%s,%s)',
                (lead_id,'created',Jsonb({'source':source,'origin':item['origin']})))
    row=con.execute('SELECT last_username FROM capture_routing_state WHERE id=1 FOR UPDATE').fetchone()
    sellers=[r[0] for r in con.execute("""SELECT username FROM app_users WHERE active AND role='Vendedor'
      AND sectors @> '["commercial"]'::jsonb ORDER BY username""").fetchall()]
    selected=next_seller(sellers,row[0] if row else None)
    if selected:
        con.execute('''UPDATE capture_leads SET owner=%s,status='Em Qualificação / Distribuído',
          assigned_at=now(),updated_at=now() WHERE id=%s''',(selected,lead_id))
        con.execute('UPDATE capture_routing_state SET last_username=%s,updated_at=now() WHERE id=1',(selected,))
        con.execute('INSERT INTO capture_lead_events(lead_id,event_type,actor,details) VALUES(%s,%s,%s,%s)',
                    (lead_id,'distributed',selected,Jsonb({'method':'round_robin'})))
    return {'id':lead_id,'created':True,'duplicate':False,'status':'Em Qualificação / Distribuído' if selected else 'Novo / Não Distribuído','owner':selected}


def lead_sla(created_at, responded_at, now=None, minutes=120):
    if responded_at:return 'Atendido'
    elapsed=((now or datetime.now(timezone.utc))-created_at).total_seconds()/60
    return 'Vencido' if elapsed>=minutes else 'A vencer'
