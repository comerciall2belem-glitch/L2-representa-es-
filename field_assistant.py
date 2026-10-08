"""Preposto virtual: trusted WhatsApp identity, guided commands, transactional CRM writes."""
import re
import hashlib
import unicodedata
from datetime import datetime, timedelta, date
from urllib.parse import urlencode
from fastapi import APIRouter, Header, HTTPException
from pydantic import Field
from psycopg.types.json import Jsonb
import strategic_crm as crm
import field_operations as operations

router=APIRouter(prefix='/api/strategy/field',tags=['Preposto Virtual'])
RESULTS={'pedido realizado':'Visitado - pedido','sem pedido':'Visitado - sem pedido','cliente ausente':'Cliente ausente','cliente quente':'Quente'}
HELP=('Preposto L2 • Comandos de campo\n'
      '• agenda hoje / agenda amanhã\n• resumo hoje\n'
      '• visita NOME ou CNPJ: pedido realizado\n• visita NOME: sem pedido\n'
      '• visita NOME: cliente ausente\n• cliente quente NOME\n'
      '• despesa combustível R$ 183,00\n• lembrete em 30 min: ligar para cliente NOME\n'
      '• envie localização atual; perto cosméticos; localizar NOME\n'
      'Depois confira o cliente e responda CONFIRMAR. CANCELAR encerra a ação.\n'
      'Pedido realizado registra a visita; valores e comissões vêm do pedido no ERP.')

def normalize(text):
    return ' '.join(''.join(c for c in unicodedata.normalize('NFD',str(text)).lower() if unicodedata.category(c)!='Mn').split())

