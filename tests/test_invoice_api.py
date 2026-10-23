import ast
import asyncio
import io
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from fastapi import Header, File, Form, UploadFile, HTTPException
from order_reconciliation import InvoiceError

source=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
nodes=[n for n in source.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in ('upload_order_invoice','confirm_order_invoice','order_cuts_pdf')]
for n in nodes:n.decorator_list=[]

class Cursor:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row

class Database:
    def __init__(self):self.queries=[];self.exited=False
    def __enter__(self):return self
    def __exit__(self,*args):self.exited=True
    def execute(self,sql,params=()):
        self.queries.append(sql)
        return Cursor(({'taxId':'22.222.222/2222-22'},) if "kind='client'" in sql else ({'id':'o1','clientId':'c1'},))

class InvoiceAPITests(unittest.TestCase):
    def scope(self):
        con=Database();events=[]
        env=dict(UploadFile=UploadFile,File=File,Form=Form,Header=Header,Path=Path,HTTPException=HTTPException,
            auth=lambda _: 'Marlene',require_sector=lambda *a:None,db=lambda:con,check_order_scope=lambda *a:None,
            re=__import__('re'),asyncio=asyncio,InvoiceError=InvoiceError,datetime=datetime,TZ=timezone.utc,
            reconcile_invoice=lambda *a:{'invoiceNumber':'1','brand':'BT'},
            reconcile_invoice_text=lambda *a:{'invoiceNumber':'1','brand':'BT'},
            extract_invoice_pdf_text=lambda content:('documento extraído',[]),
            save_invoice_preview=lambda *a: dict(a[3],saved=True),
            confirm_invoice=lambda *a:({'confirmed':True},{'id':'o1','amount':'80'}),
            project_order=lambda *a:events.append('projection'),strategic_crm=SimpleNamespace(event=lambda *a:events.append('crm')))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<invoice-routes>','exec'),env)
        return env,con,events

    def test_pdf_without_manual_text_is_saved_without_billing(self):
        env,con,_=self.scope()
        result=asyncio.run(env['upload_order_invoice']('o1',UploadFile(filename='nota.pdf',file=io.BytesIO(b'%PDF-data')),'BT','token'))
        self.assertTrue(result['saved']);self.assertEqual(result['source'],'PDF')
        self.assertFalse(any(sql.startswith('UPDATE entities') for sql in con.queries))

    def test_xml_uses_same_confirmation_flow(self):
        env,_,_=self.scope()
        result=asyncio.run(env['upload_order_invoice']('o1',UploadFile(filename='nota.xml',file=io.BytesIO(b'<NFe/>')),'BT','token'))
        self.assertTrue(result['saved']);self.assertEqual(result['source'],'XML')

    def test_scope_rejected_before_document_read(self):
        env,_,_=self.scope()
        def deny(*a):raise HTTPException(403,'Fora da carteira')
        env['check_order_scope']=deny
        class Unreadable:
            async def read(self,*a):raise AssertionError('Documento não deveria ser lido')
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(env['upload_order_invoice']('o1',Unreadable(),'BT','token'))
        self.assertEqual(caught.exception.status_code,403)

    def test_confirmation_projects_sales_and_refreshes_crm(self):
        env,con,events=self.scope()
        result=env['confirm_order_invoice']('o1','n1','token')
        self.assertTrue(result['report']['confirmed']);self.assertEqual(events,['projection','crm']);self.assertTrue(con.exited)

    def test_pdf_requires_confirmed_billing(self):
        env,_,_=self.scope()
        with self.assertRaises(HTTPException) as caught:env['order_cuts_pdf']('o1','token')
        self.assertEqual(caught.exception.status_code,409)

