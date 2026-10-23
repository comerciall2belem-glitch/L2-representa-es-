"""Read standard DANFE item rows; reject incomplete extraction using printed totals."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape


def reconcile_danfe(order, text, brand, expected_recipient_cnpj=None):
    from order_reconciliation import reconcile_invoice, InvoiceError
    def norm(s):return ''.join(c for c in unicodedata.normalize('NFKD',s.upper()) if not unicodedata.combining(c))
    normalized=norm(text)
    if not re.search(r'DADOS\s+DO\s+PRODUTO|DESCRICAO\s+DO\s+PRODUTO',normalized) or not re.search(r'NCM\s*/?\s*SH',normalized):
        raise InvoiceError('Tabela de produtos da DANFE não reconhecida')
    def number(s):
        try:value=Decimal(s.replace('.','').replace(',','.')) if ',' in s else Decimal(s)
        except InvalidOperation:raise InvoiceError('Número ilegível na tabela da DANFE') from None
        if not value.is_finite() or value<0:raise InvoiceError('Valor inválido na DANFE')
        return value
    keys={re.sub(r'\D','',m[0]) for m in re.finditer(r'(?<!\d)(?:\d{4}[ \t]+){10}\d{4}(?!\d)',text)}
    if len(keys)>1:raise InvoiceError('O PDF contém mais de uma chave de NF-e')
    key=next(iter(keys),'')
    numbers={str(int(re.sub(r'\D','',m[1]))) for m in re.finditer(r'\bN[º°]\s*\.?\s*([\d.]+)',text,re.I)}
    invoice_number=str(int(key[25:34])) if key else next(iter(numbers),'')
    if not invoice_number or (numbers and numbers!={invoice_number}):raise InvoiceError('Número da NF-e ausente ou divergente entre páginas')
    def cnpjs(s):return re.findall(r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b',s)
    dest=re.search(r'DESTINATARIO\s*/\s*REMETENTE',normalized)
    if not dest:raise InvoiceError('Seção de destinatário não identificada na DANFE')
    block=re.split(r'FATURA|CALCULO DO IMPOSTO|TRANSPORTADOR|DADOS DO PRODUTO',normalized[dest.end():],maxsplit=1)[0]
    recipients=list(dict.fromkeys(re.sub(r'\D','',v) for v in cnpjs(block)))
    if len(recipients)!=1:raise InvoiceError('CNPJ do destinatário ausente ou ambíguo na DANFE')
    recipient=recipients[0]
    if expected_recipient_cnpj and recipient!=expected_recipient_cnpj:raise InvoiceError('CNPJ do destinatário da NF-e difere do cliente do pedido')
    emitters=cnpjs(normalized[:dest.start()]);issuer=key[6:20] if key else re.sub(r'\D','',emitters[-1]) if emitters else ''
    def total(label):
        lines=normalized.splitlines()
        for i,line in enumerate(lines):
            match=re.search(label,line)
            if not match:continue
            candidates=[line[match.end():]]+[l for l in lines[i+1:i+5] if l.strip()][:1]
            for candidate in candidates:
                values=re.findall(r'(?<![\d.,])\d[\d.]*,\d{2}(?!\d)',candidate)
                if values:return number(values[-1])
        return None
    product_total=total(r'VALOR\s+TOTAL\s+(?:DOS\s+)?PRODUTOS')
    fiscal_total=total(r'VALOR\s+TOTAL\s+DA\s+NOTA')
    if product_total is None:raise InvoiceError('Não foi possível validar o total dos produtos da DANFE; extração recusada')
    item_pattern=re.compile(r'^\s*([A-Z0-9][A-Z0-9._/-]{0,79})\s+(.+?)\s+(\d{4}\.?\d{2}\.?\d{2})\s+(?:\d{3,4}|\d/\d{3})\s+\d\.?\d{3}\s+[A-Z0-9]{1,6}\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)(?:\s|$)',re.I)
    items=[]
    for line in text.splitlines():
        match=item_pattern.match(line)
        if not match:continue
        sku,description,ncm,qty,unit,value=match.groups();qty,unit,value=map(number,(qty,unit,value))
        if qty<=0 or abs((qty*unit).quantize(Decimal('.01'))-value)>Decimal('.02'):
            raise InvoiceError('Quantidade, preço ou total incompatíveis na linha '+sku)
        items.append((sku,qty,value))
    if not items:raise InvoiceError('Nenhum item reconhecido na tabela da DANFE')
    extracted=sum((v for _,_,v in items),Decimal(0))
    if abs(extracted-product_total)>Decimal('.02'):
        raise InvoiceError('Extração incompleta: soma dos itens '+str(extracted)+' difere do total de produtos '+str(product_total)+'. Nenhum confronto foi concluído.')
    details=''.join('<det><prod><cProd>'+escape(sku)+'</cProd><qCom>'+str(qty)+'</qCom><vProd>'+str(value)+'</vProd></prod></det>' for sku,qty,value in items)
    xml=('<NFe><infNFe><ide><nNF>'+invoice_number+'</nNF></ide><emit><CNPJ>'+issuer+'</CNPJ></emit><dest><CNPJ>'+recipient+'</CNPJ></dest>'+details+'</infNFe></NFe>').encode()
    report=reconcile_invoice(order,xml,brand,expected_recipient_cnpj)
    report.update(source='PDF DANFE',invoiceKey=key,series=key[22:25] if key else '',invoiceProductTotal=str(product_total.quantize(Decimal('.01'))),invoiceTotal=str((fiscal_total if fiscal_total is not None else product_total).quantize(Decimal('.01'))),warnings=[],extractedItemCount=len(items),extractionValidated=True)
    return report
