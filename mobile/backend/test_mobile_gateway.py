"""ASGI integration tests with SQLite adapter and simulated legacy ERP.
No production credentials or records. PostgreSQL locks require separate staging test.
"""
import json
import sqlite3
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from mobile_gateway import install

class Result:
    def __init__(self,cursor=None):self.cursor=cursor
    def parse(self,row):
        if row is None:return None
        def convert(x):
            if isinstance(x,str) and x[:1] in ('{','['):return json.loads(x)
            return x
        return tuple(convert(x) for x in row)
    def fetchone(self):return self.parse(self.cursor.fetchone()) if self.cursor else None
    def fetchall(self):return [self.parse(r) for r in self.cursor.fetchall()] if self.cursor else []

class Connection:
    def __init__(self,path):self.raw=sqlite3.connect(path,check_same_thread=False)
    def execute(self,sql,params=()):
        if 'pg_advisory_xact_lock' in sql or 'pg_notify' in sql:return Result()
        params=tuple(json.dumps(x.obj) if hasattr(x,'obj') else x for x in params)
        return Result(self.raw.execute(sql.replace('%s','?').replace(' FOR UPDATE',''),params))

def fake_module(path):
    m=types.SimpleNamespace(app=FastAPI(),fail_after_write=False)
    @contextmanager
    def db():
        c=Connection(path)
        try:
            with c.raw:yield c
        finally:c.raw.close()
    m.db=db
    m.auth=lambda header: 'Euler' if header=='Bearer test-token' else (_ for _ in ()).throw(HTTPException(401,'Sessão inválida'))
    m.sectors_for=lambda user:{'commercial','catalog','routes'}
    def hide(value):
        if isinstance(value,dict):return {k:hide(v) for k,v in value.items() if k!='industryCommission'}
        if isinstance(value,list):return [hide(x) for x in value]
        return value
    m.hide_industry_commissions=hide
    m.require_sector=lambda user,*sectors:None
    m.is_seller=lambda con,user:True
    def scope(con,user,cid):
        row=con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s",(cid,)).fetchone()
        if not row or row[0].get('owner')!=user:raise HTTPException(403,'Carteira de outro usuário')
    m.check_client_scope=scope
    m.normalize_uf=lambda value:str(value or '').upper()
    m.price_table_matches_client=lambda uf,state:state=='PA' if uf=='PA' else state in ('PA','AP')
    m.validate_table=lambda con,item,brand,state,allowed_states:{'id':brand+'|'+state}
    m.price_id=lambda item:item['tableId']+'|'+item['sku']
    m.Sync=lambda changes:types.SimpleNamespace(changes=changes)
    m.Change=lambda **kwargs:types.SimpleNamespace(**kwargs)
    def sync(data,authorization):
        user=m.auth(authorization)
        with m.db() as con:
            for change in data.changes:
                obj=dict(change.data)
                if obj.get('clientId'):scope(con,user,obj['clientId'])
                if change.type=='visit' and not obj.get('returnDate'):raise HTTPException(400,'Data de retorno obrigatória')
                if change.type=='order':
                    row=con.execute("SELECT count(*) FROM entities WHERE kind='order'").fetchone()
                    obj['orderNumber']=row[0]+1
                con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload',(change.type,obj['id'],json.dumps(obj)))
                if m.fail_after_write:raise HTTPException(422,'Falha de validação simulada')
                con.execute('INSERT INTO applied_changes VALUES(%s)',(change.changeId,))
            names={'client':'clients','order':'orders','visit':'visits','task':'tasks','price':'prices','interaction':'interactions'}
            result={name:[r[0] for r in con.execute('SELECT payload FROM entities WHERE kind=%s',(kind,)).fetchall()] for kind,name in names.items()}
            result.update(sellers=['Euler'],sectors=['commercial','catalog'],role='Vendedor')
            return result
    m.sync=sync
    install(m)
    return m

