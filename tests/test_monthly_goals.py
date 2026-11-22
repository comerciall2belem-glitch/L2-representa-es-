import unittest
from copy import deepcopy
from datetime import date
from monthly_goals import validate_plan, expand_goals, monthly_target
from bi_dashboard import goal_value

class MonthlyGoalsTest(unittest.TestCase):
    def setUp(self):
        self.g={'id':'p','month':'2026-11','brand':'BT','user':'Equipe','amount':1000.01,'planVersion':1,'workDates':['2026-11-02','2026-11-07'],'allocations':[{'user':'Ana Paula','weight':6000},{'user':'Euler','weight':4000}]}
    def test_validation(self):
        validate_plan(self.g)
        for field,value in [('amount',float('nan')),('amount',-1),('month','2026-13'),('workDates',[]),('workDates',['2026-12-01']),('workDates',['2026-11-31']),('allocations',[{'user':'Euler','weight':9999}])]:
            g=deepcopy(self.g);g[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):validate_plan(g)
    def test_projection_overrides_without_double_count(self):
        values=expand_goals([self.g,{'id':'old','month':'2026-11','user':'Euler','brand':'BT','amount':9999}])
        self.assertEqual(sum(round(g['amount']*100) for g in values if g.get('sourcePlan')),100001)
        self.assertEqual(values,expand_goals(values))
        self.assertEqual(len(values),3)
    def test_bi_total_person_and_month_scope(self):
        rows=[self.g,{'month':'2026-11','user':'Equipe','amount':9999}]
        start,end=date(2026,11,1),date(2026,12,1)
        self.assertEqual(float(goal_value(rows,start,end)),1000.01)
        self.assertEqual(float(goal_value(rows,start,end,seller='Euler')),400)
        self.assertIsNone(goal_value(rows,date(2026,11,2),end))
        self.assertIsNone(goal_value(rows,date(2026,12,1),date(2027,1,1)))
    def test_scoped_projection_and_shared_target(self):
        rows=expand_goals([self.g])
        personal=[g for g in rows if g.get('user')=='Euler']
        self.assertEqual(expand_goals(personal),personal)
        self.assertEqual(float(goal_value(personal,date(2026,11,1),date(2026,12,1),seller='Euler')),400)
        legacy={'month':'2026-11','brand':'Outra','user':'Equipe','amount':200}
        general={'month':'2026-11','user':'Equipe','amount':9999}
        self.assertEqual(float(monthly_target(rows+[legacy,general],'2026-11')),1200.01)
