"""Install additive mobile routes in existing L2 One; no direct industrial writes.

Usage in server.py, AFTER sync/auth definitions:
    import sys
    from mobile_gateway import install
    install(sys.modules[__name__])

The shared connection preserves existing auth, order validation and projections.
No production deployment has been performed by this delivery.
"""
import contextvars
import hashlib
import json
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from fastapi import Header, HTTPException
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

shared = contextvars.ContextVar('l2_mobile_connection', default=None)
KINDS = {'order','visit','task','interaction','closing'}
DROP = {'updatedAt','updatedBy','updatedAs'}
PUBLIC = ('clients','orders','visits','tasks','routes','goals','prices','priceTables',
          'industries','interactions','whatsappTemplates','sellers','role','sectors')

def canonical(value):
    if isinstance(value,dict): return {k:canonical(v) for k,v in sorted(value.items()) if k not in DROP}
    if isinstance(value,list): return [canonical(v) for v in value]
    return value

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),default=str).encode()).hexdigest()

class Change(BaseModel):
    changeId: str = Field(min_length=8,max_length=128)
    type: str
    data: dict
    base: dict | None = None

class Batch(BaseModel):
    changes: list[Change] = Field(min_length=1,max_length=25)

def setup(con):
    con.execute('''CREATE TABLE IF NOT EXISTS mobile_receipts(
      username TEXT NOT NULL, change_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
      result JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(username,change_id))''')
    con.execute('''CREATE TABLE IF NOT EXISTS mobile_closings(
      id TEXT PRIMARY KEY, username TEXT NOT NULL, work_date TEXT NOT NULL,
      revision INTEGER NOT NULL, payload JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(username,work_date,revision))''')

def verify_prices(module,con,order):
    """Compare reviewed price under same ERP advisory lock and transaction.

    Do not silently accept a server-side reprice on reconnect. Existing legacy
    order logic still performs price-table/quantity/minimum validation afterward.
    """
    from decimal import ROUND_HALF_UP
    client=con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s FOR UPDATE",(order.get('clientId'),)).fetchone()
    if not client: raise HTTPException(422,'Cliente não cadastrado')
    uf=module.normalize_uf(client[0].get('state'))
    state=module.normalize_uf(order.get('priceTable'))
    if not module.price_table_matches_client(uf,state):raise HTTPException(422,'Tabela não corresponde à UF do cliente')
    if not order.get('items'):raise HTTPException(422,'Pedido precisa de produtos')
    for item in order['items']:
        if not isinstance(item,dict):raise HTTPException(422,'Item inválido')
        brand=item.get('brand') or order.get('brand')
        table=module.validate_table(con,{**item,'brand':brand,'state':state},brand,state,allowed_states=['PA','AP'] if uf=='AP' else ['PA'])
        con.execute("SELECT id FROM entities WHERE kind='price_table' AND id=%s FOR UPDATE",(table['id'],))
        source={**item,'brand':brand,'tableId':table['id']}
        row=con.execute("SELECT payload FROM entities WHERE kind='price' AND id=%s FOR UPDATE",(module.price_id(source),)).fetchone()
        if not row:raise HTTPException(409,'SKU não possui preço vigente; revise o pedido')
        try:
            expected=Decimal(str(item['unitPrice']))
            actual=Decimal(str(row[0]['price']))
            if not expected.is_finite() or not actual.is_finite() or expected!=actual:
                raise HTTPException(409,'Preço mudou; revise e aprove o pedido antes de reenviar')
        except (KeyError,InvalidOperation,ValueError,TypeError):raise HTTPException(422,'Preço do item inválido')
        # Legacy ERP has no generalized discount engine; do not promise unsupported discounts.
        if item.get('discount') or item.get('discountBps'):raise HTTPException(422,'Desconto exige regra homologada no ERP')

