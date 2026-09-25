"""Números são atribuídos no servidor e permanecem estáveis em edições."""
import ast
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import unittest

tree=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
functions=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in ('sync','next_order_number')]
for node in functions: node.decorator_list=[]

class HTTPException(Exception):
    def __init__(self,status_code,detail):
        self.status_code=status_code
        self.detail=detail

class Cursor:
    def __init__(self,row=None,rows=()): self.row,self.rows=row,rows
    def fetchone(self): return self.row
    def __iter__(self): return iter(self.rows)

class FakeCon:
    def __init__(self): self.orders={};self.number=0
    def execute(self,sql,params=None):
        if sql.startswith('UPDATE order_counter SET value=value+1'):
            self.number+=1
            return Cursor((self.number,))
        if "SELECT payload FROM entities WHERE kind='client'" in sql:return Cursor(({'state':'PA'},))
        if "SELECT payload FROM entities WHERE kind='price'" in sql:return Cursor(({'price':'35.52'},))
        if "SELECT payload FROM entities WHERE kind='order'" in sql:return Cursor((self.orders[params[0]],) if params[0] in self.orders else None)
        if "SELECT 1 FROM archived_entities WHERE kind='order'" in sql:return Cursor()
        if sql.startswith('INSERT INTO entities(kind,id,payload)'):
            self.orders[params[1]]=dict(params[2])
            return Cursor()
        if sql.startswith('SELECT payload FROM entities WHERE kind=%s ORDER BY'):
            return Cursor(rows=[(order,) for order in self.orders.values()] if params[0]=='order' else [])
        return Cursor()

class FakeDB:
    def __init__(self,con): self.con=con
    def __enter__(self):return self.con
    def __exit__(self,*args):return False

namespace={'Sync':object,'Header':lambda *args,**kwargs:None,'HTTPException':HTTPException,
           'FINANCE_USERS':{'Ana Paula'},'Decimal':Decimal,'InvalidOperation':InvalidOperation,
           'normalize_uf':lambda value:value,'price_table_matches_client':lambda a,b:a==b,
           'Jsonb':lambda value:value,'re':re}
exec(compile(ast.Module(body=functions,type_ignores=[]),'<order-numbering>','exec'),namespace)

class OrderNumberTests(unittest.TestCase):
    def test_new_orders_start_at_one_and_edit_keeps_number(self):
        con=FakeCon();namespace['db']=lambda:FakeDB(con);namespace['auth']=lambda token:'Ana Paula'
        for identifier in ('first','second'):
            data={'id':identifier,'clientId':'client','brand':'Bruna Tavares','priceTable':'PA',
                  'items':[{'sku':'BBBL01B','quantity':2}],'orderNumber':999}
            change=type('Change',(),{'type':'order','data':data,'changeId':'change-'+identifier})()
            namespace['sync'](type('Sync',(),{'changes':[change]})(),'token')
        self.assertEqual([con.orders[key]['orderNumber'] for key in ('first','second')],[1,2])
        edited=dict(con.orders['first'],orderNumber=999,status='Confirmado')
        change=type('Change',(),{'type':'order','data':edited,'changeId':'change-edit'})()
        namespace['sync'](type('Sync',(),{'changes':[change]})(),'token')
        self.assertEqual(con.orders['first']['orderNumber'],1)
        self.assertEqual(con.number,2)
