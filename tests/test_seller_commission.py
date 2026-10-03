import unittest
from decimal import Decimal
from fastapi import HTTPException
from seller_commission import validate_rate, apply_seller_commission


class SellerCommissionTests(unittest.TestCase):
    def test_individual_rate_is_not_industry_rate(self):
        order={'sellerResponsible':'Erika','amount':2000,'status':'Faturado','sellerCommissionRate':99}
        apply_seller_commission(order,'2.50')
        self.assertEqual(order['sellerCommissionAmount'],'50.00')
        self.assertEqual(order['sellerCommissionRate'],'2.50')

    def test_missing_is_distinct_from_zero(self):
        order={'amount':2000,'status':'Faturado'}
        apply_seller_commission(order,None)
        self.assertIsNone(order['sellerCommissionAmount'])
        self.assertEqual(order['sellerCommissionStatus'],'Percentual não cadastrado')
        apply_seller_commission(order,0)
        self.assertEqual(order['sellerCommissionAmount'],'0.00')

    def test_billed_rate_is_preserved(self):
        previous={'sellerResponsible':'Erika','status':'Faturado','sellerCommissionRate':'2.50'}
        order={'sellerResponsible':'Erika','amount':2000,'status':'Faturado'}
        apply_seller_commission(order,5,previous)
        self.assertEqual(order['sellerCommissionAmount'],'50.00')
        order['sellerResponsible']='MB'
        apply_seller_commission(order,5,previous)
        self.assertEqual(order['sellerCommissionAmount'],'100.00')

    def test_pending_uses_updated_profile(self):
        order={'sellerResponsible':'Erika','amount':2000,'status':'Faturado'}
        apply_seller_commission(order,3,{'status':'Pendente','sellerCommissionRate':2})
        self.assertEqual(order['sellerCommissionAmount'],'60.00')

    def test_rate_validation(self):
        self.assertEqual(validate_rate('2,5'),Decimal('2.5'))
        for value in (None,'NaN','Infinity',-1,101,'0.001','abc'):
            with self.subTest(value=value),self.assertRaises(HTTPException):validate_rate(value)

    def test_cent_rounding(self):
        order={'amount':'0.20','status':'Faturado'}
        apply_seller_commission(order,'2.5')
        self.assertEqual(order['sellerCommissionAmount'],'0.01')
