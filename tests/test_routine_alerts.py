import unittest
from datetime import date
from unittest.mock import patch,MagicMock
from pydantic import ValidationError
from fastapi import HTTPException
import routine_alerts as r
class RoutineTests(unittest.TestCase):
 def setUp(self):self.today=date(2026,10,12);self.clients=[{'id':'a','name':'Loja A'},{'id':'b','name':'Loja B'},{'id':'none','name':'Sem compra'}]
 def order(self,c,d):return {'id':c+d,'clientId':c,'date':d,'status':'Faturado','amount':100}
 def test_default_limit_excludes_unknowns_budgets_future_and_recent(self):
  rows,_=r.select_alerts(self.clients,[self.order('a','2026-08-01'),{**self.order('b','2026-07-01'),'status':'Orçamento'},self.order('a','2026-11-01')],[],r.Preferences(),self.today)
  self.assertEqual([x['clientId'] for x in rows],['a']);self.assertEqual(rows[0]['limitDays'],45)
 def test_known_cycle_overrides_fallback(self):
  rows,_=r.select_alerts(self.clients,[self.order('a',d) for d in ['2026-08-01','2026-08-15','2026-08-29']],[],r.Preferences(fallbackDays=90),self.today)
  self.assertEqual(rows[0]['limitDays'],14);self.assertEqual(rows[0]['basis'],'Ciclo de compra')
 def test_daily_cap_review_interval_and_disabled(self):
  orders=[self.order(c,'2026-07-01') for c in ['a','b']]
  rows,remaining=r.select_alerts(self.clients,orders,[{'clientId':'a','date':'2026-10-12'}],r.Preferences(dailyLimit=1),self.today);self.assertEqual(rows,[]);self.assertEqual(remaining,0)
  rows,_=r.select_alerts(self.clients,orders,[{'clientId':'a','date':'2026-10-10'}],r.Preferences(),self.today);self.assertEqual([x['clientId'] for x in rows],['b'])
  self.assertEqual(r.select_alerts(self.clients,orders,[],r.Preferences(enabled=False),self.today)[0],[])
 def test_invalid_settings_rejected(self):
  for kw in ({'dailyLimit':6},{'fallbackDays':0},{'intervalDays':0}):
   with self.assertRaises(ValidationError):r.Preferences(**kw)
 def test_review_cannot_write_foreign_client(self):
  con=MagicMock();db=MagicMock();db.return_value.__enter__.return_value=con
  with patch.object(r.crm,'access',return_value='Erika'),patch.dict(r.crm._services,{'db':db,'scoped_rows':lambda con,kind,user:[]}):
   with self.assertRaises(HTTPException) as e:r.review('foreign','Bearer x')
   self.assertEqual(e.exception.status_code,403)
   self.assertFalse(any('INSERT' in call.args[0] for call in con.execute.call_args_list))