def install(module):
    if getattr(module,'_mobile_installed',False):return
    module._mobile_installed=True
    original_db=module.db
    @contextmanager
    def connection():
        con=shared.get()
        if con is not None:
            yield con
        else:
            with original_db() as own:yield own
    module.db=connection

    def snapshot(authorization):
        data=module.sync(module.Sync(changes=[]),authorization)
        user=module.auth(authorization)
        result={k:data.get(k,[]) for k in PUBLIC}
        with module.db() as con:
            setup(con)
            manager='management' in module.sectors_for(user) and not module.is_seller(con,user)
            rows=con.execute('SELECT payload FROM mobile_closings WHERE (%s OR username=%s) ORDER BY created_at DESC LIMIT 100',(manager,user)).fetchall()
            result['closings']=[r[0] for r in rows]
        result['asOf']=datetime.now(timezone.utc).isoformat()
        result['integrationMode']='guarded_legacy_v1'
        return result

    @module.app.get('/api/mobile/v1/snapshot')
    def mobile_snapshot(authorization:str|None=Header(default=None)):
        return snapshot(authorization)

    @module.app.post('/api/mobile/v1/sync')
    def mobile_sync(data:Batch,authorization:str|None=Header(default=None)):
        user=module.auth(authorization)
        results=[]
        if len(json.dumps(data.model_dump()).encode())>1_000_000:raise HTTPException(413,'Lote acima de 1 MB')
        for change in data.changes:
            if change.type not in KINDS:raise HTTPException(422,'Tipo mobile não autorizado')
            entity_id=change.data.get('id')
            if not isinstance(entity_id,str) or not 1<=len(entity_id)<=128:raise HTTPException(422,'Identificação inválida')
            hashed=digest(change.model_dump())
            try:
                with original_db() as con:
                    setup(con)
                    con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
                    token=shared.set(con)
                    try:
                        # Recheck live session and scope inside the mutation transaction.
                        module.auth(authorization)
                        cached=con.execute('SELECT payload_hash,result FROM mobile_receipts WHERE username=%s AND change_id=%s',(user,change.changeId)).fetchone()
                        if cached:
                            if cached[0]!=hashed:raise HTTPException(409,'Identificador reutilizado com outro conteúdo')
                            results.append(cached[1]);continue
                        if change.type=='closing':
                            module.require_sector(user,'commercial','office')
                            payload=dict(change.data)
                            try:
                                date=datetime.strptime(payload['workDate'],'%Y-%m-%d').date().isoformat()
                                revision=int(payload['revision'])
                                if revision<1:raise ValueError()
                            except (KeyError,TypeError,ValueError):raise HTTPException(422,'Data ou revisão inválida')
                            if len(str(payload.get('summary','')))>4000:raise HTTPException(422,'Resumo acima do limite')
                            payload.update(user=user,workDate=date,revision=revision)
                            existing=con.execute('SELECT payload FROM mobile_closings WHERE id=%s',(entity_id,)).fetchone()
                            if existing:raise HTTPException(409,'Fechamento concluído é imutável; crie nova revisão')
                            duplicate=con.execute('SELECT id FROM mobile_closings WHERE username=%s AND work_date=%s AND revision=%s',(user,date,revision)).fetchone()
                            if duplicate:raise HTTPException(409,'Revisão desse dia já registrada; sincronize e crie a próxima')
                            con.execute('INSERT INTO mobile_closings(id,username,work_date,revision,payload) VALUES(%s,%s,%s,%s,%s)',(entity_id,user,date,revision,Jsonb(payload)))
                            con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'mobile_closing',entity_id,'create'))
                            saved=payload
                        else:
                            module.require_sector(user,'commercial','office')
                            # Never reveal a remote conflict payload before validating client scope.
                            row=con.execute('SELECT payload FROM entities WHERE kind=%s AND id=%s',(change.type,entity_id)).fetchone()
                            previous=row[0] if row else None
                            if previous and not previous.get('clientId') and module.is_seller(con,user) and previous.get('user')!=user:
                                raise HTTPException(403,'Registro pertence a outro responsável')
                            for p in (previous,change.data):
                                if p and p.get('clientId'):module.check_client_scope(con,user,p['clientId'])
                            if canonical(previous)!=canonical(change.base):
                                result={'changeId':change.changeId,'state':'conflict','error':'Registro mudou em outro aparelho; revise as versões.','remote':previous}
                                con.execute('INSERT INTO mobile_receipts(username,change_id,payload_hash,result) VALUES(%s,%s,%s,%s)',(user,change.changeId,hashed,Jsonb(result)))
                                results.append(result);continue
                            if change.type=='order':verify_prices(module,con,change.data)
                            if con.execute('SELECT 1 FROM applied_changes WHERE change_id=%s',(change.changeId,)).fetchone():
                                raise HTTPException(409,'Chave já utilizada fora da fila mobile; revise a operação')
                            module.sync(module.Sync(changes=[module.Change(type=change.type,data=change.data,changeId=change.changeId)]),authorization)
                            saved=con.execute('SELECT payload FROM entities WHERE kind=%s AND id=%s',(change.type,entity_id)).fetchone()[0]
                        result={'changeId':change.changeId,'state':'acknowledged','canonical':saved}
                        con.execute('INSERT INTO mobile_receipts(username,change_id,payload_hash,result) VALUES(%s,%s,%s,%s)',(user,change.changeId,hashed,Jsonb(result)))
                        con.execute("SELECT pg_notify('l2_records_changed','')")
                        results.append(result)
                    finally:shared.reset(token)
            except HTTPException as exc:
                if exc.status_code in (401,403):raise
                results.append({'changeId':change.changeId,'state':'rejected','error':str(exc.detail)})
        return {'results':results,'snapshot':snapshot(authorization)}
