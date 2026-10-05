"""Regression checks for client document access without production credentials."""
import ast
import os
from pathlib import Path
import unittest

server_path = Path(os.getenv('L2_SERVER_SOURCE', Path(__file__).resolve().parents[1] / 'server.py'))
source = ast.parse(server_path.read_text())
nodes = [node for node in source.body if isinstance(node, ast.FunctionDef)
         and node.name == 'client_document_access']

class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail

class Connection:
    def __init__(self, exists=True):
        self.exists = exists
    def execute(self, sql, params):
        return self
    def fetchone(self):
        return (1,) if self.exists else None

namespace = {'HTTPException': HTTPException,'check_client_scope':lambda con,user,client_id:None}
exec(compile(ast.Module(body=nodes, type_ignores=[]), '<documents>', 'exec'), namespace)

class DocumentAccessTests(unittest.TestCase):
    def check(self, sectors, category=None, exists=True):
        namespace['sectors_for'] = lambda user: set(sectors)
        return namespace['client_document_access'](Connection(exists), 'client-qa', 'user-qa', category)

    def test_commercial_can_open_regular_documents(self):
        self.assertIsNone(self.check(['commercial'], 'nfe'))
        self.assertIsNone(self.check(['office'], 'contract'))

    def test_financial_documents_require_finance_sector(self):
        with self.assertRaises(HTTPException) as err:
            self.check(['commercial'], 'finance')
        self.assertEqual((err.exception.status_code, err.exception.detail),
                         (403, 'Acesso financeiro restrito'))
        self.assertIsNone(self.check(['finance'], 'finance'))

    def test_unprivileged_user_and_unknown_client_are_rejected(self):
        with self.assertRaises(HTTPException) as err:
            self.check([], 'nfe')
        self.assertEqual(err.exception.status_code, 403)
        with self.assertRaises(HTTPException) as err:
            self.check(['commercial'], exists=False)
        self.assertEqual(err.exception.status_code, 404)

if __name__ == '__main__':
    unittest.main()
