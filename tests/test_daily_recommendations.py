import unittest
from datetime import date
from daily_recommendations import build, identity

class DailyRecommendationsTests(unittest.TestCase):
    def setUp(self):
        self.today=date(2026,10,16)
        self.clients=[{'id':'c1','name':'Loja','code':'123'},{'id':'c2','name':'Arquivada','active':False}]
        self.orders=[{'id':'o1','clientId':'c1','sellerResponsible':'Erika'}, {'id':'o2','clientId':'c2','sellerResponsible':'Erika'}, {'id':'o3','clientId':'c1','sellerResponsible':'Outro'}]
        self.docs=[{'orderId':'o1','invoiceNumber':'1','billedDate':'2026-07-02','billed':300,'brand':'BT'}, {'orderId':'o1','invoiceNumber':'2','billedDate':'2026-09-02','billed':300,'brand':'BT'}, {'orderId':'o1','invoiceNumber':'aug','billedDate':'2026-08-02','billed':300,'brand':'BT'}, {'orderId':'o1','invoiceNumber':'3','billedDate':'2026-10-02','billed':100,'brand':'BT'}, {'orderId':'o3','invoiceNumber':'4','billedDate':'2026-10-03','billed':50,'brand':'BT'}, {'orderId':'o2','invoiceNumber':'5','billedDate':'2026-09-03','billed':900,'brand':'BT'}, {'orderId':'o1','billedDate':'2026-09-03','billed':900,'brand':'BT'}]
        self.goals=[{'month':'2026-10','user':'Erika','brand':'BT','amount':1000}]
    def calc(self): return build(self.clients,self.orders,self.docs,self.goals,self.today,'Erika',True)
    def test_estimate_and_scope(self):
        r=self.calc();self.assertEqual(r['status'],'em_risco');self.assertEqual(r['actual'],'100.00');self.assertEqual(len(r['rows']),1);self.assertEqual(r['rows'][0]['potential'],'150.00');self.assertIn('BT',r['rows'][0]['suggestion']);self.assertEqual(r['rows'][0]['code'],'123')
    def test_goal_and_brand_do_not_double_count(self):
        self.docs.append({'orderId':'o1','invoiceNumber':'6','billedDate':'2026-10-04','billed':900,'brand':'Outra'})
        self.assertEqual(self.calc()['actual'],'100.00')
        self.goals.append({'month':'2026-10','user':'Erika','amount':500})
        self.assertEqual(self.calc()['actual'],'1000.00');self.assertEqual(self.calc()['target'],'500.00');self.assertEqual(self.calc()['status'],'no_ritmo')
    def test_no_goal_and_no_history(self):
        r=build(self.clients,self.orders,[],[],self.today,'Erika',True);self.assertEqual(r['status'],'sem_meta');self.assertEqual(r['rows'],[]);self.assertIn('Cliente sem histórico suficiente',r['unavailable'][0]['message'])
    def test_incomplete_months_do_not_produce_potential(self):
        self.docs=[d for d in self.docs if d.get('billedDate') != '2026-08-02']
        result=self.calc();self.assertEqual(result['rows'],[]);self.assertEqual(result['unavailable'][0]['id'],'c1')
    def test_missing_goal_never_displays_potential(self):
        self.goals=[]
        result=self.calc();self.assertEqual(result['rows'],[]);self.assertEqual(result['unavailable'][0]['id'],'c1')
    def test_future_and_unauthorized_are_excluded(self):
        self.docs += [{'orderId':'missing','invoiceNumber':'x','billedDate':'2026-10-02','billed':9999}, {'orderId':'o1','invoiceNumber':'f','billedDate':'2026-10-25','billed':9999}]
        self.assertEqual(self.calc()['actual'],'100.00')
    def test_identity_separates_user_and_day(self):
        self.assertNotEqual(identity('a','2026-10-01'),identity('b','2026-10-01'));self.assertNotEqual(identity('a','2026-10-01'),identity('a','2026-10-02'))
if __name__ == '__main__': unittest.main()
