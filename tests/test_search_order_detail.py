import ast
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock

class SearchDetailTests(TestCase):
    def setUp(self):
        tree = ast.parse(Path('server.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'searched_order_detail')
        node.decorator_list = []
        self.con = MagicMock()
        self.db = MagicMock()
        self.db.return_value.__enter__.return_value = self.con
        self.scope = MagicMock()
        self.auth = MagicMock(return_value='Ana Paula')
        self.ns = dict(auth=self.auth, require_sector=MagicMock(), db=self.db, check_order_scope=self.scope, Header=lambda **kw: None)
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<detail>', 'exec'), self.ns)

    def test_full_order_and_client_are_returned_after_scope_check(self):
        order = {'id': 'o', 'clientId': 'c', 'items': [{'sku': 'sku', 'quantity': 3}]}
        self.con.execute.return_value.fetchone.side_effect = [(order,), ({'id': 'c', 'name': 'Cliente'},)]
        result = self.ns['searched_order_detail']('o', 'token')
        self.scope.assert_called_once_with(self.con, 'Ana Paula', 'o')
        self.assertEqual(result['order'], order)
        self.assertEqual(result['client']['id'], 'c')

    def test_denied_scope_prevents_reading_payload(self):
        self.scope.side_effect = PermissionError('forbidden')
        with self.assertRaises(PermissionError): self.ns['searched_order_detail']('o', 'token')
        self.con.execute.assert_not_called()

    def test_invalid_session_prevents_database_access(self):
        self.auth.side_effect = PermissionError('unauthorized')
        with self.assertRaises(PermissionError): self.ns['searched_order_detail']('o', 'bad')
        self.db.assert_not_called()
