"""Exercita upload isolado, sem credenciais nem banco de produção."""
import ast
import asyncio
from pathlib import Path
import secrets
import unittest

source = ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
functions = [n for n in source.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in ('upload_order_attachment', 'order_exists')]
for node in functions:
    node.decorator_list = []

class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail

class UploadFile: pass

def File(*args, **kwargs): return None
def Header(*args, **kwargs): return None

class FakeCon:
    def __init__(self, count=0):
        self.count = count
        self.saved = []
    def execute(self, sql, params):
        if 'SELECT 1 FROM entities' in sql or 'SELECT count(*) FROM order_attachments' in sql:
            class Cursor:
                def fetchone(inner): return (1,) if 'entities' in sql else (self.count,)
            return Cursor()
        self.saved.append((sql, params))
        return self

class FakeDB:
    def __init__(self, con): self.con = con
    def __enter__(self): return self.con
    def __exit__(self, *args): return False

class Uploaded:
    def __init__(self, name, contents): self.filename, self.contents = name, contents
    async def read(self, n): return self.contents[:n]

namespace = {'Path':Path,'secrets':secrets,'HTTPException':HTTPException,'UploadFile':UploadFile,'File':File,'Header':Header}
exec(compile(ast.Module(body=functions, type_ignores=[]), '<attachments>', 'exec'), namespace)

class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.con=FakeCon()
        namespace['db']=lambda:FakeDB(self.con)
        namespace['auth']=lambda header:'Ana Paula' if header=='valid-token' else (_ for _ in ()).throw(HTTPException(401,'auth'))

    async def upload(self, data, name='comprovante.pdf', header='valid-token'):
        return await namespace['upload_order_attachment']('pedido-1',Uploaded(name,data),header)

    async def test_pdf_saved_in_authenticated_order(self):
        response=await self.upload(b'%PDF-1.4\ncomprovante')
        self.assertEqual(response['type'],'application/pdf')
        self.assertTrue(any('INSERT INTO order_attachments' in sql for sql,_ in self.con.saved))

    async def test_rejects_unauthorized_and_invalid_content(self):
        for data,name,header,status in ((b'%PDF-a','x.pdf','wrong',401),(b'not a pdf','x.pdf','valid-token',400),(b'%PDF-a','x.exe','valid-token',400)):
            with self.assertRaises(HTTPException) as exc:
                await self.upload(data,name,header)
            self.assertEqual(exc.exception.status_code,status)

    async def test_rejects_over_limit_and_five_existing(self):
        with self.assertRaises(HTTPException) as exc:
            await self.upload(b'%PDF-'+b'x'*(5*1024*1024))
        self.assertEqual(exc.exception.status_code,400)
        self.con.count=5
        with self.assertRaises(HTTPException) as exc:
            await self.upload(b'%PDF-ok')
        self.assertEqual(exc.exception.status_code,409)

if __name__=='__main__': unittest.main()
