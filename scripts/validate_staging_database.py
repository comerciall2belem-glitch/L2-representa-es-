"""Read-only validation from the staging service's private network.
Run: L2_EXPECTED_DATABASE=l2one_homologacao python scripts/validate_staging_database.py
Never run destructive integration fixtures against this database.
"""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import psycopg
from database_connection import connection_options

def main():
    expected=os.environ.get('L2_EXPECTED_DATABASE','')
    if not expected:
        print(json.dumps({'ok':False,'error':'L2_EXPECTED_DATABASE obrigatório'}));return 2
    dsn=os.environ.get('DATABASE_URL','')
    try:
        with psycopg.connect(dsn,**connection_options(dsn)) as con:
            con.execute('SET TRANSACTION READ ONLY')
            name,readonly,ssl=con.execute("SELECT current_database(),current_setting('transaction_read_only'),coalesce((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false)").fetchone()
            if name!=expected:
                print(json.dumps({'ok':False,'error':'Banco conectado difere do banco esperado'}));return 2
            tables={}
            for table in ('public.entities','public.audit_log','public.expense_receipts','personal_finance.transactions','personal_finance.attachments','personal_finance.audit_events'):
                tables[table]=con.execute('SELECT to_regclass(%s) IS NOT NULL',(table,)).fetchone()[0]
            rls=con.execute("SELECT bool_and(relrowsecurity AND relforcerowsecurity) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='personal_finance' AND c.relname IN ('transactions','attachments','audit_events')").fetchone()[0]
            encrypted_required=os.getenv('DB_SSLMODE') in ('require','verify-ca','verify-full')
            ok=all(tables.values()) and readonly=='on' and rls is True and (ssl or not encrypted_required)
            print(json.dumps(dict(ok=ok,readOnly=readonly=='on',ssl=ssl,tables=tables,personalRls=rls),ensure_ascii=False));return 0 if ok else 1
    except Exception as exc:
        # Exception messages can contain credentials or document data. Log class only.
        print(json.dumps({'ok':False,'errorType':type(exc).__name__,'error':'Falha de conexão ou validação; confira rede interna e configuração do serviço'}));return 1

if __name__=='__main__': raise SystemExit(main())
