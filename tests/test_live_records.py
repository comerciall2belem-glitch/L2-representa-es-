import os, unittest, asyncio
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
os.environ.setdefault('DATABASE_URL','postgresql://localhost/l2_test')
os.environ.setdefault('L2_INITIAL_PASSWORD','test-password-long-enough')
from fastapi import HTTPException
import server

class Cursor:
    def fetchone(self):return None
class DB:
    def __init__(self):self.queries=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,query,params=()):self.queries.append((query,params));return Cursor()

class LiveRecordTests(unittest.TestCase):
    def test_team_monitor_feed_includes_other_sellers_and_legacy_records(self):
        from datetime import datetime, timezone
        at = datetime(2026, 10, 7, 20, tzinfo=timezone.utc)
        client = {'id':'c','name':'Cliente Euler','owner':'Euler'}
        order = {'id':'o','clientId':'c','user':'Laís','sellerResponsible':'Euler'}
        visit = {'id':'v','clientId':'c','user':'Erika'}
        finance = {'id':'secret','Área':'Financeiro','Demanda':'Restrita'}
        class FeedDB(DB):
            def execute(self, q, params=()):
                if 'SELECT id,payload FROM entities' in q:
                    return [(x['id'],{k:v for k,v in x.items() if k!='id'}) for x in {'client':[client],'order':[order],'visit':[visit],'office_action':[finance]}.get(params[0],[])]
                if 'FROM audit_log a' in q:
                    return [('Laís','order','o','upsert',at,order),('Laís','office_action','secret','upsert',at,finance)]
                if 'SELECT kind,id,payload,updated_at' in q:
                    return [('order','o',order,at),('visit','v',visit,at),('office_action','secret',finance,at)]
                return Cursor()
        for user in ('Ana Paula','Marlene'):
            with patch.object(server,'auth',return_value=user),patch.object(server,'require_sector'),patch.object(server,'sectors_for',return_value={'commercial','office'}),patch.object(server,'db',return_value=FeedDB()),patch.object(server,'scoped_rows',side_effect=AssertionError('Monitor must see team')):
                result=server.commercial_activity('test-only')
            self.assertEqual(len(result),2)
            self.assertEqual({x['user'] for x in result},{'Laís','Erika'})
            self.assertTrue(all(x['clientName']=='Cliente Euler' for x in result))
            self.assertEqual(next(x for x in result if x['kind']=='visit')['action'],'existing')
            self.assertEqual(sum(x['kind']=='order' for x in result),1)

    def test_rejected_sync_identifies_record_for_marlene_without_acknowledging_it(self):
        data=server.Sync(changes=[server.Change(type='unknown',data={'id':'local-record'},changeId='rejected-change')])
        with patch.object(server,'auth',return_value='Marlene'),patch.object(server,'db',return_value=DB()):
            with self.assertRaises(HTTPException) as error:
                server.sync(data,'test-only')
        self.assertEqual(error.exception.status_code,400)
        self.assertEqual(error.exception.headers['X-L2-Change-Id'],'rejected-change')
        self.assertEqual(error.exception.detail,'Alteração inválida')

    def test_live_script_is_served_as_an_allowed_asset(self):
        response=server.asset('live_records.js')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.path.is_file())

    def test_seller_creation_with_complete_profile_and_safe_sectors(self):
        profile=dict(fullName='Vendedora Teste',document='12345678900',email='test@example.com',phone='91999999999',bank='Banco',accountType='Corrente',branch='1',accountNumber='2',pixKey='test@example.com',commissionRate='2,50')
        data=server.TeamMember(user='Vendedora Teste',role='Vendedor',sectors=['commercial','routes'],profile=profile)
        con=DB()
        with patch.object(server,'auth',return_value='Ana Paula'),patch.object(server,'admin_access',return_value=True),patch.object(server,'db',return_value=con):
            result=server.create_team_member(data,'test-only')
        self.assertEqual(result['user'],'Vendedora Teste')
        self.assertTrue(any('INSERT INTO seller_profiles' in q for q,_ in con.queries))
        self.assertTrue(any('pg_notify' in q for q,_ in con.queries))
        self.assertEqual(next(p[0] for q,p in con.queries if 'seller_commission_rate' in q),server.Decimal('2.50'))
    def test_missing_profile_field_is_named_and_finance_remains_restricted(self):
        with self.assertRaises(HTTPException) as error:server.validate_seller_profile({'fullName':'Nome'})
        self.assertIn('banco',error.exception.detail)
        with self.assertRaises(HTTPException):server.validate_member('Vendedora','Vendedor',['commercial','finance'])
    def test_event_channel_authentication_and_signal_without_payload(self):
        class Connection:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def execute(self,q):self.query=q
            async def notifies(self,**kwargs):yield SimpleNamespace(payload='private data must not be forwarded')
        class Request:
            def __init__(self):self.calls=0
            async def is_disconnected(self):self.calls+=1;return self.calls>1
        async def run():
            with patch.object(server,'auth',return_value='Marlene'),patch.object(server.psycopg.AsyncConnection,'connect',new=AsyncMock(return_value=Connection())):
                response=await server.records_events(Request(),'test-only')
                return [chunk async for chunk in response.body_iterator]
        chunks=asyncio.run(run())
        self.assertEqual(chunks,['event: ready\ndata: {}\n\n','event: records\ndata: {}\n\n'])
        async def unauthorized():
            with patch.object(server,'auth',side_effect=HTTPException(401,'Expirado')):
                await server.records_events(Request(),'invalid')
        with self.assertRaises(HTTPException):asyncio.run(unauthorized())

if __name__=='__main__':unittest.main()
