import unittest
from lead_conversion import conversion_metrics
class ConversionTest(unittest.TestCase):
 def test_cohort_and_dedup(self):
  leads=[{'id':'a','taxId':'123','createdAt':'2026-10-01'},{'id':'b','taxId':'123','createdAt':'2026-10-02'},{'id':'c','createdAt':'2026-10-03'},{'id':'d'}]
  clients=[{'id':'c1','taxId':'123'}]
  orders=[{'clientId':'c1','status':'Orçamento','date':'2026-10-04'}]
  self.assertEqual(conversion_metrics(leads,clients,orders,'2026-10')['conversion'],0)
  orders.append({'clientId':'c1','status':'Confirmado','date':'2026-10-05'})
  result=conversion_metrics(leads,clients,orders,'2026-10')
  self.assertEqual((result['leads'],result['buyers'],result['conversion'],result['undated']),(2,1,50,1))
 def test_no_base(self):self.assertIsNone(conversion_metrics([],[],[],'2026-10')['conversion'])
 def test_prior_purchase_excluded(self):
  self.assertEqual(conversion_metrics([{'id':'a','clientId':'c','createdAt':'2026-10-01'}],[{'id':'c'}],[{'clientId':'c','status':'Faturado','date':'2026-09-30'}],'2026-10')['buyers'],0)
