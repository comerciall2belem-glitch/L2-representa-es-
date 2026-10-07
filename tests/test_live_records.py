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
