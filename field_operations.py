"""Typed field logistics: location, expenses, timed reminders and measured productivity."""
import hashlib
import math
import re
from datetime import datetime,timedelta,date
from decimal import Decimal,InvalidOperation
from fastapi import HTTPException,Header
from psycopg.types.json import Jsonb
import strategic_crm as crm


def money(raw):
    value=raw.strip().replace(' ','')
    if ',' in value:value=value.replace('.','').replace(',','.')
    try:number=Decimal(value)
    except InvalidOperation:raise HTTPException(422,'Valor inválido')
    if not number.is_finite() or not 0<number<=100000 or number.as_tuple().exponent < -2:raise HTTPException(422,'Valor deve ter até duas casas decimais e ser positivo')
    return float(number)


def parse(text):
    match=re.fullmatch(r'despesa\s+(.{3,120}?)\s+(?:r\$\s*)?([0-9][0-9., ]{0,20})',text)
    if match:
        try:return {'kind':'expense','description':match[1].strip(),'amount':money(match[2])}
        except HTTPException:return {'kind':'invalid','message':'Informe despesa combustível R$ 183,00, com valor positivo e até duas casas decimais.'}
    match=re.fullmatch(r'lembrete\s+(em \d{1,4} (?:min|minutos)|(?:hoje|amanha|\d{4}-\d{2}-\d{2}) \d{2}:\d{2})\s*:\s*(.{3,240})',text)
    if match:return {'kind':'reminder','when':match[1],'text':match[2]}
    match=re.fullmatch(r'(?:perto|proximos)(?:\s+(.{1,120}))?',text)
    if match:return {'kind':'nearby','segment':match[1] or ''}
    match=re.fullmatch(r'localizar\s+(.{2,180})',text)
    if match:return {'kind':'client_location','query':match[1]}
    return None


def due_time(raw,now=None):
    now=now or datetime.now(crm.TZ)
    if raw.startswith('em '):result=now+timedelta(minutes=int(raw.split()[1]))
    else:
        day,clock=raw.split(' ',1)
        if day in ('hoje','amanha'):day=(now.date()+timedelta(days=day=='amanha')).isoformat()
        try:result=datetime.fromisoformat(day+'T'+clock).replace(tzinfo=crm.TZ)
        except ValueError:raise HTTPException(422,'Data ou horário inválido')
    result=result.replace(second=0,microsecond=0)
    if not now<result<=now+timedelta(days=366):raise HTTPException(422,'Informe horário futuro até um ano, no fuso de Belém')
    return result


def log_event(con,user,mid,action,details,cid=None):
    con.execute('INSERT INTO field_events(message_id,username,client_id,action,payload) VALUES(%s,%s,%s,%s,%s)',(mid,user,cid,action,Jsonb(details)))
    con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'field_operation','field-'+hashlib.sha256(mid.encode()).hexdigest()[:40],action))
    crm.changed(con)


def location_valid(location):
    return isinstance(location,dict) and all(type(location.get(k)) in (float,int) and math.isfinite(location[k]) for k in ('latitude','longitude')) and -90<=location['latitude']<=90 and -180<=location['longitude']<=180


def receive_location(con,user,mid,location):
    if not location_valid(location):return 'Localização inválida. Envie a localização atual pelo WhatsApp.'
    item={'id':user,'user':user,'latitude':location['latitude'],'longitude':location['longitude'],'at':datetime.now(crm.TZ).isoformat(),'source':'WhatsApp'}
    crm.put(con,'field_location',item);log_event(con,user,mid,'location',item)
    return 'Localização atual recebida. Envie perto ou perto cosméticos para consultar clientes da sua carteira até 50 km. Para atualizar um cadastro, envie localizar NOME e confirme. A posição não altera cadastros automaticamente.'


def latest_location(con,user):
    row=con.execute("SELECT payload FROM entities WHERE kind='field_location' AND id=%s",(user,)).fetchone()
    if not row:return None
    item=row[0]
    try:age=datetime.now(crm.TZ)-datetime.fromisoformat(item['at'])
    except (ValueError,KeyError,TypeError):return None
    return item if timedelta(0)<=age<=timedelta(hours=4) and location_valid(item) else None


def nearby(clients,location,segment=''):
    from field_assistant import normalize
    result=[]
    for client in clients:
        if client.get('state') not in ('PA','AP') or not crm.coords(client):continue
        terms=' '.join(str(x or '') for x in (client.get('channel'),client.get('tdc',{}).get('channel'),client.get('tdc',{}).get('segment'),client.get('name')))
        if segment and normalize(segment) not in normalize(terms):continue
        a,b=crm.coords(location),crm.coords(client)
        p1,p2=map(math.radians,(a[0],b[0]));dl=math.radians(b[1]-a[1]);dp=p2-p1
        h=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        km=6371*2*math.asin(math.sqrt(min(1,max(0,h))))
        if km<=50:result.append({'client':client,'distanceKm':round(km,2)})
    return sorted(result,key=lambda r:(r['distanceKm'],r['client']['id']))[:10]


