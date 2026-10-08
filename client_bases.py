"""Protected, idempotent imports of commercial source bases into native clients."""
import base64
import gzip
import hashlib
import io
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from psycopg.types.json import Jsonb

BASE='Bella Brazil'
ENV='L2_BELLA_CLIENTS_B64'
def digits(value):return re.sub(r'\D','',str(value or ''))
def norm(value):return ' '.join(unicodedata.normalize('NFKD',str(value or '')).encode('ascii','ignore').decode().casefold().split())

def prepare(bundle,valid_cnpj):
    if not isinstance(bundle,dict) or not isinstance(bundle.get('rows'),list) or not 1<=len(bundle['rows'])<=2000:
        raise ValueError('Carga de clientes inválida')
    grouped={}
    for row in bundle['rows']:
        if not isinstance(row,dict):raise ValueError('Linha inválida')
        document=digits(row.get('document'))
        if len(document) not in (11,14) or (len(document)==14 and not valid_cnpj(document)):
            raise ValueError('Documento inválido')
        if row.get('state') not in ('PA','AP') or not str(row.get('city') or '').strip() or not 2<=len(str(row.get('name') or '').strip())<=180:
            raise ValueError('Identificação ou localidade inválida')
        grouped.setdefault(document,[]).append(dict(row,document=document))
    return grouped

def matching_client(source,existing):
    document=source['document']
    exact=[x for x in existing if digits(x.get('taxId'))==document or digits((x.get('sourceDocument') or {}).get('value'))==document]
    if len(exact)>1:raise ValueError('Documento duplicado na carteira atual')
    if exact:return exact[0]
    # A legacy record without a fiscal ID can be joined only by a precise address.
    legacy=[x for x in existing if not digits(x.get('taxId')) and source.get('address') and
        all(norm(x.get(k))==norm(source.get(k)) for k in ('name','city','state','address'))]
    if len(legacy)>1:raise ValueError('Cadastro legado ambíguo')
    return legacy[0] if legacy else None

def merge_client(source_rows,old=None):
    source=max(source_rows,key=lambda r:r.get('sourcePurchaseDate') or '')
    document=source['document'];old=old or {}
    fresh={'id':'bella-'+hashlib.sha256(document.encode()).hexdigest()[:32],
        'owner':'Ana Paula','origin':'Base Bella Brazil','name':source['name'],'state':source['state'],
        'city':source['city'],'taxId':document if len(document)==14 else '',
        'sourceDocument':{'type':'CNPJ' if len(document)==14 else 'CPF','value':document},
        'stateRegistration':'','channel':'','classification':'','last_purchase':'',
        **{k:source.get(k,'') for k in ('phone','address','district','postalCode','email')}}
    fresh['email']=re.split(r'[;,]',fresh['email'])[0].strip()
    result={**fresh,**old}
    # Existing identity, assignment, contacts and commercial history take precedence.
    for key in ('taxId','phone','address','district','postalCode','email','city','state'):
        if not result.get(key) and fresh.get(key):result[key]=fresh[key]
    result['clientBases']=sorted(set(old.get('clientBases') or [])|{BASE})
    result['baseImports']={**(old.get('baseImports') or {}),'bella_brazil':{
        'source':'Clientes PA.xlsx','sourceRows':source_rows,'importedAt':datetime.now(timezone.utc).isoformat()}}
    return result

def import_bella(db,valid_cnpj,encoded=None):
    encoded=os.getenv(ENV,'') if encoded is None else encoded
    if not encoded:return None
    if len(encoded)>300_000:raise ValueError('Carga excedeu limite')
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(encoded,validate=True))) as stream:
        raw=stream.read(1_000_001)
    if len(raw)>1_000_000:raise ValueError('Carga excedeu limite')
    bundle=json.loads(raw);grouped=prepare(bundle,valid_cnpj)
    identifier='bella-'+hashlib.sha256(raw).hexdigest()
    with db() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)',(8239021,))
        previous=con.execute("SELECT payload FROM entities WHERE kind='client_base_import' AND id=%s",(identifier,)).fetchone()
        if previous:return {**previous[0],'alreadyApplied':True}
        existing=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='client'").fetchall()]
        created=updated=0
        for sources in grouped.values():
            old=matching_client(sources[0],existing);item=merge_client(sources,old)
            if con.execute("SELECT 1 FROM archived_entities WHERE kind='client' AND id=%s",(item['id'],)).fetchone():
                raise ValueError('Cadastro arquivado exige revisão')
            con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()',('client',item['id'],Jsonb(item)))
            con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES('Ana Paula','client',%s,'import_bella_base')",(item['id'],))
            created+=not bool(old);updated+=bool(old)
            if not old:existing.append(item)
        summary={'id':identifier,'base':BASE,'sourceRows':len(bundle['rows']),'uniqueClients':len(grouped),
            'created':created,'updated':updated,'duplicateRows':len(bundle['rows'])-len(grouped),
            'cpfRecords':sum(len(d)==11 for d in grouped),'at':datetime.now(timezone.utc).isoformat()}
        con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s)',('client_base_import',identifier,Jsonb(summary)))
        con.execute("SELECT pg_notify('l2_records_changed','')")
    return summary
