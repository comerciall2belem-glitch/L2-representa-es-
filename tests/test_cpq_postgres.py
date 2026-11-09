"""Real PostgreSQL acceptance: API authorization, exact pricing, rollback and idempotence.
Run only with CPQ_TEST_DSN pointing to a disposable CI database.
"""
import os
from copy import deepcopy
from datetime import date
from unittest.mock import patch
import pytest
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from fastapi.testclient import TestClient
import cpq

DSN=os.getenv('CPQ_TEST_DSN','')

@pytest.fixture(scope='module')
def environment():
    if not DSN:pytest.skip('CPQ_TEST_DSN requires disposable PostgreSQL')
    info=conninfo_to_dict(DSN)
    assert info.get('dbname') in ('l2_ci','l2_cpq_test'), 'This test resets only explicitly disposable CI databases'
    with psycopg.connect(DSN) as con:
        con.execute('DROP SCHEMA IF EXISTS cpq_ci CASCADE')
        con.execute('CREATE SCHEMA cpq_ci')
    scoped=make_conninfo(DSN,options='-csearch_path=cpq_ci,public')
    os.environ.setdefault('DATABASE_URL',scoped)
    os.environ.setdefault('L2_INITIAL_PASSWORD','test-only-initial-password')
    import server
    with patch.object(server,'DATABASE_URL',scoped):
        server.initialize()
        with server.db() as con:
            for user,role,sectors in [('Erika','Vendedor',['commercial','routes']),('Outro','Vendedor',['commercial']),('Ana Paula','Administradora',['commercial','office','management','finance','catalog','admin']),('Euler','Vendedor',['commercial','management','finance','catalog'])]:
                con.execute("INSERT INTO app_users(username,password_hash,role,sectors,active) VALUES(%s,'test-only',%s,%s,true) ON CONFLICT(username) DO UPDATE SET role=excluded.role,sectors=excluded.sectors",(user,role,Jsonb(sectors)))
            client={'id':'ci-client','name':'Loja CI','taxId':'11222333000181','stateRegistration':'ISENTO','city':'Belém','state':'PA','owner':'Erika','channel':'Cosméticos'}
            cpq.put(con,'client',client)
            con.execute("INSERT INTO clientes(id,razao_social,documento) VALUES('ci-client','Loja CI','11222333000181') ON CONFLICT DO NOTHING")
            industry={'id':'Marca CI','name':'Marca CI','active':True,'commercialPolicy':[{'minimum':'100','paymentTerms':'30/60','freight':'CIF'}]}
            cpq.put(con,'industry',industry)
            cpq.put(con,'price_table',{'id':'Marca CI|PA','brand':'Marca CI','state':'PA','title':'CI','active':True,'channel':'VAREJO'})
            cpq.put(con,'price',{'id':'Marca CI|PA|sku','tableId':'Marca CI|PA','brand':'Marca CI','state':'PA','sku':'sku','description':'Produto CI','price':'100.00'})
        # Real authorization/scoping uses PostgreSQL user roles. Only session identity is stubbed.
        original=cpq._services.copy()
        strategy_original=server.strategic_crm._services.copy()
        server.strategic_crm.configure(db=server.db,auth=server.auth,require_sector=server.require_sector,valid_cnpj=server.valid_cnpj,check_client_scope=server.check_client_scope,scoped_rows=server.scoped_rows,project_attendance=server.project_attendance,is_seller=server.is_seller)
        cpq.configure(db=server.db,auth=lambda header:(header or '').removeprefix('Bearer '),require_sector=server.require_sector,check_client_scope=server.check_client_scope,scoped_rows=server.scoped_rows,is_seller=server.is_seller,normalize_uf=server.normalize_uf,price_table_matches_client=server.price_table_matches_client,valid_cnpj=server.valid_cnpj,sync_order=server.sync_cpq_order)
        with patch.object(server,'auth',side_effect=lambda header:(header or '').removeprefix('Bearer ')):
            yield server,TestClient(server.app)
        cpq._services.clear();cpq._services.update(original)
        server.strategic_crm._services.clear();server.strategic_crm._services.update(strategy_original)

def header(user='Erika'):return {'Authorization':'Bearer '+user}
def payload(**changes):
    result={'clientId':'ci-client','sellerResponsible':'Erika','priceTable':'PA','channel':'VAREJO','paymentTerms':'30/60','discountPercent':'5','items':[{'brand':'Marca CI','tableId':'Marca CI|PA','sku':'sku','quantity':10}]}
    result.update(changes);return result

def create(client,**changes):
    response=client.post('/api/cpq/quotes',json=payload(**changes),headers=header())
    assert response.status_code==200,response.text
    return response.json()

def approve(client,quote,user='Ana Paula'):
    response=client.post('/api/cpq/quotes/'+quote['id']+'/decision',json={'approve':True,'reason':'Condição comercial conferida'},headers=header(user))
    assert response.status_code==200,response.text