def prepare_client(con,phone,clients,query,payload):
    from field_assistant import match_clients,save_session
    matches=match_clients(clients,query)
    if not matches:return 'Cliente não localizado na sua carteira. Informe nome completo ou CNPJ.'
    if len(matches)>1:
        payload['choices']=[c['id'] for c in matches];save_session(con,phone,payload)
        return 'Escolha o cliente pelo número:\n'+'\n'.join(f"{i+1}. {c.get('name')} • {c.get('city')}" for i,c in enumerate(matches))
    payload['clientId']=matches[0]['id'];save_session(con,phone,payload)
    return operation_prompt(payload,matches[0])


def operation_prompt(session,client=None):
    if session['operation']=='expense':detail=f"Despesa: {session['description']} • R$ {session['amount']:.2f} • enviada para conferência operacional"
    elif session['operation']=='reminder':detail=f"Lembrete: {session['text']} • {session['dueAt']} • aviso 30 min antes e no horário (se configurado)"
    else:detail=f"Atualizar coordenadas de {client.get('name')} para {session['latitude']}, {session['longitude']}"
    return detail+'\nResponda CONFIRMAR para salvar ou CANCELAR.'


def handle(con,user,phone,mid,command,session,clients):
    from field_assistant import save_session,links
    kind=command['kind']
    if kind=='invalid':return command['message']
    if kind=='nearby':
        location=latest_location(con,user)
        if not location:return 'Envie sua localização atual no WhatsApp; ela fica válida para consulta por quatro horas.'
        rows=nearby(clients,location,command['segment'])
        return 'Clientes da sua carteira • distância em linha reta, sem trânsito:\n'+'\n'.join(f"{i+1}. {r['client'].get('name')} • {r['distanceKm']} km\nMaps: {links(r['client'])['maps']}" for i,r in enumerate(rows)) if rows else 'Nenhum cliente com coordenadas e esse perfil até 50 km na sua carteira.'
    if kind=='expense':
        payload={'operation':'expense','amount':command['amount'],'description':command['description']};save_session(con,phone,payload);return operation_prompt(payload)
    if kind=='reminder':
        try:due=due_time(command['when'])
        except HTTPException as exc:return exc.detail
        payload={'operation':'reminder','text':command['text'],'dueAt':due.isoformat()}
        query=re.fullmatch(r'ligar (?:para |pro )?(?:o )?(?:cliente )?(.+)',command['text'])
        if query:return prepare_client(con,phone,clients,query[1],payload)
        save_session(con,phone,payload);return operation_prompt(payload)
    if kind=='client_location':
        location=latest_location(con,user)
        if not location:return 'Envie sua localização atual antes de vincular a posição a um cliente.'
        payload={'operation':'location','latitude':location['latitude'],'longitude':location['longitude']}
        return prepare_client(con,phone,clients,command['query'],payload)
    if not session or not session.get('operation'):return None
    if kind=='choice' and session.get('choices'):
        index=command['choice']-1
        if index>=len(session['choices']):return 'Escolha um dos números apresentados.'
        client=next((c for c in clients if c['id']==session['choices'][index]),None)
        if not client:return 'Cliente não está mais disponível na sua carteira.'
        session['clientId']=client['id'];session.pop('choices');save_session(con,phone,session);return operation_prompt(session,client)
    if kind!='confirmar':return None
    if session.get('choices'):return 'Escolha o cliente pelo número antes de confirmar.'
    cid=session.get('clientId')
    if cid:crm._services['check_client_scope'](con,user,cid)
    identifier='field-'+hashlib.sha256(mid.encode()).hexdigest()[:40]
    if session['operation']=='expense':
        routes=[r for r in crm._services['scoped_rows'](con,'route',user) if r.get('user')==user and r.get('date')==datetime.now(crm.TZ).date().isoformat()]
        item={'id':identifier,'user':user,'date':datetime.now(crm.TZ).date().isoformat(),'amount':session['amount'],'description':session['description'],'source':'Preposto','status':'Enviada para conferência','routeId':routes[0]['id'] if len(routes)==1 else None}
        crm.put(con,'route_expense',item)
        crm.put(con,'office_action',{'id':'expense-'+identifier,'Área':'Operações','Responsável':'Marlene','Status':'Aberta','Prazo':item['date'],'Demanda ou problema':'Conferir despesa de campo: '+item['description'],'expenseId':identifier,'Valor':item['amount'],'user':user})
        reply='Despesa registrada e enviada para conferência. Nenhum pagamento de caixa foi lançado.'
    elif session['operation']=='reminder':
        due=datetime.fromisoformat(session['dueAt'])
        if due<=datetime.now(crm.TZ):return 'Horário passou durante a confirmação. Refaça o lembrete.'
        item={'id':identifier,'user':user,'clientId':cid,'date':due.date().isoformat(),'time':due.strftime('%H:%M'),'dueAt':due.isoformat(),'text':session['text'],'type':'Ligação' if session['text'].startswith('ligar') else 'Retorno','status':'Aberta','source':'Preposto','leadMinutes':30}
        crm.put(con,'task',item);reply='Lembrete salvo na Agenda. Avisos no WhatsApp dependem da integração ativa; o horário está registrado no L2 One.'
    else:
        client=crm.record(con,'client',cid);client.update(latitude=session['latitude'],longitude=session['longitude']);crm.put(con,'client',client)
        item={'clientId':cid,'latitude':session['latitude'],'longitude':session['longitude']};reply='Coordenadas do cliente atualizadas. Recalcule rotas temporárias para usar a nova posição.'
    log_event(con,user,mid,session['operation'],item,cid);con.execute('DELETE FROM field_sessions WHERE phone=%s',(phone,))
    return reply


