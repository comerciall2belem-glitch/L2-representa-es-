import unittest
from datetime import date, timedelta
from copy import deepcopy
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from strategic_crm import Qualification, RouteRequest, qualify, cadence, plan_route, template_payload, router, configure

DAY=date(2026,10,8)
def qualification(**changes):
    return Qualification(**{'name':'Loja teste','taxId':'11222333000181','state':'PA','city':'Belém','channel':'Cosméticos','cnae':'4772500','size':'Pequena','segment':'Maquiagem','demandEvidence':'Comprador solicitou reposição.','evidenceDate':DAY,'urgencyDays':7,'estimatedMonthlyValue':3000,**changes})
def route(**changes):
    return RouteRequest(**{'date':DAY,'city':'Belém','state':'PA','originLatitude':-1.45,'originLongitude':-48.49,**changes})

class LogicTests(unittest.TestCase):
    def test_qualified_and_incompatible_icp(self):
        self.assertTrue(qualify(qualification(),DAY)['qualified'])
        self.assertFalse(qualify(qualification(cnae='6201500'),DAY)['qualified'])
        self.assertFalse(qualify(qualification(channel='Farma'),DAY)['qualified'])
    def test_evidence_must_be_recent(self):
        for d in (DAY+timedelta(days=1),DAY-timedelta(days=31)):
            with self.assertRaises(HTTPException): qualify(qualification(evidenceDate=d),DAY)
    def test_pair_and_hours_validated(self):
        with self.assertRaises(HTTPException): qualify(qualification(latitude=0),DAY)
        with self.assertRaises(HTTPException): qualify(qualification(opens='18:00',closes='09:00'),DAY)
    def test_unknown_never_marked_inactive(self):
        self.assertEqual(cadence({'id':'a'},[],today=DAY)['status'],'Sem histórico')
    def test_cancelled_draft_and_future_orders_do_not_reset_cadence(self):
        orders=[{'clientId':'a','status':'Faturado','date':'2026-07-01','amount':'2000'},
                {'clientId':'a','status':'Cancelado','date':str(DAY)},
                {'clientId':'a','status':'Orçamento','date':str(DAY)},
                {'clientId':'a','status':'Confirmado','date':'2027-01-01'}]
        info=cadence({'id':'a'},orders,today=DAY)
        self.assertEqual(info['status'],'Inativo');self.assertEqual(info['recordedRevenue'],2000)
    def test_purchase_resets_and_contacts_use_at(self):
        orders=[{'clientId':'a','status':'Confirmado','date':str(DAY)}]
        info=cadence({'id':'a'},orders,today=DAY)
        self.assertEqual(info['status'],'Ativo');self.assertEqual(info['nextContact'],'2026-10-15')
        info=cadence({'id':'a'},[],[{'clientId':'a','at':'2026-10-07T16:00:00-03:00'}],DAY)
        self.assertEqual(info['nextContact'],'2026-10-14')
    def test_median_cycle_and_risk_boundaries(self):
        orders=[{'clientId':'a','status':'Faturado','date':d} for d in ('2026-08-01','2026-08-21','2026-09-10')]
        self.assertEqual(cadence({'id':'a'},orders,today=DAY)['cycleDays'],20)
        for age,status in ((14,'Ativo'),(15,'Em risco'),(59,'Em risco'),(60,'Inativo')):
            order={'clientId':'a','status':'Faturado','date':str(DAY-timedelta(days=age))}
            self.assertEqual(cadence({'id':'a'},[order],today=DAY)['status'],status)
    def test_route_respects_hours_scope_and_missing_coordinates(self):
        clients=[{'id':'open','state':'PA','city':'Belém','latitude':-1.45,'longitude':-48.49,'opens':'10:00','closes':'12:00'},
                 {'id':'closed','state':'PA','city':'Belém','latitude':-1.451,'longitude':-48.49,'opens':'09:00','closes':'09:20'},
                 {'id':'missing','state':'PA','city':'Belém'},
                 {'id':'wrong','state':'AP','city':'Macapá','latitude':0,'longitude':0}]
        r=plan_route(clients,route())
        self.assertEqual([x['clientId'] for x in r['stops']],['open'])
        self.assertEqual(r['stops'][0]['arrival'],'10:00');self.assertEqual(len(r['omitted']),2)
    def test_route_limits_and_return_budget(self):
        clients=[{'id':str(i),'state':'PA','city':'Belém','latitude':-1.45,'longitude':-48.49} for i in range(20)]
        self.assertEqual(len(plan_route(clients,route(limit=10))['stops']),10)
        self.assertEqual(plan_route(clients,route(end='09:30'))['stops'],[])
    def test_template_shape_and_invalid_phone(self):
        data=template_payload('(91) 99999-1234',{'template':'l2_retorno','language':'pt_BR'},{'name':'Loja'})
        self.assertEqual(data['to'],'5591999991234');self.assertEqual(data['type'],'template')
        self.assertEqual(len(data['template']['components'][0]['parameters']),2)
        with self.assertRaises(ValueError):template_payload('123',{'template':'x','language':'pt_BR'}, {})

