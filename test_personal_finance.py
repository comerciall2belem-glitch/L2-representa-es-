import os
import base64
from decimal import Decimal
import pytest
import psycopg
from psycopg.types.json import Jsonb
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from personal_finance import install_personal, initialize_personal, Transaction, period_summary

DSN=os.getenv('PERSONAL_TEST_DSN','')
def db(): return psycopg.connect(DSN)
def auth(header):
    token=(header or '').removeprefix('Bearer ')
    user=base64.urlsafe_b64decode(token).decode() if token else ''
    if not user: raise HTTPException(401,'Não autenticado')
    return user
def headers(user='Ana Paula'):return {'Authorization':'Bearer '+base64.urlsafe_b64encode(user.encode()).decode()}

@pytest.fixture
def client():
    if not DSN:pytest.skip('Set PERSONAL_TEST_DSN to a disposable PostgreSQL database')
    with db() as con:
        con.execute('DROP SCHEMA IF EXISTS personal_finance CASCADE')
        con.execute('CREATE TABLE IF NOT EXISTS entities(kind TEXT,id TEXT,payload JSONB,PRIMARY KEY(kind,id))')
        con.execute('TRUNCATE entities')
        for kind,i,p in [('cash_entry','withdrawal',{'type':'Saída','category':'Pró-labore','description':'Retirada Ana Paula','date':'2026-09-15','amount':'1000.00'}),
                         ('office_finance','business',{'Valor':'250.00','Descrição':'Aluguel empresa'})]:
            con.execute('INSERT INTO entities VALUES(%s,%s,%s)',(kind,i,Jsonb(p)))
    initialize_personal(db)
    app=FastAPI();install_personal(app,db,auth)
    return TestClient(app)

def payload(**changes):
    value={'date':'2026-09-29','type':'Despesa Pessoal','category_id':'category-moradia','account_id':'account-cash',
           'amount':'123.45','status':'Pago','paid_date':'2026-09-29','person':'Casal','notes':'Teste'}
    return dict(value,**changes)
def overview(client,user='Ana Paula',**filters):
    return client.get('/api/personal-finance/overview',params={'start':'2026-09-01','end':'2026-09-30',**filters},headers=headers(user))

@pytest.mark.parametrize('user',['Laís','Marlene','MB','Erika','Administrador','Euler outro'])
@pytest.mark.parametrize('method,path,body',[
    ('GET','/overview?start=2026-09-01&end=2026-09-30',None),
    ('POST','/transactions',payload()),('PUT','/transactions/unknown',payload()),
    ('GET','/attachments/unknown',None),('GET','/deleted',None),
    ('POST','/categories',{'name':'Privada'}),('POST','/accounts',{'name':'Privada'}),
    ('DELETE','/transactions/unknown?version=1',None),('POST','/transactions/unknown/restore',None)])
def test_nonmembers_cannot_access_any_endpoint(client,user,method,path,body):
    assert client.request(method,'/api/personal-finance'+path,json=body,headers=headers(user)).status_code==403

def test_anonymous_denied(client):assert overview(client,'').status_code==401

def test_couple_shared_and_company_unchanged(client):
    with db() as con:before=con.execute('SELECT * FROM entities ORDER BY kind,id').fetchall()
    response=client.post('/api/personal-finance/transactions',json=payload(),headers=headers())
    assert response.status_code==200,response.text
    result=overview(client,'Euler').json();row=result['transactions'][0]
    assert row['created_by']=='Ana Paula' and result['summary']['expenses']=='123.45'
    response=client.put('/api/personal-finance/transactions/'+row['id'],json=payload(amount='222.20',version=row['version']),headers=headers('Euler'))
    assert response.status_code==200,response.text
    assert overview(client).json()['transactions'][0]['updated_by']=='Euler'
    assert overview(client).json()['summary']['expenses']=='222.2'
    with db() as con:assert con.execute('SELECT * FROM entities ORDER BY kind,id').fetchall()==before

def test_rls_denies_even_table_owner_without_trusted_context(client):
    with db() as con:
        assert con.execute('SELECT count(*) FROM personal_finance.categories').fetchone()[0]==0
        con.execute("SELECT set_config('l2.personal_actor','Laís',true)")
        assert con.execute('SELECT count(*) FROM personal_finance.accounts').fetchone()[0]==0
    with db() as con:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            con.execute("INSERT INTO personal_finance.categories VALUES('bad','ana-paula-euler','Forbidden','Banco',true,1)")

