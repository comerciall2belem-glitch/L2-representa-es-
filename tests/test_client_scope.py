import ast
from pathlib import Path
import unittest

tree=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('is_seller','check_client_scope','scoped_rows')]
class HTTPException(Exception):
    def __init__(self,status_code,detail): self.status_code,self.detail=status_code,detail

namespace={'HTTPException':HTTPException,'admin_access':lambda user:user in ('Ana Paula','Marlene')}
exec(compile(ast.Module(body=nodes,type_ignores=[]),'<scope>','exec'),namespace)

class Cursor:
    def __init__(self,row=None,rows=()): self.row,self.rows=row,rows
    def fetchone(self):return self.row
    def __iter__(self):return iter(self.rows)

class FakeCon:
    def execute(self,sql,params=None):
        if sql.startswith('SELECT role FROM app_users'):
            return Cursor(('Vendedor',) if params[0] in ('Euler','Erika') else ('Gestão',))
        if "payload->>'owner' FROM entities" in sql:
            return Cursor(('Euler',) if params[0]=='c1' else ('Outro',))
        if sql.startswith('SELECT 1 FROM client_seller_authorizations'):
            return Cursor((1,) if params==('c2','Erika') else None)
        if sql.startswith('SELECT client_id FROM client_seller_authorizations'):
            return Cursor(rows=[('c2',)] if params[0]=='Erika' else [])
        if sql.startswith('SELECT id,payload FROM entities'):
            return Cursor(rows=[('c1',{'id':'c1','owner':'Euler'}),('c2',{'id':'c2','owner':'Outro'})])
        if "SELECT id FROM entities WHERE kind='client'" in sql:
            return Cursor(rows=[('c1',)] if params[0]=='Euler' else [])
        return Cursor(rows=[({'id':'c1','clientId':'c1'},),({'id':'c2','clientId':'c2'},)])

class ClientScopeTests(unittest.TestCase):
    def test_seller_can_only_read_assigned_client(self):
        con=FakeCon()
        namespace['check_client_scope'](con,'Euler','c1')
        with self.assertRaises(HTTPException) as err:namespace['check_client_scope'](con,'Euler','c2')
        self.assertEqual(err.exception.status_code,403)
        self.assertEqual(namespace['scoped_rows'](con,'client','Euler'),[{'id':'c1','owner':'Euler'}])
        self.assertEqual(namespace['scoped_rows'](con,'order','Euler'),[{'id':'c1','clientId':'c1'}])
        namespace['check_client_scope'](con,'Erika','c2')
        self.assertEqual(namespace['scoped_rows'](con,'client','Erika'),[{'id':'c2','owner':'Outro'}])
        self.assertEqual(len(namespace['scoped_rows'](con,'client','Ana Paula')),2)
        self.assertEqual(len(namespace['scoped_rows'](con,'client','Marlene')),2)

if __name__=='__main__': unittest.main()
