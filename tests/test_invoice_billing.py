import copy
import io
import unittest
from decimal import Decimal
from order_reconciliation import reconcile_invoice, consolidated_billing, InvoiceError
from invoice_billing import confirm_invoice, order_fingerprint
from invoice_cuts_pdf import build_cuts_pdf
from pypdf import PdfReader


def order():
    return {'id':'o1','orderNumber':1,'clientId':'c1','brand':'Multimarcas','status':'Confirmado','amount':'300.00',
            'items':[{'brand':'BT','sku':'B1','quantity':10,'subtotal':'200','description':'Produto BT'},
                     {'brand':'KNY','sku':'K1','quantity':5,'subtotal':'100','description':'Produto KNY'}]}


def report(o,qty=4,brand='BT',number='123'):
    sku='B1' if brand=='BT' else 'K1'
    xml=f'<NFe><infNFe Id="NFe{number}"><ide><nNF>{number}</nNF><serie>1</serie></ide><emit><CNPJ>11111111111111</CNPJ></emit><dest><CNPJ>22222222222222</CNPJ></dest><det><prod><cProd>{sku}</cProd><qCom>{qty}</qCom><vProd>{qty*20}</vProd></prod></det></infNFe></NFe>'
    r=reconcile_invoice(o,xml.encode(),brand,'22222222222222')
    r.update(orderFingerprint=order_fingerprint(o),confirmed=False)
    return r


class Cursor:
    def __init__(self,rows):self.rows=rows
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows


class Database:
    def __init__(self,o,r):self.order=copy.deepcopy(o);self.reports={'n1':copy.deepcopy(r)};self.audit=[];self.queries=[]
    def execute(self,sql,params=()):
        self.queries.append(sql)
        if sql.startswith('SELECT pg_'):return Cursor([])
        if sql.startswith('SELECT payload'):return Cursor([(copy.deepcopy(self.order),)])
        if sql.startswith('SELECT reconciliation'):return Cursor([(copy.deepcopy(self.reports[params[0]]),)])
        if sql.startswith('SELECT order_id'):return Cursor([('o1',copy.deepcopy(r)) for r in self.reports.values() if r.get('confirmed')])
        if sql.startswith('UPDATE order_invoices'):self.reports[params[1]]=copy.deepcopy(params[0].obj)
        elif sql.startswith('UPDATE entities'):self.order=copy.deepcopy(params[0].obj)
        elif sql.startswith('INSERT INTO audit_log'):self.audit.append(params)
        else:raise AssertionError(sql)
        return Cursor([])


