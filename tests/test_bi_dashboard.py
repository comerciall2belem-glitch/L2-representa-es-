import unittest
from datetime import date
from decimal import Decimal
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from bi_dashboard import build, install, goal_value, order_documents

START, END, NOW = date(2026,11,1), date(2026,12,1), date(2026,11,6)
CLIENTS = [{'id':'c1','name':'Loja','owner':'Erika','city':'Belém','state':'PA'}, {'id':'c2','name':'Outra','owner':'Outro','state':'AP'}]
ORDERS = [{'id':'o1','clientId':'c1','sellerResponsible':'Erika','user':'Laís','status':'Faturado'}, {'id':'o2','clientId':'c2','sellerResponsible':'Outro','status':'Faturado'}]
DOCS = [{'id':'n1','orderId':'o1','invoiceNumber':'1','brand':'BT','billedDate':'2026-11-02','billed':'1000','commissionDue':'50','commissionReceived':'20','due':'2026-11-05'}, {'id':'n2','orderId':'o1','invoiceNumber':'2','brand':'Kiss','billedDate':'2026-11-03','billed':'200','commissionDue':'10','commissionReceived':'0'}, {'id':'n3','orderId':'o2','invoiceNumber':'3','brand':'BT','billedDate':'2026-11-04','billed':'800'}]

class Metrics(unittest.TestCase):
    def result(self,**kw):return build(CLIENTS,ORDERS,DOCS,[],[],START,END,today=NOW,**kw)
    def test_partial_multibrand_invoices_do_not_duplicate_order(self):
        r=self.result(seller='Erika');self.assertEqual(r['kpis']['sales'],1200);self.assertEqual(r['kpis']['orders'],1);self.assertEqual(r['kpis']['ticket_order'],1200)
        self.assertEqual(r['sellers'],[{'label':'Erika','amount':Decimal(1200)}])
    def test_finance_is_absent_for_commercial(self):
        r=self.result();self.assertIsNone(r['financial']);self.assertNotIn('commissionDue',str(r));self.assertNotIn('_entry',str(r))
    def test_finance_current_position_and_missing_profit(self):
        r=self.result(seller='Erika',finance=True);self.assertEqual(r['financial']['expected_commission'],60);self.assertEqual(r['financial']['open_commission_today'],40);self.assertEqual(r['financial']['overdue_commission_today'],30);self.assertIsNone(r['financial']['net_profit'])
    def test_scope_filters_and_abc(self):
        r=self.result(state='PA',brand='BT');self.assertEqual(r['kpis']['sales'],1000);self.assertEqual(r['abc_clients'][0]['abc'],'A');self.assertIsNone(r['kpis']['positivation'])
    def test_duplicates_invalid_future_and_cancelled(self):
        docs=DOCS+[DOCS[0],dict(DOCS[0],invoiceNumber='4',billed='NaN'),dict(DOCS[0],invoiceNumber='5',billedDate='2026-11-30')]
        r=build(CLIENTS,ORDERS,docs,[],[],START,END,today=NOW);self.assertEqual(r['kpis']['sales'],2000);self.assertIn('2 registro',str(r['warnings']))
        orders=[dict(o,status='Cancelado') for o in ORDERS];self.assertEqual(build(CLIENTS,orders,DOCS,[],[],START,END,today=NOW)['kpis']['sales'],0)
    def test_no_history_does_not_become_churn(self):
        r=self.result();self.assertEqual(r['clients'][0]['band'],'0-29');self.assertNotIn('churn',r['kpis'])
    def test_monthly_meta_precedence_and_partial_period(self):
        goals=[dict(month='2026-11',user='Equipe',brand='',amount='2000'),dict(month='2026-11',user='Erika',brand='',amount='1000'),dict(month='2026-11',user='Equipe',brand='BT',amount='500')]
        self.assertEqual(goal_value(goals,START,END),2000);self.assertEqual(goal_value(goals,START,END,'Erika'),1000)
        self.assertIsNone(goal_value(goals,START,NOW));self.assertIsNone(goal_value(goals,START,END,narrowed=True))
    def test_bad_range(self):
        with self.assertRaises(HTTPException):build([],[],[],[],[],END,START)
    def test_operational_basis_is_separate_and_never_estimates_finance(self):
        orders=[dict(ORDERS[0],date='2026-11-02',amount='90.01',items=[{'brand':'BT','subtotal':'100'},{'brand':'Kiss','subtotal':'100'}]),dict(ORDERS[1],date='2026-11-02',amount='500',status='Confirmado')]
        docs,invalid=order_documents(orders)
        self.assertEqual(invalid,0);self.assertEqual(sum(Decimal(d['billed']) for d in docs),Decimal('90.01'))
        r=build(CLIENTS,orders,DOCS,[],[],START,END,today=NOW,basis='orders',finance=True)
        self.assertEqual(r['kpis']['sales'],Decimal('90.01'));self.assertEqual(r['kpis']['orders'],1);self.assertIsNone(r['financial']);self.assertEqual(r['basis'],'orders')
        self.assertIn('Não comprova NF',r['warnings'][0])
    def test_operational_invalid_data_is_not_zero_sales(self):
        docs,invalid=order_documents([dict(ORDERS[0],amount='NaN')]);self.assertEqual(docs,[]);self.assertEqual(invalid,1)

class API(unittest.TestCase):
    def setUp(self):
        self.current='Erika';self.sql=[]
        outer=self
        class Con:
            def execute(self,sql,args):outer.sql.append((sql,args));return [(d,) for d in DOCS]
        @contextmanager
        def db():yield Con()
        def auth(header):
            if header!='Bearer valid':raise HTTPException(401)
            return outer.current
        def scoped(con,kind,user):
            return {'client':CLIENTS[:1] if user=='Erika' else CLIENTS,'order':ORDERS[:1] if user=='Erika' else ORDERS,'goal':[],'opportunity':[]}[kind]
        app=FastAPI();install(app,db,auth,lambda u: {'commercial'} if u=='Erika' else {'finance'},scoped,lambda c,u:u=='Erika');self.client=TestClient(app)
    def get(self,extra=''):
        return self.client.get('/api/bi/dashboard?start=2026-11-01&end=2026-12-01&basis=invoices'+extra,headers={'Authorization':'Bearer valid'})
    def test_auth_required_and_cache_private(self):
        self.assertEqual(self.client.get('/api/bi/dashboard?start=2026-11-01&end=2026-12-01').status_code,401)
        r=self.get();self.assertEqual(r.status_code,200);self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertIsNone(r.json()['financial']);self.assertNotIn('Outra',r.text)
    def test_direct_query_other_seller_is_denied(self):self.assertEqual(self.get('&seller=Outro').status_code,403)
    def test_finance_role_and_invalid_dates(self):
        self.current='Laís';self.assertIsNotNone(self.get().json()['financial']);self.assertEqual(self.get('&seller=Outro').status_code,200)
        self.assertEqual(self.client.get('/api/bi/dashboard?start=invalid&end=2026-12-01').status_code,422)

if __name__=='__main__':unittest.main()
