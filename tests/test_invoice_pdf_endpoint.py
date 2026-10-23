"""Exercise the actual PDF endpoint function against a saved order and digital PDF."""
import ast
import asyncio
import io
from pathlib import Path
import re
import unittest
from reportlab.pdfgen import canvas
from order_reconciliation import extract_invoice_pdf_text, reconcile_invoice_text, InvoiceError
from test_danfe_invoice import HEADER, ROWS, ORDER

class HTTPException(Exception):
    def __init__(self,status_code,detail):self.status_code,self.detail=status_code,detail

class Con:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,params):
        assert sql.startswith('SELECT'), 'Preview must not write to database'
        self.row=({'taxId':'22.222.222/0001-22'},) if "kind='client'" in sql else (dict(ORDER,clientId='test-client'),)
        return self
    def fetchone(self):return self.row

class Upload:
    def __init__(self,text):
        buf=io.BytesIO();doc=canvas.Canvas(buf)
        for i,line in enumerate(text.splitlines()):doc.drawString(15,820-i*16,line)
        doc.save();self.data=buf.getvalue()
    async def read(self,n):return self.data[:n]

source=ast.parse(Path('server.py').read_text())
node=next(n for n in source.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='preview_order_invoice_pdf')
node.decorator_list=[]
ns=dict(HTTPException=HTTPException,UploadFile=Upload,File=lambda *a,**k:None,Form=lambda *a,**k:None,Header=lambda *a,**k:None,
        auth=lambda x:'Tester',require_sector=lambda *x:None,db=Con,check_order_scope=lambda *x:None,re=re,asyncio=asyncio,
        extract_invoice_pdf_text=extract_invoice_pdf_text,reconcile_invoice_text=reconcile_invoice_text,InvoiceError=InvoiceError)
exec(compile(ast.Module(body=[node],type_ignores=[]),'<pdf-endpoint>','exec'),ns)

class PDFEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_pdf_returns_comparison_and_no_raw_text(self):
        r=await ns['preview_order_invoice_pdf']('saved-order',Upload(HEADER+ROWS),'__all__','token')
        self.assertEqual(r['report']['cutTotal'],'35.00')
        self.assertEqual(len(r['report']['items']),3)
        self.assertNotIn('extractedText',r)

    async def test_incomplete_pdf_is_http_error(self):
        with self.assertRaises(HTTPException) as caught:
            await ns['preview_order_invoice_pdf']('saved-order',Upload(HEADER+ROWS.splitlines()[0]),'__all__','token')
        self.assertEqual(caught.exception.status_code,400)
        self.assertIn('Extração incompleta',caught.exception.detail)

if __name__=='__main__':unittest.main()
