"""Compact customer-facing price tables. Never export internal industry notes/rates."""
import base64
import io
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from daily_report import brl

PETROL = colors.HexColor('#01454f')
GOLD = colors.HexColor('#b88c38')
INK = colors.HexColor('#253743')

def commercial_lines(table, industry):
    policy = table.get('commercialPolicy', industry.get('commercialPolicy', []))
    lines = [f"Mínimo {brl(p['minimum'])} | Prazo {p.get('paymentTerms', '-')} dias | Frete {p.get('freight', '-')}"
             for p in policy if isinstance(p, dict) and p.get('minimum') is not None]
    minimums = industry.get('minimumByState', {})
    if not lines and minimums:
        states = ['PA', 'AP'] if table.get('state') == 'ALL' else [table.get('state')]
        lines.append(' | '.join(f"Mínimo {uf}: {brl(minimums[uf])}" for uf in states if uf in minimums))
    if not policy:
        if industry.get('paymentOptions'):
            lines.append('Prazos: ' + '; '.join(industry['paymentOptions']) + ' dias')
        if industry.get('freight'):
            lines.append('Frete: ' + industry['freight'])
    if industry.get('deliveryDays'):
        lines.append('Entrega: ' + ' a '.join(map(str, industry['deliveryDays'])) + ' dias após confirmação')
    return [line for line in lines if line] or ['Consulte as condições comerciais com seu representante.']

def industry_logo(base, industry):
    # Only bundled official assets. No network requests or arbitrary file access.
    name = str(industry.get('logoFile', ''))
    root = (base / 'industry-logos').resolve()
    path = (root / name).resolve()
    if name and path.parent == root and path.suffix.lower() in ('.png', '.jpg', '.jpeg') and path.is_file():
        return ImageReader(str(path))
    return None

def build_catalog_pdf(table, industry, prices, base, emitted):
    buffer = io.BytesIO()
    body = ParagraphStyle('body', fontName='Helvetica', fontSize=10, leading=14, textColor=INK, splitLongWords=True)
    small = ParagraphStyle('small', parent=body, fontSize=9, leading=12)
    price_style = ParagraphStyle('price', parent=body, alignment=TA_RIGHT)
    p = lambda value, style=body: Paragraph(escape(str(value or '-')), style)
    # Original supplied L2 asset, embedded once and reused across pages.
    l2 = ImageReader(io.BytesIO(base64.b64decode((base / 'logo-light.jpg.b64').read_text())))
    brand_logo = industry_logo(base, industry)
    def logo(canvas, image, x, y, width, height):
        iw, ih = image.getSize()
        scale = min(width / iw, height / ih)
        canvas.drawImage(image, x, y, width=iw*scale, height=ih*scale, mask='auto')
    def header(canvas, doc):
        canvas.saveState()
        logo(canvas, l2, 30, 778, 76, 48)
        if brand_logo:
            logo(canvas, brand_logo, 465, 778, 100, 48)
        canvas.setStrokeColor(GOLD)
        canvas.setLineWidth(1)
        canvas.line(30, 773, 565, 773)
        canvas.setFillColor(PETROL)
        canvas.setFont('Helvetica-Bold', 11)
        canvas.drawString(30, 752, str(table.get('brand', ''))[:80])
        canvas.setFillColor(INK)
        canvas.setFont('Helvetica', 9)
        canvas.drawString(30, 736, f"Tabela {table.get('state', '').replace('ALL', 'PA/AP')} | Emissão {emitted:%d/%m/%Y}")
        canvas.setFont('Helvetica', 8)
        canvas.drawString(30, 23, 'L2 Representações | Preços em reais por unidade')
        canvas.drawRightString(565, 23, f'Página {doc.page}')
        canvas.restoreState()
    story = [p(table.get('title') or 'Tabela comercial'), Spacer(1, 10)]
    for line in commercial_lines(table, industry):
        story += [p(line, small), Spacer(1, 4)]
    story.append(Spacer(1, 10))
    rows = [[p(x, ParagraphStyle('th', parent=small, textColor=colors.white, fontName='Helvetica-Bold')) for x in ('Código', 'Descrição', 'Apresentação / Embalagem', 'Preço unit.')]]
    for item in sorted(prices, key=lambda x: (str(x.get('description', '')).casefold(), str(x.get('sku', '')))):
        packaging = item.get('presentation') or item.get('packaging') or (f"Caixa com {item.get('packSize') or industry.get('packSize')} un." if item.get('packSize') or industry.get('packSize') else 'Não informada')
        rows.append([p(item.get('sku'), small), p(item.get('description')), p(packaging, small), p(brl(item.get('price')), price_style)])
    grid = Table(rows, colWidths=[72, 251, 120, 92], repeatRows=1, hAlign='LEFT')
    grid.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), PETROL), ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LEFTPADDING', (0,0), (-1,-1), 8), ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LINEBELOW', (0,1), (-1,-1), .25, colors.HexColor('#e3e9ea'))]))
    story.append(grid)
    doc = SimpleDocTemplate(buffer, pagesize=(595,842), leftMargin=30, rightMargin=30, topMargin=120, bottomMargin=42, pageCompression=1, title=str(table.get('title', 'Tabela comercial')), author='L2 Representações')
    doc.build(story, onFirstPage=header, onLaterPages=header)
    return buffer.getvalue()
