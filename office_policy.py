"""Monthly bonus preview and field-only promoter access. No payment posting."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import unicodedata
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, StrictBool

router = APIRouter(prefix='/api/office-policy', tags=['Bonus and promoters'])
_services = {}
def configure(**services): _services.update(services)

def period(value):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value or ''):
        raise HTTPException(400, 'Mês inválido')
    try: date.fromisoformat(value+'-01')
    except ValueError: raise HTTPException(400, 'Mês inválido')
    return value

def cents(value):
    try:
        n = Decimal(str(value).replace(',', '.'))
        if not n.is_finite() or n < 0 or n > Decimal('10000000000'):
            raise ValueError()
        return int((n * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (ValueError, InvalidOperation, TypeError):
        raise HTTPException(400, 'Valor monetário inválido na apuração')

def calculate_bonus(orders, goals, members, eligible, month, today, fulfillments=()):
    period(month)
    y, m = map(int, month.split('-'))
    cutoff = min(today, date(y, m, monthrange(y, m)[1])).isoformat()
    general = [g for g in goals if g.get('month') == month and not g.get('brand')
               and unicodedata.normalize('NFKD', str(g.get('user', ''))).strip().casefold() == 'equipe']
    # An explicit office goal is required; individual/industry goals are not guessed.
    latest = max(general, key=lambda g: (str(g.get('updatedAt', '')), str(g.get('id', ''))), default=None)
    target = cents(latest.get('amount')) if latest else None
    def stage(value):
        return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '')) if not unicodedata.combining(c)).strip().casefold()
    shipments = {f.get('orderId'): f for f in fulfillments}
    sales, billed, seen = 0, 0, set()
    billed_orders = []
    billed_stages = {stage(v) for v in ('Faturado', 'Concluído', 'Em transporte', 'Entregue', 'Pós-venda concluído')}
    accepted = billed_stages | {stage('Confirmado'), stage('Confirmado pela indústria')}
    for o in orders:
        if not o.get('id') or o['id'] in seen or stage(o.get('status')) == 'cancelado':
            continue
        seen.add(o['id'])
        try: day = date.fromisoformat(str(o.get('date', ''))).isoformat()
        except (ValueError, TypeError): continue
        if not day.startswith(month) or day > cutoff: continue
        f = shipments.get(o['id'], {})
        stages = {o.get('status'), f.get('stage')}
        stages |= {h.get('stage') for h in [*(o.get('stageHistory') or []), *(f.get('stageHistory') or [])]}
        stages = {stage(v) for v in stages}
        if not stages & accepted: continue
        amount = cents(o.get('amount'))
        sales += amount
        if stages & billed_stages:
            billed += amount
            billed_orders.append({'orderId': o['id'], 'baseCents': amount})
    reached = target is not None and target > 0 and sales >= target
    amount = int((Decimal(billed) / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if reached else 0
    rows = [{'user': name, 'eligible': name in eligible, 'bonusCents': amount if name in eligible else 0}
            for name in sorted(set(members))]
    return {'month': month, 'asOf': cutoff, 'goalCents': target, 'salesCents': sales,
            'billedCents': billed, 'billedOrders': sorted(billed_orders, key=lambda r:r['orderId']), 'goalReached': reached, 'rate': '1.00', 'members': rows,
            'basis': 'Pedidos faturados/progressão logística, pela data do pedido',
            'status': 'Prévia recalculada; não gera pagamento nem lançamento financeiro'}

def actor(authorization, finance=False):
    user = _services['auth'](authorization)
    if finance and 'finance' not in _services['sectors_for'](user):
        raise HTTPException(403, 'Bônus restrito ao financeiro')
    return user

class Eligibility(BaseModel):
    month: str
    user: str = Field(min_length=2, max_length=80)
    eligible: StrictBool

@router.put('/bonus/eligibility')
def set_eligibility(data: Eligibility, authorization: str | None = Header(default=None)):
    user = actor(authorization, finance=True)
    if not _services['admin_access'](user): raise HTTPException(403, 'Elegibilidade restrita à administração')
    period(data.month)
    with _services['db']() as con:
        con.execute('SELECT pg_advisory_xact_lock(8239020)')
        if not con.execute("SELECT 1 FROM app_users WHERE username=%s AND active AND role='Vendedor'", (data.user,)).fetchone():
            raise HTTPException(400, 'Beneficiário deve ser vendedor ativo')
        from psycopg.types.json import Jsonb
        payload = {'id': data.month+'|'+data.user, 'month': data.month, 'user': data.user, 'eligible': data.eligible, 'updatedBy': user}
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('bonus_eligibility',%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()", (payload['id'], Jsonb(payload)))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'bonus_eligibility',%s,'update')", (user,payload['id']))
        con.execute("SELECT pg_notify('l2_records_changed','')")
    return {'ok': True, **payload}

@router.get('/bonus')
def bonus(month: str, authorization: str | None = Header(default=None)):
    actor(authorization, finance=True)
    period(month)
    with _services['db']() as con:
        orders=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='order'")]
        goals=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='goal' AND payload->>'month'=%s", (month,))]
        members=[r[0] for r in con.execute("SELECT username FROM app_users WHERE active AND role='Vendedor'")]
        eligible=eligibility_for_month([r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='bonus_eligibility' AND payload->>'month'<=%s", (month,))],month)
        fulfillment=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='fulfillment'")]
    return calculate_bonus(orders,goals,members,eligible,month,_services['today'](),fulfillment)

class Assignment(BaseModel):
    clientIds: list[str] = Field(max_length=500)

@router.get('/promoters/{member}/clients')
def promoter_clients(member: str, authorization: str | None = Header(default=None)):
    user=actor(authorization)
    if not _services['admin_access'](user): raise HTTPException(403, 'Carteira restrita à administração')
    with _services['db']() as con:
        return {'clientIds':[r[0] for r in con.execute('SELECT client_id FROM client_seller_authorizations WHERE seller_username=%s AND active',(member,))]}

@router.put('/promoters/{member}/clients')
def assign_promoter(member: str, data: Assignment, authorization: str | None = Header(default=None)):
    user=actor(authorization)
    if not _services['admin_access'](user): raise HTTPException(403, 'Carteira restrita à administração')
    ids=sorted(set(data.clientIds))
    with _services['db']() as con:
        if not con.execute("SELECT 1 FROM app_users WHERE username=%s AND active AND role='Promotora'",(member,)).fetchone():
            raise HTTPException(400,'Promotora ativa não encontrada')
        for cid in ids:
            if not con.execute("SELECT 1 FROM entities WHERE kind='client' AND id=%s",(cid,)).fetchone():
                raise HTTPException(400,'Cliente não encontrado')
        con.execute('UPDATE client_seller_authorizations SET active=false WHERE seller_username=%s',(member,))
        for cid in ids:
            con.execute("INSERT INTO client_seller_authorizations(client_id,seller_username,active,approved_by) VALUES(%s,%s,true,%s) ON CONFLICT(client_id,seller_username) DO UPDATE SET active=true,approved_by=excluded.approved_by",(cid,member,user))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'promoter_portfolio',%s,'update')",(user,member))
        con.execute("SELECT pg_notify('l2_records_changed','')")
    return {'ok':True,'clientIds':ids}

def validate_promoter_change(con,user,kind,obj):
    if kind not in ('visit','interaction','task','route'):
        raise HTTPException(403,'Promotora pode registrar somente atendimentos, interações, tarefas e rotas')
    field='owner' if kind=='interaction' else 'user'
    previous=con.execute('SELECT payload FROM entities WHERE kind=%s AND id=%s',(kind,obj.get('id'))).fetchone()
    if previous and previous[0].get(field)!=user:
        raise HTTPException(403,'Registro pertence a outro responsável')
    if obj.get(field,user)!=user:
        raise HTTPException(403,'Responsável inválido')
    obj[field]=user

def promoter_snapshot(result,user):
    public={'clients','visits','interactions','tasks','routes','prices','industries','priceTables'}
    for name,value in list(result.items()):
        if isinstance(value,list) and name not in public: result[name]=[]
    result['crmTeam']={}
    result['team']=[user]
    for name in ('visits','tasks','routes'):
        result[name]=[r for r in result.get(name,[]) if r.get('user')==user]
    result['interactions']=[r for r in result.get('interactions',[]) if r.get('owner')==user]
    fields={
      'clients':('id','name','tradeName','taxId','state','city','district','address','postalCode','contact','phone','owner','latitude','longitude'),
      'prices':('id','brand','tableId','sku','description','price','state','active'),
      'industries':('id','name','displayName','active'),
      'priceTables':('id','brand','state','title','active')
    }
    for name,keys in fields.items():result[name]=[{k:r[k] for k in keys if k in r} for r in result.get(name,[])]
    return result

def promoter_route_allowed(method,path):
    return (method,path) in {
        ('POST','/api/sync'),('POST','/api/logout'),('POST','/api/change-password'),
        ('GET','/api/records/events'),('GET','/api/commercial/drafts'),('PUT','/api/commercial/drafts')
    }

def restricted_promoter_request(authorization):
    user=_services['auth'](authorization)
    with _services['db']() as con:
        row=con.execute('SELECT role FROM app_users WHERE username=%s AND active',(user,)).fetchone()
    return bool(row and row[0]=='Promotora')

def eligibility_for_month(rows,month):
    period(month)
    latest={}
    for row in rows:
        effective=row.get('month','')
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',effective) or effective>month or not row.get('user'): continue
        if row['user'] not in latest or effective>latest[row['user']]['month']:
            latest[row['user']]=row
    return {name for name,row in latest.items() if row.get('eligible') is True}


class BonusClosing(BaseModel):
    month: str
    model_config = {'extra': 'forbid'}


def close_bonus_cycle(con, month, user, today):
    """One immutable, transactionally serialized accrual per completed month."""
    from hashlib import sha256
    from datetime import datetime, timezone
    from psycopg.types.json import Jsonb
    period(month)
    if month >= today.strftime('%Y-%m'):
        raise HTTPException(409, 'Aguarde o encerramento do mês')
    # Same lock as sync: the snapshot and accruals commit atomically.
    con.execute('SELECT pg_advisory_xact_lock(8239020)')
    existing = con.execute("SELECT payload FROM entities WHERE kind='bonus_cycle' AND id=%s", (month,)).fetchone()
    if existing:
        return existing[0]
    orders = [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='order'")]
    goals = [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='goal' AND payload->>'month'=%s", (month,))]
    members = [r[0] for r in con.execute("SELECT username FROM app_users WHERE active AND role='Vendedor'")]
    eligible = eligibility_for_month([r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='bonus_eligibility' AND payload->>'month'<=%s", (month,))], month)
    fulfillment = [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='fulfillment'")]
    result = calculate_bonus(orders, goals, members, eligible, month, today, fulfillment)
    if result['goalReached'] is not True:
        raise HTTPException(409, 'Meta geral explícita não atingida; bônus não liberado')
    if not any(m['eligible'] for m in result['members']):
        raise HTTPException(409, 'Nenhum vendedor ativo elegível configurado para o período')
    # Largest remainder allocation: the sum equals the rounded office-wide 1%.
    bases = result['billedOrders']
    shares = {r['orderId']: r['baseCents']//100 for r in bases}
    target = int((Decimal(result['billedCents'])/100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    remainder = target - sum(shares.values())
    for row in sorted(bases, key=lambda r:(-(r['baseCents']%100),r['orderId']))[:remainder]:
        shares[row['orderId']] += 1
    closed_at = datetime.now(timezone.utc).isoformat()
    rule = 'office-bonus-v1'
    count = 0
    for member in result['members']:
        if not member['eligible']: continue
        for base in bases:
            key = sha256((month+'|'+member['user']+'|'+base['orderId']).encode()).hexdigest()
            accrual = {'id':key, 'month':month, 'user':member['user'], **base,
                       'bonusCents':shares[base['orderId']], 'rate':'1.00',
                       'rule':rule, 'meta_escritorio_atingida':True, 'closedBy':user, 'closedAt':closed_at}
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('bonus_accrual',%s,%s)", (key,Jsonb(accrual)))
            count += 1
    receipt = {**result, 'id':month, 'rule':rule, 'closedAt':closed_at, 'closedBy':user,
               'meta_escritorio_atingida':True, 'accrualCount':count,
               'status':'Apuração encerrada; bônus separado da comissão original; não gera pagamento'}
    con.execute("INSERT INTO entities(kind,id,payload) VALUES('bonus_cycle',%s,%s)", (month,Jsonb(receipt)))
    con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'bonus_cycle',%s,'close_office_bonus_v1')", (user,month))
    con.execute("SELECT pg_notify('l2_records_changed','')")
    return receipt


@router.post('/bonus/close')
def close_bonus(data: BonusClosing, authorization: str | None = Header(default=None)):
    user = actor(authorization, finance=True)
    if not _services['admin_access'](user):
        raise HTTPException(403, 'Fechamento restrito à administração')
    with _services['db']() as con:
        return close_bonus_cycle(con, data.month, user, _services['today']())


@router.get('/bonus/closing')
def bonus_closing(month: str, authorization: str | None = Header(default=None)):
    actor(authorization, finance=True)
    period(month)
    with _services['db']() as con:
        row = con.execute("SELECT payload FROM entities WHERE kind='bonus_cycle' AND id=%s", (month,)).fetchone()
    return {'closing':row[0] if row else None}


@router.post('/bonus/close-due')
def close_due_bonus(x_l2_bonus_job_token: str | None = Header(default=None)):
    """Cloud scheduler only; no public default credential or client-supplied goal."""
    import os, secrets
    configured = os.getenv('L2_BONUS_JOB_TOKEN', '')
    if len(configured) < 32:
        raise HTTPException(503, 'Rotina automática não configurada')
    if not x_l2_bonus_job_token or not secrets.compare_digest(configured, x_l2_bonus_job_token):
        raise HTTPException(401, 'Autenticação da rotina inválida')
    user = os.getenv('L2_BONUS_JOB_ADMIN', '')
    if not user or not _services['admin_access'](user) or 'finance' not in _services['sectors_for'](user):
        raise HTTPException(403, 'Administrador financeiro da rotina não autorizado')
    today = _services['today']()
    from datetime import timedelta
    month = (today.replace(day=1)-timedelta(days=1)).strftime('%Y-%m')
    with _services['db']() as con:
        active = con.execute('SELECT 1 FROM app_users WHERE username=%s AND active', (user,)).fetchone()
        if not active: raise HTTPException(403, 'Administrador da rotina inativo')
        return close_bonus_cycle(con, month, user, today)
