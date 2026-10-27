import unittest
from fastapi import HTTPException
from access_policy import authorize_visit_change

class VisitPermissionTests(unittest.TestCase):
    def test_seller_owns_new_record(self):
        data = {}
        authorize_visit_change('Erika', True, data)
        self.assertEqual(data['user'], 'Erika')

    def test_seller_cannot_impersonate(self):
        with self.assertRaises(HTTPException) as cm:
            authorize_visit_change('Erika', True, {'user':'Euler'})
        self.assertEqual(cm.exception.status_code, 403)

    def test_seller_cannot_change_or_delete_other_sellers_record(self):
        for deleting in (False, True):
            with self.subTest(deleting=deleting), self.assertRaises(HTTPException):
                authorize_visit_change('Erika', True, {'user':'Erika'}, {'user':'Euler'}, deleting)

    def test_seller_can_edit_and_delete_own_record(self):
        authorize_visit_change('Erika', True, {'user':'Erika'}, {'user':'Erika'})
        authorize_visit_change('Erika', True, {}, {'user':'Erika'}, True)

    def test_unidentified_legacy_owner_not_assumed(self):
        with self.assertRaises(HTTPException):
            authorize_visit_change('Erika', True, {}, {}, True)

    def test_administrative_scope_is_unchanged(self):
        data={'user':'Euler'}
        authorize_visit_change('Marlene', False, data, {'user':'Euler'}, False)
        authorize_visit_change('Ana Paula', False, {}, {'user':'Euler'}, True)

if __name__ == '__main__':
    unittest.main()
