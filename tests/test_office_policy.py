import unittest
from datetime import date
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import office_policy as p

class BonusTests(unittest.TestCase):
    def setUp(self):
        self.orders=[{'id':'a','status':'Confirmado','date':'2026-10-01','amount':'600.00'},
                     {'id':'b','status':'Faturado','date':'2026-10-02','amount':'400.00'}]
        self.goals=[{'id':'g','month':'2026-10','user':'Equipe','brand':'','amount':'1000.00'}]
    def calc(self,**changes):
        args=dict(orders=self.orders,goals=self.goals,members=['Erika','MB'],eligible={'Erika'},month='2026-10',today=date(2026,10,15))
        args.update(changes);return p.calculate_bonus(**args)
    def test_reached_only_eligible_billed_base(self):
        r=self.calc();self.assertTrue(r['goalReached'])
        self.assertEqual(r['members'],[{'user':'Erika','eligible':True,'bonusCents':400},{'user':'MB','eligible':False,'bonusCents':0}])
    def test_below_goal_and_no_goal_and_zero(self):
        for goals in ([],[{**self.goals[0],'amount':0}],[{**self.goals[0],'amount':'1000.01'}]):
            r=self.calc(goals=goals);self.assertFalse(r['goalReached']);self.assertEqual(r['members'][0]['bonusCents'],0)
    def test_no_industry_goal_inference(self):
        self.assertIsNone(self.calc(goals=[{**self.goals[0],'brand':'BT'}])['goalCents'])
    def test_no_eligibility_inference(self):
        self.assertEqual(self.calc(eligible=set())['members'][0]['bonusCents'],0)
    def test_cancelled_draft_future_other_month_excluded(self):
        extra=[{**self.orders[1],'id':str(i),'status':status,'date':day} for i,(status,day) in enumerate([('Cancelado','2026-10-01'),('Orçamento','2026-10-01'),('Faturado','2026-11-01'),('Faturado','2026-10-31')])]
        self.assertEqual(self.calc(orders=self.orders+extra)['billedCents'],40000)
    def test_progression_history_and_duplicates(self):
        o={**self.orders[0],'stageHistory':[{'stage':'Faturado'}]}
        self.assertEqual(self.calc(orders=[o,o,self.orders[1]])['billedCents'],100000)
    def test_rounding_and_replay_no_postings(self):
        o=[{'id':'x','amount':'0.50','status':'Faturado','date':'2026-10-01'}]
        g=[{**self.goals[0],'amount':'0.50'}]
        r=self.calc(orders=o,goals=g);self.assertEqual(r['members'][0]['bonusCents'],1)
        self.assertEqual(r,self.calc(orders=o,goals=g))
    def test_invalid_money_and_period_rejected(self):
        for value in ('NaN','Infinity','-1',True):
            with self.subTest(value=value),self.assertRaises(HTTPException):p.cents(value)
        for month in ('2026-13','0000-10','2026-1'):
            with self.subTest(month=month),self.assertRaises(HTTPException):p.period(month)

class Cursor:
    def __init__(self,one=None,rows=()):self.one,self.rows=one,rows
    def fetchone(self):return self.one
    def __iter__(self):return iter(self.rows)
class DB:
    def __init__(self):self.writes=[];self.existing=None
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args=()):
        if sql.startswith('SELECT payload FROM entities WHERE kind=%s'):return Cursor(self.existing)
        if sql.startswith("SELECT username FROM app_users"):return Cursor(rows=[('Erika',),('MB',)])
        if "kind='order'" in sql:return Cursor(rows=[({'id':'o','date':'2026-10-01','status':'Faturado','amount':'1000.00'},)])
        if "kind='goal'" in sql:return Cursor(rows=[({'id':'g','month':'2026-10','user':'Equipe','amount':'1000.00'},)])
        if "kind='bonus_eligibility'" in sql and sql.startswith('SELECT'):return Cursor(rows=[({'month':'2026-09','user':'Erika','eligible':True},)])
        if "kind='fulfillment'" in sql:return Cursor(rows=[])
        if "active AND role='Vendedor'" in sql:return Cursor((1,) if args[0]=='Erika' else None)
        if "active AND role='Promotora'" in sql:return Cursor((1,) if args[0]=='Promo' else None)
        if "kind='client' AND id=%s" in sql:return Cursor((1,) if args[0]=='c1' else None)
        if sql.startswith('SELECT client_id'):return Cursor(rows=[('c1',)])
        if sql.startswith('SELECT role FROM app_users'):return Cursor(('Promotora',))
        self.writes.append((sql,args));return Cursor()
