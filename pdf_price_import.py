"""Local text/OCR extraction. Every price remains a candidate for human review."""
import io
import re
import threading
import time
from decimal import Decimal
from pypdf import PdfReader

_OCR_LOCK = threading.Lock()
_ENGINE = None


def ocr_page(content, index):
    import pymupdf
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
    with pymupdf.open(stream=content, filetype='pdf') as doc:
        page = doc[index]
        scale = min(2, 1800 / max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        result, _ = _ENGINE(image)
    # Join cells only when their boxes share a row; preserve low-confidence rows for review.
    lines = []
    for box, text, score in sorted(result or [], key=lambda x: (min(p[1] for p in x[0]), min(p[0] for p in x[0]))):
        y = sum(p[1] for p in box) / 4
        height = max(p[1] for p in box) - min(p[1] for p in box)
        row = next((r for r in lines if abs(r['y']-y) <= max(5, min(r['height'], height)*0.45)), None)
        if row is None:
            row = {'y': y, 'height': height, 'cells': []}; lines.append(row)
        row['cells'].append((min(p[0] for p in box), text, float(score)))
    return [(' '.join(c[1] for c in sorted(row['cells'])), min(c[2] for c in row['cells'])) for row in lines]


def extract_price_candidates(content):
    if not content.startswith(b'%PDF-') or len(content)>10*1024*1024:
        raise ValueError('Selecione um PDF válido de até 10 MB.')
    reader=PdfReader(io.BytesIO(content))
    if reader.is_encrypted or len(reader.pages)>100:
        raise ValueError('PDF protegido ou acima de 100 páginas.')
    if not _OCR_LOCK.acquire(blocking=False):
        raise ValueError('Já há uma extração em andamento. Tente novamente em instantes.')
    try:
        rows=[]; unmatched=[]; seen=set(); scanned=[]; started=time.monotonic()
        pattern=re.compile(r'^\s*([A-Za-z0-9][A-Za-z0-9._/-]{0,49})\s+(.+?)\s+(?:R\$\s*)?([0-9][0-9.]*,[0-9]{2})\s*$')
        for index,page in enumerate(reader.pages):
            text=page.extract_text() or ''
            lines=[(line,1.0) for line in text.splitlines()]
            # Mixed PDFs: process image pages even if a small heading is selectable.
            if bool(page.images) and not any(pattern.match(line) for line in text.splitlines()):
                if len(scanned)>=20 or time.monotonic()-started>120:
                    raise ValueError('Divida PDFs digitalizados em arquivos menores (até 20 páginas de OCR).')
                lines=ocr_page(content,index); scanned.append(index+1)
            for line,confidence in lines:
                match=pattern.match(line)
                if not match or confidence<0.85:
                    if re.search(r'\d+[,.]\d{2}',line):unmatched.append(line[:250])
                    continue
                sku,description,value=match.groups()
                price=Decimal(value.replace('.','').replace(',','.'))
                if price<=0 or len(description)>250 or sku in seen:
                    unmatched.append(line[:250]);continue
                seen.add(sku);rows.append({'sku':sku,'description':description,'price':str(price),'page':index+1,'source':'ocr' if index+1 in scanned else 'text','confidence':round(confidence,3)})
                if len(rows)>2000:raise ValueError('Divida a tabela em arquivos com até 2.000 itens.')
        return {'candidates':rows,'unmatched':unmatched[:100],'ocrPages':scanned,'requiresReview':True,'warning':'Confira códigos, descrições e preços antes de importar. OCR pode interpretar caracteres incorretamente; nenhum preço foi gravado.'}
    finally:
        _OCR_LOCK.release()
