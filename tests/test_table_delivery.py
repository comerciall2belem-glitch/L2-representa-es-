import io
import json
import os
import unittest
from unittest.mock import patch
from table_delivery import catalog_pdf, Delivery
from whatsapp_media import send_media, validate_media, MediaError
from pydantic import ValidationError

class TableDeliveryTests(unittest.TestCase):
    def test_pdf_is_multipage_and_escapes_descriptions(self):
        data=catalog_pdf({'name':'Cliente & Filhos'}, {'title':'Marca <especial>', 'brand':'Marca','state':'PA'},
                         [{'sku':str(i),'description':'Produto <novo> & seguro','price':'19.90','ean':'123'} for i in range(120)])
        self.assertTrue(data.startswith(b'%PDF-'))
        from pypdf import PdfReader
        pdf=PdfReader(io.BytesIO(data))
        self.assertGreater(len(pdf.pages),1)
        text=''.join(p.extract_text() for p in pdf.pages)
        self.assertIn('19,90',text)
        self.assertNotIn('commission',text)

    def test_pdf_signature_and_caption_limits(self):
        self.assertEqual(validate_media('a.pdf','application/pdf',b'%PDF-1.4','Olá'),('application/pdf','document'))
        with self.assertRaises(MediaError): validate_media('a.pdf','application/pdf',b'bad','Olá')
        with self.assertRaises(MediaError): validate_media('a.pdf','application/pdf',b'%PDF-', 'x'*1025)

    @patch.dict(os.environ,{'WHATSAPP_ACCESS_TOKEN':'fake','WHATSAPP_PHONE_NUMBER_ID':'123','WHATSAPP_GRAPH_VERSION':'v27.0'})
    @patch('whatsapp_media._post')
    def test_document_is_uploaded_and_attached_with_caption(self,post):
        post.side_effect=[{'id':'media'},{'messages':[{'id':'wamid.test'}]}]
        self.assertEqual(send_media('5591999999999','L2_tabela.pdf','application/pdf',b'%PDF-1.4','Olá'),'wamid.test')
        payload=json.loads(post.call_args_list[1].args[2])
        self.assertEqual(payload['document'],{'id':'media','caption':'Olá','filename':'L2_tabela.pdf'})

    def test_delivery_bounds(self):
        for ids in ([],['a']*11):
            with self.assertRaises(ValidationError): Delivery(clientId='c',tableIds=ids,requestId='abcdefghijklmnop')
        with self.assertRaises(ValidationError): Delivery(clientId='c',tableIds=['a'],requestId='invalid')