class PromoterTests(unittest.TestCase):
    def test_no_financial_or_admin_http_routes(self):
        for method,path in [('GET','/api/orders/x/invoices'),('POST','/api/inventory/movements'),('GET','/api/office-policy/bonus'),('POST','/api/whatsapp/text'),('GET','/api/finance/analysis')]:
            self.assertFalse(p.promoter_route_allowed(method,path))
        self.assertTrue(p.promoter_route_allowed('POST','/api/sync'))
    def test_only_field_changes(self):
        con=DB()
        for kind in ('order','delete_order','client','goal','fulfillment','price'):
            with self.subTest(kind=kind),self.assertRaises(HTTPException):p.validate_promoter_change(con,'Promo',kind,{'id':'x'})
        for kind in ('visit','task','route','interaction'):
            obj={'id':'x'};p.validate_promoter_change(con,'Promo',kind,obj)
            self.assertEqual(obj['owner' if kind=='interaction' else 'user'],'Promo')
    def test_cannot_overwrite_or_spoof_actor(self):
        con=DB()
        with self.assertRaises(HTTPException):p.validate_promoter_change(con,'Promo','visit',{'id':'x','user':'Other'})
        con.existing=({'user':'Other'},)
        with self.assertRaises(HTTPException):p.validate_promoter_change(con,'Promo','visit',{'id':'x'})
    def test_snapshot_restricts_financial_team_and_private_costs(self):
        r=p.promoter_snapshot({'orders':[{'amount':100}],'crmTeam':{'all':'secret'},'sellerCommissions':[{'rate':2.5}],'clients':[{'id':'c1','name':'Client','creditLimit':200}],'prices':[{'id':'x','price':10,'cost':2,'commissionRate':10}],'visits':[{'user':'Promo'},{'user':'Other'}]},'Promo')
        self.assertEqual(r['orders'],[]);self.assertEqual(r['sellerCommissions'],[]);self.assertEqual(r['crmTeam'],{})
        self.assertNotIn('creditLimit',r['clients'][0]);self.assertNotIn('cost',r['prices'][0]);self.assertEqual(len(r['visits']),1)

class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.previous=dict(p._services)
        self.db=DB();self.user='Ana Paula'
        p.configure(db=lambda:self.db,auth=lambda _:self.user,sectors_for=lambda user:{'finance'} if user=='Ana Paula' else set(),admin_access=lambda user:user=='Ana Paula',today=lambda:date(2026,10,15))
        app=FastAPI();app.include_router(p.router);self.client=TestClient(app)
    def tearDown(self):
        p._services.clear();p._services.update(self.previous)
    def test_no_finance_cannot_read_or_change_bonus(self):
        self.user='Promo'
        self.assertEqual(self.client.get('/api/office-policy/bonus?month=2026-10').status_code,403)
        self.assertEqual(self.client.put('/api/office-policy/bonus/eligibility',json={'month':'2026-10','user':'Erika','eligible':True}).status_code,403)
        self.assertFalse(self.db.writes)
    def test_eligibility_validated_audited_not_auto_granted(self):
        self.assertEqual(self.client.put('/api/office-policy/bonus/eligibility',json={'month':'2026-10','user':'Promo','eligible':True}).status_code,400)
        r=self.client.put('/api/office-policy/bonus/eligibility',json={'month':'2026-10','user':'Erika','eligible':True})
        self.assertEqual(r.status_code,200);self.assertTrue(any('audit_log' in sql for sql,_ in self.db.writes))
        self.assertFalse(any('cash' in sql for sql,_ in self.db.writes))
    def test_strict_boolean(self):
        self.assertEqual(self.client.put('/api/office-policy/bonus/eligibility',json={'month':'2026-10','user':'Erika','eligible':'true'}).status_code,422)
    def test_promoter_assignment_validates_before_writes(self):
        self.assertEqual(self.client.put('/api/office-policy/promoters/Promo/clients',json={'clientIds':['missing']}).status_code,400)
        self.assertFalse(self.db.writes)
        self.assertEqual(self.client.put('/api/office-policy/promoters/Promo/clients',json={'clientIds':['c1']}).status_code,200)
        self.assertTrue(any('approved_by' in sql for sql,_ in self.db.writes))
    def test_promoter_cannot_grant_self_portfolio(self):
        self.user='Promo'
        self.assertEqual(self.client.put('/api/office-policy/promoters/Promo/clients',json={'clientIds':['c1']}).status_code,403)
        self.assertFalse(self.db.writes)

