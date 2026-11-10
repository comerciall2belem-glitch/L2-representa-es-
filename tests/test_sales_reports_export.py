import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pypdf import PdfReader
from sales_reports import install

class SalesReportsExportTests(unittest.TestCase):
    def setUp(self):
        app=FastAPI()
        def auth(header):
            if header != 'Bearer test': raise HTTPException(401)
        install(app,auth)
        self.client=TestClient(app)
        self.headers={'Authorization':'Bearer test'}
        self.payload={'title':'Vendas Belém','period':'Descrição '+('detalhada '*40),'headers':['Cliente','Valor'],'rows':[['=HYPERLINK("exemplo")',123.45],['Cliente Belém',200]]}
    def post(self,ext,payload=None):
        return self.client.post('/api/sales-reports/export/'+ext,json=payload or self.payload,headers=self.headers)
    def test_all_ten_reports_export_to_both_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'reports.json'
            subprocess.run(['node',str(Path(__file__).with_name('sales_reports.cjs')),str(output)],check=True)
            reports=json.loads(output.read_text())
        self.assertEqual(len(reports),10)
        for report in reports:
            for ext in ['pdf','xlsx']:
                with self.subTest(report=report['title'],extension=ext):
                    response=self.post(ext,report)
                    self.assertEqual(response.status_code,200,response.text if response.status_code!=200 else '')
                    if ext=='pdf': self.assertGreaterEqual(len(PdfReader(io.BytesIO(response.content)).pages),1)
                    else: self.assertEqual(load_workbook(io.BytesIO(response.content)).active.max_row,3+len(report['rows']))
    def test_excel_preserves_numbers_and_does_not_execute_text(self):
        workbook=load_workbook(io.BytesIO(self.post('xlsx').content));sheet=workbook.active
        self.assertEqual(sheet['A4'].data_type,'s');self.assertEqual(sheet['B4'].value,123.45)
        self.assertEqual(sheet.freeze_panes,'A4')
    def test_pdf_contains_all_pages(self):
        self.payload['rows']=[['Cliente '+str(i),i] for i in range(300)]
        reader=PdfReader(io.BytesIO(self.post('pdf').content))
        self.assertGreater(len(reader.pages),1)
        self.assertIn('Cliente 299',reader.pages[-1].extract_text())
    def test_authentication_and_validation(self):
        self.assertEqual(self.client.post('/api/sales-reports/export/pdf',json=self.payload).status_code,401)
        self.assertEqual(self.post('csv').status_code,400)
        self.payload['rows']=[['sem segunda coluna']]
        self.assertEqual(self.post('pdf').status_code,400)
    def test_empty_and_long_rows(self):
        self.payload['rows']=[]
        for ext in ['pdf','xlsx']:self.assertEqual(self.post(ext).status_code,200)
        self.payload['rows']=[['Texto longo '*300,1]]
        self.assertEqual(self.post('pdf').status_code,200)

if __name__=='__main__':unittest.main()
