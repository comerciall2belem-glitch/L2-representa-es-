"""TDC → SRI → MCR: shared commercial intelligence and transactional projections."""
import math
import re
import uuid
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from psycopg.types.json import Jsonb

TZ = ZoneInfo('America/Belem')
CHANNELS = {'Farma', 'Alimentar', 'Cosméticos', 'Especializada', 'Departamento', 'Salão', 'Atacado'}
BUYING = {'Confirmado', 'Faturado', 'Entregue'}
router = APIRouter(prefix='/api/strategy', tags=['TDC / SRI / MCR'])
_services = {}

def configure(**services):
    _services.update(services)

def setup(con):
    con.execute('CREATE INDEX IF NOT EXISTS strategic_client_events ON entities(kind, (payload->>\'clientId\'))')

def local_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None

def put(con, kind, item):
    con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()', (kind, item['id'], Jsonb(item)))

def changed(con):
    con.execute("SELECT pg_notify('l2_records_changed', '')")

def record(con, kind, identifier):
    row = con.execute('SELECT payload FROM entities WHERE kind=%s AND id=%s', (kind, identifier)).fetchone()
    if not row:
        raise HTTPException(404, 'Registro não encontrado')
    return row[0]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Qualification(StrictModel):
    name: str = Field(min_length=2, max_length=180)
    taxId: str = Field(min_length=14, max_length=18)
    state: str = Field(pattern='^(PA|AP)$')
    city: str = Field(min_length=2, max_length=120)
    channel: str = Field(min_length=2, max_length=40)
    cnae: str = Field(pattern=r'^\d{7}$')
    size: str = Field(pattern='^(MEI|Micro|Pequena|Média|Grande)$')
    segment: str = Field(min_length=2, max_length=120)
    demandEvidence: str = Field(min_length=10, max_length=1000)
    evidenceDate: date
    urgencyDays: int = Field(ge=0, le=90)
    estimatedMonthlyValue: float = Field(ge=0, le=100000000, allow_inf_nan=False)
    phone: str = Field(default='', max_length=30)
    district: str = Field(default='', max_length=120)
    address: str = Field(default='', max_length=250)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    opens: str = Field(default='09:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    closes: str = Field(default='18:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')

def qualify(data, today=None):
    today = today or datetime.now(TZ).date()
    age = (today - data.evidenceDate).days
    if not 0 <= age <= 30:
        raise HTTPException(422, 'Evidência de demanda deve ter até 30 dias e não pode ser futura')
    digits=re.sub(r'\D','',data.phone)
    if digits and not (len(digits) in (10,11) or (digits.startswith('55') and len(digits) in (12,13))):
        raise HTTPException(422,'Telefone inválido; informe DDD')
    if data.channel not in CHANNELS or not data.name.strip() or not data.city.strip() or not data.segment.strip() or len(data.demandEvidence.strip()) < 10:
        raise HTTPException(422, 'Revise o canal, empresa, cidade, segmento e a evidência')
    if (data.latitude is None) != (data.longitude is None) or data.opens >= data.closes:
        raise HTTPException(422, 'Informe as duas coordenadas e um horário válido no mesmo dia')
    # CNAE validates a commercial activity, never invents purchasing intent.
    channel_prefixes = {'Farma':('4771',), 'Alimentar':('471','472'), 'Cosméticos':('47725','4646'), 'Especializada':('47725','4646'), 'Departamento':('4713',), 'Salão':('9602',), 'Atacado':('46',)}
    compatible = data.cnae.startswith(channel_prefixes[data.channel])
    score = (35 if compatible else 0) + (20 if data.estimatedMonthlyValue >= 2000 else 5) + (25 if data.urgencyDays <= 14 else 10) + (20 if age <= 7 else 10)
    return {'score': score, 'qualified': compatible and score >= 70,
            'tag': 'TDC qualificado' if compatible and score >= 70 else 'TDC em análise',
            'evidence': data.demandEvidence.strip(), 'evidenceDate': data.evidenceDate.isoformat(),
            'segment': data.segment.strip(), 'channel': data.channel, 'cnae': data.cnae,
            'size': data.size, 'urgencyDays': data.urgencyDays,
            'estimatedMonthlyValue': data.estimatedMonthlyValue, 'verifiedAt': today.isoformat()}

def valid_demand(info, today):
    evidence=local_date(info.get('evidenceDate'))
    return bool(info.get('qualified') and evidence and 0<=(today-evidence).days<=30)

def safe_amount(value):
    try:
        n = float(value or 0)
        return n if math.isfinite(n) and n>=0 else 0
    except (TypeError,ValueError): return 0

def cadence(client, orders, visits=(), today=None):
    today = today or datetime.now(TZ).date()
    purchases = sorted({d for o in orders if o.get('clientId') == client['id'] and o.get('status') in BUYING and (d := local_date(o.get('date'))) and d <= today})
    contacts = [d for v in visits if v.get('clientId') == client['id'] and (d := local_date(v.get('at') or v.get('date'))) and d <= today]
    intervals = [(b-a).days for a,b in zip(purchases, purchases[1:])]
    cycle = max(7, min(180, round(median(intervals)))) if len(intervals) >= 2 else 30
    last = purchases[-1] if purchases else None
    age = (today-last).days if last else None
    status = 'Sem histórico' if age is None else 'Inativo' if age >= 60 else 'Em risco' if age >= 15 or age > cycle else 'Ativo'
    last_contact = max(contacts) if contacts else None
    next_contact = last_contact + timedelta(days=7) if last_contact else today
    # A purchase resets contact cadence even when there is no recorded visit.
    if last and (not last_contact or last > last_contact):
        next_contact = last + timedelta(days=7)
    return {'status': status, 'lastPurchase': last.isoformat() if last else None,
            'daysSincePurchase': age, 'cycleDays': cycle, 'cycleSource': 'Histórico' if len(intervals)>=2 else 'Padrão',
            'purchaseCount': len(purchases),
            'recordedRevenue': round(sum(safe_amount(o.get('amount')) for o in orders if o.get('clientId')==client['id'] and o.get('status') in BUYING and (d:=local_date(o.get('date'))) and d<=today),2),
            'nextContact': next_contact.isoformat(), 'overdueCycle': age is not None and age > cycle,
            'priority': (40 if status=='Inativo' else 25 if status=='Em risco' else 10 if status=='Sem histórico' else 0) + (min(40, safe_amount(client.get('tdc', {}).get('score', 0))*0.4) if valid_demand(client.get('tdc',{}),today) else 0),
            'evaluatedDate': today.isoformat(), 'updatedAt': datetime.now(TZ).isoformat()}

def refresh_client(con, client_id):
    con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s FOR UPDATE",(client_id,))
    client = record(con, 'client', client_id)
    rows = con.execute("SELECT kind,payload FROM entities WHERE kind IN ('order','visit','interaction') AND payload->>'clientId'=%s", (client_id,)).fetchall()
    info = cadence(client, [p for k,p in rows if k=='order'], [p for k,p in rows if k!='order'])
    client['mcr'] = info
    put(con, 'client', client)
    task_id = 'mcr-' + client_id
    previous = con.execute("SELECT payload FROM entities WHERE kind='task' AND id=%s", (task_id,)).fetchone()
    task = {'id': task_id, 'clientId': client_id, 'user': client.get('owner',''), 'date': info['nextContact'],
            'text': 'Reativar cliente' if info['status']=='Inativo' else 'Contato de acompanhamento',
            'notes': 'MCR · '+info['status'], 'status': 'Aberta', 'source':'MCR'}
    if previous and previous[0].get('date') == task['date']:
        task['status'] = previous[0].get('status', 'Aberta')
    put(con, 'task', task)
    # Preserve published agenda; update priority instead of moving appointments silently.
    for row in con.execute("SELECT payload FROM entities WHERE kind='route' AND payload->>'clientId'=%s AND payload->>'date'>=%s", (client_id, datetime.now(TZ).date().isoformat())).fetchall():
        route = row[0]
        route['priority'] = info['priority']
        route['reason'] = info['status']
        route['needsReview'] = True
        put(con, 'route', route)
    return info

def event(con, kind, item):
    if kind in ('order','visit','interaction') and item.get('clientId'):
        refresh_client(con, item['clientId'])

def minutes(value):
    h,m = map(int,value.split(':'))
    return h*60+m

def coords(item):
    lat,lon = item.get('latitude'),item.get('longitude')
    return (lat,lon) if isinstance(lat,(int,float)) and isinstance(lon,(int,float)) and math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180 else None

def travel(a,b):
    x,y = coords(a),coords(b)
    if not x or not y: return None
    lat1,lon1,lat2,lon2 = map(math.radians, (*x,*y))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    km = 6371*2*math.asin(math.sqrt(min(1,h)))
    return {'km': round(km,2), 'minutes': max(3, math.ceil(km*1.4/25*60))}

class RouteRequest(StrictModel):
    date: date
    state: str = Field(pattern='^(PA|AP)$')
    city: str = Field(min_length=2, max_length=120)
    start: str = Field(default='09:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end: str = Field(default='18:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    limit: int = Field(default=8, ge=1, le=10)
    visitMinutes: int = Field(default=40, ge=15, le=120)
    originLatitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    originLongitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    costPerKm: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    commit: bool = False

def plan_route(clients, data):
    if data.start >= data.end:
        raise HTTPException(422, 'Hora final deve ser posterior à inicial')
    current = {'latitude':data.originLatitude,'longitude':data.originLongitude}
    clock, end = minutes(data.start),minutes(data.end)
    candidates = [c for c in clients if c.get('state')==data.state and str(c.get('city','')).strip().casefold()==data.city.strip().casefold()]
    omitted = [{'clientId':c['id'],'reason':'Sem coordenadas; geocodificar endereço'} for c in candidates if not coords(c)]
    omitted += [{'clientId':c['id'],'reason':'Loja fechada nesse dia'} for c in candidates if coords(c) and data.date.weekday() in c.get('closedDays',[6])]
    remaining = [c for c in candidates if coords(c) and data.date.weekday() not in c.get('closedDays',[6])]
    stops, total = [],0
    while remaining and len(stops)<data.limit:
        feasible = []
        for c in remaining:
            t = travel(current,c)
            try: opening,closing = minutes(c.get('opens') or '09:00'), minutes(c.get('closes') or '18:00')
            except (ValueError,TypeError): continue
            arrival = max(clock+t['minutes'], opening)
            back = travel(c,{'latitude':data.originLatitude,'longitude':data.originLongitude})
            if arrival+data.visitMinutes+back['minutes'] > end or arrival+data.visitMinutes > closing: continue
            priority = float(c.get('mcr',{}).get('priority',0))
            feasible.append((priority-t['minutes']*0.5, c['id'],c,t,arrival))
        if not feasible: break
        _,_,c,t,arrival = max(feasible, key=lambda v:(v[0],v[1]))
        clock = arrival+data.visitMinutes
        stops.append({'clientId':c['id'],'order':len(stops)+1,'arrival':f'{arrival//60:02}:{arrival%60:02}',
                      'departure':f'{clock//60:02}:{clock%60:02}', 'travelMinutes':t['minutes'], 'distanceKm':t['km'],
                      'priority':c.get('mcr',{}).get('priority',0),'reason':c.get('mcr',{}).get('status','Prospecção TDC')})
        total += t['km']; current = c; remaining.remove(c)
    back = travel(current,{'latitude':data.originLatitude,'longitude':data.originLongitude}) if stops else {'km':0,'minutes':0}
    total += back['km']
    omitted += [{'clientId':c['id'],'reason':'Limite de visitas ou janela insuficiente'} for c in remaining]
    return {'stops':stops,'omitted':omitted,'distanceKm':round(total,2),'returnMinutes':back['minutes'],
            'estimatedExpense':round(total*data.costPerKm,2), 'estimate':True,
            'method':'Heurística por prioridade, distância geográfica e janelas; sem trânsito ou malha rodoviária'}

def access(header, sector='commercial'):
    user = _services['auth'](header)
    _services['require_sector'](user, sector)
    return user

@router.post('/tdc/qualify')
def tdc_qualify(data: Qualification, authorization: str | None = Header(default=None)):
    user = access(authorization)
    tax = re.sub(r'\D','',data.taxId)
    if not _services['valid_cnpj'](tax): raise HTTPException(422,'CNPJ inválido')
    info = qualify(data)
    with _services['db']() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)', (8239020,))
        con.execute('SELECT pg_advisory_xact_lock(%s)', (8239018,))
        matches = con.execute("SELECT payload FROM entities WHERE kind='client' AND regexp_replace(payload->>'taxId','[^0-9]','','g')=%s",(tax,)).fetchall()
        if len(matches)>1: raise HTTPException(409,'CNPJ duplicado na carteira; revisar antes de qualificar')
        if matches:
            client = matches[0][0]
            _services['check_client_scope'](con,user,client['id'])
        else:
            client = {'id':str(uuid.uuid4()),'name':data.name.strip(),'taxId':tax,'state':data.state,'city':data.city.strip(),'owner':user,
                      'phone':data.phone,'district':data.district,'address':data.address,'latitude':data.latitude,'longitude':data.longitude,
                      'opens':data.opens,'closes':data.closes,'stage':'Prospecção','createdAt':datetime.now(TZ).isoformat()}
        client['tdc'] = info
        lead_id = 'tdc-'+tax
        previous_lead = con.execute("SELECT payload FROM entities WHERE kind='lead' AND id=%s",(lead_id,)).fetchone()
        if previous_lead and _services['is_seller'](con,user) and previous_lead[0].get('owner')!=user:
            raise HTTPException(403,'Lead pertence a outra carteira')
        put(con,'lead',{'id':lead_id,'name':data.name.strip(),'taxId':tax,'state':data.state,'city':data.city.strip(),
                        'clientId':client['id'] if info['qualified'] or matches else None,'owner':client['owner'],
                        'status':info['tag'],'tdc':info,'createdAt':previous_lead[0].get('createdAt') if previous_lead else datetime.now(TZ).isoformat()})
        if info['qualified'] or matches:
            put(con,'client',client)
            con.execute('INSERT INTO clientes(id,razao_social,nome_fantasia,documento) VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(client['id'],client['name'],client.get('tradeName'),client['taxId']))
            refresh_client(con,client['id'])
            con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'client',client['id'],'tdc_qualification'))
        changed(con)
    return {'qualification':info,'clientId':client['id'] if info['qualified'] or matches else None,'leadId':lead_id}

