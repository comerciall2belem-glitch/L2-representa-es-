from decimal import Decimal
import unittest
from fastapi import HTTPException
from commercial_tables import bella_policy, BELLA_POLICY_VERSION, validate_bella_order, migrate_bella_policy
from cpq import commercial_policy

class BellaPolicyTests(unittest.TestCase):
    def test_minimum_and_exclusive_freight_boundary_in_both_states(self):
        for uf in ('PA','AP'):
            for net in ('1499.99','1500.00','3499.99','3500.00'):
                with self.assertRaises(HTTPException) as error:bella_policy(Decimal(net),'28',uf)
                self.assertIn('mínimo' if net=='1499.99' else 'frete pendente',error.exception.detail)
            result=bella_policy(Decimal('3500.01'),'28',uf)
            self.assertEqual(result['freight'],'CIF')
            self.assertEqual(result['minimum'],'1500.00')
            self.assertEqual(result['paymentMethod'],'Boleto Bancário')
    def test_old_terms_do_not_leak_into_new_quotes(self):
        for terms in ('21','30','28/35','30/45/60'):
            with self.assertRaises(HTTPException):bella_policy(Decimal('4000'),terms,'PA')
    def test_cpq_and_erp_share_the_same_policy(self):
        info=commercial_policy({'name':'Bella Brazil'},'AP','VAREJO',Decimal('4000'),'28')
        self.assertEqual(info['policyVersion'],BELLA_POLICY_VERSION)
        with self.assertRaises(HTTPException):commercial_policy({'name':'Bella Brazil'},'AP','VAREJO',Decimal('2000'),'28')

class MigrationTests(unittest.TestCase):
    def test_revision_is_idempotent_and_preserves_pack_commissions_and_history(self):
        from copy import deepcopy
        class DB:
            def __init__(self):
                self.payload={'id':'Bella Brazil','name':'Bella Brazil','minimumByState':{'PA':'2000','AP':'3000'},'freight':'CIF','paymentOptions':['28','28/35'],'commissionByChannel':{'VAREJO':8,'ATACADO':5},'packSize':6}
                self.updates=0;self.audit=0
            def execute(self,sql,args=()):
                if sql.startswith('SELECT payload'):return type('Result',(),{'fetchone':lambda inner:(deepcopy(self.payload),)})()
                if sql.startswith('UPDATE entities'):
                    self.payload=deepcopy(args[0].obj);self.updates+=1
                    assert "kind='industry'" in sql
                elif sql.startswith('INSERT INTO audit_log'):self.audit+=1
                else:raise AssertionError('Unexpected mutation')
        db=DB();self.assertTrue(migrate_bella_policy(db));self.assertFalse(migrate_bella_policy(db))
        self.assertEqual(db.updates,1);self.assertEqual(db.audit,1)
        self.assertEqual(db.payload['packSize'],6)
        self.assertEqual(db.payload['commissionByChannel'],{'VAREJO':8,'ATACADO':5})
        self.assertEqual(db.payload['commercialPolicyHistory'][0]['minimumByState']['AP'],'3000')
