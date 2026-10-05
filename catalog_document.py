"""Compact, customer-facing price table; no commissions or internal notes."""
import base64
import io
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
import reportlab
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
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
    styles['Heading1'].textColor = colors.HexColor('#034955')
    styles.add(ParagraphStyle(name='Product', fontName='L2Catalog', fontSize=9, leading=12))
    def p(value):
        return Paragraph(escape(str(value or '')), styles['Product'])
    story = []
    # Use the supplied original logo, never redraw it.
    logo = base / 'logo-light.jpg.b64'
    if logo.exists():
        image = Image(io.BytesIO(base64.b64decode(logo.read_text())), width=34*mm, height=18*mm, kind='proportional')
        image.hAlign = 'LEFT'
        story.append(image)
    story += [Paragraph(escape(str(table.get('brand', ''))), styles['Heading1']),
              p(table.get('title') or table.get('id')), p('Atualizada em '+issued+' · Tabela '+str(table.get('state',''))), Spacer(1, 4*mm)]
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
    for term in terms: story.append(p(term))
    story.append(Spacer(1, 4*mm))
    rows = [[p('Código'), p('Descrição'), p('Apresentação'), p('Preço')]]
    for row in sorted(prices, key=lambda r: (str(r.get('description','')), str(r.get('sku','')))):
        presentation = row.get('presentation') or row.get('packaging') or ('Caixa '+str(row['packSize'])+' un.' if row.get('packSize') else '—')
        rows.append([p(row.get('sku')), p(row.get('description')), p(presentation), p(brl(row.get('price')))])
    grid = Table(rows, colWidths=[26*mm, 91*mm, 39*mm, 24*mm], repeatRows=1, hAlign='LEFT')
    grid.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e9eff0')), ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LINEBELOW',(0,0),(-1,0),.7,colors.HexColor('#a5884e')), ('LINEBELOW',(0,1),(-1,-1),.2,colors.HexColor('#dddddd')),
        ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
    story.append(grid)
    def footer(canvas, doc):
        canvas.setFont('L2Catalog',8); canvas.setFillColor(colors.HexColor('#253743'))
        canvas.drawString(15*mm,10*mm,'L2 Representação · Tabela comercial'); canvas.drawRightString(195*mm,10*mm,str(doc.page))
    SimpleDocTemplate(output, pagesize=(210*mm,297*mm),leftMargin=15*mm,rightMargin=15*mm,topMargin=12*mm,bottomMargin=18*mm,
                      title='Tabela '+str(table.get('brand','')),pageCompression=1).build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
