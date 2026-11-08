"""Copy commercial history into the named isolated staging DB, never credentials.
Reads L2_IMPORT_SOURCE_DSN and L2_IMPORT_TARGET_DSN; prints counts only.
Default is a read-only preview. --apply is explicit and stores reversible backups.
"""
import argparse
import json
import os
from datetime import date, timedelta
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

TARGET='l2_one_next_staging_db'
CATALOG=('industry','price_table','price','client')
HISTORY=('order','visit','interaction','opportunity','fulfillment')


def preflight(source, target):
    if not source or not target:raise ValueError('Configure conexões de origem e destino no ambiente seguro')
    a,b=conninfo_to_dict(source),conninfo_to_dict(target)
    if b.get('dbname')!=TARGET:raise ValueError('Destino deve ser o banco isolado '+TARGET)
    if a.get('dbname')!='l2one':raise ValueError('Origem esperada: l2one')
    if (a.get('host'),a.get('port'),a.get('dbname'))==(b.get('host'),b.get('port'),b.get('dbname')):raise ValueError('Origem e destino não podem coincidir')


def import_snapshot(source, target, apply=False):
    preflight(source,target)
    today=date.today();since=today-timedelta(days=365)
    with psycopg.connect(source) as src:
        src.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        if src.execute('SELECT current_database()').fetchone()[0]!='l2one':raise ValueError('Banco real da origem não corresponde ao esperado')
        records=src.execute("SELECT kind,id,payload FROM entities WHERE kind=ANY(%s) OR (kind=ANY(%s) AND substring(coalesce(payload->>'date',payload->>'at',payload->>'createdAt',''),1,10)>=%s AND substring(coalesce(payload->>'date',payload->>'at',payload->>'createdAt',''),1,10)<=%s)",(list(CATALOG),list(HISTORY),str(since),str(today))).fetchall()
    counts={k:sum(kind==k for kind,_,_ in records) for k in CATALOG+HISTORY}
    if not apply:return {'preview':True,'counts':counts,'from':str(since),'through':str(today)}
    with psycopg.connect(target) as dst:
        if dst.execute('SELECT current_database()').fetchone()[0]!=TARGET:raise ValueError('Banco real de destino não corresponde ao esperado')
        dst.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
        dst.execute('CREATE TABLE IF NOT EXISTS commercial_import_backup (batch TEXT,kind TEXT,id TEXT,previous JSONB,created_at TIMESTAMPTZ DEFAULT now(),PRIMARY KEY(batch,kind,id))')
        batch=str(today)+'-'+__import__('uuid').uuid4().hex
        for kind,identifier,payload in records:
            dst.execute('INSERT INTO commercial_import_backup(batch,kind,id,previous) SELECT %s,%s,%s,payload FROM entities WHERE kind=%s AND id=%s',(batch,kind,identifier,kind,identifier))
            payload={**payload,'stagingSource':'production-commercial-import','stagingImportBatch':batch}
            dst.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()',(kind,identifier,Jsonb(payload)))
        dst.execute("SELECT pg_notify('l2_records_changed','')")
    return {'preview':False,'counts':counts,'backupBatch':batch,'copiedCredentials':False,'copiedPersonalFinance':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    try:result=import_snapshot(os.getenv('L2_IMPORT_SOURCE_DSN',''),os.getenv('L2_IMPORT_TARGET_DSN',''),args.apply)
    except (ValueError,psycopg.Error) as exc:
        # DSNs and database error bodies can contain credentials; print no provider error text.
        raise SystemExit(str(exc) if isinstance(exc,ValueError) else 'Importação não concluída; revise conectividade e permissões de banco')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
