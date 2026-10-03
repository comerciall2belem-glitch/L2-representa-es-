import unittest
from fastapi import HTTPException
from access_policy import effective_sectors, attribute_order


class Cursor:
    def __init__(self, row): self.row = row
    def fetchone(self): return self.row


class Members:
    roles = {'Laís': 'Administrativo', 'Ana Paula': 'Administradora', 'Marlene': 'Administrativo',
             'Euler': 'Vendedor', 'Erika': 'Vendedor', 'MB': 'Vendedor'}
    def execute(self, sql, args):
        role = self.roles.get(args[0])
        return Cursor((role,) if role else None)


class AccessPolicyTests(unittest.TestCase):
    def test_commercial_cannot_gain_privileges_from_stored_sectors(self):
        self.assertEqual(effective_sectors('Erika', 'Vendedor', ['commercial','routes','clients_edit','office','management','finance','admin','catalog']), {'commercial','routes','clients_edit'})

    def test_partner_and_administration_keep_access(self):
        sectors = ['commercial','office','management','finance','catalog']
        self.assertEqual(effective_sectors('Euler','Vendedor',sectors), set(sectors))
        self.assertEqual(effective_sectors('Laís','Administrativo',sectors), set(sectors))

    def test_admin_must_choose_seller(self):
        with self.assertRaises(HTTPException) as error:
            attribute_order(Members(), 'Laís', {})
        self.assertEqual(error.exception.status_code, 400)

    def test_lais_has_admin_authorship_and_no_personal_commission(self):
        order = {'user':'spoofed','enteredBy':'spoofed','sellerResponsible':'Erika','commissionSeller':'Laís'}
        attribute_order(Members(),'Laís',order)
        self.assertEqual((order['enteredBy'],order['enteredAs'],order['updatedAs']), ('Laís','Adm','Adm'))
        self.assertEqual((order['sellerResponsible'],order['commissionSeller']), ('Erika','Erika'))

    def test_edit_preserves_original_author(self):
        order = {'sellerResponsible':'Erika'}
        attribute_order(Members(),'Laís',order,{'user':'MB','sellerResponsible':'Erika'})
        self.assertEqual(order['enteredBy'],'MB')
        self.assertEqual(order['updatedAs'],'Adm')

    def test_admin_or_inactive_cannot_be_commission_seller(self):
        for name in ('Laís','Ana Paula','Marlene','Inactive'):
            with self.subTest(name=name), self.assertRaises(HTTPException):
                attribute_order(Members(),'Laís',{'sellerResponsible':name})

    def test_seller_cannot_attribute_to_another_user(self):
        with self.assertRaises(HTTPException) as error:
            attribute_order(Members(),'Erika',{'sellerResponsible':'MB'})
        self.assertEqual(error.exception.status_code,403)

    def test_seller_defaults_to_authenticated_user(self):
        order = {}
        attribute_order(Members(),'Erika',order)
        self.assertEqual(order['commissionSeller'],'Erika')


if __name__ == '__main__': unittest.main()
