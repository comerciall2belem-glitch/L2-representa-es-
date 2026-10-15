from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fastapi import HTTPException
import unicodedata


def is_closed_order(order):
    def normalized(value):
        return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '')) if not unicodedata.combining(c)).strip().casefold()
    stages = [order.get('status')] + [h.get('stage') for h in (order.get('stageHistory') or []) if isinstance(h, dict)]
    return any(normalized(v) in {'faturado', 'concluido', 'entregue', 'em transporte', 'pos-venda concluido'} for v in stages)


def preserve_closed_order(order, previous):
    """Closed orders are immutable, including legacy absent fields. Replays are safe."""
    if not previous or not is_closed_order(previous):
        return False
    ignored = {'updatedAt', 'updatedBy'}
    if {k:v for k,v in order.items() if k not in ignored} != {k:v for k,v in previous.items() if k not in ignored}:
        raise HTTPException(409, 'Pedido faturado ou concluído é imutável; registre ajustes separadamente')
    order.clear()
    order.update(previous)
    return True


def validate_rate(value):
    try:
        rate = Decimal(str(value).replace(',', '.'))
    except (ValueError, InvalidOperation):
        raise HTTPException(400, 'Informe a comissão do vendedor entre 0 e 100%, com até duas casas decimais')
    if not rate.is_finite() or not 0 <= rate <= 100 or rate.as_tuple().exponent < -2:
        raise HTTPException(400, 'Informe a comissão do vendedor entre 0 e 100%, com até duas casas decimais')
    return rate


def apply_seller_commission(order, rate, previous=None):
    previous = previous or {}
    if preserve_closed_order(order, previous):
        return
    order['sellerCommissionRate'] = str(validate_rate(rate)) if rate is not None else None
    order['sellerCommissionAmount'] = None if rate is None else str((Decimal(str(order.get('amount') or 0)) * validate_rate(rate) / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    order['sellerCommissionStatus'] = 'Percentual não cadastrado' if rate is None else 'Apurada' if order.get('status') == 'Faturado' else 'Previsão'
