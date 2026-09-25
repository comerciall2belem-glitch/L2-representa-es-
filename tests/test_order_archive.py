"""Verifica a recuperação de pedido arquivado sem perder o registro comercial."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import unittest

source=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='restore_archived_order')
function.decorator_list=[]

class HTTPException(Exception):
    def __init__(self,status_code,detail):
        self.status_code=status_code
        self.detail=detail

def Header(*args,**kwargs): return None

class Cursor:
    def __init__(self,row=None): self.row=row
    def fetchone(self): return self.row

class FakeCon:
    def __init__(self): self.queries=[]
    def execute(self,sql,params=None):
        self.queries.append((sql,params))
        if "SELECT payload FROM archived_entities" in sql:return Cursor(({'id':'p1','status':'Faturado','amount':53.55},))
        if "SELECT 1 FROM entities" in sql:return Cursor()
        return Cursor()

class FakeDB:
    def __init__(self,con): self.con=con
    def __enter__(self):return self.con
    def __exit__(self,*args):return False

namespace={'HTTPException':HTTPException,'Header':Header,'Jsonb':lambda x:x}
exec(compile(ast.Module(body=[function],type_ignores=[]),'<archive>','exec'),namespace)

class ArchiveTests(unittest.TestCase):
    def test_admin_can_restore_order_without_deleting_attachments(self):
        con=FakeCon();namespace['db']=lambda:FakeDB(con);namespace['auth']=lambda _: 'Ana Paula'
        result=namespace['restore_archived_order']('p1','token')
        self.assertTrue(result['restored'])
        statements='\n'.join(sql for sql,_ in con.queries)
        self.assertIn("INSERT INTO entities(kind,id,payload)",statements)
        self.assertIn("DELETE FROM archived_entities",statements)
        self.assertNotIn("DELETE FROM order_attachments",statements)

    def test_other_user_cannot_restore(self):
        namespace['auth']=lambda _: 'Euler'
        with self.assertRaises(HTTPException) as caught:
            namespace['restore_archived_order']('p1','token')
        self.assertEqual(caught.exception.status_code,403)
