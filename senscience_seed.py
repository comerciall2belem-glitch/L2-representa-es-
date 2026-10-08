"""Source-backed Senscience catalog, with explicitly internal product identifiers."""
import json
from decimal import Decimal
from psycopg.types.json import Jsonb

def seed_senscience(con, base):
    rows=json.loads((base/'senscience_catalog_2026.json').read_text(encoding='utf-8'))
    if len(rows)!=34 or len({x['sku'] for x in rows})!=34 or any(Decimal(x['price'])<=0 for x in rows):
        raise RuntimeError('Catálogo Senscience incompleto, duplicado ou com preço inválido')
    marker='senscience-catalog-20260120-v1'
    if not con.execute('INSERT INTO catalog_seeds(name) VALUES(%s) ON CONFLICT DO NOTHING RETURNING name',(marker,)).fetchone():
        return
    notes='Catálogo recebido em 07/10/2026, atualizado em 20/01/2026. SKUs L2-SNC são internos; códigos do fabricante não informados. Preços únicos do documento para PA/AP, sem acréscimos. Não cadastrar itens riscados: Renewal Shampoo 280 ml, Renewal Reconstructive Mask 150 ml e CPR Step 0. Inner Restore Intensif 50 ml: preço impresso R$ 261,27, requer conferência. Prazos, frete, mínimos e comissão não informados.'
    industry={'id':'Senscience','name':'Senscience','active':True,'notes':notes,'commercialPolicy':[]}
    tid='Senscience|CATALOGO-20260120'
    table={'id':tid,'brand':'Senscience','state':'ALL','title':'Senscience — Catálogo atualizado 20/01/2026','active':True,'notes':notes}
    for kind,key,payload in [('industry','Senscience',industry),('price_table',tid,table)]:
        con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(kind,key,Jsonb(payload)))
    for row in rows:
        key=tid+'|'+row['sku']
        payload={'id':key,'brand':'Senscience','state':'ALL','tableId':tid,**row}
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('price',%s,%s) ON CONFLICT DO NOTHING",(key,Jsonb(payload)))
