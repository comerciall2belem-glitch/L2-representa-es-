"""Pure sell-through projections. Callers MUST authorize and normalize inputs first.

Input amounts/units are signed (returns negative), documents are unique per
(stage, source, external_id), and dates use America/Belem business dates.
No database, API, UI or production integration is enabled by this module.
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal


def months_before(day, months):
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def project(documents, doors, start, end, history_complete=False):
    """Compute [start,end) KPIs from FULL authorized history up to end.

    end is the exclusive business date; active window is [end-12 months,end).
    Missing historical coverage makes first-time/restart/ceased counts unknown.
    doors includes previously purchasing and never-purchasing authorized doors.
    Each document: source, external_id, stage, date, client_id, amount, units,
    status, kind ('sale' or 'return'). Only posted documents are recognized.
    """
    if start >= end:
        raise ValueError('start must precede end')
    doors = set(doors)
    seen, rows = set(), []
    for doc in documents:
        key = (doc['stage'], doc['source'], doc['external_id'])
        if key in seen:
            raise ValueError('duplicate source document')
        seen.add(key)
        if doc['stage'] not in {'sell_in', 'sell_through', 'sell_out'}:
            raise ValueError('invalid stage')
        if doc['kind'] not in {'sale', 'return'}:
            raise ValueError('invalid document kind')
        amount, units = Decimal(str(doc['amount'])), Decimal(str(doc['units']))
        if not amount.is_finite() or not units.is_finite():
            raise ValueError('nonfinite amount or units')
        if (doc['kind'] == 'sale' and (amount < 0 or units <= 0)) or (doc['kind'] == 'return' and (amount > 0 or units >= 0)):
            raise ValueError('invalid sign')
        if doc['stage'] == 'sell_through' and doc['status'] == 'posted' and doc['date'] < end and doc['client_id'] in doors:
            rows.append({**doc, 'amount': amount, 'units': units})
    purchases = {}
    for row in rows:
        if row['kind'] == 'sale':
            purchases.setdefault(row['client_id'], set()).add(row['date'])
    window = months_before(end, 12)
    active = {c for c, dates in purchases.items() if any(window <= d < end for d in dates)}
    period = [r for r in rows if start <= r['date'] < end]
    positive = {r['client_id'] for r in period if r['kind'] == 'sale'}
    sales = sum((r['amount'] for r in period), Decimal(0))
    units = sum((r['units'] for r in period), Decimal(0))
    orders = sum(r['kind'] == 'sale' for r in period)
    new, restarted = set(), set()
    for client, dates in purchases.items():
        dates = sorted(dates)
        if window <= dates[0] < end:
            new.add(client)
        for previous, current in zip(dates, dates[1:]):
            if start <= current < end and previous < months_before(current, 12):
                restarted.add(client)
    ratio = lambda value, count: value / count if count else None
    return {
        'doors': len(doors), 'active_12m': len(active), 'positive_period': len(positive),
        'sell_through': sales, 'units': units, 'orders': orders,
        'productivity': ratio(sales, len(active)),
        'activity_percent': ratio(Decimal(len(positive & active)) * 100, len(active)),
        'upa': ratio(units, len(active)), 'average_order': ratio(sales, orders),
        'average_unit_price': ratio(sales, units) if units > 0 else None,
        'starts_12m': len(new) if history_complete else None,
        'restarts_period': len(restarted) if history_complete else None,
        'ceased_12m': len(set(purchases) - active) if history_complete else None,
        'history_complete': history_complete,
    }
