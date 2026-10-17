"""Connection policy shared by web, async record feed and reporting."""
import os
from psycopg.conninfo import conninfo_to_dict

SSL_LEVELS = {'require': 1, 'verify-ca': 2, 'verify-full': 3}

def connection_options(dsn, environ=None):
    env=os.environ if environ is None else environ
    try:
        parsed=conninfo_to_dict(dsn)
    except Exception:
        raise ValueError('DATABASE_URL inválida; confira a configuração do serviço') from None
    options={}
    raw=env.get('DB_TIMEOUT')
    if raw is not None:
        try:
            if not str(raw).isdigit(): raise ValueError()
            millis=int(raw)
            if not 1000<=millis<=300000: raise ValueError()
        except (TypeError,ValueError):
            raise ValueError('DB_TIMEOUT deve ser inteiro em milissegundos, entre 1000 e 300000') from None
        options['connect_timeout']=(millis+999)//1000
    elif 'connect_timeout' not in parsed:
        options['connect_timeout']=30
    ssl=env.get('DB_SSLMODE')
    if ssl is not None:
        if ssl not in SSL_LEVELS:
            raise ValueError('DB_SSLMODE deve ser require, verify-ca ou verify-full')
        # A new require policy never weakens certificate verification already in the URL.
        existing=parsed.get('sslmode','')
        options['sslmode']=existing if SSL_LEVELS.get(existing,0)>SSL_LEVELS[ssl] else ssl
    return options


def validate_staging_database(dsn,expected,environ=None):
    import psycopg
    if not expected: raise ValueError('L2_EXPECTED_DATABASE obrigatório')
    env=os.environ if environ is None else environ
    with psycopg.connect(dsn,**connection_options(dsn,env)) as con:
        con.execute('SET TRANSACTION READ ONLY')
        name,readonly,ssl=con.execute("SELECT current_database(),current_setting('transaction_read_only'),coalesce((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false)").fetchone()
        if name!=expected:
            raise ValueError('Banco conectado difere do banco esperado')
        tables={}
        for table in ('public.entities','public.audit_log','personal_finance.transactions','personal_finance.attachments','personal_finance.audit_events'):
            tables[table]=con.execute('SELECT to_regclass(%s) IS NOT NULL',(table,)).fetchone()[0]
        rls=con.execute("SELECT bool_and(relrowsecurity AND relforcerowsecurity) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='personal_finance' AND c.relname IN ('transactions','attachments','audit_events')").fetchone()[0]
        encrypted_required=env.get('DB_SSLMODE') in ('require','verify-ca','verify-full')
        ok=all(tables.values()) and readonly=='on' and rls is True and (ssl or not encrypted_required)
        return dict(ok=ok,readOnly=readonly=='on',ssl=ssl,tables=tables,personalRls=rls)