@router.get('/overview')
def overview(authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        clients = _services['scoped_rows'](con,'client',user)
        orders = _services['scoped_rows'](con,'order',user)
        visits = _services['scoped_rows'](con,'visit',user)+_services['scoped_rows'](con,'interaction',user)
    rows = [{'id':c['id'],'name':c.get('name',''),'city':c.get('city',''),'state':c.get('state',''),
             'tdc':c.get('tdc'),'mcr':cadence(c,orders,visits),
             'brandCycles': {brand:cadence(c,[o for o in orders if o.get('brand')==brand or any(i.get('brand')==brand for i in o.get('items',[]))],visits) for brand in sorted({o.get('brand') or i.get('brand') for o in orders if o.get('clientId')==c['id'] for i in (o.get('items') or [{}])} - {None,''})}} for c in clients]
    return {'clients':sorted(rows,key=lambda c:c['mcr']['priority'],reverse=True),
            'searchSuggestions':sorted({c['tdc']['segment'] for c in rows if c['tdc'] and c['mcr']['status']=='Inativo'}),
            'updatedAt':datetime.now(TZ).isoformat()}

@router.post('/sri/plan')
def sri_plan(data: RouteRequest, authorization: str | None = Header(default=None)):
    user = access(authorization,'routes')
    if data.date < datetime.now(TZ).date(): raise HTTPException(422,'Escolha hoje ou uma data futura')
    with _services['db']() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)', (8239019,))
        clients = _services['scoped_rows'](con,'client',user)
        orders = _services['scoped_rows'](con,'order',user)
        visits = _services['scoped_rows'](con,'visit',user)+_services['scoped_rows'](con,'interaction',user)
        for c in clients: c['mcr'] = cadence(c,orders,visits,today=data.date)
        result = plan_route(clients,data)
        if data.commit:
            existing = con.execute("SELECT 1 FROM entities WHERE kind='route' AND payload->>'user'=%s AND payload->>'date'=%s LIMIT 1", (user,data.date.isoformat())).fetchone()
            if existing: raise HTTPException(409,'Já existe rota nesse dia; revise a agenda antes de substituir')
            for stop in result['stops']:
                identifier = str(uuid.uuid4())
                put(con,'route',{'id':identifier,'user':user,'date':data.date.isoformat(),'source':'SRI','status':'Planejado',**stop})
                put(con,'task',{'id':'sri-'+identifier,'clientId':stop['clientId'],'user':user,'date':data.date.isoformat(),
                                'text':'Visita SRI às '+stop['arrival'],'status':'Aberta','source':'SRI','routeId':identifier})
            # Forecast only: an estimated route is never booked as an actual expense.
            put(con,'task',{'id':'sri-expense-'+user+'-'+data.date.isoformat(),'user':user,'date':data.date.isoformat(),
                           'text':'Conferir despesas da rota SRI','status':'Aberta','estimatedExpense':result['estimatedExpense'],'source':'SRI'})
            changed(con)
    return result

