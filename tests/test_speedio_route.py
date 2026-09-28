"""Exercita a rota Speedio isolada: sem banco, usuário real nem rede externa."""
import ast
from pathlib import Path
import unittest
from speedio_integration import SpeedioError


source = ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'speedio_lookup']
for node in functions: node.decorator_list = []


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


class Cursor:
    def __init__(self, row=None): self.row = row
    def fetchone(self): return self.row


class FakeCon:
    def __init__(self, existing=False, staged=False): self.existing, self.staged = existing, staged
    def execute(self, sql, params):
        return Cursor((1,) if (self.existing if 'FROM clientes' in sql else self.staged) else None)


class FakeDB:
    def __init__(self, con): self.con = con
    def __enter__(self): return self.con
    def __exit__(self, *args): return False


class Query:
    cnpj = '12.345.678/0001-95'  # só é usado por funções simuladas


class SpeedioRouteTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.namespace = {
            'SpeedioQuery': object, 'Header': lambda *a, **k: None,
            'HTTPException': HTTPException, 'SpeedioError': SpeedioError,
            'auth': lambda header: 'Ana Paula' if header == 'Bearer mock' else
                    (_ for _ in ()).throw(HTTPException(401, 'Sessão inválida')),
            'require_sector': lambda user, *sectors: None,
            'valid_cnpj': lambda cnpj: cnpj == '12345678000195',
            'os': type('Env', (), {'getenv': staticmethod(lambda key, default: {'SPEEDIO_USERNAME':'mock-user','SPEEDIO_PASSWORD':'mock-pass'}.get(key, default))}),
            're': __import__('re'),
            'db': lambda: FakeDB(FakeCon()),
            'lookup_cnpj': lambda *args: self.calls.append(args) or
                           {'id':'speedio:12345678000195','state':'PA','name':'Empresa simulada'},
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<speedio-route>', 'exec'), self.namespace)

    def test_route_normalizes_and_calls_only_mock(self):
        result = self.namespace['speedio_lookup'](Query(), 'Bearer mock')
        self.assertEqual(self.calls, [('12345678000195','mock-user','mock-pass')])
        self.assertEqual(result['lead']['state'], 'PA')
        self.assertFalse(result['alreadyRegistered'])

    def test_invalid_auth_and_cnpj_never_call_provider(self):
        with self.assertRaises(HTTPException) as denied:
            self.namespace['speedio_lookup'](Query(), 'wrong')
        self.assertEqual(denied.exception.status_code, 401)
        self.namespace['valid_cnpj'] = lambda _: False
        with self.assertRaises(HTTPException) as invalid:
            self.namespace['speedio_lookup'](Query(), 'Bearer mock')
        self.assertEqual(invalid.exception.status_code, 400)
        self.assertEqual(self.calls, [])

    def test_missing_provider_credentials_return_safe_error(self):
        self.namespace['lookup_cnpj'] = lambda *args: (_ for _ in ()).throw(
            SpeedioError(503, 'Credenciais da Speedio não configuradas no servidor'))
        with self.assertRaises(HTTPException) as missing:
            self.namespace['speedio_lookup'](Query(), 'Bearer mock')
        self.assertEqual(missing.exception.status_code, 503)


if __name__ == '__main__': unittest.main()
