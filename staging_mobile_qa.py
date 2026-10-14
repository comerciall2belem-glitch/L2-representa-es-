"""Temporary QA seed, permitted only on an explicitly named isolated database."""
import os
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
    module.initialize = initialize
