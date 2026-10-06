import io
import unittest
from reportlab.pdfgen import canvas
from pdf_price_import import extract_price_candidates

class PDFPriceImportTests(unittest.TestCase):
    def make_pdf(self, lines):
        b=io.BytesIO();p=canvas.Canvas(b)
        for i,line in enumerate(lines):p.drawString(40,800-i*20,line)
        p.save();return b.getvalue()
    def test_prices_require_review_and_preserve_duplicates_as_unmatched(self):
        result=extract_price_candidates(self.make_pdf(['SKU01 Batom vermelho R$ 12,50','SKU01 Outro preço R$ 20,00','SKU02 Creme corporal R$ 1.234,56','Total 1.247,06']))
        self.assertEqual(len(result['candidates']),2)
        self.assertEqual(result['candidates'][1]['price'],'1234.56')
        self.assertTrue(result['requiresReview']);self.assertEqual(len(result['unmatched']),2)
    def test_image_or_blank_pdf_has_no_fake_prices(self):
        self.assertEqual(extract_price_candidates(self.make_pdf(['Tabela sem valores']))['candidates'],[])
    def test_invalid_pdf_is_rejected(self):
        with self.assertRaises(ValueError):extract_price_candidates(b'not a PDF')

    def test_scanned_pdf_uses_local_ocr_and_requires_review(self):
        from PIL import Image, ImageDraw, ImageFont
        from reportlab.lib.utils import ImageReader
        image=Image.new('RGB',(1400,500),'white')
        draw=ImageDraw.Draw(image)
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',38)
        draw.text((40,90),'SKU01 Batom vermelho R$ 12,50',font=font,fill='black')
        buffer=io.BytesIO();pdf=canvas.Canvas(buffer,pagesize=(700,250))
        pdf.drawImage(ImageReader(image),0,0,width=700,height=250);pdf.save()
        result=extract_price_candidates(buffer.getvalue())
        self.assertEqual(result['ocrPages'],[1])
        self.assertEqual(result['candidates'][0]['sku'],'SKU01')
        self.assertEqual(result['candidates'][0]['price'],'12.50')
        self.assertEqual(result['candidates'][0]['source'],'ocr')
        self.assertTrue(result['requiresReview'])
