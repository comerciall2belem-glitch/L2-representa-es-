import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from pypdf import PdfReader
from catalog_document import build_catalog_pdf
from login_identity import resolve_identity
from whatsapp_media import send_media, validate_media, MediaError

class CatalogDocumentTests(unittest.TestCase):
    def test_names_resolve_unambiguously(self):
        self.assertEqual(resolve_identity('  ANA   PAULA ', ['Ana Paula','Laís']), 'Ana Paula')
        self.assertEqual(resolve_identity('lais', ['Ana Paula','Laís']), 'Laís')
        self.assertIsNone(resolve_identity('Ana', ['Ana Paula']))
        self.assertIsNone(resolve_identity('LAIS', ['Laís','Lais']))

    def pdf(self, count=1):
        return build_catalog_pdf({'brand':'BT','state':'PA','title':'BT Pará'},
            {'notes':'SEGREDO INTERNO comissão 10%', 'paymentOptions':['30/60/90'],'freight':'CIF'},
            [{'sku':str(i),'description':'Batom hidratante vermelho <lançamento> com descrição para leitura no celular','price':'19.90','packSize':6,'commissionRate':10} for i in range(count)],
            Path(__file__).resolve().parents[1], '05/10/2026')

    def test_pdf_pagination_and_no_internal_finance(self):
        content = self.pdf(120)
        reader = PdfReader(io.BytesIO(content))
        self.assertGreater(len(reader.pages), 1)
        text = '\n'.join(p.extract_text() for p in reader.pages)
        for word in ['Código','Descrição','Apresentação','Preço','R$ 19,90','30/60/90','CIF']:
            self.assertIn(word, text)
        self.assertNotIn('SEGREDO', text)
        self.assertNotIn('comissão', text)
        self.assertLess(len(content), 1024*1024)

    @patch.dict('os.environ', {'WHATSAPP_ACCESS_TOKEN':'mock','WHATSAPP_PHONE_NUMBER_ID':'12345','WHATSAPP_GRAPH_VERSION':'v27.0'})
    @patch('whatsapp_media._post')
    def test_document_upload_and_message(self, post):
        post.side_effect = [{'id':'doc-id'},{'messages':[{'id':'wamid.test'}]}]
        self.assertEqual(send_media('5591999999999','tabela-BT.pdf','application/pdf',self.pdf(),'Tabela solicitada'), 'wamid.test')
        payload = json.loads(post.call_args_list[1].args[2])
        self.assertEqual(payload['type'], 'document')
        self.assertEqual(payload['document'], {'id':'doc-id','caption':'Tabela solicitada','filename':'tabela-BT.pdf'})
        self.assertNotIn('image', payload)
        self.assertNotIn('video', payload)

    def test_invalid_pdf_is_rejected(self):
        with self.assertRaises(MediaError): validate_media('table.pdf','application/pdf',b'%PDF-fake','Teste')
        with self.assertRaises(MediaError): validate_media('table.pdf','image/png',self.pdf(),'Teste')
