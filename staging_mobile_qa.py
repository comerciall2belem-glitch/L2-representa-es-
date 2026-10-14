"""Temporary QA seed, permitted only on an explicitly named isolated database."""
import os
import logging
import copy
from datetime import date
from psycopg.types.json import Jsonb

USER = 'L2 Mobile QA 20261014'
BRAND = 'L2 QA - SOMENTE TESTE'
CLIENT = 'l2-mobile-qa-client-20261014'
TABLE = BRAND+'|PA'

def install(module):
    original = module.initialize
    def initialize():
        original()
        password = os.getenv('L2_MOBILE_QA_PASSWORD')
        if not password:
            return
        with module.db() as con:
            if con.execute('SELECT current_database()').fetchone()[0] != 'l2one_homologacao':
                raise RuntimeError('QA seed requires isolated homologation database')
            con.execute('''INSERT INTO app_users(username,password_hash,active,must_change_password,sectors,role,department)
                VALUES(%s,%s,true,false,%s,'Vendedor','Homologação Mobile') ON CONFLICT DO NOTHING''',
                (USER,module.password_hash(password),Jsonb(['commercial','clients_edit','routes','catalog'])))
            fixtures = [
                ('client',CLIENT,{'id':CLIENT,'name':'Loja fictícia QA Mobile','owner':USER,'state':'PA','city':'Belém','taxId':'11222333000181','stateRegistration':'ISENTO','active':True}),
                ('industry',BRAND,{'id':BRAND,'name':BRAND,'active':True,'notes':'Dados fictícios. Não enviar a fornecedor.'}),
                ('price_table',TABLE,{'id':TABLE,'brand':BRAND,'state':'PA','title':'Tabela fictícia QA','active':True}),
                ('price',TABLE+'|QA001',{'id':TABLE+'|QA001','tableId':TABLE,'brand':BRAND,'state':'PA','sku':'QA001','description':'Produto fictício QA','price':'10.00'})
            ]
            for kind,key,payload in fixtures:
                con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(kind,key,Jsonb(payload)))
        run_checks(module,password)
    module.initialize = initialize

def run_checks(module,password):
    """Real deployed API handlers + PostgreSQL; no physical-device claim."""
    from fastapi.testclient import TestClient
    checks=[]
    try:
        client=TestClient(module.app)
        assert client.get('/api/mobile/v1/snapshot').status_code==401
        checks.append('unauthenticated_denied')
        login=client.post('/api/login',json={'user':USER,'password':password})
        assert login.status_code==200
        token=login.json()['token'];headers={'Authorization':'Bearer '+token}
        checks.append('dedicated_user_login')
        snapshot=client.get('/api/mobile/v1/snapshot',headers=headers)
        assert snapshot.status_code==200
        assert any(x['id']==CLIENT for x in snapshot.json()['clients'])
        checks.append('scoped_snapshot_postgres')
        order={'id':'l2-mobile-qa-order-20261014-v1','clientId':CLIENT,'brand':BRAND,'priceTable':'PA','date':'2026-10-14','status':'Confirmado','sellerResponsible':USER,'notes':'HOMOLOGAÇÃO FICTÍCIA - NÃO ENVIAR','items':[{'brand':BRAND,'tableId':TABLE,'sku':'QA001','quantity':2,'unitPrice':'10.00'}]}
        operation={'changes':[{'changeId':'l2-mobile-qa-operation-20261014-v1','type':'order','data':order,'base':None}]}
        response=client.post('/api/mobile/v1/sync',headers=headers,json=operation)
        assert response.status_code==200 and response.json()['results'][0]['state']=='acknowledged'
        checks.append('order_accepted_real_rules')
        repeat=client.post('/api/mobile/v1/sync',headers=headers,json=operation)
        assert repeat.status_code==200 and repeat.json()['results'][0]==response.json()['results'][0]
        with module.db() as con:
            assert con.execute("SELECT count(*) FROM entities WHERE kind='order' AND id=%s",(order['id'],)).fetchone()[0]==1
        checks.append('idempotent_retry_single_record')
        current=client.get('/api/mobile/v1/snapshot',headers=headers).json()
        assert any(x['id']==order['id'] for x in current['orders'])
        checks.append('order_returned_in_snapshot')
        stale=copy.deepcopy(operation);stale['changes'][0]['changeId']='l2-mobile-qa-stale-price-20261014-v1'
        stale['changes'][0]['data']['id']='l2-mobile-qa-rejected-order-20261014-v1';stale['changes'][0]['data']['items'][0]['unitPrice']='9.00'
        rejected=client.post('/api/mobile/v1/sync',headers=headers,json=stale)
        assert rejected.status_code==200 and rejected.json()['results'][0]['state']=='rejected'
        with module.db() as con:
            assert con.execute("SELECT count(*) FROM entities WHERE kind='order' AND id=%s",(stale['changes'][0]['data']['id'],)).fetchone()[0]==0
        checks.append('stale_price_rejected_without_order')
        client.post('/api/logout',headers=headers,json={})
        assert client.get('/api/mobile/v1/snapshot',headers=headers).status_code==401
        checks.append('session_revocation')
        logging.getLogger('uvicorn.error').info('mobile_qa_result passed=%s checks=%s transport=ASGI database=isolated_postgres physical_device=false',len(checks),','.join(checks))
    except Exception as exc:
        logging.getLogger('uvicorn.error').error('mobile_qa_result failed_after=%s error_type=%s physical_device=false',len(checks),type(exc).__name__)