def test_versions_prevent_overwrite(client):
    i=client.post('/api/personal-finance/transactions',json=payload(),headers=headers()).json()['id']
    assert client.put('/api/personal-finance/transactions/'+i,json=payload(version=0),headers=headers('Euler')).status_code==409
    assert overview(client).json()['summary']['expenses']=='123.45'

def test_conciliation_partial_limit_and_restore(client):
    income=payload(type='Receita Pessoal',category_id='category-pro-labore',withdrawal_id='withdrawal',amount='600.00')
    a=client.post('/api/personal-finance/transactions',json=income,headers=headers());assert a.status_code==200,a.text
    b=client.post('/api/personal-finance/transactions',json={**income,'amount':'400.01'},headers=headers('Euler'));assert b.status_code==409
    assert overview(client).json()['withdrawals'][0]['available']=='400.00'
    i=a.json()['id'];assert client.delete('/api/personal-finance/transactions/'+i+'?version=1',headers=headers()).status_code==200
    assert len(client.get('/api/personal-finance/deleted',headers=headers('Euler')).json())==1
    assert overview(client).json()['withdrawals'][0]['available']=='1000.00'
    assert client.post('/api/personal-finance/transactions/'+i+'/restore',headers=headers('Euler')).status_code==200
    with db() as con:
        con.execute("UPDATE entities SET payload=jsonb_set(payload,'{amount}','\"500.00\"') WHERE id='withdrawal'")
    assert overview(client).json()['transactions'][0]['reconciliation_warning'] is True

def test_unknown_category_or_account_rejected(client):
    for changes in ({'category_id':'company-category'},{'account_id':'company-account'}):
        assert client.post('/api/personal-finance/transactions',json=payload(**changes),headers=headers()).status_code==400

def test_pending_and_period_account_filters(client):
    assert client.post('/api/personal-finance/transactions',json=payload(status='Pendente',paid_date=None,due_date='2026-10-01'),headers=headers()).status_code==200
    assert overview(client).json()['summary']['pending']=='0'
    assert overview(client,account='another-account').json()['transactions']==[]

def test_attachments_private_and_preserved_in_trash(client):
    i=client.post('/api/personal-finance/transactions',json=payload(),headers=headers()).json()['id']
    r=client.post(f'/api/personal-finance/transactions/{i}/attachments',files={'file':('receipt.pdf',b'%PDF-1.4\nreceipt','application/pdf')},headers=headers())
    assert r.status_code==200,r.text
    a=r.json()['id'];path='/api/personal-finance/attachments/'+a
    assert client.get(path,headers=headers('Euler')).content==b'%PDF-1.4\nreceipt'
    assert client.get(path,headers=headers('Laís')).status_code==403
    assert client.get(path,headers=headers('Euler')).headers['cache-control']=='no-store'
    assert client.post(f'/api/personal-finance/transactions/{i}/attachments',files={'file':('fake.pdf',b'<script>bad</script>','application/pdf')},headers=headers()).status_code==400
    assert client.delete(f'/api/personal-finance/transactions/{i}?version=1',headers=headers()).status_code==200
    assert client.get(path,headers=headers()).status_code==404
    assert client.post(f'/api/personal-finance/transactions/{i}/restore',headers=headers()).status_code==200
    assert client.get(path,headers=headers('Euler')).status_code==200

def test_named_edit_and_deactivation(client):
    r=client.post('/api/personal-finance/accounts',json={'name':'Conta pessoal','kind':'Banco'},headers=headers());assert r.status_code==200
    i=r.json()['id']
    assert client.put('/api/personal-finance/accounts/'+i,json={'name':'Conta casal','kind':'Banco','active':False,'version':1},headers=headers('Euler')).status_code==200
    assert client.post('/api/personal-finance/transactions',json=payload(account_id=i),headers=headers()).status_code==400

@pytest.mark.parametrize('changes',[{'amount':'0'},{'amount':'-1'},{'amount':'10.001'},{'amount':'NaN'},
    {'paid_date':None},{'status':'Pendente'},{'withdrawal_id':'withdrawal'},{'person':'Laís'}])
def test_invalid_transaction_model(changes):
    with pytest.raises(ValueError):Transaction(**payload(**changes))

def test_money_exact_no_float_accumulation():
    rows=[payload(amount=x) for x in (0.1,0.2)]
    result=period_summary(rows,[],'2026-09-01','2026-09-30')
    assert Decimal(result['expenses'])==Decimal('0.30')
