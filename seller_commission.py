from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fastapi import HTTPException


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
    # Faturamentos já apurados preservam o percentual daquele pedido.
    if previous.get('status') == 'Faturado' and previous.get('sellerResponsible') == order.get('sellerResponsible') and previous.get('sellerCommissionRate') is not None:
        rate = previous['sellerCommissionRate']
    order['sellerCommissionRate'] = str(validate_rate(rate)) if rate is not None else None
    order['sellerCommissionAmount'] = None if rate is None else str((Decimal(str(order.get('amount') or 0)) * validate_rate(rate) / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    order['sellerCommissionStatus'] = 'Percentual não cadastrado' if rate is None else 'Apurada' if order.get('status') == 'Faturado' else 'Previsão'
