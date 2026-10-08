"""Davines FOCO price list, preserving supplier codes and EANs."""
import json
from decimal import Decimal
from psycopg.types.json import Jsonb

def seed_davines(con,base):
    rows=json.loads((base/'davines_catalog_2026.json').read_text(encoding='utf-8'))
    if len(rows)!=222 or len({r['sku'] for r in rows})!=222 or any(Decimal(r['price'])<=0 for r in rows):
        raise RuntimeError('Tabela Davines incompleta ou inválida')
    if not con.execute("INSERT INTO catalog_seeds(name) VALUES('davines-foco-20260519-v1') ON CONFLICT DO NOTHING RETURNING name").fetchone():return
    notes='FOCO Distribuidora. Tabela atualizada em 19/05/2026, recebida em 07/10/2026. Códigos e EAN preservados. Documento não diferencia preços por UF. Metas de venda e clientes positivados devem ser definidas pela gestão.'
    tid='Davines|FOCO-20260519'
    for kind,key,obj in [('industry','Davines',{'id':'Davines','name':'Davines','active':True,'notes':notes}),('price_table',tid,{'id':tid,'brand':'Davines','state':'ALL','title':'Davines · FOCO · 19/05/2026','active':True,'notes':notes})]:
        con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(kind,key,Jsonb(obj)))
    for row in rows:
        key=tid+'|'+row['sku'];obj={'id':key,'brand':'Davines','state':'ALL','tableId':tid,**row}
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('price',%s,%s) ON CONFLICT DO NOTHING",(key,Jsonb(obj)))
