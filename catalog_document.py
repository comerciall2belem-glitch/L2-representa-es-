"""Compact, customer-facing price table; no commissions or internal notes."""
import base64
import io
import re
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, CondPageBreak
from daily_report import brl

def build_catalog_pdf(table, industry, prices, base, issued):
    base = Path(base)
    output = io.BytesIO()
    styles = getSampleStyleSheet()
    fonts = Path(reportlab.__file__).parent / 'fonts'
    if 'L2Catalog' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('L2Catalog', str(fonts / 'Vera.ttf')))
        pdfmetrics.registerFont(TTFont('L2CatalogBold', str(fonts / 'VeraBd.ttf')))
    styles['Heading1'].fontName = 'L2CatalogBold'
    styles['Heading1'].textColor = colors.HexColor('#203e47')
    styles['Heading1'].fontSize = 20
    styles.add(ParagraphStyle(name='Section', fontName='L2CatalogBold', fontSize=12, leading=16, textColor=colors.HexColor('#203e47'), spaceBefore=14, spaceAfter=8, keepWithNext=False))
    styles.add(ParagraphStyle(name='Price',fontName='L2CatalogBold',fontSize=10,leading=13,alignment=2,textColor=colors.HexColor('#203e47'))) 
    styles.add(ParagraphStyle(name='Product', fontName='L2Catalog', fontSize=9, leading=12))
    def p(value):
        return Paragraph(escape(str(value or '')), styles['Product'])
    story = []
    logo = base / 'logo-light.jpg.b64'
    image = Image(io.BytesIO(base64.b64decode(logo.read_text())), width=32*mm, height=22*mm, kind='proportional') if logo.exists() else p('L2 Representações')
    heading=[Paragraph(escape(str(table.get('brand',''))),styles['Heading1']),p(table.get('title') or table.get('id')),p('TABELA COMERCIAL | '+str(table.get('state','')))]
    header=Table([[image,heading]],colWidths=[42*mm,138*mm]);header.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'MIDDLE'),('BOTTOMPADDING',(0,0),(-1,-1),12),('LINEBELOW',(0,0),(-1,-1),1,colors.HexColor('#b59b6a'))]));story.append(header)
    story += [Spacer(1,4*mm),p('Emissão: '+issued+' | Vigência: '+str(table.get('effectiveDate') or 'Conforme tabela cadastrada')),p(str(len(prices))+' produtos | Preços em reais | '+str(table.get('focus') or table.get('brand',''))),Spacer(1,3*mm)]
    # Explicit commercial fields only: industry notes may contain commissions.
    terms = []
    if industry.get('paymentOptions'): terms.append('Prazos: '+', '.join(map(str, industry['paymentOptions'])))
    if industry.get('freight'): terms.append('Frete: '+str(industry['freight']))
    if table.get('audience'): terms.append(str(table['audience']))
    for rule in industry.get('commercialPolicy', []):
        if isinstance(rule, dict):
            terms.append('A partir de '+brl(rule.get('minimum'))+' · '+str(rule.get('paymentTerms',''))+' · '+str(rule.get('freight','')))
    if industry.get('minimumByState'):
        terms.append('Pedido mínimo: '+', '.join(str(uf)+' '+brl(value) for uf,value in industry['minimumByState'].items()))
    if terms:
        story.append(Paragraph('Condições comerciais',styles['Section']))
        panel=Table([[p(term)] for term in terms],colWidths=[180*mm]);panel.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#f4f1e9')),('LEFTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]));story.append(panel)
    groups=defaultdict(list)
    for row in prices:
        volume=re.search(r'\b(\d+(?:[.,]\d+)?)\s*(ML|G|KG|L)\b',str(row.get('description','')),re.I)
        group=row.get('category') or row.get('line') or row.get('presentation') or ('Apresentação '+volume.group(0).upper() if volume else 'Produtos')
        groups[str(group)].append(row)
    for group,items in sorted(groups.items()):
        story.append(CondPageBreak(38*mm))
        story.append(Paragraph(escape(group)+' <font size="9">| '+str(len(items))+' itens</font>',styles['Section']))
        has_pack=any(r.get('presentation') or r.get('packaging') or r.get('packSize') for r in items)
        rows=[[p('Código'),p('Descrição')]+([p('Apresentação')] if has_pack else [])+[p('Preço unitário')]]
        for row in sorted(items,key=lambda r:(str(r.get('description','')),str(r.get('sku','')))):
            presentation=row.get('presentation') or row.get('packaging') or ('Caixa '+str(row['packSize'])+' un.' if row.get('packSize') else '-')
            rows.append([p(row.get('sku')),p(row.get('description'))]+([p(presentation)] if has_pack else [])+[Paragraph(escape(brl(row.get('price'))),styles['Price'])])
        grid=Table(rows,colWidths=([25*mm,92*mm,35*mm,28*mm] if has_pack else [25*mm,127*mm,28*mm]),repeatRows=1,hAlign='LEFT')
        grid.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eaf0ee')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f7f9f8')]),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,0),.8,colors.HexColor('#b59b6a')),('LINEBELOW',(0,1),(-1,-1),.2,colors.HexColor('#e1e7e4')),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9)]));story.append(grid)
    def footer(canvas, doc):
        if doc.page>1:
            canvas.setFont('L2CatalogBold',8);canvas.setFillColor(colors.HexColor('#203e47'))
            canvas.drawString(15*mm,287*mm,str(table.get('brand',''))+' | Tabela comercial')
            canvas.setStrokeColor(colors.HexColor('#b59b6a'));canvas.line(15*mm,284*mm,195*mm,284*mm)
        canvas.setFont('L2Catalog',8); canvas.setFillColor(colors.HexColor('#253743'))
        canvas.drawString(15*mm,10*mm,'L2 Representações | Pará & Amapá'); canvas.drawRightString(195*mm,10*mm,str(doc.page))
    SimpleDocTemplate(output, pagesize=(210*mm,297*mm),leftMargin=15*mm,rightMargin=15*mm,topMargin=16*mm,bottomMargin=18*mm,
                      title='Tabela '+str(table.get('brand','')),pageCompression=1).build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
