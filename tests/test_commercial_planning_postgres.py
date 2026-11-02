"""Real PostgreSQL authenticated integration. Runs in isolated schema, never production.
Set L2_TEST_POSTGRES_DSN to a dedicated ephemeral test database. CI provisions it.
"""
import hashlib
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from unittest.mock import patch
import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.types.json import Jsonb
import commercial_planning as cp

DSN=os.getenv('L2_TEST_POSTGRES_DSN','')
pytestmark=pytest.mark.skipif(not DSN,reason='Requires isolated PostgreSQL via L2_TEST_POSTGRES_DSN; CI provisions this service')
H={'Authorization':'Bearer '+('m'*48)}
SELLER={'Authorization':'Bearer '+('s'*48)}
NO_FINANCE={'Authorization':'Bearer '+('n'*48)}


@pytest.fixture
def api(monkeypatch):
    schema='planning_test_'+uuid.uuid4().hex
    with psycopg.connect(DSN) as con:
        database=con.execute('SELECT current_database()').fetchone()[0]
        if not database.endswith('_test'):
            pytest.fail('Integration tests require a database name ending in _test')
        con.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    def db():return psycopg.connect(DSN,options='-csearch_path='+schema)
    with db() as con:
        con.execute('CREATE TABLE entities(kind TEXT,id TEXT,payload JSONB,updated_at TIMESTAMPTZ DEFAULT now(),PRIMARY KEY(kind,id))')
        con.execute('CREATE TABLE app_users(username TEXT PRIMARY KEY,role TEXT,sectors JSONB,active BOOLEAN DEFAULT true,password_hash TEXT)')
        con.execute('CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,username TEXT,expires_at TIMESTAMPTZ)')
        con.execute('CREATE TABLE client_seller_authorizations(client_id TEXT,seller_username TEXT,active BOOLEAN)')
        con.execute('CREATE TABLE audit_log(username TEXT,kind TEXT,entity_id TEXT,action TEXT)')
        for name,role,sectors,token in [('manager','Gestão',['admin','commercial','clients_edit','management','finance'],'m'*48),('seller','Vendedor',['commercial','clients_edit'],'s'*48),('Marlene','Administrativo',['commercial','management','admin','clients_edit'],'n'*48)]:
            con.execute('INSERT INTO app_users(username,role,sectors,password_hash) VALUES(%s,%s,%s,%s)',(name,role,Jsonb(sectors),'test-hash'))
            con.execute("INSERT INTO sessions VALUES(%s,%s,now()+interval '1 hour')",(hashlib.sha256(token.encode()).hexdigest(),name))
        for identifier,name,owner,tax in [('a','Salão A','seller','11222333000181'),('b','Perfumaria B','another','52998224725')]:
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('client',%s,%s)",(identifier,Jsonb({'id':identifier,'name':name,'owner':owner,'taxId':tax,'state':'PA'})))
        cp.setup(con);cp.setup(con)
    def auth(header):
        if not header or not header.startswith('Bearer '):raise HTTPException(401,'Sessão inválida')
        with db() as con:
            row=con.execute('SELECT u.username FROM sessions s JOIN app_users u USING(username) WHERE s.token_hash=%s AND s.expires_at>now() AND u.active',(hashlib.sha256(header[7:].encode()).hexdigest(),)).fetchone()
        if not row:raise HTTPException(401,'Sessão inválida')
        return row[0]
    def sectors(user):
        with db() as con:row=con.execute('SELECT sectors FROM app_users WHERE username=%s AND active',(user,)).fetchone()
        return set(row[0]) if row else set()
    def require(user,*needed):
        if not sectors(user)&set(needed):raise HTTPException(403,'Sem permissão')
    old=cp.services.copy()
    cp.configure(db=db,auth=auth,sectors_for=sectors,require_sector=require,password_ok=lambda p,h:p=='test-password' and h=='test-hash')
    monkeypatch.setenv('L2_COMMERCIAL_PLANNING_ENABLED','true')
    monkeypatch.setenv('L2_COMMERCIAL_MFA_KEY',Fernet.generate_key().decode())
    app=FastAPI();app.include_router(cp.router)
    with TestClient(app) as client:
        yield client,db
    cp.services.clear();cp.services.update(old)
    with psycopg.connect(DSN) as con:con.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def payload(**updates):
    return dict(source='erp',external_id='NF1',client_id='a',stage='sell_through',business_date='2026-10-01',items=[dict(brand='TRUSS',sku='SKU1',units='10',amount='100.00',portfolio='Estratégico')],**updates)


def post_doc(client,**updates):
    r=client.post('/api/strategy/commercial/documents',headers=H,json=payload(**updates))
    assert r.status_code==200,r.text
    return r.json()['id']


def test_authenticated_scope_and_financial_denial(api):
    c,db=api;post_doc(c);post_doc(c,external_id='NF2',client_id='b')
    query='/api/strategy/commercial/indicators?start=2026-10-01&end=2026-11-01'
    assert c.get(query,headers=SELLER).json()['metrics']['sell_through']==100
    assert c.get(query,headers=H).json()['metrics']['sell_through']==200
    assert c.get('/api/strategy/commercial/interactions/b',headers=SELLER).status_code==404
    assert c.get('/api/strategy/commercial/finance/receivables',headers=NO_FINANCE).status_code==403
    assert c.get('/api/strategy/commercial/ports',headers={'Authorization':'Bearer invalid'}).status_code==401


