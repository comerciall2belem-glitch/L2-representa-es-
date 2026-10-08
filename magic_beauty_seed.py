"""Magic Beauty price list, preserving supplier codes and EANs."""
import json
from decimal import Decimal
from psycopg.types.json import Jsonb

def seed_magic_beauty(con,base):
    rows=json.loads((base/'magic_beauty_catalog_2026.json').read_text(encoding='utf-8'))
    if len(rows)!=73 or len({r['sku'] for r in rows})!=73 or any(Decimal(r['price'])<=0 for r in rows):
        raise RuntimeError('Tabela Magic Beauty incompleta ou inválida')
    if not con.execute("INSERT INTO catalog_seeds(name) VALUES('magic-beauty-20261007-v1') ON CONFLICT DO NOTHING RETURNING name").fetchone():return
    notes='Tabela recebida em 07/10/2026. Códigos e EAN preservados. Documento não diferencia preços por UF. Metas de venda e clientes positivados seguem a configuração comum por indústria. Prazos, mínimos, frete e comissão não informados na tabela.'
    tid='Magic Beauty|CATALOGO-20261007'
    for kind,key,obj in [('industry','Magic Beauty',{'id':'Magic Beauty','name':'Magic Beauty','active':True,'notes':notes}),('price_table',tid,{'id':tid,'brand':'Magic Beauty','state':'ALL','title':'Magic Beauty · Tabela recebida 07/10/2026','active':True,'notes':notes})]:
        con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(kind,key,Jsonb(obj)))
    for row in rows:
        key=tid+'|'+row['sku'];obj={'id':key,'brand':'Magic Beauty','state':'ALL','tableId':tid,**row}
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('price',%s,%s) ON CONFLICT DO NOTHING",(key,Jsonb(obj)))
