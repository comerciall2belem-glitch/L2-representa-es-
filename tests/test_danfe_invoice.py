"""Public synthetic DANFE fixtures: no customer documents or credentials."""
import unittest
from copy import deepcopy
from order_reconciliation import reconcile_invoice_text, InvoiceError

HEADER = '''DANFE
Nº 000.046.131
CHAVE DE ACESSO
CNPJ 11.111.111/0001-11
DESTINATÁRIO / REMETENTE
NOME/RAZÃO SOCIAL CNPJ/CPF
CLIENTE TESTE 22.222.222/0001-22
CÁLCULO DO IMPOSTO
VALOR TOTAL DOS PRODUTOS
40,00
VALOR DO FRETE VALOR TOTAL DA NOTA
5,00 45,00
DADOS DO PRODUTO / SERVIÇO
CODIGO DESCRIÇÃO DO PRODUTO NCM/SH CST CFOP UN QTDE VL UNITARIO VL TOTAL
'''
ROWS = '''97640 PRODUTO UM 33049990 500 6102 UN 2,0000 10,0000 20,00 0,00
97306 PRODUTO DOIS 3304.99.90 0500 6.102 UN 1,0000 20,0000 20,00 0,00
'''
ORDER = {'items':[
    {'sku':'97640','description':'Produto um','brand':'Marca A','quantity':4,'unitPrice':'10.00'},
    {'sku':'97306','description':'Produto dois','brand':'Marca B','quantity':1,'unitPrice':'20.00'},
    {'sku':'99999','description':'Produto ausente','brand':'Marca A','quantity':3,'unitPrice':'5.00'}]}

class DANFETests(unittest.TestCase):
    def compare(self, text=HEADER+ROWS, order=None, recipient='22222222000122', brand='__all__'):
        return reconcile_invoice_text(order or ORDER,text,brand,recipient)

    def test_all_brands_partial_total_and_complete(self):
        original=deepcopy(ORDER)
        r=self.compare()
        self.assertEqual(r['invoiceNumber'],'46131')
        self.assertEqual(r['invoiceProductTotal'],'40.00')
        self.assertEqual(r['invoiceTotal'],'45.00') # freight is not an item quantity cut
        self.assertEqual(r['cutTotal'],'35.00')
        self.assertEqual({i['classification'] for i in r['items']},{'Corte total','Corte parcial','Faturamento completo'})
        self.assertEqual(len(r['cuts']),2)
        self.assertEqual(ORDER,original)

    def test_multipage_rows_and_repeated_headers(self):
        text=HEADER+ROWS.splitlines()[0]+'\nDANFE\nNº 000.046.131\nDADOS DO PRODUTO / SERVIÇO\nNCM/SH\n'+ROWS.splitlines()[1]
        self.assertEqual(self.compare(text)['extractedItemCount'],2)

    def test_incomplete_pdf_is_rejected_instead_of_false_cuts(self):
        with self.assertRaisesRegex(InvoiceError,'Extração incompleta'):
            self.compare(HEADER+ROWS.splitlines()[0])

    def test_wrong_recipient_and_different_invoice_pages(self):
        for text,recipient in [(HEADER+ROWS,'33333333000133'),(HEADER+ROWS+'\nNº 000.046.132','22222222000122')]:
            with self.assertRaises(InvoiceError):self.compare(text,recipient=recipient)

    def test_wrong_row_arithmetic_and_missing_total(self):
        for text in [(HEADER+ROWS).replace('2,0000','3,0000'),(HEADER+ROWS).replace('VALOR TOTAL DOS PRODUTOS','TOTAL ILEGÍVEL')]:
            with self.assertRaises(InvoiceError):self.compare(text)

    def test_plural_header_sp_and_wrapped_cst(self):
        header=HEADER.replace('DO PRODUTO','DOS PRODUTOS').replace('NCM/SH','NCM / SP')
        rows=ROWS.replace('500 6102','2 6102').replace('0500 6.102','1 6.102').replace('20,00 0,00','20,00 0,00\n          00')
        r=self.compare(header+rows+'\nPed. Nº. 049613/01')
        self.assertEqual(r['invoiceNumber'],'46131')
        self.assertEqual(r['extractedItemCount'],2)
        self.assertEqual(r['cutTotal'],'35.00')

    def test_other_establishment_is_visible_review_only(self):
        r=self.compare(recipient='22222222000376')
        self.assertTrue(r['requiresRecipientReview'])
        self.assertTrue(r['billingBlocked'])
        self.assertEqual(r['status'],'Revisar')
        self.assertIn('mesma raiz',r['warnings'][0])
        self.assertEqual(len(r['cuts']),2)

    def test_different_recipient_on_second_page_is_rejected(self):
        text=HEADER+ROWS+HEADER.replace('22.222.222/0001-22','33.333.333/0001-33')
        with self.assertRaisesRegex(InvoiceError,'divergente entre páginas'):
            self.compare(text)

    def test_full_billing_and_extra_item(self):
        order={'items':[{'sku':'97640','brand':'A','quantity':2,'unitPrice':'10.00'},{'sku':'97306','brand':'B','quantity':1,'unitPrice':'20.00'}]}
        r=self.compare(order=order)
        self.assertEqual(r['cuts'],[])
        self.assertEqual(r['status'],'Conferido')
        r=self.compare(order={'items':order['items'][:1]})
        self.assertEqual(next(i for i in r['items'] if i['sku']=='97306')['classification'],'Item fora do pedido')

if __name__=='__main__':unittest.main()
