import sqlite3
import sys
import types
from unittest.mock import patch
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import webchat

class DB:
    def __init__(self):
        self.con=sqlite3.connect(':memory:',check_same_thread=False)
        self.con.executescript('''CREATE TABLE webchat_sessions(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE, ip_hash TEXT,name TEXT,mode TEXT DEFAULT 'bot',created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,expires_at TEXT DEFAULT '2099-01-01');
        CREATE TABLE webchat_messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE,session_id TEXT,sender TEXT,body TEXT,nonce TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,seen_at TEXT,UNIQUE(session_id,nonce,sender));
        CREATE TABLE audit_log(username TEXT,kind TEXT,entity_id TEXT,action TEXT);''')
    def __enter__(self):return self
    def __exit__(self,typ,*args):self.con.rollback() if typ else self.con.commit()
    def execute(self,sql,args=()):
        if 'pg_advisory_xact_lock' in sql:return self.con.execute('SELECT 1')
        sql=sql.replace('%s','?').replace(' FOR UPDATE','').replace("now()-interval '1 hour'","datetime('now','-1 hour')").replace("now()-interval '1 minute'","datetime('now','-1 minute')").replace('now()','CURRENT_TIMESTAMP')
        return self.con.execute(sql,args)

# SQLite stores timestamps as strings; the real adapter exposes datetimes.
class Timestamp(str):
    def isoformat(self):return str(self)

@pytest.fixture
def harness():
    store=DB();store.con.row_factory=lambda cur,row: tuple(Timestamp(v) if cur.description[i][0] in ('created_at','seen_at','updated_at') and v else v for i,v in enumerate(row))
    app=FastAPI();app.include_router(webchat.router)
    def auth(header):
        if header!='Bearer staff-test':raise HTTPException(401)
        return 'Marlene'
    server=types.SimpleNamespace(auth=auth)
    with patch.object(webchat,'db',return_value=store),patch.dict(sys.modules,{'server':server}):
        yield TestClient(app,base_url='https://testserver'),store,app


def start(client):
    r=client.post('/api/chat/session',json={'name':'Cliente Teste'});assert r.status_code==200
    assert 'Secure' in r.headers['set-cookie'] and 'HttpOnly' in r.headers['set-cookie']
    return r.json()['id']


def test_isolation_recovery_and_order(harness):
    c,db,app=harness;sid=start(c)
    r=c.post('/api/chat/messages',json={'text':'Preciso de catálogo','nonce':'a'*32})
    assert r.status_code==200
    assert [m['sender'] for m in r.json()['messages']]==['bot','client','bot']
    other=TestClient(app,base_url='https://testserver')
    assert other.get('/api/chat/messages').status_code==401
    assert c.post('/api/chat/session',json={'name':'Outra'}).json()['id']==sid
    start(other)
    assert all(m['text']!='Preciso de catálogo' for m in other.get('/api/chat/messages').json()['messages'])


def test_idempotency_and_staff_read_reply(harness):
    c,db,app=harness;sid=start(c);data={'text':'Tabela BT','nonce':'b'*32}
    for _ in range(2):assert c.post('/api/chat/messages',json=data).status_code==200
    assert db.con.execute('SELECT count(*) FROM webchat_messages').fetchone()[0]==3
    assert c.get('/api/chat/staff/conversations').status_code==401
    headers={'Authorization':'Bearer staff-test'}
    rows=c.get('/api/chat/staff/conversations',headers=headers).json();assert rows[0]['unread']==1
    c.get('/api/chat/staff/conversations/'+sid,headers=headers)
    reply={'text':'Vamos verificar a tabela solicitada.','nonce':'c'*32}
    for _ in range(2):assert c.post('/api/chat/staff/conversations/'+sid+'/reply',json=reply,headers=headers).status_code==200
    items=c.get('/api/chat/messages').json()['messages'];assert items[-1]['sender']=='staff' and items[-1]['status']=='seen'
    assert db.con.execute('SELECT count(*) FROM audit_log').fetchone()[0]==1


def test_cross_origin_rate_limit_and_validation(harness):
    c,db,app=harness;start(c)
    assert c.post('/api/chat/messages',headers={'Origin':'https://evil.example'},json={'text':'Olá','nonce':'d'*32}).status_code==403
    assert c.post('/api/chat/messages',json={'text':'  ','nonce':'e'*32}).status_code==422
    for i in range(12):assert c.post('/api/chat/messages',json={'text':'Teste','nonce':str(i).zfill(32)}).status_code==200
    assert c.post('/api/chat/messages',json={'text':'Teste','nonce':'f'*32}).status_code==429


def test_storage_failure_is_not_acknowledged(harness):
    c,db,app=harness;start(c)
    with patch.object(webchat,'save',side_effect=RuntimeError('storage down')):
        failing=TestClient(app,base_url='https://testserver',raise_server_exceptions=False);failing.cookies.update(c.cookies)
        assert failing.post('/api/chat/messages',json={'text':'Teste','nonce':'g'*32}).status_code==500
    assert db.con.execute('SELECT count(*) FROM webchat_messages').fetchone()[0]==1
