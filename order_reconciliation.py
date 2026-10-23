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
    def child(node,name): return next((x for x in (node if node is not None else []) if local(x.tag)==name),None)
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
            qty=Decimal(value(prod,'qCom'));total=Decimal(value(prod,'vProd'))-Decimal(value(prod,'vDesc') or '0')
            if not qty.is_finite() or not total.is_finite() or qty<0 or total<0: raise ValueError()
            invoice_items[sku][0]+=qty;invoice_items[sku][1]+=total
    except (InvalidOperation,ValueError) as exc:
        raise InvoiceError('Quantidade ou valor inválido no XML') from exc
    if not invoice_items: raise InvoiceError('NF-e sem itens')
    product_total=sum((v[1] for v in invoice_items.values()),Decimal(0)).quantize(Decimal('.01'))
    fiscal_total=value(child(child(inf,'total'),'ICMSTot'),'vNF')
    try:
        invoice_total=Decimal(fiscal_total) if fiscal_total else product_total
        if not invoice_total.is_finite() or invoice_total<0: raise ValueError()
    except (InvalidOperation,ValueError) as exc: raise InvoiceError('Total da NF-e inválido') from exc
    expected=defaultdict(lambda:[Decimal(0),Decimal(0)])
    for item in order.get('items') or []:
        item_brand=item.get('brand') or order.get('brand')
        if item_brand!=brand: continue
        sku=str(item.get('sku') or '').strip().upper()
        if not sku: continue
        try:
            qty=Decimal(str(item['quantity']))
            total=Decimal(str(item['subtotal'])) if item.get('subtotal') is not None else qty*Decimal(str(item['unitPrice']))
            if not qty.is_finite() or not total.is_finite() or qty<=0 or total<0: raise ValueError()
            expected[sku][0]+=qty
            expected[sku][1]+=total
        except (InvalidOperation,KeyError,ValueError) as exc:
            raise InvoiceError('Pedido contém item inválido') from exc
    if not expected: raise InvoiceError('Indústria não corresponde aos itens do pedido')
    differences=[]
    descriptions={str(i.get('sku','')).strip().upper():str(i.get('description') or i.get('name') or i.get('sku') or '') for i in order.get('items',[]) if (i.get('brand') or order.get('brand'))==brand}
    lines=[]
    for sku in sorted(set(expected)|set(invoice_items)):
        eq,ev=expected[sku];iq,iv=invoice_items[sku]
        cut=max(Decimal(0),eq-iq)
        lines.append({'sku':sku,'description':descriptions.get(sku,sku),
                      'orderedQuantity':str(eq),'invoicedQuantity':str(iq),'cutQuantity':str(cut),
                      'cutValue':str((ev*cut/eq if eq else Decimal(0)).quantize(Decimal('.01'))),
                      'invoiceValue':str(iv.quantize(Decimal('.01'))),
                      'classification':('Item extra' if not eq else 'Excesso faturado' if iq>eq else 'Faturamento Completo' if iq==eq else 'Corte Parcial' if iq>0 else 'Corte Total')})
        if eq!=iq or abs(ev-iv)>Decimal('0.01'):
            differences.append({'sku':sku,'pedidoQuantidade':str(eq),'notaQuantidade':str(iq),
                                'pedidoValor':str(ev.quantize(Decimal('.01'))),
                                'notaValor':str(iv.quantize(Decimal('.01')))})
    return {'invoiceNumber':number,'brand':brand,'issuerCnpj':issuer,'recipientCnpj':recipient,
            'invoiceKey':inf.get('Id','').removeprefix('NFe'),'series':value(child(inf,'ide'),'serie'),
            'invoiceDate':(value(child(inf,'ide'),'dhEmi') or value(child(inf,'ide'),'dEmi'))[:10],
            'status':'Conferido' if not differences else 'Divergente',
            'orderTotal':str(sum((v[1] for v in expected.values()),Decimal(0)).quantize(Decimal('.01'))),
            'invoiceTotal':str(invoice_total.quantize(Decimal('.01'))),
            'invoiceProductTotal':str(product_total),'totalBasis':'Total da NF-e' if fiscal_total else 'Soma dos itens extraídos',
            'items':lines,'cuts':[line for line in lines if Decimal(line['cutQuantity'])>0],
            'differences':differences}

