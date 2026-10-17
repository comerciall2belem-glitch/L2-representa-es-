"""Run only against an explicitly marked disposable PostgreSQL database."""
import base64
import os
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from expense_ai import install, setup
from personal_finance import initialize_personal

DSN=os.getenv('PERSONAL_TEST_DSN','')
pytestmark=pytest.mark.skipif(not DSN or os.getenv('L2_EXPENSE_TEST_DISPOSABLE')!='yes',reason='Requires explicitly disposable PostgreSQL database')

def db(): return psycopg.connect(DSN)
def headers(user='Ana Paula'): return {'Authorization':base64.urlsafe_b64encode(user.encode()).decode()}
def auth(header): return base64.urlsafe_b64decode(header).decode() if header else ''
def today(): return datetime.now(ZoneInfo('America/Belem')).date().isoformat()

@pytest.fixture
def client():
    with db() as con:
        con.execute('DROP SCHEMA IF EXISTS personal_finance CASCADE')
        con.execute('DROP TABLE IF EXISTS expense_receipts,entities,audit_log CASCADE')
        con.execute('CREATE TABLE entities(kind TEXT,id TEXT,payload JSONB,updated_at TIMESTAMPTZ DEFAULT now(),created_at TIMESTAMPTZ DEFAULT now(),PRIMARY KEY(kind,id))')
        con.execute('CREATE TABLE audit_log(username TEXT,kind TEXT,entity_id TEXT,action TEXT)')
        setup(con)
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('cash_day','test-day',%s)",(Jsonb(dict(date=today(),status='Aberto',opening=0)),))
    initialize_personal(db)
    app=FastAPI();install(app,db,auth,lambda u: {'finance'} if u in ('Ana Paula','Euler','Laís') else set())
    return TestClient(app)

def upload(client,scope='empresa',content=b'receipt-a',user='Ana Paula',**extra):
    with patch('expense_ai.extract',return_value=(dict(supplier='POSTO TESTE',date=today(),amount='186.40',category='Combustível'),'image/png')):
        return client.post('/api/expense-ai/receipts',headers=headers(user),data=dict(scope=scope,**extra),files={'file':('teste.png',content,'image/png')})

def count(con,table): return con.execute('SELECT count(*) FROM '+table).fetchone()[0]

def test_corporate_amount_attachment_and_audit_are_atomic(client):
    response=upload(client);assert response.status_code==200,response.text
    eid=response.json()['id']
    with db() as con:
        payload=con.execute("SELECT payload FROM entities WHERE kind='cash_entry' AND id=%s",(eid,)).fetchone()[0]
        assert Decimal(str(payload['amount']))==Decimal('186.40')
        assert payload['type']=='Saída'
        assert con.execute('SELECT content FROM expense_receipts WHERE id=%s',(eid,)).fetchone()[0]==b'receipt-a'
        assert count(con,'audit_log')==1
        con.execute("SELECT set_config('l2.personal_actor','Ana Paula',true)")
        assert count(con,'personal_finance.transactions')==0

def test_personal_does_not_touch_corporate_ledger(client):
    response=upload(client,'pessoal');assert response.status_code==200,response.text
    with db() as con:
        assert con.execute("SELECT count(*) FROM entities WHERE kind='cash_entry'").fetchone()[0]==0
        con.execute("SELECT set_config('l2.personal_actor','Euler',true)")
        assert con.execute('SELECT amount FROM personal_finance.transactions').fetchone()[0]==Decimal('186.40')
        assert count(con,'personal_finance.attachments')==1
        assert count(con,'personal_finance.audit_events')==1
    assert client.get('/api/expense-ai/receipts/'+response.json()['id'],headers=headers('Laís')).status_code==403
    assert client.get('/api/expense-ai/receipts/'+response.json()['id'],headers=headers('Euler')).content==b'receipt-a'

def test_global_hash_blocks_duplicate_across_ledgers(client):
    assert upload(client).status_code==200
    assert upload(client,'pessoal').status_code==409
    assert upload(client).status_code==409
    with db() as con: assert count(con,'expense_receipts')==1