class EligibilityHistoryTests(unittest.TestCase):
    def test_applies_forward_until_revoked(self):
        rows=[{'user':'Erika','month':'2026-09','eligible':True},{'user':'Erika','month':'2026-11','eligible':False}]
        self.assertEqual(p.eligibility_for_month(rows,'2026-10'),{'Erika'})
        self.assertEqual(p.eligibility_for_month(rows,'2026-11'),set())
        self.assertEqual(p.eligibility_for_month(rows,'2026-08'),set())

class BonusReadTests(unittest.TestCase):
    setUp=EndpointTests.setUp
    tearDown=EndpointTests.tearDown
    def test_authorized_preview_reads_current_database(self):
        r=self.client.get('/api/office-policy/bonus?month=2026-10')
        self.assertEqual(r.status_code,200)
        data=r.json()
        self.assertTrue(data['goalReached'])
        self.assertEqual(data['members'][0]['bonusCents'],1000)
        self.assertEqual(data['members'][1]['bonusCents'],0)
        self.assertFalse(self.db.writes)

class MemberValidationTests(unittest.TestCase):
    def test_promoter_has_dedicated_sectors(self):
        import ast,re,unicodedata
        from pathlib import Path
        source=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
        node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='validate_member')
        scope={'HTTPException':HTTPException,'re':re,'unicodedata':unicodedata,
               'SECTORS':{'promotion','routes','commercial','finance','admin','clients_edit','catalog','office','management'},
               'COMMERCIAL_SECTORS':{'commercial','clients_edit','routes'}}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<member>','exec'),scope)
        self.assertEqual(scope['validate_member']('Promo','Promotora',['promotion','routes']),'Promo')
        for sectors in (['commercial'],['promotion','finance'],['promotion','admin']):
            with self.subTest(sectors=sectors),self.assertRaises(HTTPException):scope['validate_member']('Promo','Promotora',sectors)

class PromoterMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.previous=dict(p._services)
    async def asyncTearDown(self):p._services.clear();p._services.update(self.previous)
    async def test_main_api_firewall_blocks_non_field_routes(self):
        import ast,asyncio
        from pathlib import Path
        from types import SimpleNamespace
        from fastapi.responses import JSONResponse
        source=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
        node=next(n for n in source.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='restrict_promoter_api')
        node.decorator_list=[]
        namespace={'office_policy':p,'asyncio':asyncio,'HTTPException':HTTPException,'JSONResponse':JSONResponse,'Request':object}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<firewall>','exec'),namespace)
        p.configure(db=lambda:DB(),auth=lambda token:'Promo')
        async def next_handler(request):return 'next'
        request=SimpleNamespace(url=SimpleNamespace(path='/api/orders/o/invoices'),headers={'authorization':'Bearer token'},method='GET')
        blocked=await namespace['restrict_promoter_api'](request,next_handler)
        self.assertEqual(blocked.status_code,403)
        request.url.path='/api/sync';request.method='POST'
        self.assertEqual(await namespace['restrict_promoter_api'](request,next_handler),'next')


class SchedulerSecurityTests(unittest.TestCase):
    def test_missing_or_wrong_job_secret_fails_closed(self):
        from unittest.mock import patch
        import os
        with patch.dict(os.environ, {'L2_BONUS_JOB_TOKEN':''}):
            with self.assertRaises(HTTPException) as e:p.close_due_bonus('anything')
            self.assertEqual(e.exception.status_code,503)
        with patch.dict(os.environ, {'L2_BONUS_JOB_TOKEN':'x'*40}):
            for token in (None,'wrong'):
                with self.assertRaises(HTTPException) as e:p.close_due_bonus(token)
                self.assertEqual(e.exception.status_code,401)

    def test_job_identity_requires_admin_and_finance(self):
        from unittest.mock import patch
        import os
        previous=dict(p._services)
        try:
            p.configure(admin_access=lambda user:False,sectors_for=lambda user:{'finance'})
            with patch.dict(os.environ, {'L2_BONUS_JOB_TOKEN':'x'*40,'L2_BONUS_JOB_ADMIN':'NoAdmin'}):
                with self.assertRaises(HTTPException) as e:p.close_due_bonus('x'*40)
                self.assertEqual(e.exception.status_code,403)
        finally:p._services.clear();p._services.update(previous)