class BillingTests(unittest.TestCase):
    def test_classifies_complete_partial_total_and_preserves_original(self):
        o=order();r=report(o)
        self.assertEqual(r['items'][0]['classification'],'Corte Parcial')
        self.assertEqual(r['cuts'][0]['cutValue'],'120.00')
        self.assertEqual(report(o,10)['items'][0]['classification'],'Faturamento Completo')
        result=consolidated_billing(o,[r])
        self.assertEqual(result['cutTotal'],'220.00')
        self.assertEqual({i['classification'] for i in result['cuts']},{'Corte Parcial','Corte Total'})
        self.assertEqual(o['amount'],'300.00')

    def test_duplicate_sku_rows_are_aggregated(self):
        o=order();o['items'].append({'brand':'BT','sku':'B1','quantity':2,'subtotal':'40'})
        self.assertEqual(report(o)['items'][0]['orderedQuantity'],'12')

    def test_confirmation_updates_actual_and_preserves_items(self):
        o=order();db=Database(o,report(o))
        r,updated=confirm_invoice(db,'o1','n1','Marlene','22222222222222','2026-10-23')
        self.assertTrue(r['confirmed']);self.assertEqual(updated['amount'],'80.00')
        self.assertEqual(updated['originalAmount'],'300.00');self.assertEqual(updated['status'],'Faturado')
        self.assertEqual(updated['items'],o['items']);self.assertEqual(len(db.audit),1)
        self.assertEqual(updated['invoiceBilling']['documents'][0]['billed'],'80.00')
        self.assertEqual(updated['invoiceBilling']['documents'][0]['billedDate'],'2026-10-23')
        self.assertIn('FOR UPDATE',db.queries[1])

    def test_repeat_confirmation_is_idempotent(self):
        o=order();db=Database(o,report(o))
        for _ in range(2):confirm_invoice(db,'o1','n1','Marlene','22222222222222','2026-10-23')
        self.assertEqual(db.order['amount'],'80.00');self.assertEqual(len(db.audit),1)

    def test_multiple_invoices_recompute_cumulative_cuts(self):
        o=order();db=Database(o,report(o))
        confirm_invoice(db,'o1','n1','Marlene','22222222222222','2026-10-23')
        db.reports['n2']=report(o,6,number='124')
        _,updated=confirm_invoice(db,'o1','n2','Marlene','22222222222222','2026-10-23')
        self.assertEqual(updated['amount'],'200.00');self.assertEqual(updated['originalAmount'],'300.00')
        self.assertEqual([i['sku'] for i in updated['invoiceBilling']['cuts']],['K1'])

    def test_all_industries_fully_billed_has_no_cuts(self):
        o=order();r=consolidated_billing(o,[report(o,10),report(o,5,'KNY','124')])
        self.assertEqual(r['cuts'],[]);self.assertEqual(r['billedAmount'],'300.00')
        self.assertEqual(r['billedByBrand'],{'BT':'200.00','KNY':'100.00'})

    def test_stale_order_rejected_without_writes(self):
        o=order();db=Database(o,report(o));db.order['items'][0]['quantity']=11
        with self.assertRaisesRegex(InvoiceError,'alterado'):confirm_invoice(db,'o1','n1','Marlene','22222222222222','now')
        self.assertFalse(db.reports['n1']['confirmed']);self.assertEqual(db.audit,[])

    def test_wrong_or_missing_recipient_rejected(self):
        for recipient in ('','33333333333333'):
            o=order();db=Database(o,report(o))
            with self.assertRaisesRegex(InvoiceError,'CNPJ'):confirm_invoice(db,'o1','n1','Marlene',recipient,'now')
            self.assertFalse(db.reports['n1']['confirmed'])

    def test_cancelled_order_rejected(self):
        o=order();o['status']='Cancelado';db=Database(o,report(o))
        with self.assertRaisesRegex(InvoiceError,'cancelado'):confirm_invoice(db,'o1','n1','Marlene','22222222222222','now')

    def test_duplicate_invoice_identity_not_counted_twice(self):
        o=order();db=Database(o,report(o))
        confirm_invoice(db,'o1','n1','Marlene','22222222222222','now')
        db.reports['n2']=report(o)
        with self.assertRaisesRegex(InvoiceError,'duplicado'):confirm_invoice(db,'o1','n2','Marlene','22222222222222','now')
        self.assertEqual(db.order['amount'],'80.00')

    def test_cumulative_overbilling_rejected(self):
        o=order()
        with self.assertRaisesRegex(InvoiceError,'acima'):consolidated_billing(o,[report(o,6),report(o,6,number='124')])

    def test_discount_affects_sales_but_cut_uses_original_price(self):
        xml=b'<NFe><infNFe><ide><nNF>1</nNF></ide><det><prod><cProd>B1</cProd><qCom>4</qCom><vProd>80</vProd><vDesc>8</vDesc></prod></det></infNFe></NFe>'
        r=reconcile_invoice(order(),xml,'BT')
        self.assertEqual(r['invoiceTotal'],'72.00');self.assertEqual(r['cuts'][0]['cutValue'],'120.00')

    def test_fiscal_total_is_used_instead_of_gross_products(self):
        xml=b'<NFe><infNFe><ide><nNF>1</nNF></ide><det><prod><cProd>B1</cProd><qCom>4</qCom><vProd>80</vProd></prod></det><total><ICMSTot><vNF>84.00</vNF></ICMSTot></total></infNFe></NFe>'
        r=reconcile_invoice(order(),xml,'BT')
        self.assertEqual(r['invoiceProductTotal'],'80.00');self.assertEqual(r['invoiceTotal'],'84.00')
        self.assertEqual(consolidated_billing(order(),[r])['billedAmount'],'84.00')

    def test_pdf_contains_only_outstanding_items_and_all_columns(self):
        o=order();o['invoiceBilling']=consolidated_billing(o,[report(o,10)])
        text='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(build_cuts_pdf(o,{'name':'Cliente teste'}))).pages)
        for value in ('K1','Qtd. pedida','Qtd. faturada','Qtd. cortada','Valor do corte','100,00'):self.assertIn(value,text)
        self.assertNotIn('Produto BT',text)

if __name__=='__main__':unittest.main()