def setup(con):
    con.execute('''CREATE TABLE IF NOT EXISTS field_operators(phone TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT true,updated_by TEXT NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    con.execute('''CREATE TABLE IF NOT EXISTS field_sessions(phone TEXT PRIMARY KEY,payload JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    con.execute('''CREATE TABLE IF NOT EXISTS field_events(message_id TEXT PRIMARY KEY,username TEXT NOT NULL,
        client_id TEXT,action TEXT NOT NULL,payload JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')

class Operator(crm.StrictModel):
    username: str=Field(min_length=2,max_length=80)
    phone: str=Field(min_length=10,max_length=30)
    enabled: bool=True

def phone_number(raw):
    digits=re.sub(r'\D','',raw)
    if len(digits) in (10,11):digits='55'+digits
    if not re.fullmatch(r'55\d{10,11}',digits):raise HTTPException(422,'Informe telefone brasileiro com DDD')
    return digits

@router.put('/operators')
def bind_operator(data:Operator,authorization:str|None=Header(default=None)):
    actor=crm.access(authorization)
    if actor not in ('Ana Paula','Euler'):raise HTTPException(403,'Vinculação restrita aos sócios')
    phone=phone_number(data.phone)
    with crm._services['db']() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
        row=con.execute('SELECT role,sectors FROM app_users WHERE username=%s AND active',(data.username,)).fetchone()
        if not row or 'commercial' not in (row[1] or []):raise HTTPException(422,'Escolha um usuário ativo do setor comercial')
        duplicate=con.execute('SELECT username FROM field_operators WHERE phone=%s',(phone,)).fetchone()
        if duplicate and duplicate[0]!=data.username:raise HTTPException(409,'Telefone já vinculado a outro operador')
        old=con.execute('SELECT phone FROM field_operators WHERE username=%s',(data.username,)).fetchone()
        if old and old[0]!=phone:
            con.execute('DELETE FROM field_sessions WHERE phone=%s',(old[0],))
        con.execute('''INSERT INTO field_operators(phone,username,enabled,updated_by) VALUES(%s,%s,%s,%s)
            ON CONFLICT(username) DO UPDATE SET phone=excluded.phone,enabled=excluded.enabled,updated_by=excluded.updated_by,updated_at=now()''',
            (phone,data.username,data.enabled,actor))
        con.execute('DELETE FROM field_sessions WHERE phone=%s',(phone,))
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(actor,'field_operator',data.username,'bind' if data.enabled else 'disable'))
    return {'username':data.username,'enabled':data.enabled}

@router.get('/operators')
def list_operators(authorization:str|None=Header(default=None)):
    user=crm.access(authorization)
    with crm._services['db']() as con:
        rows=con.execute('SELECT username,phone,enabled FROM field_operators WHERE (%s OR username=%s) ORDER BY username',(user in ('Ana Paula','Euler'),user)).fetchall()
    return [{'username':u,'phone':p,'enabled':a} for u,p,a in rows]

@router.get('/events')
def list_events(authorization:str|None=Header(default=None)):
    user=crm.access(authorization)
    with crm._services['db']() as con:
        rows=con.execute('SELECT username,client_id,action,payload,created_at FROM field_events WHERE (%s OR username=%s) ORDER BY created_at DESC LIMIT 100',(user in ('Ana Paula','Euler'),user)).fetchall()
    return [{'user':u,'clientId':c,'action':a,'details':p,'at':d.isoformat()} for u,c,a,p,d in rows]

def intent(body):
    text=normalize(body)
    extra=operations.parse(text)
    if extra:return extra
    if text in ('cancelar','confirmar','ajuda','menu'):return {'kind':text}
    m=re.fullmatch(r'(agenda|resumo)(?: (hoje|amanha|\d{4}-\d{2}-\d{2}))?',text)
    if m:return {'kind':m[1],'day':m[2] or 'hoje'}
    m=re.fullmatch(r'(?:registrar )?visita (.+?)\s*[:;|]\s*(pedido realizado|sem pedido|cliente ausente|cliente quente)(?:\s*[:;|]\s*(.{1,1000}))?',text)
    if m:return {'kind':'visit','query':m[1].strip(),'result':m[2],'note':m[3] or ''}
    m=re.fullmatch(r'(pedido realizado|sem pedido|cliente ausente|cliente quente) (.+)',text)
    if m:return {'kind':'visit','query':m[2].strip(),'result':m[1],'note':''}
    if text.isdigit() and 1<=int(text)<=10:return {'kind':'choice','choice':int(text)}
    return {'kind':'help'}

def match_clients(clients,query):
    q=normalize(query);digits=re.sub(r'\D','',q)
    exact=[c for c in clients if c.get('id')==query or normalize(c.get('name'))==q or (len(digits)==14 and re.sub(r'\D','',c.get('taxId',''))==digits)]
    if exact:return exact
    return [c for c in clients if all(t in normalize(' '.join(str(c.get(k) or '') for k in ('name','tradeName','city','district'))) for t in q.split())][:10]

def links(client):
    destination=f"{client['latitude']},{client['longitude']}" if crm.coords(client) else ' '.join(str(client.get(k) or '') for k in ('address','district','city','state')).strip()
    result={'maps':'https://www.google.com/maps/dir/?'+urlencode({'api':1,'destination':destination,'travelmode':'driving'}),
            'waze':'https://waze.com/ul?'+urlencode({'ll':destination,'navigate':'yes'}) if crm.coords(client) else 'https://waze.com/ul?'+urlencode({'q':destination,'navigate':'yes'})}
    try:
        phone=phone_number(client.get('phone',''));result['whatsapp']='https://wa.me/'+phone;result['call']='tel:+'+phone
    except HTTPException:pass
    return result

def selected_day(raw):
    now=datetime.now(crm.TZ).date()
    if raw in ('hoje','amanha'):return now+timedelta(days=raw=='amanha')
    try:return date.fromisoformat(raw)
    except ValueError:raise HTTPException(422,'Data inválida')

def daily_summary(con,user,day):
    visits=crm._services['scoped_rows'](con,'visit',user)
    own=[v for v in visits if v.get('user')==user and v.get('date')==day.isoformat()]
    counts={r:sum(v.get('result')==r for v in own) for r in ('Visitado - pedido','Visitado - sem pedido','Cliente ausente')}
    routes=[r for r in crm._services['scoped_rows'](con,'route',user) if r.get('user')==user and r.get('date')==day.isoformat()]
    completed={v.get('clientId') for v in own}
    return f"Resumo L2 • {user} • {day.isoformat()}\nVisitas registradas: {len(own)}\nCom pedido: {counts['Visitado - pedido']}\nSem pedido: {counts['Visitado - sem pedido']}\nCliente ausente: {counts['Cliente ausente']}\nPontos da rota ainda sem registro: {sum(r.get('clientId') not in completed for r in routes)}"

def save_session(con,phone,payload):
    con.execute('INSERT INTO field_sessions(phone,payload) VALUES(%s,%s) ON CONFLICT(phone) DO UPDATE SET payload=excluded.payload,updated_at=now()', (phone,Jsonb(payload)))

def prompt(client,action):
    label=action['result']
    return f"Conferir registro: {client.get('name','')} • {client.get('city','')} / {client.get('state','')}\nResultado: {label}\nResponda CONFIRMAR para salvar ou CANCELAR."

def handle_message(con,phone,mid,body,location=None):
    """Called inside the signed webhook's inbound transaction, before acknowledgment."""
    operator=con.execute('SELECT username FROM field_operators WHERE phone=%s AND enabled',(phone,)).fetchone()
    if not operator:return None
    con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
    operator=con.execute('SELECT username FROM field_operators WHERE phone=%s AND enabled',(phone,)).fetchone()
    if not operator:return 'Acesso de campo suspenso. Procure a administração L2.'
    user=operator[0]
    account=con.execute('SELECT role,sectors FROM app_users WHERE username=%s AND active',(user,)).fetchone()
    if not account or 'commercial' not in (account[1] or []):return 'Acesso de campo suspenso. Procure a administração L2.'
    previous=con.execute('SELECT 1 FROM field_events WHERE message_id=%s',(mid,)).fetchone()
    if previous:return 'Essa atualização já foi registrada.'
    if location is not None:return operations.receive_location(con,user,mid,location)
    command=intent(body)
    row=con.execute('SELECT payload,updated_at FROM field_sessions WHERE phone=%s FOR UPDATE',(phone,)).fetchone()
    session=row[0] if row and datetime.now(crm.TZ)-row[1]<timedelta(minutes=15) else None
    if command['kind']=='cancelar':
        con.execute('DELETE FROM field_sessions WHERE phone=%s',(phone,));return 'Ação cancelada. '+HELP
    clients=crm._services['scoped_rows'](con,'client',user)
    extra=operations.handle(con,user,phone,mid,command,session,clients)
    if extra is not None:return extra
    if command['kind'] in ('agenda','resumo'):
        try:day=selected_day(command['day'])
        except HTTPException:return 'Data inválida. Use agenda hoje ou agenda 2026-10-08.'
        if command['kind']=='resumo':return daily_summary(con,user,day)
        clients={c['id']:c for c in crm._services['scoped_rows'](con,'client',user)}
        routes=sorted([r for r in crm._services['scoped_rows'](con,'route',user) if r.get('user')==user and r.get('date')==day.isoformat()],key=lambda x:x.get('order',0))
        lines=[f"Agenda L2 • {user} • {day.isoformat()}"]
        for route in routes[:10]:
            c=clients.get(route.get('clientId'))
            if not c:continue
            lines.append(f"{route.get('order','')}. {c.get('name')} • {route.get('arrival','Horário a confirmar')}\nMaps: {links(c)['maps']}")
        tasks=[t for t in crm._services['scoped_rows'](con,'task',user) if t.get('user')==user and t.get('date')==day.isoformat() and t.get('status')=='Aberta']
        for task in sorted(tasks,key=lambda t:t.get('time','99:99'))[:15]:lines.append(f"Lembrete • {task.get('time','Sem horário')} • {task.get('text')}")
        return '\n'.join(lines) if len(lines)>1 else 'Nenhum compromisso agendado para essa data.'
    clients=crm._services['scoped_rows'](con,'client',user)
    if command['kind']=='choice':
        if not session or not session.get('choices'):return 'Nenhuma escolha pendente. '+HELP
        index=command['choice']-1
        if index>=len(session['choices']):return 'Escolha um dos números apresentados.'
        c=next((c for c in clients if c['id']==session['choices'][index]),None)
        if not c:return 'Cliente indisponível na sua carteira. Refaça a busca.'
        session['clientId']=c['id'];session.pop('choices',None);save_session(con,phone,session)
        return prompt(c,session)
    if command['kind']=='confirmar':
        if not session or not session.get('clientId'):return 'Nenhum registro pendente. '+HELP
        cid=session['clientId'];crm._services['check_client_scope'](con,user,cid)
        client=crm.record(con,'client',cid)
        today=datetime.now(crm.TZ).date();identifier='field-'+hashlib.sha256(mid.encode()).hexdigest()[:40]
        result=RESULTS[session['result']]
        if result=='Quente':
            client['tags']=list(dict.fromkeys([*(client.get('tags') if isinstance(client.get('tags'),list) else []),'Cliente quente']))
            client['fieldTemperature']='Quente';crm.put(con,'client',client)
            crm.put(con,'opportunity',{'id':identifier,'clientId':cid,'owner':user,'stage':'Qualificação','brand':'','amount':0,'notes':session.get('note') or 'Cliente quente informado pelo Preposto','followUp':today.isoformat(),'createdAt':datetime.now(crm.TZ).isoformat()})
            contact={'id':identifier,'clientId':cid,'owner':user,'user':user,'type':'WhatsApp','at':datetime.now(crm.TZ).isoformat(),'text':'Preposto: cliente quente. '+session.get('note',''),'source':'Preposto'}
            crm.put(con,'interaction',contact);crm._services['project_attendance'](con,'interaction',identifier,contact);crm.event(con,'interaction',contact)
        else:
            visit={'id':identifier,'clientId':cid,'user':user,'date':today.isoformat(),'result':result,'notes':session.get('note',''),
                   'next':'Conferir pedido no ERP' if result=='Visitado - pedido' else 'Retomar contato','returnDate':(today+timedelta(days=1 if result=='Cliente ausente' else 7)).isoformat(),'source':'Preposto','messageId':mid}
            crm.put(con,'visit',visit);crm._services['project_attendance'](con,'visit',identifier,visit);crm.event(con,'visit',visit)
            crm.put(con,'task',{'id':'return-'+identifier,'sourceVisitId':identifier,'clientId':cid,'user':user,'date':visit['returnDate'],'text':visit['next'],'status':'Aberta','source':'Preposto'})
            for r in crm._services['scoped_rows'](con,'route',user):
                if r.get('clientId')==cid and r.get('user')==user and r.get('date')==today.isoformat():
                    r['status']='Cliente ausente' if result=='Cliente ausente' else 'Concluída';r['visitId']=identifier;crm.put(con,'route',r)
        con.execute('INSERT INTO field_events(message_id,username,client_id,action,payload) VALUES(%s,%s,%s,%s,%s)',(mid,user,cid,session['result'],Jsonb({'result':result,'note':session.get('note','')})))
        con.execute('DELETE FROM field_sessions WHERE phone=%s',(phone,))
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'visit' if result!='Quente' else 'opportunity',identifier,'field_register'))
        crm.changed(con)
        return f"Registro salvo • {client.get('name')} • {session['result']}. Carteira, histórico e cadência atualizados."+ (' Confira o pedido no ERP para faturamento e comissão.' if result=='Visitado - pedido' else '')
    if command['kind']=='visit':
        matches=match_clients(clients,command['query'])
        if not matches:return 'Cliente não localizado na sua carteira. Informe nome mais completo ou CNPJ.'
        payload={'result':command['result'],'note':command['note']}
        if len(matches)>1:
            payload['choices']=[c['id'] for c in matches];save_session(con,phone,payload)
            return 'Encontrei mais de um cliente. Responda com o número:\n'+'\n'.join(f"{i+1}. {c.get('name')} • {c.get('city')} / {c.get('state')}" for i,c in enumerate(matches))
        payload['clientId']=matches[0]['id'];save_session(con,phone,payload);return prompt(matches[0],payload)
    return HELP

class Notifications(crm.StrictModel):
    enabled:bool=False
    template:str=Field(default='',max_length=100,pattern=r'^[a-z0-9_]*$')
    language:str=Field(default='pt_BR',pattern=r'^[a-z]{2}_[A-Z]{2}$')

@router.get('/settings')
def notification_settings(authorization:str|None=Header(default=None)):
    crm.access(authorization)
    with crm._services['db']() as con:
        row=con.execute("SELECT payload FROM entities WHERE kind='strategy_settings' AND id='field'").fetchone()
    return row[0] if row else Notifications().model_dump()

@router.put('/settings')
def save_notification_settings(data:Notifications,authorization:str|None=Header(default=None)):
    user=crm.access(authorization)
    if user not in ('Ana Paula','Euler'):raise HTTPException(403,'Configuração restrita aos sócios')
    if data.enabled and not data.template:raise HTTPException(422,'Informe modelo aprovado de resumo e agenda com duas variáveis')
    with crm._services['db']() as con:
        crm.put(con,'strategy_settings',{'id':'field','updatedBy':user,**data.model_dump()})
    return data.model_dump()

def notification_text(con,user,now):
    if now.hour>=18:return 'summary',daily_summary(con,user,now.date())
    day=now.date()
    routes=[r for r in crm._services['scoped_rows'](con,'route',user) if r.get('user')==user and r.get('date')==day.isoformat()]
    clients={c['id']:c for c in crm._services['scoped_rows'](con,'client',user)}
    lines=[f'Agenda L2 • {day.isoformat()} • {len(routes)} pontos']
    for r in sorted(routes,key=lambda r:r.get('order',0))[:10]:
        c=clients.get(r.get('clientId'),{})
        lines.append(f"{r.get('order','')}. {c.get('name','Cliente')} • {r.get('arrival','A confirmar')}")
    return 'agenda','\n'.join(lines)

def _notification_once():
    """One daily agenda and summary per enabled operator, only with approved template.
    Reserve before network I/O. Ambiguous sends are never automatically repeated.
    """
    from whatsapp_media import provider_config,_post
    import json
    now=datetime.now(crm.TZ);config=provider_config()
    if not config:return
    with crm._services['db']() as con:
        if not con.execute('SELECT pg_try_advisory_xact_lock(%s)',(8239020,)).fetchone()[0]:return
        row=con.execute("SELECT payload FROM entities WHERE kind='strategy_settings' AND id='field'").fetchone()
        settings=row[0] if row else {}
        if not settings.get('enabled'):return
        operators=con.execute("SELECT f.phone,f.username FROM field_operators f JOIN app_users u ON u.username=f.username WHERE f.enabled AND u.active AND u.sectors ? 'commercial'").fetchall()
        for phone,user in operators:
            reminder=operations.reminder_candidate(con,user,now)
            if reminder:
                key,mode,body=reminder
                crm.put(con,'field_send',{'id':key,'user':user,'mode':mode,'status':'Enviando','attemptAt':now.isoformat()})
                break
            if now.weekday()>=5 or not 8<=now.hour<20:continue
            notices=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='field_notice' AND payload->>'user'=%s AND payload->>'date'>=%s ORDER BY updated_at DESC LIMIT 100",(user,now.date().isoformat())).fetchall()]
            pending=None
            for notice in notices:
                candidate='field-confirm-'+notice['id']
                if not con.execute("SELECT 1 FROM entities WHERE kind='field_send' AND id=%s",(candidate,)).fetchone():pending=notice;break
            if pending:
                mode='confirmation';key='field-confirm-'+pending['id'];body=f"Roteiro confirmado • {pending['date']} • {pending['visits']} visitas. Consulte agenda {pending['date']} para os pontos e atalhos."
            else:
                if not (8<=now.hour<10 or 18<=now.hour<20):continue
                mode='summary' if now.hour>=18 else 'agenda'
                key='field-notify-'+hashlib.sha256(user.encode()).hexdigest()[:16]+'-'+mode+'-'+now.date().isoformat()
                if con.execute("SELECT 1 FROM entities WHERE kind='field_send' AND id=%s",(key,)).fetchone():continue
                mode,body=notification_text(con,user,now)
            crm.put(con,'field_send',{'id':key,'user':user,'mode':mode,'status':'Enviando','attemptAt':now.isoformat()})
            break
        else:return
    payload={'messaging_product':'whatsapp','to':phone,'type':'template','template':{'name':settings['template'],'language':{'code':settings['language']},'components':[{'type':'body','parameters':[{'type':'text','text':user},{'type':'text','text':body[:3000]}]}]}}
    mid=None;status='Conferir envio'
    try:
        token,phone_id,version=config
        response=_post(f'https://graph.facebook.com/{version}/{phone_id}/messages',token,json.dumps(payload).encode(),'application/json')
        candidate=response['messages'][0]['id']
        if not isinstance(candidate,str) or not candidate or len(candidate)>256:raise ValueError('Resposta inválida')
        mid=candidate;status='Aceito pela Meta'
    except Exception:pass
    with crm._services['db']() as con:
        crm.put(con,'field_send',{'id':key,'user':user,'mode':mode,'status':status,'messageId':mid,'attemptAt':now.isoformat()})
        if mid:
            con.execute("INSERT INTO zara_conversations(phone,name,mode) VALUES(%s,%s,'bot') ON CONFLICT(phone) DO UPDATE SET updated_at=now()",(phone,user))
            con.execute("INSERT INTO zara_messages(message_id,phone,direction,body,delivered) VALUES(%s,%s,'out',%s,false) ON CONFLICT DO NOTHING",(mid,phone,'Preposto • '+body))
        crm.changed(con)
    return True

def notification_tick():
    # Bounded drain handles simultaneous reminders without blocking the API thread.
    for _ in range(4):
        if not _notification_once():break

@router.get('/history')
def notification_history(authorization:str|None=Header(default=None)):
    user=crm.access(authorization)
    with crm._services['db']() as con:
        rows=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='field_send' AND (%s OR payload->>'user'=%s) ORDER BY updated_at DESC LIMIT 100",(user in ('Ana Paula','Euler'),user)).fetchall()]
        for row in rows:
            if row.get('messageId'):
                statuses={r[0] for r in con.execute('SELECT status FROM zara_delivery_events WHERE message_id=%s',(row['messageId'],)).fetchall()}
                row['deliveryStatus']=next((s for s in ('read','delivered','failed','sent') if s in statuses),'pending')
    return rows

@router.get('/productivity')
def field_productivity(start:str='',end:str='',authorization:str|None=Header(default=None)):
    user=crm.access(authorization);today=datetime.now(crm.TZ).date()
    try:
        a=date.fromisoformat(start) if start else today.replace(day=1)
        b=date.fromisoformat(end) if end else today
    except ValueError:raise HTTPException(422,'Datas inválidas')
    if not a<=b<=today or (b-a).days>92:raise HTTPException(422,'Escolha período até hoje, limitado a 93 dias')
    with crm._services['db']() as con:
        seller=crm._services['is_seller'](con,user)
        visits=crm._services['scoped_rows'](con,'visit',user)
        routes=crm._services['scoped_rows'](con,'route',user)
        if seller:visits=[v for v in visits if v.get('user')==user]
        expenses=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='route_expense' AND (%s OR payload->>'user'=%s)",(not seller,user)).fetchall()]
        clients=crm._services['scoped_rows'](con,'client',user)
    return operations.productivity(clients,visits,routes,expenses,a.isoformat(),b.isoformat())


def opening_status(client,now=None):
    now=now or datetime.now(crm.TZ)
    if now.weekday() in client.get('closedDays',[6]):return 'Fechado hoje (cadastro)'
    opening,closing=client.get('opens'),client.get('closes')
    if not opening or not closing:return 'Horário não informado'
    clock=now.strftime('%H:%M')
    return 'Aberto agora (cadastro)' if opening<=clock<closing else 'Fechado agora (cadastro)'

@router.get('/agenda')
def field_agenda(day:str='',authorization:str|None=Header(default=None)):
    user=crm.access(authorization)
    try:selected=date.fromisoformat(day) if day else datetime.now(crm.TZ).date()
    except ValueError:raise HTTPException(422,'Data inválida')
    with crm._services['db']() as con:
        clients={c['id']:c for c in crm._services['scoped_rows'](con,'client',user)}
        routes=crm._services['scoped_rows'](con,'route',user)
        tasks=crm._services['scoped_rows'](con,'task',user)
    rows=[]
    for kind,items in [('route',routes),('task',tasks)]:
        for item in items:
            if item.get('user')!=user or item.get('date')!=selected.isoformat():continue
            client=clients.get(item.get('clientId'))
            rows.append({'id':item['id'],'kind':kind,'date':item['date'],'time':item.get('time') or item.get('arrival') or '',
                'text':item.get('text') or item.get('objective') or 'Visita planejada','clientName':client.get('name') if client else 'Geral','status':item.get('status','Aberta'),
                'openingStatus':opening_status(client) if client else '', 'needsReview':item.get('needsReview',False),'links':links(client) if client else {}})
    return sorted(rows,key=lambda r:(r['time'] or '99:99',r['kind'],r['id']))


@router.get('/conversation')
def conversation(authorization:str|None=Header(default=None)):
    user=crm.access(authorization)
    import zara
    import field_audio
    with crm._services['db']() as con:
        row=con.execute('SELECT phone FROM field_operators WHERE username=%s AND enabled',(user,)).fetchone()
        rows=con.execute('SELECT direction,body,created_at FROM zara_messages WHERE phone=%s ORDER BY created_at DESC,message_id DESC LIMIT 50',(row[0],)).fetchall() if row else []
    return {'messages':[{'direction':d,'body':b,'at':at.isoformat()} for d,b,at in reversed(rows)],'audioReady':field_audio.ready(),'whatsappReady':all(zara.configured_value(k) for k in ('WA_ACCESS_TOKEN','WA_PHONE_NUMBER_ID','WA_APP_SECRET','WA_GRAPH_VERSION'))}
