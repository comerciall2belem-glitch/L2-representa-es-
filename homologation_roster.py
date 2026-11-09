"""Provision active sales staff with independent credentials in isolated staging only."""
import secrets
import os
from psycopg.types.json import Jsonb

DATABASE = 'l2_one_next_staging_db'
SERVICE = 'srv-dbgcn4mgekts738hi3ig'
ACTIVE_SELLERS = ('Euler', 'Erika', 'Anapon', 'Danyelle Oliveira', 'MB')


def seed(connect, hash_password):
    if os.getenv('RENDER_SERVICE_ID') != SERVICE:
        return False
    if os.getenv('L2_EXPECTED_DATABASE') != DATABASE or os.getenv('RENDER_SERVICE_ID') != SERVICE:
        raise RuntimeError('Cadastro de homologação recusado fora do ambiente isolado')
    with connect() as con:
        if con.execute('SELECT current_database()').fetchone()[0] != DATABASE:
            raise RuntimeError('Banco real de homologação divergente')
        for name in ACTIVE_SELLERS:
            old = con.execute('SELECT active FROM app_users WHERE username=%s FOR UPDATE', (name,)).fetchone()
            if old:
                if not old[0]:
                    raise RuntimeError('Vendedor existente está inativo na homologação')
                # Preserve existing passwords, roles, sectors and sessions, including Euler.
                continue
            sectors = ['commercial', 'clients_edit', 'routes']
            if name == 'Euler':
                raise RuntimeError('Conta preexistente de Euler ausente; revisar permissões')
            con.execute('INSERT INTO app_users(username,password_hash,role,sectors,department,must_change_password) VALUES(%s,%s,%s,%s,%s,true)',
                        (name, hash_password(secrets.token_urlsafe(48)), 'Vendedor', Jsonb(sectors), 'Comercial — homologação'))
    return True
