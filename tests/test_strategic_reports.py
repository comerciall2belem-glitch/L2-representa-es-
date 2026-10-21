import csv
import io
import unittest
from datetime import date
from decimal import Decimal
from strategic_reports import build, abc, export_csv, CATALOG

class StrategicReportsTest(unittest.TestCase):
    def report(self,name,data,start=date(2026,10,1),end=date(2026,10,31)):
        return build(data,name,start,end,date(2026,10,31))
    def test_every_catalog_report_has_columns(self):
        for entries in CATALOG.values():
            for name,_ in entries:
                with self.subTest(name=name): self.assertTrue(self.report(name,{})['columns'])
    def test_multibrand_totals_and_draft_exclusion(self):
        item={'brand':'BT','category':'Boca','sku':'X','quantity':3,'unitPrice':'0.10'}
        data={'order':[{'date':'2026-10-05','status':'Confirmado','items':[item]}, {'date':'2026-10-05','status':'Orçamento','items':[item]}]}
        self.assertEqual(self.report('brands',data)['rows'],[['BT / Boca',3.0,.3]])
    def test_only_documented_billing(self):
        data={'order':[{'date':'2026-10-01','status':'Faturado','amount':900}], 'settlement':[{'billedDate':'2026-10-01','invoiceNumber':'123','billed':100},{'billedDate':'2026-10-01','billed':800}]}
        self.assertEqual(self.report('billing',data)['rows'][0][1],100)
    def test_bonus_and_historical_rate(self):
        data={'order':[{'id':'o','sellerResponsible':'Erika','sellerCommissionRate':'2.5'}], 'settlement':[{'orderId':'o','invoiceNumber':'1','billedDate':'2026-10-10','billed':1000}], 'goal':[{'month':'2026-10','user':'Equipe','amount':1000}], 'financial_input':[{'kind':'seller_contract','name':'Erika','effectiveDate':'2026-10-01','bonusRate':1,'basis':'Faturado','contractReference':'Contrato'}]}
        rows=self.report('commissions',data)['rows']
        self.assertEqual(rows[0][-2],25)
        self.assertEqual(rows[1][-1],10)
        self.assertIsNone(self.report('commissions',data,start=date(2026,10,2))['rows'][1][-1])
    def test_abc_crossing_is_in_higher_class(self):
        rows=abc({'x':Decimal(85),'y':Decimal(10),'z':Decimal(5)})
        self.assertEqual([r[-1] for r in rows],['A','B','C'])
    def test_inactivity_uses_latest_history(self):
        data={'client':[{'id':'1','name':'Ativo','last_purchase':'2026-10-01'}, {'id':'2','name':'Sem histórico'}], 'order':[{'clientId':'1','date':'2026-01-01','status':'Faturado'}]}
        self.assertEqual(self.report('inactive',data)['rows'],[['Sem histórico',None,None,None,'Sem histórico']])
    def test_cash_excludes_paid_and_does_not_use_order_revenue(self):
        data={'office_finance':[{'Tipo':'Receita','Vencimento':'2026-10-20','Valor':50}, {'Tipo':'Despesa','Vencimento':'2026-10-21','Valor':20}, {'Tipo':'Receita','Vencimento':'2026-10-21','Valor':100,'Situação':'Pago/Recebido'}], 'order':[{'amount':1000,'status':'Faturado','date':'2026-10-01'}]}
        self.assertEqual(self.report('cashflow',data)['rows'][-1][-1],30)
    def test_aging_liquidation_history(self):
        data={'office_finance':[{'Tipo':'Receita','Vencimento':'2026-10-01','Liquidação':'2026-10-05','Valor':25,'Situação':'Pago/Recebido'}]}
        self.assertEqual(self.report('aging',data)['rows'][0][2:4],['Liquidado',4])
    def test_csv_formula_injection_and_utf8(self):
        raw=export_csv({'columns':['Nome'],'rows':[['=HYPERLINK("x")'],[' normal'],[None]]}).decode('utf-8-sig')
        self.assertTrue(list(csv.reader(io.StringIO(raw),delimiter=';'))[1][0].startswith("'="))

if __name__=='__main__':unittest.main()

class StrategicEndpointAccessTest(unittest.TestCase):
    def setUp(self):
        import ast
        from pathlib import Path
        from types import SimpleNamespace
        tree=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
        functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('strategic_report_catalog','strategic_report')]
        for n in functions:n.decorator_list=[]
        class HTTPError(Exception):
            def __init__(self,status,detail):self.status=status
        self.error=HTTPError
        self.env={'date':date,'Header':lambda **k:None,'auth':lambda a:'Field','sectors_for':lambda u:{'commercial'},'HTTPException':HTTPError}
        exec(compile(ast.Module(body=functions,type_ignores=[]),'reports','exec'),self.env)
    def test_financial_reports_absent_for_field(self):
        catalog=self.env['strategic_report_catalog']()
        self.assertEqual(catalog['financial'],[])
        self.assertNotIn('commissions',[r['id'] for r in catalog['commercial']])
    def test_direct_export_denied_before_read(self):
        def deny(*args):raise self.error(403,'Denied')
        self.env['require_sector']=deny
        for name in ('commissions','billing','aging','cashflow'):
            with self.subTest(name=name),self.assertRaises(self.error) as error:
                self.env['strategic_report'](name,date(2026,10,1),date(2026,10,31),format='csv')
            self.assertEqual(error.exception.status,403)
    def test_invalid_dates_denied_before_read(self):
        with self.assertRaises(self.error) as error:
            self.env['strategic_report']('brands',date(2026,10,31),date(2026,10,1))
        self.assertEqual(error.exception.status,422)