def reconcile_invoice_text(order, text, brand, expected_recipient_cnpj=None):
    """Parse labelled or delimited invoice text; reject ambiguous item rows."""
    import re
    import unicodedata
    from xml.sax.saxutils import escape
    if not isinstance(text, str) or not text.strip() or len(text) > 200000:
        raise InvoiceError('Cole o texto da nota, até 200.000 caracteres')
    def norm(value):
        return ''.join(c for c in unicodedata.normalize('NFKD', value.lower()) if not unicodedata.combining(c)).strip()
    def number(value):
        value = re.sub(r'[^0-9,.-]', '', value.strip())
        if ',' in value: value = value.replace('.', '').replace(',', '.')
        try: result = Decimal(value)
        except InvalidOperation: raise InvoiceError('Quantidade ou valor inválido no texto')
        if not result.is_finite() or result < 0: raise InvoiceError('Quantidade ou valor inválido no texto')
        return result
    nf = re.search(r'(?:n[úu]mero\s*(?:da\s*)?(?:nf(?:-?e)?|nota)|nota\s*fiscal|nf(?:-?e)?|nNF)\s*(?:n[º°o.]\s*)?[:#.-]?\s*(\d[\d.]*)', text, re.I)
    if not nf: raise InvoiceError('Número da nota não encontrado. Inclua NF: 12345')
    invoice_number = re.sub(r'\D', '', nf.group(1))
    cnpjs = re.findall(r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b', text)
    cnpjs = list(dict.fromkeys(re.sub(r'\D', '', v) for v in cnpjs))
    if not cnpjs: raise InvoiceError('CNPJ não encontrado no texto')
    def labelled_cnpj(label):
        m = re.search(label + r'[^\n\d]{0,45}(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})', text, re.I)
        return re.sub(r'\D', '', m.group(1)) if m else ''
    recipient = labelled_cnpj(r'(?:destinat[aá]rio|cliente|CNPJ\s*dest)')
    issuer = labelled_cnpj(r'(?:emitente|fornecedor|CNPJ\s*emit)')
    if not recipient and expected_recipient_cnpj in cnpjs: recipient = expected_recipient_cnpj
    if not recipient and len(cnpjs) == 1 and not issuer: recipient = cnpjs[0]
    columns = None
    items = []
    suspicious = []
    def header(parts):
        names = [norm(p) for p in parts]
        def find(pattern): return next((i for i, name in enumerate(names) if re.search(pattern, name)), None)
        mapping = {'sku':find(r'^(?:sku|codigo|cod\.?|cprod|codigo do produto|codigo produto)$'),
                   'qty':find(r'^(?:quantidade|qtd\.?|qtde\.?|qcom)$'),
                   'total':find(r'^(?:total|valor total|vprod|total item|valor do item|v\\.? total)$'),
                   'unit':find(r'^(?:preco unitario|valor unitario|unitario|v\\.? unitario|vuncom)$')}
        return mapping if mapping['sku'] is not None and mapping['qty'] is not None and (mapping['total'] is not None or mapping['unit'] is not None) else None
    for raw in text.splitlines():
        line = raw.strip()
        if not line: continue
        parts = re.split(r'\s*[;|\t]\s*|\s{2,}', line)
        mapping = header(parts)
        if mapping:
            columns = mapping
            continue
        if columns and len(parts) > max(v for v in columns.values() if v is not None):
            code = parts[columns['sku']].strip()
            try:
                qty = number(parts[columns['qty']])
                total = number(parts[columns['total']]) if columns['total'] is not None else qty * number(parts[columns['unit']])
            except InvoiceError:
                suspicious.append(line)
                continue
            if code and qty > 0: items.append((code, qty, total))
            else: suspicious.append(line)
            continue
        match = re.search(r'(?:SKU|c[oó]digo|cProd)\s*[:=]\s*(.+?)\s+(?:quantidade|qtd\.?|qCom)\s*[:=]\s*([\d.,]+)\s+(?:valor\s*total|total|vProd)\s*[:=]\s*(?:R\$\s*)?([\d.,]+)', line, re.I)
        if match:
            items.append((match.group(1).strip(), number(match.group(2)), number(match.group(3))))
        elif re.search(r'(?:SKU|c[oó]digo|cProd)\s*[:=]', line, re.I):
            suspicious.append(line)
    if suspicious:
        raise InvoiceError('Há linhas de itens incompletas ou ambíguas. Use colunas Código; Quantidade; Valor total e confira todas as linhas.')
    if not items: raise InvoiceError('Itens não reconhecidos. Use cabeçalho Código; Quantidade; Valor total, seguido dos produtos.')
    details = ''.join('<det><prod><cProd>'+escape(code)+'</cProd><qCom>'+str(qty)+'</qCom><vProd>'+str(total)+'</vProd></prod></det>' for code,qty,total in items)
    xml = ('<nfeProc><NFe><infNFe><ide><nNF>'+invoice_number+'</nNF></ide><emit><CNPJ>'+issuer+'</CNPJ></emit><dest><CNPJ>'+recipient+'</CNPJ></dest>'+details+'</infNFe></NFe></nfeProc>').encode()
    report = reconcile_invoice(order, xml, brand)
    total_match=re.search(r'(?:valor\s*total\s*(?:da\s*)?(?:nota|nf(?:-?e)?)|total\s*da\s*nota|vNF)\s*[:=]\s*(?:R\$\s*)?([\d.,]+)',text,re.I)
    if total_match:
        report['invoiceTotal']=str(number(total_match.group(1)).quantize(Decimal('.01')))
        report['totalBasis']='Total da NF-e extraído do texto'
    report['source'] = 'texto colado'
    report['cnpjs'] = cnpjs
    report['warnings'] = []
    if expected_recipient_cnpj and recipient and recipient != expected_recipient_cnpj:
        report['warnings'].append('CNPJ do destinatário difere do cliente do pedido')
        report['status'] = 'Divergente'
    elif not recipient:
        report['warnings'].append('Não foi possível identificar o CNPJ do destinatário. Confira os CNPJs extraídos.')
        if report['status'] == 'Conferido': report['status'] = 'Revisar'
    return report

def extract_invoice_pdf_text(content):
    import io
    from pypdf import PdfReader
    if not isinstance(content, bytes) or not content.startswith(b'%PDF-') or len(content) > 10*1024*1024:
        raise InvoiceError('Envie um PDF válido de até 10 MB')
    try: reader = PdfReader(io.BytesIO(content))
    except Exception as exc: raise InvoiceError('Não foi possível abrir o PDF') from exc
    if reader.is_encrypted or len(reader.pages) > 20:
        raise InvoiceError('PDF protegido ou acima de 20 páginas')
    pages = []
    scanned = []
    for index, page in enumerate(reader.pages):
        text = page.extract_text(extraction_mode='layout') or ''
        if len(text.strip()) < 40:
            from pdf_price_import import ocr_page, _OCR_LOCK
            if not _OCR_LOCK.acquire(blocking=False): raise InvoiceError('Extração em andamento. Tente novamente.')
            try: text = '\n'.join(line for line, confidence in ocr_page(content, index))
            finally: _OCR_LOCK.release()
            scanned.append(index+1)
        pages.append(text)
    text = '\n'.join(pages).strip()
    if not text: raise InvoiceError('PDF sem texto legível')
    return text, scanned


def consolidated_billing(order, reports):
    """Rebuild totals from confirmed invoices, never add to a mutable running total."""
    from xml.sax.saxutils import escape
    items=[]
    by_brand={}
    for brand in sorted({i.get('brand') or order.get('brand') for i in order.get('items',[])}):
        totals=defaultdict(lambda:[Decimal(0),Decimal(0)])
        for report in reports:
            if report['brand']!=brand: continue
            for item in report['items']:
                totals[item['sku']][0]+=Decimal(item['invoicedQuantity'])
                totals[item['sku']][1]+=Decimal(item['invoiceValue'])
        details=''.join('<det><prod><cProd>'+escape(sku)+'</cProd><qCom>'+str(qty)+'</qCom><vProd>'+str(total)+'</vProd></prod></det>' for sku,(qty,total) in totals.items())
        if not details:
            # A zero row permits a report for an industry not invoiced yet.
            first=next(i for i in order['items'] if (i.get('brand') or order.get('brand'))==brand)
            details='<det><prod><cProd>'+escape(first['sku'])+'</cProd><qCom>0</qCom><vProd>0</vProd></prod></det>'
        result=reconcile_invoice(order,('<NFe><infNFe><ide><nNF>0</nNF></ide>'+details+'</infNFe></NFe>').encode(),brand)
        by_brand[brand]=str(sum((Decimal(r['invoiceTotal']) for r in reports if r['brand']==brand),Decimal(0)).quantize(Decimal('.01')))
        items.extend(dict(i,brand=brand) for i in result['items'])
    if any(i['classification'] in ('Item extra','Excesso faturado') for i in items):
        raise InvoiceError('Há SKU não identificado ou quantidade acima do pedido. Revise antes de confirmar.')
    return {'items':items,'cuts':[i for i in items if Decimal(i['cutQuantity'])>0],
            'billedByBrand':by_brand,'billedAmount':str(sum(map(Decimal,by_brand.values()),Decimal(0)).quantize(Decimal('.01'))),
            'cutTotal':str(sum((Decimal(i['cutValue']) for i in items),Decimal(0)).quantize(Decimal('.01')))}
