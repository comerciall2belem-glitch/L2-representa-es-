from datetime import date
from decimal import Decimal
import pytest
from commercial_indicators import project, months_before

START, END = date(2026, 10, 1), date(2026, 11, 1)
def doc(key, client, day, amount=100, units=10, **changes):
    return dict(source='erp', external_id=key, stage='sell_through', date=date.fromisoformat(day), client_id=client, amount=amount, units=units, status='posted', kind='sale') | changes

def test_ratios_and_stages():
    rows = [doc('1','a','2026-10-02'), doc('2','a','2026-10-03'), doc('3','b','2026-09-01'), doc('4','a','2026-10-04',stage='sell_out')]
    r = project(rows, ['a','b','c'], START, END, True)
    assert (r['doors'],r['active_12m'],r['positive_period']) == (3,2,1)
    assert (r['sell_through'], r['productivity'],r['activity_percent'], r['upa'],r['average_order'],r['average_unit_price']) == (200,100,50,10,100,10)

def test_history_and_restart():
    rows = [doc('1','a','2024-01-01'),doc('2','a','2026-10-10'),doc('3','b','2024-01-01')]
    r = project(rows,['a','b','never'],START,END,True)
    assert (r['starts_12m'],r['restarts_period'],r['ceased_12m']) == (0,1,1)
    assert project(rows,['a'],START,END)['starts_12m'] is None

def test_returns_and_permissions():
    rows = [doc('1','a','2026-10-01'),doc('2','a','2026-10-02',-30,-3,kind='return'),doc('3','secret','2026-10-01',9999)]
    r = project(rows,['a'],START,END,True)
    assert (r['sell_through'],r['units'],r['average_order']) == (70,7,70)

def test_date_boundary_and_leap():
    assert months_before(date(2024,2,29),12) == date(2023,2,28)
    rows = [doc('1','a','2025-11-01'),doc('2','b','2025-10-31'),doc('3','c','2026-11-01')]
    assert project(rows,['a','b','c'],START,END,True)['active_12m'] == 1

def test_empty_denominators():
    r = project([],[],START,END,True)
    assert r['productivity'] is None and r['activity_percent'] is None
    assert r['sell_through'] == Decimal(0)

def test_duplicate_rejected():
    d = doc('1','a','2026-10-01')
    with pytest.raises(ValueError): project([d,d],['a'],START,END)

@pytest.mark.parametrize('changes',[{'amount':'NaN'},{'units':-1},{'stage':'bad'},{'kind':'bad'}])
def test_invalid(changes):
    with pytest.raises(ValueError): project([doc('1','a','2026-10-01',**changes)],['a'],START,END)
