"""Confronto de NF-e XML com itens de pedidos, sem inferir dados de PDFs."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from defusedxml import ElementTree as SafeXML


class InvoiceError(ValueError):
    pass


def reconcile_invoice(order, xml_bytes, brand, expected_recipient_cnpj=None):
    if not isinstance(xml_bytes,bytes) or len(xml_bytes)>5*1024*1024:
        raise InvoiceError('XML da NF-e inválido ou acima de 5 MB')
    try:
        root=SafeXML.fromstring(xml_bytes)
    except Exception as exc:
        raise InvoiceError('XML da NF-e inválido') from exc
    def local(tag): return tag.rsplit('}',1)[-1]
    def child(node,name): return next((x for x in node if local(x.tag)==name),None)
    def value(node,name):
        element=child(node,name) if node is not None else None
        return (element.text or '').strip() if element is not None else ''
    inf=next((x for x in root.iter() if local(x.tag)=='infNFe'),None)
    if inf is None: raise InvoiceError('Envie um XML de NF-e com infNFe')
    number=value(child(inf,'ide'),'nNF')
    if not number or not number.isdigit(): raise InvoiceError('Número da NF-e ausente')
    issuer=value(child(inf,'emit'),'CNPJ')
    recipient=value(child(inf,'dest'),'CNPJ')
    if expected_recipient_cnpj and recipient and recipient!=expected_recipient_cnpj:
        raise InvoiceError('CNPJ do destinatário da NF-e difere do cliente do pedido')
    invoice_items=defaultdict(lambda:[Decimal(0),Decimal(0)])
    try:
        for det in (x for x in inf if local(x.tag)=='det'):
            prod=child(det,'prod');sku=value(prod,'cProd').upper()
            if not sku: raise InvoiceError('NF-e contém produto sem código')
            qty=Decimal(value(prod,'qCom'));total=Decimal(value(prod,'vProd'))
            if not qty.is_finite() or not total.is_finite() or qty<0 or total<0: raise ValueError()
            invoice_items[sku][0]+=qty;invoice_items[sku][1]+=total
    except (InvalidOperation,ValueError) as exc:
        raise InvoiceError('Quantidade ou valor inválido no XML') from exc
    if not invoice_items: raise InvoiceError('NF-e sem itens')
    expected=defaultdict(lambda:[Decimal(0),Decimal(0)])
    for item in order.get('items') or []:
        item_brand=item.get('brand') or order.get('brand')
        if item_brand!=brand: continue
        sku=str(item.get('sku') or '').strip().upper()
        if not sku: continue
        try:
            expected[sku][0]+=Decimal(str(item['quantity']))
            expected[sku][1]+=Decimal(str(item['subtotal']))
        except (InvalidOperation,KeyError,ValueError) as exc:
            raise InvoiceError('Pedido contém item inválido') from exc
    if not expected: raise InvoiceError('Indústria não corresponde aos itens do pedido')
    differences=[]
    for sku in sorted(set(expected)|set(invoice_items)):
        eq,ev=expected[sku];iq,iv=invoice_items[sku]
        if eq!=iq or abs(ev-iv)>Decimal('0.01'):
            differences.append({'sku':sku,'pedidoQuantidade':str(eq),'notaQuantidade':str(iq),
                                'pedidoValor':str(ev.quantize(Decimal('.01'))),
                                'notaValor':str(iv.quantize(Decimal('.01')))})
    return {'invoiceNumber':number,'brand':brand,'issuerCnpj':issuer,'recipientCnpj':recipient,
            'status':'Conferido' if not differences else 'Divergente',
            'orderTotal':str(sum((v[1] for v in expected.values()),Decimal(0)).quantize(Decimal('.01'))),
            'invoiceTotal':str(sum((v[1] for v in invoice_items.values()),Decimal(0)).quantize(Decimal('.01'))),
            'differences':differences}
