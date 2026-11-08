from datetime import date
import unittest
from fastapi import HTTPException
from pipeline_rules import validate_opportunity, canonical_stage

DAY=date(2026,11,8)
def item(**changes):
    value={'stage':'Prospectado','clientId':'a','owner':'Erika','next':'Enviar catálogo','contactEvidence':'Comprador solicitou catálogo','followUp':str(DAY)}
    value.update(changes);return value

def quote(**changes):
    value={'clientId':'a','sellerResponsible':'Erika','approvalStatus':'Aprovada','documentIssuedAt':str(DAY),'sentAt':str(DAY),'acceptedAt':str(DAY),'convertedOrderId':'o'}
    value.update(changes);return value

class PipelineTests(unittest.TestCase):
    def test_required_commitment_and_basic_client(self):
        for changes in ({'next':''},{'contactEvidence':''},{'followUp':'2026-11-07'},{'followUp':'2026-02-30'}):
            with self.assertRaises(HTTPException):validate_opportunity(item(**changes),{},DAY)
        with self.assertRaises(HTTPException):validate_opportunity(item(),{},DAY,client={'name':'Loja'})
    def test_stage_two_requires_emitted_sent_approved_cpq(self):
        for q in (None,quote(clientId='b'),quote(sellerResponsible='Outro'),quote(documentIssuedAt=None),quote(sentAt=None),quote(approvalStatus='Pendente')):
            with self.assertRaises(HTTPException):validate_opportunity(item(stage='Proposta enviada'),{},DAY,q)
        validate_opportunity(item(stage='Proposta enviada'),{},DAY,quote(acceptedAt=None))
    def test_negotiation_requires_real_acceptance(self):
        with self.assertRaises(HTTPException):validate_opportunity(item(stage='Negociação'),{},DAY,quote(acceptedAt=None))
        validate_opportunity(item(stage='Negociação'),{},DAY,quote())
    def test_order_conversion_and_billing_gate(self):
        orders={'o':{'id':'o','clientId':'a','status':'Confirmado'}}
        value=item(stage='Pedido confirmado',orderId='o')
        with self.assertRaises(HTTPException):validate_opportunity(value,orders,DAY,quote(convertedOrderId=None))
        validate_opportunity(value,orders,DAY,quote())
        value['stage']='Pós-venda'
        with self.assertRaises(HTTPException):validate_opportunity(value,orders,DAY,quote())
        orders['o']['status']='Faturado';validate_opportunity(value,orders,DAY,quote())
    def test_loss_reason_and_legacy_labels(self):
        with self.assertRaises(HTTPException):validate_opportunity({'stage':'Perdido'},{},DAY)
        validate_opportunity({'stage':'Perdido','lossReason':'Sem orçamento'},{},DAY)
        self.assertEqual(canonical_stage('Qualificado'),'Prospectado')
        self.assertEqual(canonical_stage('Faturado'),'Pós-venda')
