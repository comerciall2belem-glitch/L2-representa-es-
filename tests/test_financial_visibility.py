import unittest
from financial_visibility import hide_industry_commissions, preserve_industry_commissions


class FinancialVisibilityTests(unittest.TestCase):
    def test_nested_commissions_hidden_and_commercial_prices_preserved(self):
        original={'industry':{'name':'Bella Brazil','commissionByChannel':{'VAREJO':8},'notes':'Entrega em 6 a 9 dias. Comissão 8%; dia 15. Bonificação 5% a 15%.'},'order':{'sellerCommissionRate':'2.5','items':[{'unitPrice':'4.84','quantity':6,'commissionRate':8}]}}
        hidden=hide_industry_commissions(original)
        self.assertNotIn('commissionByChannel',hidden['industry'])
        self.assertNotIn('Comissão',hidden['industry']['notes'])
        self.assertIn('Bonificação',hidden['industry']['notes'])
        self.assertEqual(hidden['order']['items'],[{'unitPrice':'4.84','quantity':6}])
        self.assertEqual(hidden['order']['sellerCommissionRate'],'2.5')
        self.assertEqual(original['order']['items'][0]['commissionRate'],8)

    def test_commercial_edit_preserves_private_financial_fields(self):
        previous={'title':'Old','commissionRate':8,'notes':'Entrega rápida. Comissão 8%.'}
        edited=preserve_industry_commissions(previous,{'title':'New','commissionRate':99,'notes':'Entrega em 9 dias. Comissão 99%.'})
        self.assertEqual(edited['commissionRate'],8)
        self.assertEqual(edited['title'],'New')
        self.assertIn('Comissão 8%',edited['notes'])
        self.assertNotIn('99%',edited['notes'])
