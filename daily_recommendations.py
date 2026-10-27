"""Daily opportunity estimates based exclusively on registered invoice settlements."""
import calendar
import hashlib
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fastapi import APIRouter, Header, HTTPException
import strategic_crm as crm

router = APIRouter(prefix='/api/daily-recommendation', tags=['Recomendação diária'])
CENT = Decimal('0.01')

def money(value):
    try:
        n = Decimal(str(value))
        return n.quantize(CENT, rounding=ROUND_HALF_UP) if n.is_finite() and n >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None

def month_offset(today, n):
    value = today.year * 12 + today.month - 1 - n
    return f'{value // 12:04d}-{value % 12 + 1:02d}'

def build(clients, orders, settlements, goals, today, user, seller):
    """Three completed calendar months including zero months; amounts in cents."""
    month = today.strftime('%Y-%m'); previous = [month_offset(today, n) for n in (1, 2, 3)]
    elapsed, days = today.day, calendar.monthrange(today.year, today.month)[1]
    clients = {str(c['id']): c for c in clients if c.get('id') and c.get('active') is not False and not c.get('archived') and str(c.get('status','')).casefold() not in ('inativo','inativa','arquivado','arquivada')}
    orders = {str(o['id']): o for o in orders if o.get('id') and str(o.get('clientId','')) in clients and o.get('status') != 'Cancelado'}
    totals = {cid: {m: Decimal(0) for m in previous + [month]} for cid in clients}
    brands = {}; current_docs = []; history_months = {cid: set() for cid in clients}
    for doc in settlements:
        order = orders.get(str(doc.get('orderId',''))); day = crm.local_date(doc.get('billedDate')); amount = money(doc.get('billed'))
        if not order or not doc.get('invoiceNumber') or not day or day > today or amount is None: continue
        period = day.strftime('%Y-%m'); cid = str(order['clientId'])
        if period in totals[cid]: totals[cid][period] += amount
        if period in previous: history_months[cid].add(period)
        if period in previous and doc.get('brand'):
            b = str(doc['brand']).strip(); brands.setdefault(cid, {})[b] = brands.setdefault(cid, {}).get(b, Decimal(0)) + amount
        owner = order.get('sellerResponsible') or order.get('commissionSeller') or order.get('user')
        if period == month and (not seller or owner == user): current_docs.append((str(doc.get('brand') or '').strip().casefold(), amount))
    selected = [g for g in goals if g.get('month') == month and str(g.get('user','')).casefold() == (user if seller else 'Equipe').casefold()]
    latest = {}
    for g in selected:
        k = str(g.get('brand') or '').strip().casefold()
        if k not in latest or str(g.get('updatedAt','')) >= str(latest[k].get('updatedAt','')): latest[k] = g
    # A general goal replaces the sum of individual brand goals, never both.
    goal_parts = [latest['']] if '' in latest else list(latest.values())
    parsed = [money(g.get('amount')) for g in goal_parts]
    target = sum(parsed, Decimal(0)) if parsed and all(p is not None for p in parsed) else None
    goal_brands = set(latest)
    actual = sum((amount for brand, amount in current_docs if '' in goal_brands or brand in goal_brands), Decimal(0)) if goal_brands else sum((amount for _, amount in current_docs), Decimal(0))
    projection = (actual * days / elapsed).quantize(CENT, rounding=ROUND_HALF_UP)
    status = 'sem_meta' if target is None or target <= 0 else 'em_risco' if projection < target else 'no_ritmo'
    rows = []; unavailable = []
    for cid, c in clients.items():
        if len(history_months[cid]) < 3 or target is None or target <= 0:
            unavailable.append({'id': cid, 'code': str(c.get('code') or c.get('codigo') or cid), 'name': str(c.get('name') or 'Cliente'), 'message': 'Cliente sem histórico suficiente ou meta cadastrada para cálculo de potencial.'})
            continue
        history = sum((totals[cid][m] for m in previous), Decimal(0))
        if history <= 0: continue
        average = history / Decimal(3); current = totals[cid][month]
        if current >= average * elapsed / days: continue
        potential = max(Decimal(0), average - current).quantize(CENT, rounding=ROUND_HALF_UP)
        if not potential: continue
        top = sorted(brands.get(cid, {}).items(), key=lambda x: (-x[1], x[0]))[:2]
        suggestion = ('Converse sobre reposição de ' + ' e '.join(b for b, _ in top) + ', presentes no histórico faturado; confirme o estoque e a necessidade atual.') if top else 'Converse sobre reposição e confirme o estoque e a necessidade atual do cliente.'
        rows.append({'id': cid, 'code': str(c.get('code') or c.get('codigo') or cid), 'name': str(c.get('name') or 'Cliente'), 'potential': str(potential), 'suggestion': suggestion})
    rows.sort(key=lambda x: (-Decimal(x['potential']), x['name'].casefold(), x['id']))
    unavailable.sort(key=lambda x: (x['name'].casefold(), x['id']))
    return {'date':today.isoformat(), 'status':status, 'actual':str(actual.quantize(CENT)), 'target':str(target) if target is not None else None, 'projection':str(projection), 'rows':rows[:10], 'unavailable':unavailable[:max(0, 10-len(rows))], 'basis':'Média do faturamento dos três meses completos anteriores com faturamento registrado em cada mês; potencial estimado = média mensal menos faturamento do mês. Projeção linear por dias corridos.'}

def identity(user, day):
    return hashlib.sha256((user+'|'+day).encode()).hexdigest()

def authorize(header):
    user = crm._services['auth'](header)
    crm._services['require_sector'](user, 'commercial', 'office')
    return user

@router.get('')
def recommendation(authorization: str | None = Header(default=None)):
    user=authorize(authorization); today=datetime.now(crm.TZ).date(); key=identity(user,today.isoformat())
    with crm._services['db']() as con:
        if con.execute("SELECT 1 FROM entities WHERE kind='daily_recommendation_ack' AND id=%s",(key,)).fetchone(): return {'date':today.isoformat(),'acknowledged':True}
        seller=crm._services['is_seller'](con,user)
        clients=crm._services['scoped_rows'](con,'client',user); orders=crm._services['scoped_rows'](con,'order',user)
        # Invoice settlements are read on the server and filtered by authorized order IDs in build().
        settlements=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='settlement' AND coalesce(payload->>'invoiceNumber','')<>''")]
        goals=crm._services['scoped_rows'](con,'goal',user)
        result=build(clients,orders,settlements,goals,today,user,seller)
    return {**result,'acknowledged':False}

@router.get('/status')
def status(authorization: str | None = Header(default=None)):
    user=authorize(authorization); today=datetime.now(crm.TZ).date().isoformat()
    with crm._services['db']() as con:
        done=bool(con.execute("SELECT 1 FROM entities WHERE kind='daily_recommendation_ack' AND id=%s", (identity(user,today),)).fetchone())
    return {'date':today,'acknowledged':done}

@router.post('/ack')
def acknowledge(day: str, authorization: str | None = Header(default=None)):
    user=authorize(authorization); today=datetime.now(crm.TZ).date().isoformat()
    if day != today: raise HTTPException(409, 'A recomendação é de outro dia. Atualize para consultar a recomendação atual.')
    with crm._services['db']() as con:
        crm.put(con,'daily_recommendation_ack',{'id':identity(user,today),'user':user,'date':today})
    return {'ok':True,'date':today}