class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=str(Path(self.tmp.name)/'server.sqlite')
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE entities(kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')
            db.execute('CREATE TABLE applied_changes(change_id TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE audit_log(username TEXT,kind TEXT,entity_id TEXT,action TEXT)')
            db.execute('INSERT INTO entities VALUES(?,?,?)',('client','c1',json.dumps({'id':'c1','name':'Loja','state':'PA','owner':'Euler'})))
            db.execute('INSERT INTO entities VALUES(?,?,?)',('price','Marca|PA|SKU',json.dumps({'price':'10.00'})))
        self.module=fake_module(self.path);self.client=TestClient(self.module.app)
        self.headers={'Authorization':'Bearer test-token'}
    def tearDown(self):self.client.close();self.tmp.cleanup()
    def op(self,type='order',id='o1',change_id='operation-0001',base=None):
        data={'id':id,'clientId':'c1','priceTable':'PA','brand':'Marca','items':[{'sku':'SKU','brand':'Marca','unitPrice':'10.00','quantity':1}]}
        return {'changes':[{'changeId':change_id,'type':type,'data':data,'base':base}]}
    def post(self,body):return self.client.post('/api/mobile/v1/sync',json=body,headers=self.headers)
    def count(self,kind):
        with sqlite3.connect(self.path) as con:return con.execute('SELECT count(*) FROM entities WHERE kind=?',(kind,)).fetchone()[0]
    def test_authentication_required(self):self.assertEqual(self.client.get('/api/mobile/v1/snapshot').status_code,401)
    def test_idempotent_order_same_receipt_and_single_record(self):
        a=self.post(self.op());b=self.post(self.op());self.assertEqual(a.status_code,200);self.assertEqual(a.json()['results'],b.json()['results']);self.assertEqual(self.count('order'),1)
    def test_changed_payload_with_same_key_rejected(self):
        self.post(self.op());changed=self.op();changed['changes'][0]['data']['items'][0]['quantity']=2
        response=self.post(changed).json();self.assertEqual(response['results'][0]['state'],'rejected');self.assertEqual(self.count('order'),1)
    def test_stale_price_requires_review_before_acceptance(self):
        op=self.op();op['changes'][0]['data']['items'][0]['unitPrice']='9.00'
        result=self.post(op).json();self.assertEqual(result['results'][0]['state'],'rejected');self.assertEqual(self.count('order'),0)
    def test_remote_conflict_does_not_overwrite(self):
        self.post(self.op());second=self.op(change_id='operation-0002');response=self.post(second).json();self.assertEqual(response['results'][0]['state'],'conflict');self.assertEqual(self.count('order'),1)
    def test_unknown_client_scope_denied(self):
        op=self.op();op['changes'][0]['data']['clientId']='outside';self.assertEqual(self.post(op).status_code,403);self.assertEqual(self.count('order'),0)
    def test_rolls_back_legacy_write_when_validation_fails(self):
        self.module.fail_after_write=True;response=self.post(self.op()).json();self.assertEqual(response['results'][0]['state'],'rejected');self.assertEqual(self.count('order'),0)
    def test_closing_unique_date_revision(self):
        op={'changes':[{'changeId':'closing-0001','type':'closing','data':{'id':'close1','workDate':'2026-10-14','revision':1,'summary':'Dia concluído'},'base':None}]}
        self.assertEqual(self.post(op).json()['results'][0]['state'],'acknowledged')
        self.assertEqual(len(self.client.get('/api/mobile/v1/snapshot',headers=self.headers).json()['closings']),1)
        op['changes'][0]['changeId']='closing-0002';op['changes'][0]['data']['id']='close2'
        self.assertEqual(self.post(op).json()['results'][0]['state'],'rejected')
    def test_receipt_does_not_expose_private_commission(self):
        op=self.op();op['changes'][0]['data']['industryCommission']='private'
        result=self.post(op).json()['results'][0]
        self.assertNotIn('industryCommission',result['canonical'])
    def test_retry_respects_current_portfolio(self):
        op=self.op();self.post(op)
        with sqlite3.connect(self.path) as con:
            con.execute("UPDATE entities SET payload=? WHERE kind='client' AND id='c1'",(json.dumps({'id':'c1','name':'Loja','state':'PA','owner':'Outra pessoa'}),))
        self.assertEqual(self.post(op).status_code,403)
    def test_old_staging_price_contract(self):
        self.module.validate_table=lambda con,item,brand,state:{'id':brand+'|'+state}
        self.assertEqual(self.post(self.op()).json()['results'][0]['state'],'acknowledged')

if __name__=='__main__':unittest.main()
