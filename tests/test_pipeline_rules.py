from datetime import date
import unittest
from fastapi import HTTPException
from pipeline_rules import validate_opportunity

DAY = date(2026, 11, 8)
def item(**changes):
    return dict(stage='Prospectado', clientId='a', next='Enviar catálogo', contactEvidence='Comprador solicitou o catálogo', followUp=str(DAY), **changes)

class PipelineTests(unittest.TestCase):
    def test_missing_next_step_and_past_date_rejected(self):
        for change in ({'next':''}, {'contactEvidence':''}, {'followUp':'2026-11-07'}, {'followUp':'2026-02-30'}):
            value=item();value.update(change)
            with self.assertRaises(HTTPException):validate_opportunity(value,{},DAY)
    
    def test_sent_proposal_must_belong_to_client(self):
        value=item();value.update(stage='Negociação',proposalId='p')
        for proposal in ({'clientId':'b','proposalSentAt':str(DAY)}, {'clientId':'a'}, {'clientId':'a','proposalSentAt':'2026-11-09'}):
            with self.assertRaises(HTTPException):validate_opportunity(value,{'p':proposal},DAY)
        validate_opportunity(value,{'p':{'clientId':'a','proposalSentAt':str(DAY)}},DAY)
    
    def test_acceptance_and_real_order_required(self):
        value=item();value.update(stage='Pedido confirmado',proposalId='p',orderId='o')
        orders={'p':{'clientId':'a','proposalSentAt':str(DAY)},'o':{'clientId':'a','status':'Confirmado'}}
        with self.assertRaises(HTTPException):validate_opportunity(value,orders,DAY)
        value['acceptanceEvidence']='Aceite recebido por e-mail'
        validate_opportunity(value,orders,DAY)
        value['stage']='Entregue'
        with self.assertRaises(HTTPException):validate_opportunity(value,orders,DAY)
        orders['o']['status']='Entregue'
        validate_opportunity(value,orders,DAY)
    
    def test_lost_opportunity_can_be_closed_without_followup(self):
        validate_opportunity({'stage':'Perdido'}, {},DAY)