@router.post('/mcr/refresh')
def mcr_refresh(authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)', (8239020,))
        clients = _services['scoped_rows'](con,'client',user)
        for client in clients: refresh_client(con,client['id'])
        changed(con)
    return {'updated':len(clients)}

class CadenceSettings(StrictModel):
    enabled: bool = False
    template: str = Field(default='', max_length=100, pattern=r'^[a-z0-9_]*$')
    language: str = Field(default='pt_BR', pattern=r'^[a-z]{2}(?:_[A-Z]{2})?$')

class Consent(StrictModel):
    enabled: bool
    evidence: str = Field(min_length=5, max_length=500)

@router.put('/mcr/settings')
def mcr_settings(data: CadenceSettings, authorization: str | None = Header(default=None)):
    user = access(authorization)
    if user not in ('Ana Paula','Euler'): raise HTTPException(403,'Configuração restrita aos sócios')
    if data.enabled and not data.template: raise HTTPException(422,'Informe o nome do modelo aprovado na Meta')
    with _services['db']() as con:
        put(con,'strategy_settings',{'id':'mcr','updatedBy':user,**data.model_dump()})
    return data.model_dump()

@router.put('/mcr/consent/{client_id}')
def consent(client_id: str, data: Consent, authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)', (8239020,))
        _services['check_client_scope'](con,user,client_id)
        client = record(con,'client',client_id)
        client['mcrConsent'] = {**data.model_dump(),'recordedBy':user,'recordedAt':datetime.now(TZ).isoformat()}
        put(con,'client',client)
        changed(con)
    return {'clientId':client_id,'enabled':data.enabled}

