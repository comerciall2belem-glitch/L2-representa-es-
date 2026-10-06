import unittest
from financial_analysis import analyze_finance
class AnalysisTest(unittest.TestCase):
 def test_formulas(self):
  inputs=[{'kind':'product_cost','name':'Custo','brand':'BT','sku':'1','amount':1,'effectiveDate':'2026-10-01'},{'kind':'operating_cost','name':'Aluguel','amount':100,'effectiveDate':'2026-10-01'},{'kind':'opening_balance','name':'Banco','amount':50,'effectiveDate':'2026-10-01'}]
  orders=[{'status':'Faturado','date':'2026-10-02','amount':1000,'sellerCommissionRate':2,'items':[{'brand':'BT','sku':'1','quantity':10,'unitPrice':100}]}]
  r=analyze_finance(inputs,orders,[{'Tipo':'Despesa','Vencimento':'2026-10-05','Valor':80}],[{'id':'bt','rate':10}],'2026-10')
  self.assertEqual(r['contribution'],70);self.assertEqual(r['ticketBreakEven'],1428.57);self.assertEqual(r['workingCapitalGap'],30)
 def test_missing_cost(self):
  r=analyze_finance([], [{'status':'Faturado','date':'2026-10-01','amount':10,'items':[{'brand':'A','sku':'1'}]}],[],[],'2026-10')
  self.assertIsNone(r['contribution']);self.assertIsNone(r['workingCapitalGap'])
