import json
import unittest
from unittest.mock import patch
import homologation_roster as h

class RosterTests(unittest.TestCase):
    def test_disabled(self):
        with patch.dict('os.environ', {'L2_HOMOLOGATION_SELLERS_JSON': ''}):
            self.assertFalse(h.seed(lambda: self.fail('DB accessed'), str))

    def test_guards(self):
        for service, database in [('production', h.DATABASE), (h.SERVICE, 'l2one')]:
            with patch.dict('os.environ', {'L2_HOMOLOGATION_SELLERS_JSON': '{}', 'RENDER_SERVICE_ID': service, 'L2_EXPECTED_DATABASE': database}):
                with self.assertRaises(RuntimeError):
                    h.seed(lambda: self.fail('DB accessed'), str)

    def test_complete_roster_required(self):
        with patch.dict('os.environ', {'L2_HOMOLOGATION_SELLERS_JSON': json.dumps({'Erika': 'x'*40}), 'RENDER_SERVICE_ID': h.SERVICE, 'L2_EXPECTED_DATABASE': h.DATABASE}):
            with self.assertRaises(RuntimeError):
                h.seed(lambda: self.fail('DB accessed'), str)

    def test_preserves_existing_accounts_and_inserts_sellers(self):
        class Cursor:
            def __init__(self, value): self.value = value
            def fetchone(self): return self.value
        class Connection:
            def __init__(self): self.inserted = []
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, sql, args=()):
                if 'current_database' in sql: return Cursor((h.DATABASE,))
                if sql.startswith('SELECT active'): return Cursor((True,) if args[0] == 'Euler' else None)
                if sql.startswith('INSERT'): self.inserted.append(args); return Cursor(None)
                raise AssertionError('Unexpected mutation')
        con = Connection()
        env = {'L2_HOMOLOGATION_SELLERS_JSON': json.dumps({name:'x'*40 for name in h.ACTIVE_SELLERS}), 'RENDER_SERVICE_ID': h.SERVICE, 'L2_EXPECTED_DATABASE': h.DATABASE}
        with patch.dict('os.environ', env):
            self.assertTrue(h.seed(lambda:con, lambda password:'hash'))
        self.assertEqual([r[0] for r in con.inserted], list(h.ACTIVE_SELLERS[1:]))
        self.assertTrue(all('finance' not in r[3].obj for r in con.inserted))

import os
import uuid
import psycopg

@unittest.skipUnless(os.getenv('L2_SEARCH_TEST_DSN'), 'PostgreSQL de teste não configurado')
class RosterPostgresTests(unittest.TestCase):
    def test_restart_preserves_passwords_permissions_and_accounts(self):
        dsn = os.environ['L2_SEARCH_TEST_DSN']
        schema = 'roster_' + uuid.uuid4().hex
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'CREATE SCHEMA {schema}')
            try:
                def connect(): return psycopg.connect(dsn, options=f'-c search_path={schema}')
                with connect() as con:
                    con.execute('CREATE TABLE app_users(username TEXT PRIMARY KEY,password_hash TEXT,active BOOLEAN DEFAULT true,role TEXT,sectors JSONB,department TEXT,must_change_password BOOLEAN DEFAULT false)')
                    con.execute("INSERT INTO app_users VALUES('Euler','original',true,'Vendedor','[\"commercial\",\"finance\"]','Comercial',false)")
                env = {'L2_HOMOLOGATION_SELLERS_JSON': json.dumps({name:'x'*40 for name in h.ACTIVE_SELLERS}), 'RENDER_SERVICE_ID': h.SERVICE, 'L2_EXPECTED_DATABASE': h.DATABASE}
                with patch.dict('os.environ', env):
                    h.seed(connect, lambda p:'newhash')
                    h.seed(connect, lambda p:'differenthash')
                with connect() as con:
                    rows = con.execute('SELECT username,password_hash,sectors,must_change_password FROM app_users ORDER BY username').fetchall()
                self.assertEqual(len(rows), 5)
                for name, password, sectors, provisional in rows:
                    if name == 'Euler':
                        self.assertEqual(password, 'original')
                        self.assertIn('finance', sectors)
                        self.assertFalse(provisional)
                    else:
                        self.assertEqual(password, 'newhash')
                        self.assertNotIn('finance', sectors)
                        self.assertTrue(provisional)
            finally:
                admin.execute(f'DROP SCHEMA {schema} CASCADE')
