"""Five commercial stages, validated on the server; legacy labels normalize on save."""
from datetime import date
from fastapi import HTTPException

STAGES = ('Prospectado','Proposta enviada','Negociação','Pedido confirmado','Pós-venda','Perdido')
ALIASES = {'Qualificado':'Prospectado','Visita agendada':'Prospectado','Ganho':'Negociação','Faturado':'Pós-venda','Entregue':'Pós-venda'}

def canonical_stage(stage): return ALIASES.get(stage, stage)

def validate_opportunity(item, orders, today, quote=None, client=None):
    stage = canonical_stage(item.get('stage'))
    if stage not in STAGES: raise HTTPException(400, 'Etapa comercial inválida')
    item['stage'] = stage
    if stage == 'Perdido':
        if not str(item.get('lossReason') or '').strip(): raise HTTPException(400, 'Informe o motivo da perda')
        return
    for key, label in (('next','próxima ação'), ('contactEvidence','contexto do contato e interesse')):
        value = item.get(key)
        if not isinstance(value,str) or not 3 <= len(value.strip()) <= 1000:
            raise HTTPException(400, 'Informe '+label+' (3 a 1.000 caracteres)')
    try: due = date.fromisoformat(item.get('followUp',''))
    except (ValueError,TypeError): raise HTTPException(400,'Agende a próxima ação da oportunidade')
    if due < today: raise HTTPException(400,'Reagende a próxima ação para hoje ou uma data futura')
    if client is not None and not all(str(client.get(k) or '').strip() for k in ('name','taxId','city','state')):
        raise HTTPException(400,'Complete o cadastro base do cliente')
    if stage == 'Prospectado': return
    if not quote or quote.get('clientId') != item.get('clientId') or quote.get('sellerResponsible') != item.get('owner'):
        raise HTTPException(400,'Vincule a proposta CPQ do mesmo cliente e vendedor')
    if quote.get('approvalStatus') != 'Aprovada': raise HTTPException(409,'Alçada pendente ou recusada')
    if not quote.get('documentIssuedAt') or not quote.get('sentAt'): raise HTTPException(400,'Emita e registre o envio da proposta CPQ')
    if stage in ('Negociação','Pedido confirmado','Pós-venda') and not quote.get('acceptedAt'):
        raise HTTPException(400,'Registre o aceite formal das condições comerciais')
    if stage in ('Pedido confirmado','Pós-venda'):
        order = orders.get(item.get('orderId'))
        if not order or order.get('clientId') != item.get('clientId') or order.get('status') not in ('Confirmado','Faturado','Entregue') or quote.get('convertedOrderId') != order['id']:
            raise HTTPException(400,'Converta a proposta aceita em pedido no ERP')
        if stage == 'Pós-venda' and order.get('status') not in ('Faturado','Entregue'):
            raise HTTPException(400,'Aguarde o faturamento real do pedido')