def reminder_candidate(con,user,now):
    tasks=crm._services['scoped_rows'](con,'task',user)
    client_ids={c['id'] for c in crm._services['scoped_rows'](con,'client',user)}
    for task in sorted(tasks,key=lambda t:t.get('dueAt','9999')):
        if task.get('user')!=user or task.get('status')!='Aberta' or not task.get('time') or not task.get('date') or (task.get('clientId') and task['clientId'] not in client_ids):continue
        try:due=datetime.fromisoformat(task['date']+'T'+task['time']).replace(tzinfo=crm.TZ)
        except (ValueError,TypeError):continue
        if due.tzinfo is None:continue
        for phase,scheduled in [('advance',due-timedelta(minutes=30)),('due',due)]:
            valid=scheduled<=now<due if phase=='advance' else due<=now<due+timedelta(minutes=15)
            if not valid:continue
            key='field-remind-'+hashlib.sha256((task['id']+'|'+due.isoformat()+'|'+task['text']+'|'+phase).encode()).hexdigest()[:40]
            if con.execute("SELECT 1 FROM entities WHERE kind='field_send' AND id=%s",(key,)).fetchone():continue
            remaining=max(0,math.ceil((due-now).total_seconds()/60))
            return key,'reminder',('Em '+str(remaining)+' min: ' if phase=='advance' else 'Agora: ')+task['text']+' • '+due.strftime('%d/%m %H:%M')
    return None


def productivity(clients,visits,routes,expenses,start,end):
    eligible=[v for v in visits if start<=str(v.get('date',''))<=end and v.get('result') in ('Visitado - pedido','Visitado - sem pedido','Cliente ausente')]
    sales=sum(v.get('result')=='Visitado - pedido' for v in eligible);absent=sum(v.get('result')=='Cliente ausente' for v in eligible)
    rs=[r for r in routes if start<=str(r.get('date',''))<=end]
    costs=[e for e in expenses if start<=str(e.get('date',''))<=end]
    observed_minutes=[]
    for v in eligible:
        try:
            a=datetime.fromisoformat(v['checkIn']);b=datetime.fromisoformat(v['checkOut']);n=(b-a).total_seconds()/60
            if 0<=n<=1440:observed_minutes.append(n)
        except (KeyError,ValueError,TypeError):pass
    return {'start':start,'end':end,'fieldVisits':len(eligible),'withOrder':sales,'withoutImmediateOrder':len(eligible)-sales,'absent':absent,'orderVisitRate':round(100*sales/len(eligible),1) if eligible else None,
        'actualSubmittedExpense':round(sum(crm.safe_amount(e.get('amount')) for e in costs),2),'expenseRecords':len(costs),'expensePerRecordedVisit':round(sum(crm.safe_amount(e.get('amount')) for e in costs)/len(eligible),2) if eligible and costs else None,
        'plannedDistanceKm':round(sum(crm.safe_amount(r.get('distanceKm')) for r in rs),2),'plannedTravelMinutes':sum(crm.safe_amount(r.get('travelMinutes')) for r in rs),'routePointsWithEstimate':sum('distanceKm' in r for r in rs),'observedVisitMinutes':round(sum(observed_minutes),1) if observed_minutes else None,'visitsWithObservedDuration':len(observed_minutes),
        'interpretation':'Visita sem pedido imediato pode cumprir relacionamento ou pós-venda. Despesas são valores submetidos, não pagamentos aprovados. Distância e deslocamento são planejamento; sem medição não há economia ou perda comprovada.'}
