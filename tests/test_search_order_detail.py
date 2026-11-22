import ast
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock
from copy import deepcopy
from financial_visibility import hide_industry_commissions

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
        self.sectors = MagicMock(return_value={'commercial', 'finance'})
        self.ns = dict(sectors_for=self.sectors, hide_industry_commissions=hide_industry_commissions, auth=self.auth, require_sector=MagicMock(), db=self.db, check_order_scope=self.scope, Header=lambda **kw: None)
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

    def test_non_financial_profiles_cannot_receive_industry_commissions(self):
        for user in ('Marlene', 'Erika', 'MB', 'Anapon', 'Danyelle Oliveira'):
            with self.subTest(user=user):
                self.auth.return_value = user
                self.sectors.return_value = {'commercial', 'office'}
                order = {'id': 'o', 'clientId': 'c', 'amount': 100, 'sellerCommissionRate': 2.5,
                         'commissionAmount': 8, 'notes': 'Entrega combinada. Comissão da indústria 8%.',
                         'items': [{'sku': 'sku', 'quantity': 3, 'commissionRate': 8}]}
                original = deepcopy(order)
                self.con.execute.return_value.fetchone.side_effect = [(order,), ({'id': 'c'},)]
                result = self.ns['searched_order_detail']('o', 'valid-session')
                self.assertNotIn('commissionAmount', result['order'])
                self.assertNotIn('commissionRate', result['order']['items'][0])
                self.assertEqual(result['order']['notes'], 'Entrega combinada.')
                self.assertEqual(result['order']['sellerCommissionRate'], 2.5)
                self.assertEqual(result['order']['items'][0]['quantity'], 3)
                self.assertEqual(result['order']['amount'], 100)
                self.assertEqual(order, original)

    def test_financial_profiles_keep_full_commercial_conditions(self):
        for user in ('Ana Paula', 'Euler', 'Laís'):
            with self.subTest(user=user):
                self.auth.return_value = user
                self.sectors.return_value = {'commercial', 'finance'}
                order = {'id': 'o', 'clientId': 'c', 'commissionAmount': 8,
                         'items': [{'sku': 'sku', 'commissionRate': 8}]}
                self.con.execute.return_value.fetchone.side_effect = [(order,), ({'id': 'c'},)]
                self.assertEqual(self.ns['searched_order_detail']('o', 'valid-session')['order'], order)
