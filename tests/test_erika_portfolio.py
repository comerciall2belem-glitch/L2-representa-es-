import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

source=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
nodes=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in ('erika_pa_portfolio','authorize_erika_pa_client')]
for n in nodes:n.decorator_list=[]
class HTTPException(Exception):
    def __init__(self,status_code,detail):self.status_code,self.detail=status_code,detail
class Cursor:
    def __init__(self,row=None,rows=()):self.row,self.rows=row,rows
    def fetchone(self):return self.row
    def __iter__(self):return iter(self.rows)
class DB:
    def __init__(self,client):self.client=client;self.writes=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,params=None):
        if "username='Erika'" in sql and 'app_users' in sql:return Cursor((1,))
        if "SELECT payload FROM entities WHERE kind='client'" in sql:return Cursor((self.client,))
        if sql.strip().startswith(('INSERT','UPDATE')):self.writes.append((sql,params))
        return Cursor()
namespace={'HTTPException':HTTPException,'Header':lambda default=None:None,'PortfolioAuthorization':object,
           'auth':lambda _: 'Marlene','normalize_uf':lambda state:state,'Jsonb':lambda x:x}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'<erika>','exec'),namespace)
class PortfolioTests(unittest.TestCase):
    def test_only_marlene_approves_pa(self):
        database=DB({'state':'PA'});namespace['db']=lambda:database
        result=namespace['authorize_erika_pa_client']('c1',SimpleNamespace(approved=True),None)
        self.assertEqual((result['seller'],result['approvedBy']),('Erika','Marlene'))
        self.assertTrue(any('client_seller_authorizations' in sql for sql,_ in database.writes))
        database=DB({'state':'AP'});namespace['db']=lambda:database
        with self.assertRaises(HTTPException) as err:namespace['authorize_erika_pa_client']('c2',SimpleNamespace(approved=True),None)
        self.assertEqual(err.exception.status_code,404)
        self.assertFalse(database.writes)
        namespace['auth']=lambda _:'Erika'
        with self.assertRaises(HTTPException) as err:namespace['authorize_erika_pa_client']('c1',SimpleNamespace(approved=True),None)
        self.assertEqual(err.exception.status_code,403)
if __name__=='__main__':unittest.main()
