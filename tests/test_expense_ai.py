from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
import pytest
import pymupdf
from fastapi import HTTPException
from expense_ai import authorize_scope, parse_receipt, extract, post_receipt

@pytest.mark.parametrize('user', ['Marlene','Erika','Laís','Outro'])
def test_personal_scope_refuses_every_non_member(user):
    with pytest.raises(HTTPException) as e: authorize_scope(user,'pessoal',lambda _: {'finance','admin'})
    assert e.value.status_code == 403

@pytest.mark.parametrize('scope', ['', 'geral', 'Empresa', None])
def test_destination_is_required(scope):
    with pytest.raises(HTTPException): authorize_scope('Ana Paula',scope,lambda _: {'finance'})

def test_commercial_cannot_post_corporate_cash():
    with pytest.raises(HTTPException): authorize_scope('Vendedor','empresa',lambda _: {'commercial'})

def test_extract_total_not_subtotal_change_or_taxes():
    r=parse_receipt('POSTO BELEM\nCNPJ 00.000.000/0001-00\n17/10/2026\nSubtotal 200,00\nDesconto total 13,60\nVALOR TOTAL R$ 186,40\nTroco 13,60\nTributos 20,00')
    assert r['amount']=='186.40' and r['category']=='Combustível' and r['date']=='2026-10-17'

def test_missing_date_uses_submission():
    r=parse_receipt('PAPELARIA PARA\nTOTAL 1.234,56',datetime(2026,10,17))
    assert r['date']=='2026-10-17' and r['dateSource']=='envio' and r['amount']=='1234.56'

@pytest.mark.parametrize('text',['LOJA EXEMPLO\nTOTAL 20,00\nTOTAL 30,00','LOJA EXEMPLO\nSubtotal 50,00','LOJA EXEMPLO\nTOTAL 0,00','LOJA EXEMPLO\n31/02/2026\nTOTAL 20,00'])
def test_uncertain_receipts_never_post(text):
    with pytest.raises(ValueError): parse_receipt(text)

def test_real_selectable_pdf():
    with pymupdf.open() as doc:
        p=doc.new_page();p.insert_text((40,40),'HOTEL BELEM\nCNPJ 00.000.000/0001-00\n17/10/2026\nVALOR TOTAL R$ 186,40')
        data=doc.tobytes()
    out,mime=extract(data)
    assert out['amount']=='186.40' and out['category']=='Hospedagem' and mime=='application/pdf'

@pytest.mark.parametrize('data',[b'not an image',b'%PDF-'+b'0'*(5*1024*1024)])
def test_invalid_or_oversize(data):
    with pytest.raises(ValueError): extract(data)

class Cursor:
    def __init__(self,row=None): self.row=row
    def fetchone(self): return self.row
class Connection:
    def __init__(self,duplicate=False): self.sql=[];self.duplicate=duplicate
    def execute(self,sql,args=None):
        self.sql.append((sql,args))
        if "SELECT 1 FROM entities WHERE kind='expense_receipt'" in sql: return Cursor((1,) if self.duplicate else None)
        if "SELECT payload FROM entities WHERE kind='cash_day'" in sql: return Cursor(({'status':'Aberto'},))
        if 'SELECT 1 FROM personal_finance.accounts' in sql: return Cursor((1,))
        if 'SELECT id FROM personal_finance.categories' in sql: return Cursor(('cat',))
        return Cursor()

@pytest.mark.parametrize('scope',['empresa','pessoal'])
def test_only_selected_ledger_receives_posting(scope):
    c=Connection();out=post_receipt(c,'Ana Paula',scope,b'image','receipt.png',dict(supplier='Loja',date='2026-10-17',amount='10.20',category='Alimentação'),'image/png')
    sql='\n'.join(s for s,a in c.sql)
    assert ('INSERT INTO personal_finance.transactions' in sql)==(scope=='pessoal')
    assert ("INSERT INTO entities(kind,id,payload) VALUES('cash_entry'" in sql)==(scope=='empresa')
    assert out['scope']==scope
    if scope=='pessoal': assert 'pg_notify' not in sql

@pytest.mark.parametrize('scope',['empresa','pessoal'])
def test_duplicate_never_reaches_either_ledger(scope):
    c=Connection(duplicate=True)
    with pytest.raises(HTTPException) as e: post_receipt(c,'Ana Paula',scope,b'image','receipt.png',{},'image/png')
    assert e.value.status_code==409
    assert not any('INSERT' in s for s,a in c.sql)

def test_api_scope_auth_runs_before_ocr():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from expense_ai import install
    app=FastAPI();install(app,lambda: None,lambda _: 'Marlene',lambda _: {'finance','admin'})
    with patch('expense_ai.extract') as ocr:
        response=TestClient(app).post('/api/expense-ai/receipts',data={'scope':'pessoal'},files={'file':('receipt.png',b'fake','image/png')})
    assert response.status_code==403 and not ocr.called

def test_api_missing_scope_never_extracts():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from expense_ai import install
    app=FastAPI();install(app,lambda: None,lambda _: 'Ana Paula',lambda _: {'finance'})
    with patch('expense_ai.extract') as ocr:
        response=TestClient(app).post('/api/expense-ai/receipts',files={'file':('receipt.png',b'fake','image/png')})
    assert response.status_code==422 and not ocr.called

def test_whatsapp_non_expense_does_not_process():
    import asyncio
    from expense_ai import whatsapp_receipt
    assert asyncio.run(whatsapp_receipt({'type':'image','image':{'caption':'produto'}},'123',None,None,None)) is None

@pytest.mark.parametrize('kind',['png','jpeg','pdf-scan'])
def test_real_raster_receipt_ocr(kind):
    pytest.importorskip('rapidocr_onnxruntime')
    with pymupdf.open() as doc:
        page=doc.new_page(width=420,height=400)
        page.insert_text((25,40),'POSTO BELEM\nCNPJ 00.000.000/0001-00\n17/10/2026\nGASOLINA\nVALOR TOTAL R$ 186,40',fontsize=20)
        pix=page.get_pixmap(matrix=pymupdf.Matrix(2,2))
        content=pix.tobytes('jpeg' if kind=='jpeg' else 'png')
        if kind=='pdf-scan':
            with pymupdf.open() as scan:
                p=scan.new_page(width=420,height=400)
                p.insert_image(p.rect,stream=content)
                content=scan.tobytes()
    out,mime=extract(content)
    assert out['supplier'].replace(' ','')=='POSTOBELEM'
    assert out['amount']=='186.40' and out['date']=='2026-10-17'
    assert out['category']=='Combustível'
