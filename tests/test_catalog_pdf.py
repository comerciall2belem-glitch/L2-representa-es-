import io
import unittest
from datetime import date
from pathlib import Path
from pypdf import PdfReader
from catalog_pdf import build_catalog_pdf, industry_logo, commercial_lines

class CatalogPDFTests(unittest.TestCase):
    def test_complete_light_multipage_document_without_internal_fields(self):
        rows=[{'sku':f'SKU{i:03}', 'description':'Máscara de hidratação <intensa> '+str(i), 'price':'1234.56', 'packSize':6, 'commissionRate':99} for i in range(160)]
        table={'brand':'Indústria teste','state':'PA','title':'Tabela teste','commissionRate':99}
        industry={'notes':'SEGREDO INTERNO','commissionRate':99,'commercialPolicy':[{'minimum':'2000','paymentTerms':'30/60','freight':'CIF'}]}
        content=build_catalog_pdf(table,industry,rows,Path(__file__).resolve().parents[1],date(2026,10,5))
        reader=PdfReader(io.BytesIO(content))
        text='\n'.join(page.extract_text() for page in reader.pages)
        self.assertGreater(len(reader.pages),1)
        self.assertLess(len(content),200*1024)
        for row in rows:self.assertEqual(text.count(row['sku']),1)
        for page in reader.pages:self.assertIn('Código',page.extract_text())
        for label in ('R$ 1.234,56','Caixa com 6 un.','30/60','<intensa>'):self.assertIn(label,text)
        self.assertNotIn('SEGREDO INTERNO',text)
        self.assertNotIn('commission',text)
    def test_missing_packaging_and_official_logo_not_invented(self):
        base=Path(__file__).resolve().parents[1]
        self.assertIsNone(industry_logo(base,{'logoFile':'../logo-l2.jpeg'}))
        self.assertIsNone(industry_logo(base,{}))
        self.assertEqual(commercial_lines({},{}),['Consulte as condições comerciais com seu representante.'])
