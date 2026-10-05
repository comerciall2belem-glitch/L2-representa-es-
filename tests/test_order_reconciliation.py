import unittest
from order_reconciliation import reconcile_invoice, InvoiceError

def xml(items, number='123'):
    details=''.join(f'<det><prod><cProd>{sku}</cProd><qCom>{qty}</qCom><vProd>{total}</vProd></prod></det>' for sku,qty,total in items)
    return f'<nfeProc><NFe><infNFe><ide><nNF>{number}</nNF></ide>{details}</infNFe></NFe></nfeProc>'.encode()

class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.order={'brand':'Multimarcas','items':[
            {'brand':'BT','sku':'BT1','quantity':2,'subtotal':'20.00'},
            {'brand':'KNY','sku':'K1','quantity':3,'subtotal':'45.00'}]}

    def test_each_industry_is_reconciled_separately(self):
        result=reconcile_invoice(self.order,xml([('BT1',2,'20.00')]),'BT')
        self.assertEqual(result['status'],'Conferido')
        self.assertEqual(result['orderTotal'],'20.00')

    def test_differences_include_missing_and_extra_skus(self):
        result=reconcile_invoice(self.order,xml([('K1',2,'30.00'),('OUTRO',1,'5.00')]),'KNY')
        self.assertEqual(result['status'],'Divergente')
        self.assertEqual({x['sku'] for x in result['differences']},{'K1','OUTRO'})

    def test_invalid_xml_and_wrong_brand_are_rejected(self):
        for content,brand in ((b'not xml','BT'),(xml([('BT1',2,'20.00')]),'Outra')):
            with self.assertRaises(InvoiceError): reconcile_invoice(self.order,content,brand)

    def test_unsafe_xml_entity_is_rejected(self):
        with self.assertRaises(InvoiceError):
            reconcile_invoice(self.order,b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><nfeProc>&x;</nfeProc>','BT')

if __name__=='__main__': unittest.main()
