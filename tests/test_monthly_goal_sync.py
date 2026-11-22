from copy import deepcopy
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from test_order_numbering import FakeCon, FakeDB, Cursor, namespace, HTTPException

class GoalCon(FakeCon):
    def __init__(self):super().__init__();self.goals={}
    def execute(self,q,p=None):
        if q.startswith("SELECT payload FROM entities WHERE kind='goal' AND id="):
            return Cursor((self.goals[p[0]],) if p[0] in self.goals else None)
        if q.startswith('SELECT 1 FROM app_users'):return Cursor((1,))
        if q.startswith('INSERT INTO entities') and p[0]=='goal':self.goals[p[1]]=deepcopy(p[2]);return Cursor()
        return super().execute(q,p)

class MonthlySyncTests(TestCase):
    def setUp(self):
        self.g={'id':'p','month':'2026-11','brand':'BT','user':'Equipe','amount':10.01,'planVersion':1,'workDates':['2026-11-03'],'allocations':[{'user':'Ana Paula','weight':10000}]}
        self.con=GoalCon()
    def send(self,g,permissions={'commercial','management'}):
        with patch.dict(namespace,{'auth':lambda token:'Ana Paula','db':lambda:FakeDB(self.con),'effective_sectors':lambda *a:permissions}):
            return namespace['sync'](SimpleNamespace(changes=[SimpleNamespace(type='goal',data=g,changeId='change')]),'test')
    def test_permissions(self):
        with self.assertRaises(HTTPException) as e:self.send(self.g,{'commercial'})
        self.assertEqual(e.exception.status_code,403);self.assertFalse(self.con.goals)
    def test_history_and_conflict(self):
        self.send(self.g);stored=deepcopy(self.con.goals['p']);self.assertEqual(stored['history'],[])
        g={**self.g,'amount':20.01,'baseUpdatedAt':stored['updatedAt']};self.send(g)
        self.assertEqual(self.con.goals['p']['history'][0]['amount'],10.01)
        with self.assertRaises(HTTPException) as e:self.send(g)
        self.assertEqual(e.exception.status_code,409)
    def test_invalid_plan_and_projection_cannot_be_written(self):
        for g in [{**self.g,'workDates':[]},{**self.g,'sourcePlan':'p'}]:
            with self.assertRaises(HTTPException) as e:self.send(g)
            self.assertEqual(e.exception.status_code,400)
        self.assertFalse(self.con.goals)
