import ast
import asyncio
import io
import re
from datetime import datetime, timezone
import unittest
from pathlib import Path
from types import SimpleNamespace
from fastapi import HTTPException, File, Form, Header, UploadFile
from whatsapp_media import validate_media, MediaError

source=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
nodes=[x for x in source.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x.name in ('send_whatsapp_media','preview_price_pdf','commercial_activity','commercial_drafts_save','commercial_drafts_list','send_whatsapp_text')]
for node in nodes:node.decorator_list=[]
class Cursor:
    def fetchone(self):return ({'phone':'91999999999'},)
class DB:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,*args):return Cursor()
class OperationalEndpointTests(unittest.TestCase):
    def scope(self, allowed=True):
        def require(*args):
            if not allowed:raise HTTPException(403,'Restrito')
        scope=dict(File=File,Form=Form,Header=Header,UploadFile=UploadFile,CommercialDraft=object,WhatsAppText=object,Jsonb=lambda x:x,datetime=datetime,TZ=timezone.utc,json=__import__('json'),secrets=__import__('secrets'),project_attendance=lambda *a:None,send_text=lambda *a:'wamid.text',HTTPException=HTTPException,auth=lambda _: 'Marlene',sectors_for=lambda _: {'catalog'},CATALOG_EDITORS={'Marlene'},require_sector=require,db=lambda:DB(),provider_config=lambda:True,validate_media=validate_media,MediaError=MediaError,check_client_scope=require,re=re,asyncio=asyncio,send_media=lambda *args:'wamid.mock')
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<endpoints>','exec'),scope);return scope
    def test_general_media_endpoint_accepts_pdf_and_checks_scope(self):
        scope=self.scope();file=UploadFile(filename='tabela.pdf',file=io.BytesIO(b'%PDF-1.4\n%%EOF'),headers={'content-type':'application/pdf'})
        result=asyncio.run(scope['send_whatsapp_media']('client','Teste',file,'auth'))
        self.assertEqual(result['messageId'],'wamid.mock')
    def test_scope_rejects_media_before_send(self):
        scope=self.scope(False);file=UploadFile(filename='foto.jpg',file=io.BytesIO(b'\xff\xd8\xff'),headers={'content-type':'image/jpeg'})
        with self.assertRaises(HTTPException):asyncio.run(scope['send_whatsapp_media']('client','Teste',file,'auth'))
    def test_pdf_import_requires_catalog_editor(self):
        scope=self.scope();scope['CATALOG_EDITORS']=set()
        with self.assertRaises(HTTPException):asyncio.run(scope['preview_price_pdf'](UploadFile(filename='x.pdf',file=io.BytesIO(b'%PDF-1.4')),'auth'))

    def test_activity_scopes_events_and_omits_financial_payload(self):
        scope=self.scope()
        class ActivityDB(DB):
            def execute(self,query,params):
                self.query=query
                return [('Euler','visit','v1','update',datetime.now(timezone.utc),{'clientId':'c1','amount':999,'notes':'privado'}),('Ana Paula','visit','v2','update',datetime.now(timezone.utc),{'clientId':'c2'}),('Marlene','whatsapp_media','c1','send',datetime.now(timezone.utc),{'phone':'999'})]
        con=ActivityDB()
        scope.update(db=lambda:con,is_seller=lambda *_:False,scoped_rows=lambda con,kind,user: {'client':[{'id':'c1','name':'Loja'}],'visit':[{'id':'v1','clientId':'c1'}]}.get(kind,[]))
        result=scope['commercial_activity']('auth')
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['clientName'],'Loja')
        self.assertEqual(set(result[0]),{'user','kind','action','at','clientName'})
        self.assertIn("IN ('whatsapp_media','whatsapp_document')",con.query)

    def test_drafts_are_saved_under_authenticated_user_only(self):
        scope=self.scope();queries=[]
        class DraftDB(DB):
            def execute(self,query,params):queries.append((query,params));return []
        scope['db']=lambda:DraftDB()
        data=SimpleNamespace(key='order:new',payload={'fields':{'clientId':'c1','notes':'Em edição'}},updatedAt=int(datetime.now(timezone.utc).timestamp()*1000))
        self.assertTrue(scope['commercial_drafts_save'](data,'auth')['saved'])
        self.assertEqual(queries[-1][1][0],'Marlene')
        self.assertIn('client_timestamp<excluded.client_timestamp',queries[-1][0])
        scope['commercial_drafts_list']('auth')
        self.assertEqual(queries[-1][1],('Marlene',))

    def test_draft_scope_rejects_foreign_client_and_secret_fields(self):
        scope=self.scope(False)
        data=SimpleNamespace(key='visit:new',payload={'fields':{'clientId':'c2'}},updatedAt=int(datetime.now(timezone.utc).timestamp()*1000))
        with self.assertRaises(HTTPException):scope['commercial_drafts_save'](data,'auth')
        scope=self.scope();data.payload={'fields':{'access_token':'forbidden'}}
        with self.assertRaises(HTTPException):scope['commercial_drafts_save'](data,'auth')

    def test_text_endpoint_checks_client_and_records_acceptance(self):
        scope=self.scope();data=SimpleNamespace(clientId='client',message='Mensagem comercial')
        result=asyncio.run(scope['send_whatsapp_text'](data,'auth'))
        self.assertEqual(result['messageId'],'wamid.text')
        self.assertEqual(result['status'],'accepted')
        self.assertTrue(result['historyRecorded'])
        scope=self.scope(False)
        with self.assertRaises(HTTPException):asyncio.run(scope['send_whatsapp_text'](data,'auth'))