def template_payload(phone, settings, client):
    phone = re.sub(r'\D','',str(phone))
    if len(phone) in (10,11): phone = '55'+phone
    if not re.fullmatch(r'55\d{10,11}',phone): raise ValueError('Telefone inválido')
    return {'messaging_product':'whatsapp','to':phone,'type':'template',
            'template':{'name':settings['template'],'language':{'code':settings['language']},
                        'components':[{'type':'body','parameters':[{'type':'text','text':client.get('name','Cliente')[:180]},
                                    {'type':'text','text':client.get('tdc',{}).get('segment','nosso portfólio')[:120]}]}]}}

def tick():
    """Durable at-most-once attempts; ambiguous sends require manual reconciliation."""
    from whatsapp_media import provider_config, _post
    import json
    now = datetime.now(TZ)
    # Refresh reminders while running; no page reload and no network call inside transaction.
    with _services['db']() as con:
        if not con.execute('SELECT pg_try_advisory_xact_lock(%s)',(8239020,)).fetchone()[0]: return
        clients = [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='client'").fetchall()]
        settings = con.execute("SELECT payload FROM entities WHERE kind='strategy_settings' AND id='mcr'").fetchone()
        settings = settings[0] if settings else {}
        config = provider_config()
        for client in clients:
            info = client.get('mcr',{})
            if info.get('evaluatedDate') != now.date().isoformat():
                info = refresh_client(con,client['id'])
            if not settings.get('enabled') or not config or now.weekday()>=5 or not 9<=now.hour<18: continue
            if not client.get('mcrConsent',{}).get('enabled') or info['nextContact']>now.date().isoformat(): continue
            try: payload = template_payload(client.get('phone',''),settings,client)
            except ValueError: continue
            # Date of the scheduled touch is the deduplication key, not the worker execution day.
            key = 'mcr-'+client['id']+'-'+info['nextContact']
            exists = con.execute("SELECT 1 FROM entities WHERE kind='strategy_send' AND id=%s",(key,)).fetchone()
            if exists: continue
            put(con,'strategy_send',{'id':key,'clientId':client['id'],'status':'Enviando','scheduledDate':info['nextContact'],'attemptAt':now.isoformat()})
            # One reservation per tick, commit before sending. Restart never resends a reservation.
            break
        else:
            changed(con)
            return
        changed(con)
    try:
        token,phone_id,version = config
        response = _post(f'https://graph.facebook.com/{version}/{phone_id}/messages',token,json.dumps(payload).encode(),'application/json')
        mid = response['messages'][0]['id']
        if not isinstance(mid,str) or not mid or len(mid)>256: raise ValueError('Resposta inválida')
        status = 'Aceito pela Meta'
    except Exception:
        mid = None; status = 'Conferir envio'
    with _services['db']() as con:
        put(con,'strategy_send',{'id':key,'clientId':client['id'],'status':status,'messageId':mid,'scheduledDate':info['nextContact'],'attemptAt':now.isoformat()})
        if mid:
            con.execute("INSERT INTO zara_conversations(phone,name,mode) VALUES(%s,%s,'human') ON CONFLICT(phone) DO UPDATE SET updated_at=now()",(payload['to'],client.get('name','')))
            con.execute("INSERT INTO zara_messages(message_id,phone,direction,body,delivered) VALUES(%s,%s,'out',%s,false) ON CONFLICT DO NOTHING",(mid,payload['to'],'MCR · Modelo '+settings['template']))
            put(con,'interaction',{'id':key,'clientId':client['id'],'date':now.date().isoformat(),'user':client.get('owner',''),
                                  'type':'WhatsApp','at':now.isoformat(),'owner':client.get('owner',''),'text':'MCR · Modelo '+settings['template']+' aceito pela Meta','messageId':mid,'source':'MCR'})
            interaction = record(con,'interaction',key)
            _services['project_attendance'](con,'interaction',key,interaction)
            refresh_client(con,client['id'])
        changed(con)

async def worker():
    import asyncio
    import logging
    while True:
        try: await asyncio.to_thread(tick)
        except asyncio.CancelledError: raise
        except Exception:
            logging.getLogger(__name__).error('MCR tick failed; next scheduled tick will retry reminders')
        await asyncio.sleep(60)

@router.get('/tdc/discover')
def discover(state: str = '', segment: str = '', channel: str = '', urgency: int = 90, authorization: str | None = Header(default=None)):
    user = access(authorization)
    if state and state not in ('PA','AP'): raise HTTPException(422,'UF deve ser PA ou AP')
    if not 0<=urgency<=90 or len(segment)>120 or (channel and channel not in CHANNELS):raise HTTPException(422,'Filtros inválidos')
    with _services['db']() as con:
        all_users = not _services['is_seller'](con,user)
        leads = [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='lead' AND (%s OR payload->>'owner'=%s) ORDER BY updated_at DESC LIMIT 1000",(all_users,user)).fetchall()]
    today = datetime.now(TZ).date()
    results=[]
    for lead in leads:
        info=lead.get('tdc',{})
        if not info or (state and lead.get('state')!=state) or (channel and info.get('channel')!=channel) or segment.casefold() not in info.get('segment','').casefold() or info.get('urgencyDays',91)>urgency: continue
        evidence=local_date(info.get('evidenceDate'))
        item={k:lead.get(k) for k in ('id','clientId','name','state','city','owner','status','tdc')}
        item['evidenceExpired']=not evidence or not 0<=(today-evidence).days<=30
        results.append(item)
    return {'leads':results[:100], 'source':'Leads registrados no L2 One; sem inferir demanda externa'}


def intake_tdc(con, item, result):
    """Webhook evidence is scored with exactly the same rules as manual qualification."""
    if result.get('duplicate'): return result
    fields=item.get('customFields') or {}
    required=('channel','cnae','size','segment','demandEvidence','evidenceDate','urgencyDays','estimatedMonthlyValue')
    if not all(k in fields for k in required) or not result.get('owner'):
        return {**result,'tdcStatus':'Aguardando dados de qualificação ou responsável'}
    try:
        data=Qualification(name=item.get('company') or item['name'],taxId=item.get('taxId') or '',state=item.get('state') or '',city=item.get('city') or '',phone=item.get('phone') or '',**{k:fields[k] for k in required})
    except ValidationError as exc:
        raise HTTPException(422,'Revise os campos de qualificação TDC do lead') from exc
    info=qualify(data)
    tax=re.sub(r'\D','',data.taxId)
    if not _services['valid_cnpj'](tax):raise HTTPException(422,'CNPJ inválido para TDC')
    con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
    matches=con.execute("SELECT payload FROM entities WHERE kind='client' AND regexp_replace(payload->>'taxId','[^0-9]','','g')=%s",(tax,)).fetchall()
    if len(matches)>1:raise HTTPException(409,'CNPJ duplicado na carteira')
    client=matches[0][0] if matches else {'id':str(uuid.uuid4()),'name':data.name,'taxId':tax,'state':data.state,'city':data.city,'phone':data.phone,'owner':result['owner'],'stage':'Prospecção'}
    # Existing ownership takes precedence over the intake round robin.
    if info['qualified']:
        client['tdc']=info
        put(con,'client',client)
        con.execute('INSERT INTO clientes(id,razao_social,nome_fantasia,documento) VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(client['id'],client['name'],client.get('tradeName'),tax))
        refresh_client(con,client['id'])
    put(con,'lead',{'id':'capture-'+result['id'],'clientId':client['id'] if info['qualified'] else None,
                    'owner':client['owner'],'name':data.name,'taxId':tax,'state':data.state,'city':data.city,'tdc':info,'status':info['tag'],'createdAt':datetime.now(TZ).isoformat()})
    changed(con)
    return {**result,'tdcStatus':info['tag'],'clientId':client['id'] if info['qualified'] else None}

class Location(StrictModel):
    latitude: float = Field(ge=-90,le=90,allow_inf_nan=False)
    longitude: float = Field(ge=-180,le=180,allow_inf_nan=False)
    opens: str = Field(default='09:00',pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    closes: str = Field(default='18:00',pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    closedDays: list[int] = Field(default_factory=lambda:[6],max_length=7)

@router.put('/sri/location/{client_id}')
def save_location(client_id: str, data: Location, authorization: str | None = Header(default=None)):
    user=access(authorization,'routes')
    if data.opens>=data.closes or any(d<0 or d>6 for d in data.closedDays):raise HTTPException(422,'Horários ou dias de fechamento inválidos')
    with _services['db']() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
        _services['check_client_scope'](con,user,client_id)
        client=record(con,'client',client_id)
        client.update(data.model_dump())
        put(con,'client',client)
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'client',client_id,'sri_location'))
        changed(con)
    return {'clientId':client_id,'saved':True}

@router.get('/mcr/settings')
def read_settings(authorization: str | None = Header(default=None)):
    user=access(authorization)
    with _services['db']() as con:
        row=con.execute("SELECT payload FROM entities WHERE kind='strategy_settings' AND id='mcr'").fetchone()
    return row[0] if row else {'enabled':False,'template':'','language':'pt_BR'}

@router.get('/mcr/history')
def history(client_id: str, authorization: str | None = Header(default=None)):
    user=access(authorization)
    with _services['db']() as con:
        _services['check_client_scope'](con,user,client_id)
        record(con,'client',client_id)
        rows=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='strategy_send' AND payload->>'clientId'=%s ORDER BY updated_at DESC LIMIT 100",(client_id,)).fetchall()]
        for row in rows:
            if row.get('messageId'):
                statuses={r[0] for r in con.execute('SELECT status FROM zara_delivery_events WHERE message_id=%s',(row['messageId'],)).fetchall()}
                row['deliveryStatus']=next((s for s in ('read','delivered','failed','sent') if s in statuses),'pending')
    return {'messages':rows}
