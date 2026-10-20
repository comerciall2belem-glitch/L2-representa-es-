"""One-time, explicitly enabled portfolio transfer requested by Ana Paula."""
import os
from psycopg.types.json import Jsonb

OPERATION = '20261020-anapon-ana-paula-to-erika'
FLAG = 'L2_PORTFOLIO_TRANSFER_20261020'


def transfer(con):
    if os.environ.get(FLAG) != OPERATION:
        return 0
    # Same transaction lock as normal CRM sync; the caller commits all or nothing.
    con.execute('SELECT pg_advisory_xact_lock(%s)', (8239020,))
    con.execute('''CREATE TABLE IF NOT EXISTS portfolio_transfer_runs (
        operation_id TEXT PRIMARY KEY, approved_by TEXT NOT NULL,
        destination TEXT NOT NULL, transferred INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    con.execute('''CREATE TABLE IF NOT EXISTS portfolio_transfer_items (
        operation_id TEXT NOT NULL REFERENCES portfolio_transfer_runs(operation_id),
        client_id TEXT NOT NULL, previous_owner TEXT NOT NULL,
        payload_before JSONB NOT NULL, updated_before TIMESTAMPTZ NOT NULL,
        PRIMARY KEY(operation_id,client_id))''')
    if con.execute('SELECT 1 FROM portfolio_transfer_runs WHERE operation_id=%s', (OPERATION,)).fetchone():
        return 0
    if not con.execute("SELECT 1 FROM app_users WHERE username='Erika' AND active AND role='Vendedor'").fetchone():
        raise RuntimeError('A transferência exige o cadastro ativo da vendedora Erika')
    if not con.execute("SELECT 1 FROM app_users WHERE username='Ana Paula' AND active").fetchone():
        raise RuntimeError('Gestora autorizadora indisponível')
    rows = con.execute("""SELECT id,payload,updated_at FROM entities
        WHERE kind='client' AND lower(btrim(payload->>'owner')) IN ('anapon','ana paula')
        ORDER BY id FOR UPDATE""").fetchall()
    con.execute('INSERT INTO portfolio_transfer_runs(operation_id,approved_by,destination,transferred) VALUES(%s,%s,%s,%s)', (OPERATION,'Ana Paula','Erika',len(rows)))
    for client_id,payload,updated_at in rows:
        con.execute('INSERT INTO portfolio_transfer_items(operation_id,client_id,previous_owner,payload_before,updated_before) VALUES(%s,%s,%s,%s,%s)',
            (OPERATION,client_id,payload.get('owner',''),Jsonb(payload),updated_at))
        con.execute("""UPDATE entities SET payload=payload || jsonb_build_object(
            'owner','Erika','updatedBy','Ana Paula','updatedAt',to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')),
            updated_at=now() WHERE kind='client' AND id=%s""", (client_id,))
        con.execute("""INSERT INTO client_seller_authorizations(client_id,seller_username,approved_by,active)
            VALUES(%s,'Erika','Ana Paula',true) ON CONFLICT(client_id,seller_username)
            DO UPDATE SET active=true,approved_by='Ana Paula',updated_at=now()""", (client_id,))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES('Ana Paula','client',%s,%s)", (client_id,'portfolio_transfer:'+OPERATION))
    if rows:
        con.execute("SELECT pg_notify('l2_records_changed','')")
    return len(rows)
