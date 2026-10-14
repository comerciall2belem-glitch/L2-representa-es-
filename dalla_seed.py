"""Replace only the Dalla catalog using the supplied REDES_VAREJO 2026 sheet."""
import json
from decimal import Decimal
from psycopg.types.json import Jsonb

BRAND = 'Dalla Beauty Fair 2026'
TABLE = BRAND + '|REDES-VAREJO-20261014'
MARKER = 'dalla-redes-varejo-20261014-v1'

def seed_dalla(con, base):
    rows = json.loads((base / 'dalla_catalog_20261014.json').read_text(encoding='utf-8'))
    priced = [r for r in rows if r['price'] is not None]
    pending = [r for r in rows if r['price'] is None]
    if len(rows) != 138 or len({r['sku'] for r in rows}) != 138 or len(priced) != 134:
        raise RuntimeError('Tabela Dalla incompleta')
    if any(not Decimal(r['price']).is_finite() or Decimal(r['price']) <= 0 for r in priced):
        raise RuntimeError('Preço Dalla inválido')
    if not con.execute("INSERT INTO catalog_seeds(name) VALUES(%s) ON CONFLICT DO NOTHING RETURNING name", (MARKER,)).fetchone():
        return
    # Keep an exact recoverable copy of the replaced catalog. Never touch orders.
    con.execute("""INSERT INTO archived_entities(kind,id,payload,reason)
        SELECT kind,id,payload,'substituída pela tabela Dalla Redes Varejo recebida em 14/10/2026'
        FROM entities WHERE kind IN ('price','price_table') AND payload->>'brand'=%s
        ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,reason=excluded.reason,archived_at=now()""", (BRAND,))
    con.execute("DELETE FROM entities WHERE kind IN ('price','price_table') AND payload->>'brand'=%s", (BRAND,))
    table = {'id':TABLE,'brand':BRAND,'state':'ALL','title':'Dalla · Redes Varejo 2026','active':True,
             'sourceFile':'PLANILHA DE PEDIDO REDES_VAREJO .xlsx','sourceSheet':'REDES_VAREJO',
             'priceIncludesTaxes':False,'priceUnit':'UND','missingPriceProducts':pending,
             'notes':'Preços unitários sem imposto da coluna L. Quantidades em unidades; caixas e displays conforme cadastro. 4 SKUs aguardam preço: DL1001-01, DL1001-02, DL1001-03, DL1001-04.'}
    con.execute("INSERT INTO entities(kind,id,payload) VALUES('price_table',%s,%s)", (TABLE,Jsonb(table)))
    for row in priced:
        key = TABLE + '|' + row['sku']
        payload = {'id':key,'brand':BRAND,'state':'ALL','tableId':TABLE,'priceIncludesTaxes':False,'priceUnit':'UND',**row}
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('price',%s,%s)", (key,Jsonb(payload)))
    con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES('Ana Paula','price_table',%s,'replace_dalla_redes_varejo_134_prices_4_pending')", (TABLE,))
    con.execute("SELECT pg_notify('l2_records_changed','')")
