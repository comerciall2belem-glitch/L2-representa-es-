"""Integração real PostgreSQL; use L2_SEARCH_TEST_DSN somente para banco descartável."""
import os
import unittest
import uuid
from unittest.mock import patch
import psycopg
from psycopg.types.json import Jsonb
import global_search_server as search
import next_staging_seed


@unittest.skipUnless(os.getenv('L2_SEARCH_TEST_DSN'), 'PostgreSQL de teste não configurado')
class GlobalSearchPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = psycopg.connect(os.environ['L2_SEARCH_TEST_DSN'], autocommit=True)
        cls.schema = 'l2_search_test_' + uuid.uuid4().hex
        cls.con.execute('CREATE SCHEMA ' + cls.schema)
        cls.con.execute('SET search_path TO ' + cls.schema)
        cls.con.execute("CREATE TABLE entities(kind text, id text, payload jsonb, updated_at timestamptz default now(), PRIMARY KEY(kind,id))")
        cls.con.execute('CREATE TABLE client_seller_authorizations(client_id text, seller_username text, active boolean)')
        search.create_indexes(cls.con)
        records = [('client','a',{'id':'a','name':'São João Cosméticos','taxId':'12.345.678/0001-90','owner':'Ana'}),
                   ('client','b',{'id':'b','name':'Loja Alegria','owner':'Bia'}),
                   ('order','o1',{'id':'o1','clientId':'a','orderNumber':'713','status':'Fechado'}),
                   ('order','o2',{'id':'o2','clientId':'b','orderNumber':'714'})]
        for kind, id, payload in records:
            cls.con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s)',(kind,id,Jsonb(payload)))
        cls.con.execute("INSERT INTO client_seller_authorizations VALUES('b','Ana',true)")

    @classmethod
    def tearDownClass(cls):
        cls.con.execute('DROP SCHEMA ' + cls.schema + ' CASCADE')
        cls.con.close()

    def test_accent_prefix_and_order_matching(self):
        r = search.search(self.con, 'sao jo', 'Ana', True)
        self.assertEqual([c['id'] for c in r['clients']], ['a'])
        self.assertEqual([o['id'] for o in r['orders']], ['o1'])

    def test_cnpj_normalization(self):
        r = search.search(self.con, '12.345.678/0001-90', 'Ana', True)
        self.assertEqual([c['id'] for c in r['clients']], ['a'])

    def test_seller_scope_and_explicit_authorization(self):
        self.assertEqual(search.search(self.con, '713', 'Bia', True)['orders'], [])
        self.assertEqual(search.search(self.con, '714', 'Ana', True)['orders'][0]['id'], 'o2')
        self.assertEqual(search.search(self.con, '713', 'Admin', False)['orders'][0]['id'], 'o1')

    def test_query_is_not_sql_or_tsquery_injection(self):
        self.assertEqual(search.search(self.con, "' | !:*", 'Ana', True)['clients'], [])
        self.assertRaises(ValueError, search.query_tokens, 'a'*81)

    def test_indexes_exist_and_are_usable(self):
        indexes = {r[0] for r in self.con.execute('SELECT indexname FROM pg_indexes WHERE schemaname=%s',(self.schema,))}
        self.assertTrue({'l2_search_client_v1','l2_search_order_v1'} <= indexes)
        self.con.execute("INSERT INTO entities(kind,id,payload) SELECT 'client', 'bulk-' || i, jsonb_build_object('name', 'Loja comum ' || i) FROM generate_series(1,5000) i ON CONFLICT DO NOTHING")
        self.con.execute('ANALYZE entities')
        self.con.execute('SET enable_seqscan=off')
        plan = '\n'.join(row[0] for row in self.con.execute(f"EXPLAIN SELECT * FROM entities c WHERE c.kind='client' AND {search.CLIENT_DOCUMENT} @@ to_tsquery('simple'::regconfig, 'sao:*')"))
        self.assertIn('l2_search_client_v1', plan)
        self.con.execute('RESET enable_seqscan')

    def test_seed_is_restricted_and_idempotent(self):
        with patch.dict(os.environ, {'L2_EXPECTED_DATABASE': 'wrong'}):
            with self.assertRaises(RuntimeError):
                next_staging_seed.seed(self.con)
        with patch.dict(os.environ, {'L2_EXPECTED_DATABASE': next_staging_seed.EXPECTED}):
            next_staging_seed.seed(self.con)
            next_staging_seed.seed(self.con)
        count = self.con.execute("SELECT count(*) FROM entities WHERE id LIKE 'next-seed-%'").fetchone()[0]
        self.assertEqual(count, 8)
