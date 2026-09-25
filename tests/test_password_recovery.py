"""Admin password recovery must revoke sessions and preserve user data."""
import ast
from pathlib import Path
import unittest

source = ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
reset = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'admin_reset_my_password')

class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        super().__init__(detail)

class Connection:
    def __init__(self): self.statements = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, args):
        self.statements.append((sql, args))
        return self
    def fetchone(self): return ('old-hash',)

class ResetTests(unittest.TestCase):
    def setUp(self):
        self.con = Connection()
        self.actor = 'Ana Paula'
        scope = {'app': type('App', (), {'post': lambda self, path: (lambda fn: fn)})(), 'AdminPasswordReset': object, 'Header': lambda default=None: None, 'HTTPException': HTTPException,
                 'auth': lambda header: self.actor, 'db': lambda: self.con,
                 'PASSWORDS': {'Ana Paula': 'provisional'},
                 'password_ok': lambda new, old: new == old, 'password_hash': lambda value: 'hashed-'+value}
        exec(compile(ast.Module(body=[reset], type_ignores=[]), '<recovery>', 'exec'), scope)
        self.reset = scope['admin_reset_my_password']
        self.data = type('Data', (), {'newPassword': 'new-private-password'})()

    def test_only_admin_can_reset(self):
        self.actor = 'Euler'
        with self.assertRaises(HTTPException) as error:
            self.reset(self.data, 'Bearer token')
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(self.con.statements, [])

    def test_reset_revokes_sessions_and_writes_audit(self):
        self.assertEqual(self.reset(self.data, 'Bearer token')['loginRequired'], True)
        sql = [statement for statement, _ in self.con.statements]
        self.assertTrue(any('UPDATE app_users SET password_hash' in statement for statement in sql))
        self.assertTrue(any('DELETE FROM sessions WHERE username' in statement for statement in sql))
        self.assertTrue(any('INSERT INTO audit_log' in statement for statement in sql))
        self.assertFalse(any('DELETE FROM entities' in statement for statement in sql))

if __name__ == '__main__': unittest.main()
