"""Canonical monthly industry plans; individual goals are read-only projections."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re


def validate_plan(goal):
    if goal.get('planVersion') != 1:
        raise ValueError('Versão do plano inválida')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', str(goal.get('month', ''))):
        raise ValueError('Mês inválido')
    if goal.get('user') != 'Equipe' or not isinstance(goal.get('brand'), str) or not goal['brand'].strip():
        raise ValueError('O plano mensal deve pertencer à equipe e a uma indústria')
    try:
        amount = Decimal(str(goal['amount']))
        if not amount.is_finite() or amount < 0 or amount > 10**10 or amount != amount.quantize(Decimal('.01')):
            raise ValueError('Meta inválida')
    except (KeyError, InvalidOperation, TypeError):
        raise ValueError('Meta inválida')
    days = goal.get('workDates')
    if not isinstance(days, list) or not days or len(days) > 31 or len(set(days)) != len(days):
        raise ValueError('Selecione os dias de trabalho do mês')
    for day in days:
        try:
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day or not day.startswith(goal['month'] + '-'):
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Dia de trabalho fora do mês')
    allocations = goal.get('allocations')
    if not isinstance(allocations, list) or not 1 <= len(allocations) <= 100:
        raise ValueError('Selecione ao menos um integrante')
    users, total = set(), 0
    for row in allocations:
        if not isinstance(row, dict):
            raise ValueError('Rateio inválido')
        user, weight = row.get('user'), row.get('weight')
        if not isinstance(user, str) or not user.strip() or len(user) > 120 or user == 'Equipe' or user in users:
            raise ValueError('Integrante inválido ou repetido')
        if type(weight) is not int or not 0 < weight <= 10000:
            raise ValueError('Percentual inválido')
        users.add(user); total += weight
    if total != 10000:
        raise ValueError('Os percentuais devem somar 100%')


def allocate(amount, rows):
    total = int((Decimal(str(amount)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    values = [total * row['weight'] // 10000 for row in rows]
    ranked = sorted(range(len(rows)), key=lambda i: (-(total * rows[i]['weight'] % 10000), i))
    for i in ranked[:total - sum(values)]:
        values[i] += 1
    return values


def expand_goals(goals):
    """A plan replaces legacy individual goals for its month/industry, never adds twice."""
    plans = {}
    for g in goals:
        if g.get('planVersion') == 1 and not g.get('sourcePlan'):
            k = (g.get('month'), g.get('brand'))
            if k not in plans or str(g.get('updatedAt', '')) >= str(plans[k].get('updatedAt', '')):
                plans[k] = g
    result = [deepcopy(g) for g in goals if (g.get('month'), g.get('brand')) not in plans]
    for g in plans.values():
        result.append(deepcopy(g))
        for i, (row, value) in enumerate(zip(g['allocations'], allocate(g['amount'], g['allocations']))):
            result.append({'id': g['id'] + '-member-' + str(i), 'month': g['month'], 'brand': g['brand'],
                           'user': row['user'], 'amount': value / 100, 'sourcePlan': g['id'],
                           'workDates': list(g['workDates']), 'updatedAt': g.get('updatedAt', '')})
    return result


def monthly_target(goals, month):
    rows = [g for g in expand_goals(goals) if g.get('month') == month]
    if not any(g.get('planVersion') or g.get('sourcePlan') for g in rows):
        return sum((Decimal(str(g.get('amount') or 0)) for g in rows if not g.get('brand')), Decimal(0))
    latest = {}
    for g in rows:
        if not g.get('brand'):
            continue
        k = (g['brand'], g.get('user'))
        if k not in latest or str(g.get('updatedAt', '')) >= str(latest[k].get('updatedAt', '')):
            latest[k] = g
    total = Decimal(0)
    for brand in {g['brand'] for g in latest.values()}:
        group = [g for g in latest.values() if g['brand'] == brand]
        team = next((g for g in group if g.get('user') == 'Equipe'), None)
        total += sum((Decimal(str(g.get('amount') or 0)) for g in ([team] if team else group)), Decimal(0))
    return total
