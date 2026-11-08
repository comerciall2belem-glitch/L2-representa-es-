"""Commercial stage gates, enforced by the synchronization endpoint."""
from datetime import date, datetime
from fastapi import HTTPException

STAGES = ('Prospectado', 'Qualificado', 'Visita agendada', 'Proposta enviada',
          'Negociação', 'Ganho', 'Pedido confirmado', 'Faturado', 'Entregue',
          'Pós-venda', 'Perdido')

def validate_opportunity(item, orders, today):
    stage = item.get('stage')
    if stage not in STAGES:
        raise HTTPException(400, 'Etapa comercial inválida')
    if stage == 'Perdido':
        return
    for key, label in (('next', 'próxima ação'), ('contactEvidence', 'contexto do contato e interesse')):
        value = item.get(key)
        if not isinstance(value, str) or not 3 <= len(value.strip()) <= 1000:
            raise HTTPException(400, 'Informe '+label+' (3 a 1.000 caracteres)')
    try:
        due = date.fromisoformat(item.get('followUp', ''))
    except (ValueError, TypeError):
        raise HTTPException(400, 'Agende a próxima ação da oportunidade')
    if due < today:
        raise HTTPException(400, 'Reagende a próxima ação para hoje ou uma data futura')
    # Prospecting and quote preparation precede formal proposal dispatch.
    if stage in ('Prospectado', 'Qualificado', 'Visita agendada'):
        return
    proposal = orders.get(item.get('proposalId'))
    if not proposal or proposal.get('clientId') != item.get('clientId') or proposal.get('status') == 'Cancelado':
        raise HTTPException(400, 'Vincule a proposta do mesmo cliente')
    try:
        sent = datetime.fromisoformat(str(proposal.get('proposalSentAt', '')).replace('Z', '+00:00'))
        if sent.date() > today:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(400, 'Registre o envio formal da proposta antes de avançar')
    if stage in ('Ganho', 'Pedido confirmado', 'Faturado', 'Entregue', 'Pós-venda'):
        evidence = item.get('acceptanceEvidence')
        if not isinstance(evidence, str) or not 3 <= len(evidence.strip()) <= 1000:
            raise HTTPException(400, 'Registre o aceite das condições comerciais')
    if stage in ('Pedido confirmado', 'Faturado', 'Entregue', 'Pós-venda'):
        order = orders.get(item.get('orderId'))
        if not order or order.get('clientId') != item.get('clientId') or order.get('status') not in ('Confirmado', 'Faturado', 'Entregue'):
            raise HTTPException(400, 'Vincule um pedido confirmado do mesmo cliente')
        if stage in ('Faturado', 'Entregue', 'Pós-venda') and order.get('status') not in ('Faturado', 'Entregue'):
            raise HTTPException(400, 'Aguarde o faturamento real do pedido')
        if stage in ('Entregue', 'Pós-venda') and order.get('status') != 'Entregue':
            raise HTTPException(400, 'Aguarde a entrega real do pedido')
