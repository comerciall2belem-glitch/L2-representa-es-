"""Daily commercial review list; no automatic customer messages or fabricated visits."""
import hashlib
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException
from pydantic import Field
import strategic_crm as crm

router=APIRouter(prefix='/api/strategy/routine',tags=['Rotina estratégica'])
class Preferences(crm.StrictModel):
    enabled:bool=True
    fallbackDays:int=Field(default=45,ge=1,le=180)
    dailyLimit:int=Field(default=3,ge=1,le=5)
    intervalDays:int=Field(default=14,ge=1,le=90)

def identifier(user,client=''):return hashlib.sha256((user+'|'+client).encode()).hexdigest()
def preferences(con,user):
    row=con.execute("SELECT payload FROM entities WHERE kind='routine_preferences' AND id=%s",(identifier(user),)).fetchone()
    return Preferences(**{k:v for k,v in row[0].items() if k in Preferences.model_fields}) if row else Preferences()

def select_alerts(clients,orders,reviews,prefs,today):
    reviewed_today=sum(crm.local_date(r.get('date'))==today for r in reviews)
    remaining=max(0,prefs.dailyLimit-reviewed_today);rows=[]
    if not prefs.enabled:return [],remaining
    for client in clients:
        info=crm.cadence(client,orders,today=today);age=info['daysSincePurchase']
        if age is None:continue
        threshold=info['cycleDays'] if info['cycleSource']=='Histórico' else prefs.fallbackDays
        if age<=threshold:continue
        last=next((crm.local_date(r.get('date')) for r in reviews if r.get('clientId')==client['id']),None)
        if last and (today-last).days<prefs.intervalDays:continue
        rows.append({'clientId':client['id'],'name':client['name'],'phone':client.get('phone',''),
            'lastPurchase':info['lastPurchase'],'days':age,'limitDays':threshold,
            'basis':'Ciclo de compra' if info['cycleSource']=='Histórico' else 'Limite sem ciclo',
            'message':'Olá! Aqui é da L2 Representações. Gostaria de conferir a reposição e as oportunidades para sua loja. Podemos conversar?','overdueDays':age-threshold})
    return sorted(rows,key=lambda r:(-r['overdueDays'],r['name']))[:remaining],remaining

@router.get('/settings')
def get_settings(authorization:str|None=Header(default=None)):
    user=crm.access(authorization)
    with crm._services['db']() as con:return preferences(con,user).model_dump()

@router.put('/settings')
def save_settings(data:Preferences,authorization:str|None=Header(default=None)):
    user=crm.access(authorization)
    with crm._services['db']() as con:
        crm.put(con,'routine_preferences',{'id':identifier(user),'user':user,**data.model_dump()});crm.changed(con)
    return data.model_dump()

@router.get('/alerts')
def alerts(authorization:str|None=Header(default=None)):
    user=crm.access(authorization);today=datetime.now(crm.TZ).date()
    with crm._services['db']() as con:
        prefs=preferences(con,user);clients=crm._services['scoped_rows'](con,'client',user)
        orders=crm._services['scoped_rows'](con,'order',user)
        reviews=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='routine_review' AND payload->>'user'=%s",(user,)).fetchall()]
    rows,remaining=select_alerts(clients,orders,reviews,prefs,today)
    return {'date':today.isoformat(),'settings':prefs.model_dump(),'rows':rows,'remaining':remaining,
        'notice':'Lista para revisão no painel. Nenhuma mensagem é enviada automaticamente.'}

@router.post('/review/{client_id}')
def review(client_id:str,authorization:str|None=Header(default=None)):
    user=crm.access(authorization);now=datetime.now(crm.TZ);today=now.date().isoformat()
    with crm._services['db']() as con:
        if client_id not in {c['id'] for c in crm._services['scoped_rows'](con,'client',user)}:raise HTTPException(403,'Cliente fora da carteira autorizada')
        prefs=preferences(con,user);orders=crm._services['scoped_rows'](con,'order',user)
        con.execute('SELECT pg_advisory_xact_lock(%s)',(8239022,))
        reviews=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='routine_review' AND payload->>'user'=%s",(user,)).fetchall()]
        if any(r.get('clientId')==client_id and r.get('date')==today for r in reviews):return {'reviewed':True}
        rows,_=select_alerts(crm._services['scoped_rows'](con,'client',user),orders,reviews,prefs,now.date())
        if client_id not in {r['clientId'] for r in rows}:raise HTTPException(409,'Alerta não está na lista atual; consulte novamente')
        crm.put(con,'routine_review',{'id':identifier(user,client_id),'user':user,'clientId':client_id,'date':today,'at':now.isoformat()});crm.changed(con)
    return {'reviewed':True}
