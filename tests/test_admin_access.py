import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

source=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
functions=[node for node in source.body if isinstance(node,ast.FunctionDef) and node.name in ('admin_access','create_team_member','update_team_member','reset_team_access')]
for node in functions: node.decorator_list=[]

class HTTPException(Exception):
    def __init__(self,status_code,detail): self.status_code,self.detail=status_code,detail

scope={'HTTPException':HTTPException,'auth':lambda _: 'Marlene','sectors_for':lambda user: {'admin'} if user=='Marlene' else set(),'Header':lambda default=None:None,
       'TeamMember':object,'TeamMemberUpdate':object,'TeamAccessReset':object,'validate_member':lambda *args:None}
exec(compile(ast.Module(body=functions,type_ignores=[]),'<admin>','exec'),scope)

class AdminAccessTests(unittest.TestCase):
    def test_marlene_has_admin_without_finance(self):
        self.assertTrue(scope['admin_access']('Marlene'))
        self.assertFalse(scope['admin_access']('Euler'))

    def test_marlene_cannot_grant_finance_or_admin(self):
        for sectors in (['commercial','finance'],['commercial','admin']):
            data=SimpleNamespace(user='Novo',role='Administrativo',department='',sectors=sectors,profile=None)
            with self.assertRaises(HTTPException) as error: scope['create_team_member'](data,None)
            self.assertEqual(error.exception.status_code,403)
            with self.assertRaises(HTTPException) as error: scope['update_team_member']('Euler',data,None)
            self.assertEqual(error.exception.status_code,403)

    def test_cannot_reset_privileged_accounts(self):
        with self.assertRaises(HTTPException):scope['reset_team_access'](SimpleNamespace(user='Ana Paula'),None)
        with self.assertRaises(HTTPException):scope['reset_team_access'](SimpleNamespace(user='Marlene'),None)

if __name__=='__main__': unittest.main()
