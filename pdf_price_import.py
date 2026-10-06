"""Conservative PDF extraction. Candidate prices require explicit human review."""
import io
import re
from decimal import Decimal
from pypdf import PdfReader


def extract_price_candidates(content):
    if not content.startswith(b'%PDF-') or len(content)>10*1024*1024:
        raise ValueError('Selecione um PDF válido de até 10 MB.')
    reader=PdfReader(io.BytesIO(content))
    if reader.is_encrypted or len(reader.pages)>100:
        raise ValueError('PDF protegido ou acima de 100 páginas. Envie uma versão menor e sem senha.')
    rows=[]; unmatched=[]; seen=set()
    pattern=re.compile(r'^\s*([A-Za-z0-9][A-Za-z0-9._/-]{0,49})\s+(.+?)\s+(?:R\$\s*)?([0-9][0-9.]*,[0-9]{2})\s*$')
    for page in reader.pages:
        text=(page.extract_text() or '')
        for line in text.splitlines():
            match=pattern.match(line)
            if not match:
                if re.search(r'\d+,\d{2}',line):unmatched.append(line[:250])
                continue
            sku,description,value=match.groups()
            price=Decimal(value.replace('.','').replace(',','.'))
            if price<=0 or len(description)>250 or sku in seen:
                unmatched.append(line[:250]);continue
            seen.add(sku);rows.append({'sku':sku,'description':description,'price':str(price)})
            if len(rows)>2000:raise ValueError('Divida a tabela em arquivos com até 2.000 itens.')
    return {'candidates':rows,'unmatched':unmatched[:100],'requiresReview':True,'warning':'Confira códigos, descrição e preços antes de importar. PDFs digitalizados ou layouts complexos podem exigir CSV; nenhum preço foi gravado.'}