class Cursor:
    def __init__(self,rows=()):self.rows=rows
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows
    def __iter__(self):return iter(self.rows)
class MemoryCon:
    def __init__(self):self.entities={};self.audit=[]
    def __enter__(self):self.before=deepcopy(self.entities);return self
    def __exit__(self,typ,*args):
        if typ:self.entities=self.before
    def execute(self,sql,params=()):
        if sql.startswith('INSERT INTO entities'):
            self.entities[(params[0],params[1])]=deepcopy(params[2].obj);return Cursor()
        if sql.startswith('INSERT INTO audit'):self.audit.append(params);return Cursor()
        if sql.startswith('SELECT payload FROM entities WHERE kind=%s AND id='):
            item=self.entities.get(tuple(params));return Cursor([(item,)]) if item else Cursor()
        if "kind='client'" in sql and 'regexp_replace' in sql:
            return Cursor([(x,) for (k,i),x in self.entities.items() if k=='client' and x['taxId']==params[0]])
        if "SELECT kind,payload" in sql:
            return Cursor([(k,x) for (k,i),x in self.entities.items() if k in ('order','visit','interaction') and x.get('clientId')==params[0]])
        if "SELECT 1" in sql and "kind='route'" in sql:return Cursor()
        if "SELECT payload FROM entities WHERE kind='route'" in sql:return Cursor()
        if "SELECT payload FROM entities WHERE kind='client' AND id=" in sql:return Cursor()
        for kind in ('lead','task'):
            if f"SELECT payload FROM entities WHERE kind='{kind}' AND id=" in sql:
                x=self.entities.get((kind,params[0]));return Cursor([(x,)]) if x else Cursor()
        return Cursor()
