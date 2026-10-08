"""Isolated E2E protocol mock. Does not prove delivery to a physical device."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
import zara
from scripts.simulate_zara import payload, signed


class SQLStore:
    """SQLite test adapter: exercises queries; PostgreSQL migration needs its own test."""
    def __init__(self):
        self.lock = threading.RLock()
        self.con = sqlite3.connect(':memory:', check_same_thread=False)
        self.con.create_function('zara_now',0,lambda:datetime.now().isoformat())
        self.con.executescript('''
        CREATE TABLE field_operators(phone TEXT PRIMARY KEY,username TEXT,enabled BOOLEAN);
        CREATE TABLE zara_conversations(phone TEXT PRIMARY KEY,name TEXT,mode TEXT DEFAULT 'bot',
            created_at TEXT DEFAULT (zara_now()),updated_at TEXT DEFAULT (zara_now()));
        CREATE TABLE zara_messages(message_id TEXT PRIMARY KEY,phone TEXT,direction TEXT,body TEXT,
            delivered BOOLEAN DEFAULT false,created_at TEXT DEFAULT (zara_now()),
            reply_to TEXT,panel_confirmed_at TEXT);
        CREATE TABLE zara_delivery_events(message_id TEXT,phone TEXT,status TEXT,event_at INTEGER,
            received_at TEXT DEFAULT (zara_now()),PRIMARY KEY(message_id,phone,status));
        ''')

    def __enter__(self):
        self.lock.acquire()
        return self

    def __exit__(self, kind, *args):
        self.con.rollback() if kind else self.con.commit()
        self.lock.release()

    def execute(self, sql, args=()):
        sql = sql.replace('%s', '?').replace('now()', 'zara_now()').replace(' FOR UPDATE', '')
        cur = self.con.execute(sql, args)
        columns = [x[0] for x in cur.description] if cur.description else []
        def convert(row):
            if row is None:
                return None
            return tuple(datetime.fromisoformat(v) if v and columns[i] in ('created_at','updated_at','panel_confirmed_at') else v for i,v in enumerate(row))
        class Result:
            def fetchone(self):
                return convert(cur.fetchone())
            def fetchall(self):
                return [convert(row) for row in cur.fetchall()]
        return Result()


def status_payload(mid, status='delivered', phone='5591999999999', phone_id='123'):
    return {'object': 'whatsapp_business_account','entry':[{'changes':[{'field':'messages','value':{
        'metadata':{'phone_number_id':phone_id},'statuses':[{'id':mid,'recipient_id':phone,
        'status':status,'timestamp':'1791046500'}]}}]}]}


class Harness:
    phone = '5591999999999'
    mid = 'wamid.mock.out'
    def __enter__(self):
        self.store = SQLStore()
        self.requests = []
        def graph(req):
            self.requests.append(json.loads(req.content))
            return httpx.Response(200,json={'messages':[{'id':self.mid}]})
        real = httpx.AsyncClient
        def client(**kwargs):
            return real(transport=httpx.MockTransport(graph), **kwargs)
        self.patches = [patch.dict(os.environ, {'WA_VERIFY_TOKEN':'mock-verify','WA_APP_SECRET':'secret',
            'WA_ACCESS_TOKEN':'mock-access','WA_PHONE_NUMBER_ID':'123','OPENAI_API_KEY':''}),
            patch.object(zara,'db',return_value=self.store),patch.object(zara,'admin'),
            patch.object(zara.httpx,'AsyncClient',side_effect=client)]
        for p in self.patches:
            p.start()
        app=FastAPI(); app.include_router(zara.router)
        self.client=TestClient(app)
        return self

    def __exit__(self,*args):
        self.client.close()
        for p in reversed(self.patches):
            p.stop()
        self.store.con.close()

    def post(self,data):
        raw,headers=signed(data,'secret')
        return self.client.post('/api/zara/webhook',content=raw,headers=headers)

    def items(self):
        return self.client.get('/api/zara/conversations/'+self.phone).json()

    def outbound(self):
        return next(x for x in self.items() if x['id']==self.mid)

    def confirm(self):
        return self.client.post('/api/zara/conversations/'+self.phone+'/messages/'+self.mid+'/confirm-e2e')

    def inbound_reply(self, linked=True):
        data=payload(); msg=data['entry'][0]['changes'][0]['value']['messages'][0]
        msg['text']['body']='Recebi no meu aparelho de teste'
        if linked:
            msg['context']={'id':self.mid}
        # Human mode keeps this test focused on the original outgoing message.
        self.store.execute("UPDATE zara_conversations SET mode='human' WHERE phone=%s",(self.phone,))
        return self.post(data)


def run():
    with Harness() as h:
        assert h.post(payload()).status_code==200
        assert h.outbound()['deliveryStatus']=='graph_accepted'
        assert h.confirm().status_code==409
        assert h.post(status_payload(h.mid)).status_code==200
        assert h.outbound()['deliveryStatus']=='delivered'
        assert h.confirm().status_code==409
        assert h.inbound_reply().status_code==200
        rows=h.client.get('/api/zara/conversations').json()
        assert any(x['phone']==h.phone and 'aparelho' in x['lastMessage'] for x in rows)
        assert h.outbound()['e2eReady'] and h.outbound()['e2eStatus']=='pending'
        assert h.confirm().status_code==200
        assert h.outbound()['e2eStatus']=='confirmed'
    print('OK: E2E simulado (aceitação → entrega assinada → retorno vinculado → painel → confirmação). Homologação física pendente.')


if __name__=='__main__':
    run()

