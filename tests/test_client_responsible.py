import ast
from pathlib import Path
import unittest

tree=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='valid_client_responsible')
scope={}
exec(compile(ast.Module(body=[node],type_ignores=[]),'<responsible>','exec'),scope)

class Cursor:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row

class Connection:
    def execute(self,sql,params):
        members={'Marlene':('Administrativo',['admin','commercial']),
                 'Erika':('Vendedor',['commercial']),
                 'Lais':('Administrativo',['office'])}
        return Cursor(members.get(params[0]))

class ResponsibleTests(unittest.TestCase):
    def test_administrative_owner_and_seller_are_valid(self):
        con=Connection()
        self.assertTrue(scope['valid_client_responsible'](con,'Marlene'))
        self.assertTrue(scope['valid_client_responsible'](con,'Erika'))
        self.assertFalse(scope['valid_client_responsible'](con,'Lais'))
        self.assertFalse(scope['valid_client_responsible'](con,'Inativo'))

if __name__=='__main__':unittest.main()
