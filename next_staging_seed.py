"""Massa fictícia idempotente, exclusiva ao banco dedicado Next Staging."""
import os
import psycopg
from psycopg.types.json import Jsonb

EXPECTED = 'l2_one_next_staging_db'


def seed(con):
    actual = con.execute('SELECT current_database()').fetchone()[0]
    if actual != EXPECTED or os.getenv('L2_EXPECTED_DATABASE') != EXPECTED:
        raise RuntimeError('Seed recusado: banco não é o Next Staging dedicado')
    clients = [
        {'id':'next-seed-client-01','name':'TESTE Loja Aurora Cosméticos','tradeName':'TESTE Aurora','taxId':'11.111.111/0001-11','owner':'Ana Paula','state':'PA','city':'Belém'},
        {'id':'next-seed-client-02','name':'TESTE Perfumaria Horizonte','tradeName':'TESTE Horizonte','taxId':'22.222.222/0001-22','owner':'Marlene','state':'AP','city':'Macapá'},
        {'id':'next-seed-client-03','name':'TESTE Bela Vitrine','tradeName':'TESTE Bela','taxId':'33.333.333/0001-33','owner':'Ana Paula','state':'PA','city':'Ananindeua'},
    ]
    visits = [
        {'id':'next-seed-visit-01','clientId':'next-seed-client-01','date':'2026-10-26','user':'Ana Paula','result':'Visitado','notes':'Registro fictício de homologação'},
        {'id':'next-seed-visit-02','clientId':'next-seed-client-02','date':'2026-10-25','user':'Marlene','result':'Contato remoto','notes':'Registro fictício de homologação'},
        {'id':'next-seed-visit-03','clientId':'next-seed-client-03','date':'2026-10-24','user':'Ana Paula','result':'Pedido fechado','notes':'Registro fictício de homologação'},
    ]
    orders = [
        {'id':'next-seed-order-01','clientId':'next-seed-client-01','orderNumber':'900001','date':'2026-10-26','status':'Rascunho','amount':1250,'brand':'TESTE'},
        {'id':'next-seed-order-02','clientId':'next-seed-client-02','orderNumber':'900002','date':'2026-10-25','status':'Rascunho','amount':850,'brand':'TESTE'},
    ]
    for kind, rows in [('client', clients), ('visit', visits), ('order', orders)]:
        for row in rows:
            con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO NOTHING', (kind,row['id'],Jsonb(row)))


if __name__ == '__main__':
    if os.getenv('L2_ALLOW_TEST_SEED') != 'yes':
        raise SystemExit('Defina L2_ALLOW_TEST_SEED=yes para instalar apenas registros fictícios')
    with psycopg.connect(os.environ['DATABASE_URL']) as connection:
        seed(connection)
    print('Massa fictícia inserida; registros existentes foram preservados.')
