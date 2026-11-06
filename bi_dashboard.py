"""Read-only BI over recorded invoices; never substitutes orders for invoicing."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from calendar import monthrange
from zoneinfo import ZoneInfo
from fastapi import Header, HTTPException
from fastapi.responses import Response
import csv
import io
import json
from typing import Literal


def order_documents(orders):
    """Allocate recorded order totals by brand; explicitly not fiscal evidence."""
    docs = []
    invalid = 0
    for o in orders:
        if o.get('status') != 'Faturado':
            continue
        total = amount(o.get('amount'))
        if total is None:
            invalid += 1
            continue
        gross = defaultdict(lambda: Decimal(0))
        for item in o.get('items') or []:
            value = amount(item.get('subtotal'))
            if value is None:
                price, quantity = amount(item.get('unitPrice')), amount(item.get('quantity'))
                value = price * quantity if price is not None and quantity is not None else None
            if value is not None:
                gross[item.get('brand') or o.get('brand') or 'Não informada'] += value
        gross_total = sum(gross.values(), Decimal(0))
        if not gross_total:
            gross = {o.get('brand') or 'Não informada': Decimal(1)}
            gross_total = Decimal(1)
        cents = int((total * 100).quantize(Decimal(1)))
        allocations = [[b, int(cents * v / gross_total), (cents * v / gross_total) % 1] for b, v in sorted(gross.items())]
        remainder = cents - sum(r[1] for r in allocations)
        for r in sorted(allocations, key=lambda r: (-r[2], r[0]))[:remainder]:
            r[1] += 1
        for brand, value, _ in allocations:
            docs.append({'id': 'order-status:' + o['id'] + ':' + brand, 'orderId': o['id'],
                         'invoiceNumber': 'Status faturado / ' + brand, 'brand': brand,
                         'billedDate': str(o.get('billedAt') or o.get('date') or '')[:10],
                         'billed': str(Decimal(value) / 100)})
    return docs, invalid

TZ = ZoneInfo('America/Belem')


def amount(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() and value >= 0 else None
    except (InvalidOperation, TypeError):
        return None


def day(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def percentage(value, base):
    return value * 100 / base if base and base > 0 else None


def goal_value(goals, start, end, seller='', brand='', narrowed=False):
    # Monthly goals cannot be arbitrarily allocated to days, cities or customers.
    if narrowed or start.day != 1 or end != start.replace(day=monthrange(start.year, start.month)[1]) + timedelta(days=1):
        return None
    latest = {}
    for g in goals:
        if g.get('month') != start.strftime('%Y-%m'):
            continue
        key = (g.get('user', ''), g.get('brand', ''))
        if key not in latest or str(g.get('updatedAt', '')) >= str(latest[key].get('updatedAt', '')):
            latest[key] = g
    candidates = list(latest.values())
    if seller:
        candidates = [g for g in candidates if g.get('user') == seller]
    if brand:
        candidates = [g for g in candidates if g.get('brand') == brand]
    elif any(not g.get('brand') for g in candidates):
        candidates = [g for g in candidates if not g.get('brand')]
    buckets = defaultdict(list)
    for g in candidates:
        buckets[g.get('brand', '')].append(g)
    values = []
    for group in buckets.values():
        team = [g for g in group if g.get('user') == 'Equipe']
        selected = team if team else group
        parsed = [amount(g.get('amount')) for g in selected]
        if any(v is None for v in parsed):
            return None
        values.extend(parsed)
    return sum(values, Decimal(0)) if values else None


def build(clients, orders, settlements, goals, opportunities, start, end,
          seller='', brand='', state='', city='', segment='', finance=False, today=None, basis='invoices'):
    today = today or datetime.now(TZ).date()
    invalid_orders = 0
    if basis == 'orders':
        settlements, invalid_orders = order_documents(orders)
        finance = False  # Order amounts never establish financial rights or receipts.
    if start >= end or (end - start).days > 366:
        raise HTTPException(422, 'Selecione de 1 a 366 dias; a data final não é incluída')
    clients = {c['id']: c for c in clients if c.get('id')}
    selected_clients = {cid: c for cid, c in clients.items()
                        if (not state or c.get('state') == state)
                        and (not city or c.get('city') == city)
                        and (not segment or c.get('segment') == segment)
                        and (not seller or c.get('owner') == seller)}
    order_map = {o['id']: o for o in orders if o.get('id')}
    rows, invalid, seen = [], invalid_orders, set()
    for entry in settlements:
        o = order_map.get(entry.get('orderId'))
        if not o or o.get('status') == 'Cancelado' or o.get('clientId') not in clients:
            continue
        c = clients[o['clientId']]
        owner = o.get('sellerResponsible') or o.get('commissionSeller') or o.get('user') or ''
        # Historical sales belong to the responsible seller, not the operator/current owner.
        if ((seller and owner != seller) or (brand and entry.get('brand') != brand)
                or (state and c.get('state') != state) or (city and c.get('city') != city)
                or (segment and c.get('segment') != segment)):
            continue
        dt, value = day(entry.get('billedDate')), amount(entry.get('billed'))
        invoice = str(entry.get('invoiceNumber') or '').strip()
        if not dt or value is None or not invoice:
            invalid += 1
            continue
        key = (o['id'], invoice)
        if key in seen:
            invalid += 1
            continue
        seen.add(key)
        rows.append({'id': entry.get('id'), 'order_id': o['id'], 'invoice': invoice,
                     'date': dt, 'client_id': o['clientId'], 'client': c.get('name', ''),
                     'seller': owner, 'brand': entry.get('brand') or 'Não informada',
                     'city': c.get('city') or 'Localização pendente', 'state': c.get('state', ''),
                     'amount': value, '_entry': entry})
    eligible = [r for r in rows if start <= r['date'] < end and r['date'] <= today]
    total = sum((r['amount'] for r in eligible), Decimal(0))
    positive = {r['client_id'] for r in eligible if r['amount'] > 0}
    distinct_orders = {r['order_id'] for r in eligible if r['amount'] > 0}
    def aggregate(field):
        out = defaultdict(lambda: Decimal(0))
        for r in eligible:
            out[str(r[field])] += r['amount']
        return [{'label': k, 'amount': v} for k, v in sorted(out.items(), key=lambda x: (-x[1], x[0]))]
    target = goal_value(goals, start, end, seller, brand, bool(state or city or segment))
    last = {}
    for r in rows:
        if r['amount'] > 0 and r['date'] < min(end, today + timedelta(days=1)):
            last[r['client_id']] = max(last.get(r['client_id'], date.min), r['date'])
    cut = min(end - timedelta(days=1), today)
    inactive = []
    for cid, c in selected_clients.items():
        age = (cut - last[cid]).days if cid in last else None
        inactive.append({'id': cid, 'name': c.get('name', ''), 'city': c.get('city', ''),
                         'days': age, 'band': 'Sem histórico' if age is None else '90+' if age >= 90 else '60-89' if age >= 60 else '30-59' if age >= 30 else '0-29'})
    groups = aggregate('client_id')
    cumulative = Decimal(0)
    for g in groups:
        before = percentage(cumulative, total)
        g['abc'] = 'Sem base' if not total or g['amount'] <= 0 else 'A' if before < 80 else 'B' if before < 95 else 'C'
        g['name'] = clients[g['label']].get('name', '')
        cumulative += g['amount']
    by_stage = defaultdict(int)
    for o in opportunities:
        if o.get('clientId') not in selected_clients or (brand and o.get('brand') != brand):
            continue
        if seller and o.get('owner') != seller:
            continue
        by_stage[o.get('stage') or 'Sem etapa'] += 1
    warnings = ['Base: faturamentos registrados no financeiro por NF e data de faturamento; pedidos em aberto não entram.',
                'Cobertura parcial: devoluções, histórico anterior e cancelamentos fiscais devem ser conciliados. MoM/YoY, churn e ativação permanecem indisponíveis sem essa validação.',
                'Carteira usa responsável atual; vendas usam vendedor do pedido. Positivação por marca não possui carteira específica validada.',
                'Giro, encalhe e ABC de SKU exigem itens efetivamente faturados; não são estimados a partir do pedido.']
    if basis == 'orders':
        warnings[0] = 'Base operacional: somente pedidos com status Faturado. Usa billedAt quando disponível; caso contrário, data do pedido. Não comprova NF nem data fiscal e nunca soma com a base de NFs.'
        warnings.append('Totais multimarcas distribuídos proporcionalmente pelos itens, preservando descontos e centavos. Comissões, recebíveis e lucro não são inferidos dos pedidos.')
    if invalid:
        warnings.append(f'{invalid} registro(s) inválido(s) ou duplicado(s) excluído(s); revisar conciliação.')
    financial = None
    if finance:
        due_total = received = costs = Decimal(0)
        pending = overdue = Decimal(0)
        financial_missing = 0
        for r in rows:
            e = r['_entry']
            due = amount(e.get('commissionDue'))
            paid = amount(e.get('commissionReceived', 0))
            cost = amount(e.get('allocatedCost', 0))
            if due is None or paid is None or cost is None:
                financial_missing += 1
                continue
            if r in eligible:
                due_total += due; received += paid; costs += cost
            if r['date'] <= today:
                balance = max(Decimal(0), due - paid)
                pending += balance
                if day(e.get('due')) and day(e['due']) < today:
                    overdue += balance
        financial = {'expected_commission': due_total, 'received_on_selected_invoices': received,
                     'allocated_cost': costs, 'open_commission_today': pending,
                     'overdue_commission_today': overdue, 'net_profit': None,
                     'missing': financial_missing, 'position_date': str(today)}
        warnings.append('Financeiro: comissões previstas e recebidas dos documentos selecionados; posição aberta/vencida em hoje, independente do período. Não representa contas de clientes perante as indústrias nem lucro líquido.')
    return {'period': {'start': str(start), 'end_exclusive': str(end)},
            'generated_at': datetime.now(TZ).isoformat(), 'formula_version': 'bi-invoices-v1',
            'filters': dict(seller=seller, brand=brand, state=state, city=city, segment=segment, basis=basis),
            'basis': basis,
            'kpis': {'sales': total, 'target': target, 'attainment': percentage(total, target),
                     'orders': len(distinct_orders), 'buyers': len(positive),
                     'ticket_order': total / len(distinct_orders) if distinct_orders else None,
                     'ticket_client': total / len(positive) if positive else None,
                     'portfolio': len(selected_clients),
                     'positivation': percentage(Decimal(len(positive & set(selected_clients))), len(selected_clients)) if not brand else None},
            'brands': aggregate('brand'), 'sellers': aggregate('seller'), 'geography': aggregate('city'),
            'trend': sorted(aggregate('date'), key=lambda x: x['label']), 'abc_clients': groups,
            'clients': inactive, 'funnel_snapshot': dict(by_stage), 'financial': financial,
            'documents': [{k: str(v) if isinstance(v, date) else v for k, v in r.items() if k != '_entry'} for r in eligible],
            'warnings': warnings}


def install(app, db, auth, sectors_for, scoped_rows, is_seller):
    @app.get('/api/bi/dashboard')
    def dashboard(start: date, end: date, seller: str = '', brand: str = '', state: str = '',
                  city: str = '', segment: str = '', basis: Literal['orders','invoices'] = 'orders', authorization: str | None = Header(default=None)):
        user = auth(authorization)
        permissions = sectors_for(user)
        if not {'commercial', 'management', 'finance', 'office'} & permissions:
            raise HTTPException(403, 'BI sem permissão')
        if start >= end or (end - start).days > 366:
            raise HTTPException(422, 'Selecione de 1 a 366 dias; fim é exclusivo')
        with db() as con:
            clients = scoped_rows(con, 'client', user)
            orders = scoped_rows(con, 'order', user)
            goals = scoped_rows(con, 'goal', user)
            opportunities = scoped_rows(con, 'opportunity', user)
            # SQL restricts private invoice payloads to authorized order IDs first.
            ids = [o['id'] for o in orders if o.get('id')]
            settlements = [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='settlement' AND payload->>'orderId'=ANY(%s) ORDER BY updated_at,id", (ids,))] if ids else []
            if is_seller(con, user):
                # Do not expose another seller through overlapping authorized clients.
                orders = [o for o in orders if (o.get('sellerResponsible') or o.get('commissionSeller') or o.get('user')) == user]
                if seller and seller != user:
                    raise HTTPException(403, 'Vendedor fora do seu escopo')
                seller = user
            result = build(clients, orders, settlements, goals, opportunities, start, end,
                           seller, brand, state, city, segment, 'finance' in permissions, basis=basis)
        # No shared response/cache can retain data after role or account changes.
        return Response(json.dumps(result, default=str), media_type='application/json', headers={'Cache-Control': 'no-store'})