class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.con=MemoryCon()
        def auth(token):
            if token!='Bearer allowed':raise HTTPException(401,'Não autenticado')
            return 'Erika'
        def scope(con,user,cid):
            if con.entities[('client',cid)].get('owner')!=user:raise HTTPException(403,'Carteira restrita')
        configure(db=lambda:self.con,auth=auth,require_sector=lambda *a:None,valid_cnpj=lambda c:c=='11222333000181',
                  check_client_scope=scope,is_seller=lambda *a:True,scoped_rows=lambda c,k,u:[x for (kind,i),x in c.entities.items() if kind==k and (x.get('owner')==u or x.get('clientId') in {v['id'] for (ki,j),v in c.entities.items() if ki=='client' and v.get('owner')==u})])
        app=FastAPI();app.include_router(router);self.api=TestClient(app);self.headers={'Authorization':'Bearer allowed'}
    def test_authentication_and_validation(self):
        self.assertEqual(self.api.get('/api/strategy/overview').status_code,401)
        d=qualification().model_dump(mode='json');d['state']='SP'
        self.assertEqual(self.api.post('/api/strategy/tdc/qualify',json=d,headers=self.headers).status_code,422)
    def test_qualification_injects_once_and_creates_cadence(self):
        with patch('strategic_crm.qualify',side_effect=lambda d:qualify(d,DAY)):
            for _ in range(2):
                r=self.api.post('/api/strategy/tdc/qualify',json=qualification().model_dump(mode='json'),headers=self.headers)
                self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(sum(k=='client' for k,i in self.con.entities),1)
        self.assertEqual(sum(k=='lead' for k,i in self.con.entities),1)
        self.assertEqual(sum(k=='task' for k,i in self.con.entities),1)
    def test_existing_other_portfolio_is_protected(self):
        self.con.entities[('client','c')]={'id':'c','owner':'Euler','name':'Loja','taxId':'11222333000181'}
        with patch('strategic_crm.qualify',side_effect=lambda d:qualify(d,DAY)):
            r=self.api.post('/api/strategy/tdc/qualify',json=qualification().model_dump(mode='json'),headers=self.headers)
        self.assertEqual(r.status_code,403);self.assertNotIn(('lead','tdc-11222333000181'),self.con.entities)
    def test_settings_are_owner_only(self):
        r=self.api.put('/api/strategy/mcr/settings',headers=self.headers,json={'enabled':True,'template':'teste','language':'pt_BR'})
        self.assertEqual(r.status_code,403)
    def test_nonqualified_stays_lead_only(self):
        with patch('strategic_crm.qualify',side_effect=lambda d:qualify(d,DAY)):
            r=self.api.post('/api/strategy/tdc/qualify',headers=self.headers,json=qualification(cnae='6201500').model_dump(mode='json'))
        self.assertEqual(r.status_code,200);self.assertIsNone(r.json()['clientId'])
        self.assertEqual(sum(k=='client' for k,i in self.con.entities),0)

    def test_temporary_publish_is_idempotent_and_preserves_agenda(self):
        from datetime import datetime,timedelta
        from strategic_crm import TZ
        day=datetime.now(TZ).date()+timedelta(days=1)
        self.con.entities[('client','c')]={'id':'c','owner':'Erika','name':'Loja','city':'Belém','state':'PA','latitude':-1.4,'longitude':-48.4,'closedDays':[]}
        data={'date':day.isoformat(),'state':'PA','city':'Belém','originLatitude':-1.4,'originLongitude':-48.4,'temporary':True}
        r=self.api.post('/api/strategy/sri/plan',json=data,headers=self.headers)
        self.assertEqual(r.status_code,200,r.text);identifier=r.json()['temporaryId']
        self.assertFalse(any(k=='route' for k,i in self.con.entities))
        for _ in range(2):
            r=self.api.post('/api/strategy/sri/temporary/'+identifier+'/publish',headers=self.headers)
            self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(sum(k=='route' for k,i in self.con.entities),1)
        self.assertTrue(('field_notice',identifier) in self.con.entities)
        self.assertEqual(sum(k=='task' for k,i in self.con.entities),1)
    def test_publication_rejects_changed_location(self):
        from datetime import datetime,timedelta
        from strategic_crm import TZ
        day=datetime.now(TZ).date()+timedelta(days=1)
        self.con.entities[('client','c')]={'id':'c','owner':'Erika','name':'Loja','city':'Belém','state':'PA','latitude':-1.4,'longitude':-48.4,'closedDays':[]}
        r=self.api.post('/api/strategy/sri/plan',json={'date':day.isoformat(),'state':'PA','city':'Belém','originLatitude':-1.4,'originLongitude':-48.4,'temporary':True},headers=self.headers)
        identifier=r.json()['temporaryId'];self.con.entities[('client','c')]['latitude']=-2
        r=self.api.post('/api/strategy/sri/temporary/'+identifier+'/publish',headers=self.headers)
        self.assertEqual(r.status_code,409)
    def test_expense_scope_and_review_workflow(self):
        self.con.entities[('client','c')]={'id':'c','owner':'Erika'}
        self.con.entities[('route','r')]={'id':'r','user':'Erika','clientId':'c'}
        r=self.api.post('/api/strategy/sri/expenses',headers=self.headers,json={'routeId':'r','amount':35,'description':'Combustível'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(sum(k=='route_expense' for k,i in self.con.entities),1)
        self.assertEqual(sum(k=='office_action' for k,i in self.con.entities),1)
        self.con.entities[('route','r')]['user']='Euler'
        self.assertEqual(self.api.post('/api/strategy/sri/expenses',headers=self.headers,json={'routeId':'r','amount':35,'description':'Combustível'}).status_code,403)

class WorkerTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime
        from strategic_crm import TZ
        self.now=datetime(2026,10,8,10,0,tzinfo=TZ)
        self.con=MemoryCon()
        original=self.con.execute
        def execute(sql,params=()):
            if sql.startswith('SELECT pg_try_advisory'):return Cursor([(True,)])
            if sql=="SELECT payload FROM entities WHERE kind='client'":return Cursor([(x,) for (k,i),x in self.con.entities.items() if k=='client'])
            if "kind='strategy_settings' AND id='mcr'" in sql:
                x=self.con.entities.get(('strategy_settings','mcr'));return Cursor([(x,)]) if x else Cursor()
            if "SELECT 1 FROM entities WHERE kind='strategy_send'" in sql:return Cursor([(1,)]) if ('strategy_send',params[0]) in self.con.entities else Cursor()
            return original(sql,params)
        self.con.execute=execute
        info=cadence({'id':'c'},[],today=DAY)
        self.con.entities[('client','c')]={'id':'c','name':'Loja','phone':'91999991234','owner':'Erika','mcr':info,'mcrConsent':{'enabled':True}}
        self.con.entities[('strategy_settings','mcr')]={'id':'mcr','enabled':True,'template':'l2_retorno','language':'pt_BR'}
        configure(db=lambda:self.con,project_attendance=lambda *a:None)
        self.clock=patch('strategic_crm.datetime');mock_clock=self.clock.start();mock_clock.now.return_value=self.now
        self.provider=patch('whatsapp_media.provider_config',return_value=('secret-not-logged','123','v23.0'));self.provider.start()
        self.post=patch('whatsapp_media._post',return_value={'messages':[{'id':'wamid.mock'}]});self.send=self.post.start()
    def tearDown(self):self.clock.stop();self.provider.stop();self.post.stop()
    def test_success_creates_auditable_contact_and_never_duplicates(self):
        from strategic_crm import tick
        tick();tick()
        self.assertEqual(self.send.call_count,1)
        row=self.con.entities[('strategy_send','mcr-c-2026-10-08')]
        self.assertEqual(row['status'],'Aceito pela Meta')
        self.assertEqual(self.con.entities[('client','c')]['mcr']['nextContact'],'2026-10-15')
    def test_ambiguous_failure_requires_review_without_retry(self):
        from strategic_crm import tick
        self.send.side_effect=TimeoutError()
        tick();tick()
        self.assertEqual(self.send.call_count,1)
        self.assertEqual(self.con.entities[('strategy_send','mcr-c-2026-10-08')]['status'],'Conferir envio')
        self.assertFalse(any(k=='interaction' for k,i in self.con.entities))
    def test_optout_or_disabled_never_sends(self):
        from strategic_crm import tick
        self.con.entities[('client','c')]['mcrConsent']['enabled']=False
        tick();self.send.assert_not_called()
        self.con.entities[('client','c')]['mcrConsent']['enabled']=True
        self.con.entities[('strategy_settings','mcr')]['enabled']=False
        tick();self.send.assert_not_called()
