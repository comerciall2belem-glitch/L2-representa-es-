"""Transactional NF-e confirmation. Caller owns the database transaction and scope checks."""
import hashlib
import json
from copy import deepcopy
from decimal import Decimal
from psycopg.types.json import Jsonb
from order_reconciliation import consolidated_billing, InvoiceError


def order_fingerprint(order):
    return hashlib.sha256(json.dumps({'clientId':order.get('clientId'),'items':order.get('items'),
        'brand':order.get('brand')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def confirm_invoice(con, order_id, invoice_id, user, recipient, now):
    # Same transaction lock as offline sync; protects duplicate imports across orders.
    con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
    row=con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s FOR UPDATE",(order_id,)).fetchone()
    if not row: raise InvoiceError('Pedido não encontrado')
    order=deepcopy(row[0])
    row=con.execute('SELECT reconciliation FROM order_invoices WHERE id=%s AND order_id=%s FOR UPDATE',(invoice_id,order_id)).fetchone()
    if not row: raise InvoiceError('Conferência não encontrada')
    report=deepcopy(row[0])
    if report.get('confirmed'): return report,order
    if order.get('status')=='Cancelado': raise InvoiceError('Pedido cancelado não pode receber faturamento')
    if report.get('orderFingerprint')!=order_fingerprint(order): raise InvoiceError('Pedido alterado após a extração. Faça a conferência novamente.')
    if not recipient or report.get('recipientCnpj')!=recipient:
        raise InvoiceError('CNPJ destinatário ausente ou diferente do cliente. Revise o documento.')
    # XML identity is authoritative. PDFs without key use issuer + series + number + brand.
    confirmed=con.execute("SELECT order_id,reconciliation FROM order_invoices WHERE reconciliation->>'confirmed'='true'").fetchall()
    for other_order,other in confirmed:
        same_key=report.get('invoiceKey') and report.get('invoiceKey')==other.get('invoiceKey')
        same_document=(report.get('issuerCnpj')==other.get('issuerCnpj') and report.get('series','')==other.get('series','')
                       and report['invoiceNumber']==other['invoiceNumber'] and report['brand']==other['brand'])
        if same_key or same_document: raise InvoiceError('Esta NF-e já foi contabilizada. Não será lançado faturamento duplicado.')
    reports=[r for oid,r in confirmed if oid==order_id]+[report]
    billing=consolidated_billing(order,reports)
    report.update(confirmed=True,confirmedBy=user,confirmedAt=now)
    con.execute('UPDATE order_invoices SET reconciliation=%s WHERE id=%s',(Jsonb(report),invoice_id))
    order.setdefault('originalAmount',order.get('amount'))
    order.update(amount=billing['billedAmount'],billedAmount=billing['billedAmount'],
        billedByBrand=billing['billedByBrand'],invoiceBilling=billing,status='Faturado',updatedBy=user,updatedAt=now)
    con.execute("UPDATE entities SET payload=%s,updated_at=now() WHERE kind='order' AND id=%s",(Jsonb(order),order_id))
    con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'order_invoice',%s,'billing_confirmed')",(user,invoice_id))
    return report,order
