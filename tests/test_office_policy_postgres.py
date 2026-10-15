"""Real PostgreSQL contract test; writes only into a named disposable QA database."""
import ast,os,unittest
from datetime import date
from pathlib import Path
import psycopg
from psycopg.types.json import Jsonb
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import office_policy as p

@unittest.skipUnless(os.getenv('L2_POLICY_QA_DB'),'Disposable PostgreSQL integration job only')
class PostgreSQLPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url=os.environ['L2_POLICY_QA_DB']
        with psycopg.connect(cls.url) as con:
            if con.execute('SELECT current_database()').fetchone()[0]!='l2_policy_qa':
                raise RuntimeError('Refusing to write outside l2_policy_qa')
            con.execute('CREATE SCHEMA IF NOT EXISTS policy_qa')
        def connection():return psycopg.connect(cls.url,options='-c search_path=policy_qa')
        cls.connection=staticmethod(connection)
        with cls.connection() as con:
            con.execute('CREATE TABLE IF NOT EXISTS app_users(username text PRIMARY KEY,role text,active boolean,sectors jsonb)')
            con.execute('CREATE TABLE IF NOT EXISTS entities(kind text,id text,payload jsonb,updated_at timestamptz DEFAULT now(),PRIMARY KEY(kind,id))')
            con.execute('CREATE TABLE IF NOT EXISTS audit_log(username text,kind text,entity_id text,action text)')
            con.execute('CREATE TABLE IF NOT EXISTS client_seller_authorizations(client_id text,seller_username text REFERENCES app_users(username),approved_by text REFERENCES app_users(username),active boolean,PRIMARY KEY(client_id,seller_username))')
    def setUp(self):
        self.previous=dict(p._services)
        with self.connection() as con:
            con.execute('TRUNCATE client_seller_authorizations,audit_log,entities,app_users')
            for name,role,sectors in [('QA Admin','Administrativo',['admin','finance']),('QA Seller','Vendedor',['commercial']),('QA Promoter','Promotora',['promotion','routes'])]:
                con.execute('INSERT INTO app_users VALUES(%s,%s,true,%s)',(name,role,Jsonb(sectors)))
            rows=[('client','c1',{'id':'c1','name':'FICTICIO','owner':'QA Seller'}),
                  ('client','c2',{'id':'c2','name':'FORA DA CARTEIRA','owner':'QA Seller'}),
                  ('goal','g',{'id':'g','month':'2026-10','user':'Equipe','amount':'1000.00'}),
                  ('order','o1',{'id':'o1','status':'Confirmado','date':'2026-10-01','amount':'600.00'}),
                  ('order','o2',{'id':'o2','status':'Faturado','date':'2026-10-02','amount':'400.00'})]
            for kind,key,value in rows:con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s)',(kind,key,Jsonb(value)))
        def auth(header):
            users={'Bearer qa-admin':'QA Admin','Bearer qa-promoter':'QA Promoter'}
            if header not in users:raise HTTPException(401,'QA auth required')
            return users[header]
        def sectors(user):
            with self.connection() as con:return set(con.execute('SELECT sectors FROM app_users WHERE username=%s',(user,)).fetchone()[0])
        p.configure(db=self.connection,auth=auth,sectors_for=sectors,admin_access=lambda user:user=='QA Admin',today=lambda:date(2026,10,15))
        app=FastAPI();app.include_router(p.router);self.client=TestClient(app)
        self.headers={'Authorization':'Bearer qa-admin'}
    def tearDown(self):p._services.clear();p._services.update(self.previous)
    def test_persistent_eligibility_and_automatic_preview_without_payments(self):
        payload={'month':'2026-09','user':'QA Seller','eligible':True}
        for _ in range(2):
            self.assertEqual(self.client.put('/api/office-policy/bonus/eligibility',headers=self.headers,json=payload).status_code,200)
        for _ in range(2):
            result=self.client.get('/api/office-policy/bonus?month=2026-10',headers=self.headers).json()
            self.assertTrue(result['goalReached']);self.assertEqual(result['members'][0]['bonusCents'],400)
        with self.connection() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM entities WHERE kind='bonus_eligibility'").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT count(*) FROM entities WHERE kind='order'").fetchone()[0],2)
        self.assertEqual(self.client.put('/api/office-policy/bonus/eligibility',headers=self.headers,json={**payload,'month':'2026-10','eligible':False}).status_code,200)
        self.assertEqual(self.client.get('/api/office-policy/bonus?month=2026-10',headers=self.headers).json()['members'][0]['bonusCents'],0)
    def test_portfolio_scope_uses_real_authorizations(self):
        url='/api/office-policy/promoters/QA%20Promoter/clients'
        self.assertEqual(self.client.put(url,headers=self.headers,json={'clientIds':['c1']}).status_code,200)
        self.assertEqual(self.client.get(url,headers=self.headers).json()['clientIds'],['c1'])
        tree=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('is_seller','check_client_scope','scoped_rows')]
        scope={'HTTPException':HTTPException}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<scope>','exec'),scope)
        with self.connection() as con:
            scope['check_client_scope'](con,'QA Promoter','c1')
            with self.assertRaises(HTTPException):scope['check_client_scope'](con,'QA Promoter','c2')
            self.assertEqual([r['id'] for r in scope['scoped_rows'](con,'client','QA Promoter')],['c1'])
    def test_promoter_cannot_read_bonus_or_assign_self(self):
        headers={'Authorization':'Bearer qa-promoter'}
        self.assertEqual(self.client.get('/api/office-policy/bonus?month=2026-10',headers=headers).status_code,403)
        self.assertEqual(self.client.put('/api/office-policy/promoters/QA%20Promoter/clients',headers=headers,json={'clientIds':['c2']}).status_code,403)
        with self.connection() as con:self.assertEqual(con.execute('SELECT count(*) FROM client_seller_authorizations').fetchone()[0],0)

    def enable_and_finish_month(self):
        self.client.put('/api/office-policy/bonus/eligibility',headers=self.headers,
                        json={'month':'2026-10','user':'QA Seller','eligible':True}).raise_for_status()
        p._services['today']=lambda:date(2026,11,1)

    def test_closing_is_atomic_idempotent_and_does_not_modify_orders(self):
        self.enable_and_finish_month()
        with self.connection() as con:
            original=con.execute("SELECT id,payload FROM entities WHERE kind='order' ORDER BY id").fetchall()
        from concurrent.futures import ThreadPoolExecutor
        def close():
            return self.client.post('/api/office-policy/bonus/close',headers=self.headers,json={'month':'2026-10'})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:close(),range(2)))
        self.assertEqual([r.status_code for r in responses],[200,200])
        first=responses[0].json()
        self.assertEqual(first,responses[1].json())
        self.assertTrue(first['meta_escritorio_atingida'])
        self.assertEqual(first['members'][0]['bonusCents'],400)
        with self.connection() as con:
            self.assertEqual(con.execute("SELECT id,payload FROM entities WHERE kind='order' ORDER BY id").fetchall(),original)
            self.assertEqual(con.execute("SELECT count(*) FROM entities WHERE kind='bonus_cycle'").fetchone()[0],1)
            rows=con.execute("SELECT payload FROM entities WHERE kind='bonus_accrual'").fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0][0]['orderId'],'o2')
            self.assertEqual(rows[0][0]['bonusCents'],400)
            self.assertEqual(con.execute("SELECT count(*) FROM audit_log WHERE kind='bonus_cycle'").fetchone()[0],1)
            con.execute("UPDATE entities SET payload=jsonb_set(payload,'{amount}','1') WHERE kind='goal'")
        self.assertEqual(close().json(),first)
        self.assertEqual(self.client.get('/api/office-policy/bonus/closing?month=2026-10',headers=self.headers).json()['closing'],first)

    def test_closing_denies_open_month_unmet_goal_and_forged_flag(self):
        url='/api/office-policy/bonus/close'
        self.assertEqual(self.client.post(url,headers=self.headers,json={'month':'2026-10'}).status_code,409)
        self.enable_and_finish_month()
        self.assertEqual(self.client.post(url,headers=self.headers,json={'month':'2026-10','meta_escritorio_atingida':True}).status_code,422)
        with self.connection() as con:
            con.execute("UPDATE entities SET payload=jsonb_set(payload,'{amount}','2000') WHERE kind='goal'")
        self.assertEqual(self.client.post(url,headers=self.headers,json={'month':'2026-10'}).status_code,409)
        with self.connection() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM entities WHERE kind IN ('bonus_cycle','bonus_accrual')").fetchone()[0],0)

    def test_non_admin_cannot_close_or_change_eligibility(self):
        self.enable_and_finish_month()
        headers={'Authorization':'Bearer qa-promoter'}
        self.assertEqual(self.client.post('/api/office-policy/bonus/close',headers=headers,json={'month':'2026-10'}).status_code,403)
        self.assertEqual(self.client.put('/api/office-policy/bonus/eligibility',headers=headers,json={'month':'2026-10','user':'QA Seller','eligible':True}).status_code,403)

    def test_rounding_allocation_matches_office_total(self):
        self.enable_and_finish_month()
        with self.connection() as con:
            con.execute("DELETE FROM entities WHERE kind='order'")
            con.execute("UPDATE entities SET payload=jsonb_set(payload,'{amount}','1') WHERE kind='goal'")
            for i in range(3):
                obj={'id':'round-'+str(i),'status':'concluído','date':'2026-10-03','amount':'0.50'}
                con.execute("INSERT INTO entities(kind,id,payload) VALUES('order',%s,%s)",(obj['id'],Jsonb(obj)))
        response=self.client.post('/api/office-policy/bonus/close',headers=self.headers,json={'month':'2026-10'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['members'][0]['bonusCents'],2)
        with self.connection() as con:
            rows=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='bonus_accrual'")]
        self.assertEqual(sum(r['bonusCents'] for r in rows),2)
        self.assertEqual(sorted(r['bonusCents'] for r in rows),[0,1,1])