def accept(client,quote):
    base='/api/cpq/quotes/'+quote['id']
    r=client.get(base+'/pdf',headers=header());assert r.status_code==200,r.text;assert r.content.startswith(b'%PDF-')
    r=client.post(base+'/dispatch',json={'channel':'Link','evidence':'Envio operacional de teste confirmado'},headers=header());assert r.status_code==200,r.text
    r=client.post(base+'/accept',json={'evidence':'Aceite documental de teste'},headers=header());assert r.status_code==200,r.text

@pytest.mark.parametrize('discount,tier',[('5','Automática'),('5.01','Gestor Comercial'),('10','Gestor Comercial'),('10.01','Diretoria Executiva')])
def test_approval_matrix_and_scope(environment,discount,tier):
    server,client=environment;quote=create(client,discountPercent=discount)
    assert quote['requiredApproval']==tier
    if discount!='5':
        r=client.get('/api/cpq/quotes/'+quote['id']+'/pdf',headers=header());assert r.status_code==409
        r=client.post('/api/cpq/quotes/'+quote['id']+'/decision',json={'approve':True,'reason':'Tentativa sem alçada'},headers=header());assert r.status_code==403
        approve(client,quote,'Euler' if discount=='10.01' else 'Ana Paula')
    r=client.get('/api/cpq/quotes/'+quote['id']+'/pdf',headers=header('Outro'));assert r.status_code==403
    assert client.get('/api/cpq/quotes',headers=header('Outro')).json()==[]


def test_conversion_idempotent_and_snapshot_rollback(environment):
    server,client=environment;quote=create(client)
    accept(client,quote);url='/api/cpq/quotes/'+quote['id']+'/convert'
    a=client.post(url,headers=header());assert a.status_code==200,a.text
    b=client.post(url,headers=header());assert b.status_code==200,b.text
    assert a.json()['orderId']==b.json()['orderId'];assert b.json()['alreadyConverted']
    with server.db() as con:
        order=cpq.get(con,'order',a.json()['orderId'])
        assert order['amount']==950.0;assert order['items'][0]['subtotal']=='950.00'
        assert con.execute("SELECT count(*) FROM entities WHERE kind='order' AND payload->>'cpqQuoteId'=%s",(quote['id'],)).fetchone()[0]==1
    # Tampering through the legacy sync entry point is rejected and leaves the snapshot intact.
    tampered=deepcopy(order);tampered['items'][0]['quantity']=11
    response=client.post('/api/sync',json={'changes':[{'type':'order','data':tampered,'changeId':'tamper-'+quote['id']}]},headers=header())
    assert response.status_code==409,response.text
    with server.db() as con:assert cpq.get(con,'order',order['id'])['amount']==950.0


def test_changed_prices_invalidate_approval(environment):
    server,client=environment;quote=create(client,discountPercent='10.01');approve(client,quote,'Euler')
    with server.db() as con:
        price=cpq.get(con,'price','Marca CI|PA|sku');price['price']='101.00';cpq.put(con,'price',price)
    try:
        response=client.get('/api/cpq/quotes/'+quote['id']+'/pdf',headers=header());assert response.status_code==409,response.text
    finally:
        with server.db() as con:price['price']='100.00';cpq.put(con,'price',price)


def test_legacy_bypass_and_financial_boundaries(environment):
    server,client=environment
    order={'id':'ci-bypass','clientId':'ci-client','sellerResponsible':'Erika','brand':'Marca CI','priceTable':'PA','items':payload()['items'],'status':'Confirmado','date':str(date.today())}
    response=client.post('/api/sync',json={'changes':[{'type':'order','data':order,'changeId':'ci-bypass-change'}]},headers=header());assert response.status_code==409,response.text
    with server.db() as con:assert not con.execute("SELECT 1 FROM entities WHERE kind='order' AND id='ci-bypass'").fetchone()
    response=client.post('/api/sync',json={'changes':[{'type':'office_finance','data':{'id':'ci-secret','Valor':'100'},'changeId':'ci-finance-change'}]},headers=header());assert response.status_code==403,response.text


def test_converted_snapshot_survives_price_and_table_changes(environment):
    server,client=environment;quote=create(client)
    accept(client,quote)
    result=client.post('/api/cpq/quotes/'+quote['id']+'/convert',headers=header());assert result.status_code==200,result.text
    with server.db() as con:
        order=cpq.get(con,'order',result.json()['orderId'])
        price=cpq.get(con,'price','Marca CI|PA|sku');price['price']='200.00';cpq.put(con,'price',price)
        table=cpq.get(con,'price_table','Marca CI|PA');table['active']=False;cpq.put(con,'price_table',table)
    try:
        order['status']='Faturado'
        response=client.post('/api/sync',json={'changes':[{'type':'order','data':order,'changeId':'status-'+quote['id']}]},headers=header())
        assert response.status_code==200,response.text
        with server.db() as con:
            updated=cpq.get(con,'order',order['id']);assert updated['amount']==950.0;assert updated['status']=='Faturado'
    finally:
        with server.db() as con:
            price['price']='100.00';cpq.put(con,'price',price)
            table['active']=True;cpq.put(con,'price_table',table)