def test_idempotency_and_return_balance(api):
    c,db=api;identifier=post_doc(c)
    r=c.post('/api/strategy/commercial/documents',headers=H,json=payload())
    assert r.json()['already_imported']
    bad=payload(external_id='RET1',kind='return',original_id=identifier)
    bad['items'][0]['units']='11'
    assert c.post('/api/strategy/commercial/documents',headers=H,json=bad).status_code==422
    bad['items'][0]['units']='3';bad['items'][0]['amount']='30.00'
    assert c.post('/api/strategy/commercial/documents',headers=H,json=bad).status_code==200
    with db() as con:assert con.execute('SELECT count(*) FROM l2_commercial_documents').fetchone()[0]==2


def test_concurrent_identical_document_import(api):
    c,db=api
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:c.post('/api/strategy/commercial/documents',headers=H,json=payload()),range(2)))
    assert [r.status_code for r in results]==[200,200]
    with db() as con:assert con.execute('SELECT count(*) FROM l2_commercial_documents').fetchone()[0]==1


def test_two_sessions_profile_conflict_and_duplicate(api):
    c,db=api
    profile=dict(expected_version=0,tax_id='11222333000181',channel='Salão de Beleza',profile='Premium',management='Familiar',focus='Tratamento',portfolio='Estratégico')
    assert c.put('/api/strategy/commercial/ports/a/profile',headers=SELLER,json=profile).status_code==200
    ports=c.get('/api/strategy/commercial/ports',headers=SELLER).json()['ports']
    assert ports[0]['version']==1
    assert c.put('/api/strategy/commercial/ports/a/profile',headers=H,json=profile).status_code==409
    profile.update(expected_version=1,tax_id='52998224725')
    assert c.put('/api/strategy/commercial/ports/a/profile',headers=H,json=profile).status_code==409


def test_mfa_exact_reconciliation_and_replay(api):
    c,db=api;identifier=post_doc(c)
    enroll=c.post('/api/strategy/commercial/security/mfa/enroll',headers=H,json={'password':'test-password'})
    assert enroll.status_code==200,enroll.text
    secret=enroll.json()['secret'];counter=int(time.time())//30
    with patch('commercial_planning.time.time',return_value=counter*30):
        code=cp.totp(secret,counter)
        assert c.post('/api/strategy/commercial/security/mfa/confirm',headers=H,json={'code':code}).status_code==200
        data={'document_id':identifier,'reference':'NF1','bank_account':'001/1234/5678','due_date':'2026-10-30','amount':'100.00'}
        assert c.post('/api/strategy/commercial/finance/receivables',headers=H|{'X-TOTP':code},json=data).status_code==403
    with patch('commercial_planning.time.time',return_value=(counter+1)*30):
        r=c.post('/api/strategy/commercial/finance/receivables',headers=H|{'X-TOTP':cp.totp(secret,counter+1)},json=data)
        assert r.status_code==200,r.text
    transaction={'account':'001/1234/5678','external_id':'fit1','business_date':date(2026,10,2),'reference':'NF1','amount':cp.Decimal('100.00')}
    with db() as con:assert cp.bank_transaction(con,'manager',transaction)['status']=='Conciliado'
    with db() as con:
        assert cp.bank_transaction(con,'manager',transaction)['duplicate']
        assert con.execute('SELECT paid FROM l2_commercial_receivables').fetchone()[0]==100
        assert con.execute('SELECT count(*) FROM l2_commercial_allocations').fetchone()[0]==1
        assert con.execute('SELECT count(*) FROM l2_commercial_documents').fetchone()[0]==1


def test_ambiguous_payment_not_automatched(api):
    c,db=api
    with db() as con:
        t={'account':'001','external_id':'f','business_date':date(2026,10,1),'reference':'','amount':cp.Decimal('100')}
        assert cp.bank_transaction(con,'manager',t)['status']=='Pendente'
        assert con.execute('SELECT count(*) FROM l2_commercial_allocations').fetchone()[0]==0


def test_export_baseline_and_schedule_idempotency(api):
    c,db=api;post_doc(c)
    q='?start=2026-10-01&end=2026-11-01'
    assert c.get('/api/strategy/commercial/reports/pdf'+q,headers=SELLER).content.startswith(b'%PDF')
    assert c.get('/api/strategy/commercial/reports/xlsx'+q,headers=SELLER).content.startswith(b'PK')
    data={'name':'Campanha','start':'2026-10-01','end':'2026-11-01'}
    assert c.post('/api/strategy/commercial/baselines',headers=H,json=data).status_code==200
    r=c.post('/api/strategy/commercial/report-schedules',headers=H,json={'cadence':'daily','hour':8})
    assert r.status_code==200
    now=datetime(2026,11,2,9,tzinfo=cp.TZ)
    cp.run_schedules(now);cp.run_schedules(now)
    runs=c.get('/api/strategy/commercial/report-runs',headers=H).json()
    assert len(runs)==1
    path='/api/strategy/commercial/report-runs/'+runs[0]['id']+'/pdf'
    assert c.get(path,headers=H).status_code==200
    assert c.get(path,headers=SELLER).status_code==404