def test_closed_day_rolls_back_everything(client):
    with db() as con: con.execute("UPDATE entities SET payload=jsonb_set(payload,'{status}','\"Fechado\"') WHERE kind='cash_day'")
    assert upload(client).status_code==409
    with db() as con:
        assert count(con,'expense_receipts')==0
        assert count(con,'audit_log')==0
        assert con.execute("SELECT count(*) FROM entities WHERE kind='cash_entry'").fetchone()[0]==0

def test_forced_late_failure_rolls_back_expense_and_attachment(client):
    with db() as con:
        con.execute("ALTER TABLE expense_receipts ADD CONSTRAINT fail_test CHECK(filename<>'teste.png')")
    with pytest.raises(psycopg.errors.CheckViolation): upload(client,'pessoal')
    with db() as con:
        con.execute("SELECT set_config('l2.personal_actor','Ana Paula',true)")
        assert count(con,'personal_finance.transactions')==0
        assert count(con,'personal_finance.attachments')==0
        assert count(con,'personal_finance.audit_events')==0
        assert count(con,'expense_receipts')==0

def test_corporate_reverse_compensates_once(client):
    response=upload(client);assert response.status_code==200,response.text
    url='/api/expense-ai/receipts/'+response.json()['id']+'/reverse'
    assert client.post(url,headers=headers()).status_code==200
    assert client.post(url,headers=headers()).status_code==409
    with db() as con:
        net=con.execute("SELECT sum(CASE WHEN payload->>'type'='Entrada' THEN (payload->>'amount')::numeric ELSE -(payload->>'amount')::numeric END) FROM entities WHERE kind='cash_entry'").fetchone()[0]
        assert net==0
        assert count(con,'expense_receipts')==1

def test_personal_reverse_preserves_file_and_audit(client):
    response=upload(client,'pessoal');assert response.status_code==200,response.text
    url='/api/expense-ai/receipts/'+response.json()['id']+'/reverse'
    assert client.post(url,headers=headers('Euler')).status_code==200
    assert client.post(url,headers=headers('Euler')).status_code==409
    with db() as con:
        con.execute("SELECT set_config('l2.personal_actor','Ana Paula',true)")
        assert con.execute('SELECT count(*) FROM personal_finance.transactions WHERE deleted_at IS NULL').fetchone()[0]==0
        assert count(con,'personal_finance.attachments')==1
        assert count(con,'personal_finance.audit_events')==2

def test_rls_denies_without_household_context(client):
    with db() as con:
        assert not con.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()[0], 'Use non-superuser test role'
    assert upload(client,'pessoal').status_code==200
    with db() as con:
        assert count(con,'personal_finance.transactions')==0
        con.execute("SELECT set_config('l2.personal_actor','Laís',true)")
        assert count(con,'personal_finance.attachments')==0

def test_category_history_is_isolated_by_scope(client):
    assert upload(client,'empresa').status_code==200
    with db() as con:
        con.execute("UPDATE entities SET payload=jsonb_set(payload,'{category}','\"Categoria Empresarial\"') WHERE kind='cash_entry'")
    response=upload(client,'pessoal',content=b'receipt-b')
    assert response.status_code==200,response.text
    assert response.json()['category']=='Combustível'

def test_invalid_personal_account_rolls_back(client):
    assert upload(client,'pessoal',account='corporate-account').status_code==422
    with db() as con:
        assert count(con,'expense_receipts')==0
        con.execute("SELECT set_config('l2.personal_actor','Ana Paula',true)")
        assert count(con,'personal_finance.transactions')==0


def test_concurrent_duplicate_creates_only_one_expense(client):
    from concurrent.futures import ThreadPoolExecutor
    def send():
        return client.post('/api/expense-ai/receipts',headers=headers(),data={'scope':'empresa'},files={'file':('teste.png',b'same-concurrent-receipt','image/png')}).status_code
    with patch('expense_ai.extract',return_value=(dict(supplier='LOJA CONCORRENTE',date=today(),amount='15.10',category='Outros'),'image/png')):
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=sorted(pool.map(lambda _:send(),range(2)))
    assert statuses==[200,409]
    with db() as con:
        assert count(con,'expense_receipts')==1
        assert con.execute("SELECT count(*) FROM entities WHERE kind='cash_entry'").fetchone()[0]==1
