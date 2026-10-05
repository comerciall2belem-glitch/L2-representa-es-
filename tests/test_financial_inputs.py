import unittest
from pydantic import ValidationError
from financial_inputs import FinancialInput
class FinancialInputsTest(unittest.TestCase):
 def test_negative_balance(self):
  self.assertEqual(FinancialInput(id='1',kind='opening_balance',name='Banco',effectiveDate='2026-10-01',amount=-50).amount,-50)
 def test_cost_requires_sku(self):
  with self.assertRaises(ValidationError):FinancialInput(id='1',kind='product_cost',name='Custo',effectiveDate='2026-10-01')
 def test_contract_requires_evidence(self):
  with self.assertRaises(ValidationError):FinancialInput(id='1',kind='seller_contract',name='Erika',effectiveDate='2026-10-01',commissionRate=2.5)
 def test_contract_rates(self):
  x=FinancialInput(id='1',kind='seller_contract',name='Erika',effectiveDate='2026-10-01',commissionRate=2.5,bonusRate=1,contractReference='Contrato vigente')
  self.assertEqual(x.bonusRate,1)
 def test_bad_dates_and_rates(self):
  for values in [{'effectiveDate':'2026-02-30'},{'commissionRate':101}]:
   with self.assertRaises(ValidationError):FinancialInput(**dict(id='1',kind='operating_cost',name='Aluguel',effectiveDate='2026-10-01',**{} )|values)
