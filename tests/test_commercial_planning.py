"""Unit/API contracts. Real PostgreSQL flows are in the separate integration file."""
import hashlib
import hmac
import io
import json
import os
import time
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pydantic import ValidationError
import commercial_planning as cp


def document(**changes):
    return cp.Document.model_validate({'source':'erp','external_id':'NF1','stage':'sell_through','client_id':'a','business_date':'2026-10-01','items':[{'brand':'TRUSS','sku':'SKU1','amount':'100.00','units':'10.00','portfolio':'Estratégico'}],**changes})


def normalized(**changes):
    d=document().model_dump(mode='json')|changes
    d['date']=date.fromisoformat(d['business_date']);d['status']='posted'
    sign=-1 if d['kind']=='return' else 1
    d['amount']=sum(Decimal(x['amount']) for x in d['items'])*sign
    d['units']=sum(Decimal(x['units']) for x in d['items'])*sign
    return d


CLIENTS=[('a',{'name':'Salão A','state':'PA'},{'channel':'Salão de Beleza'},1)]
START,END=date(2026,10,1),date(2026,11,1)
OFX='''OFXHEADER:100
<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>BRL
<BANKACCTFROM><BANKID>001<BRANCHID>1234<ACCTID>5678</BANKACCTFROM>
<BANKTRANLIST><STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261002120000[-3:BRT]<TRNAMT>100.00<FITID>abc<REFNUM>NF1</STMTTRN></BANKTRANLIST>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>'''


@pytest.mark.parametrize('value',['11222333000181','52998224725','11.222.333/0001-81','529.982.247-25'])
def test_tax_valid(value):assert cp.valid_tax_id(value)


@pytest.mark.parametrize('value',['11111111111','00000000000000','11222333000180','52998224724','abc'])
def test_tax_invalid(value):assert not cp.valid_tax_id(value)


def test_annual_productivity_and_abc():
    r=cp.calculate(CLIENTS,[normalized(),normalized(external_id='OLD',business_date='2026-09-01')],START,END,True)
    m=r['metrics']
    assert m['productivity_12m']==200 and m['productivity_period']==100
    assert m['upa_12m']==20 and m['average_unit_price_12m']==10
    assert r['portfolio'][0]['abc']=='A' and r['strategic_potential_share_percent']==100
    assert m['productivity_12m']==m['upa_12m']*m['average_unit_price_12m']
    assert r['stages']['sell_out'] is None and r['stages']['gmv_sell_out_net'] is None


def test_order_partial_invoices_count_once():
    docs=[normalized(external_id='1',order_id='P1'),normalized(external_id='2',order_id='P1')]
    r=cp.calculate(CLIENTS,docs,START,END,True)
    assert r['metrics']['orders']==1 and r['metrics']['average_order']==200


def test_gmv_never_sums_chain():
    docs=[normalized(stage=stage) for stage in cp.STAGES]
    r=cp.calculate(CLIENTS,docs,START,END,True)
    assert r['stages']['gmv_sell_out_net']==100
    assert r['regional'][0]['gmv_sell_out_net']==100


def test_unknown_history_and_returns():
    r=cp.calculate(CLIENTS,[normalized(),normalized(external_id='ret',kind='return',original_id='1')],START,END,False)
    assert r['metrics']['starts_12m'] is None
    assert r['metrics']['sell_through']==0 and r['metrics']['average_unit_price'] is None


def test_cessation_risk_and_never_buyers():
    clients=CLIENTS+[('b',{'name':'Nunca comprou'},None,None)]
    r=cp.calculate(clients,[normalized(business_date='2025-11-10')],START,END,True)
    assert len(r['retention'])==1 and r['retention'][0]['status']=='Em risco'


def test_ofx_exact_reference_and_account():
    result=cp.parse_ofx(OFX)
    assert result==[{'account':'001/1234/5678','external_id':'abc','business_date':date(2026,10,2),'amount':Decimal('100.00'),'reference':'NF1'}]


@pytest.mark.parametrize('bad',[OFX.replace('BRL','USD'),OFX.replace('100.00','NaN'),OFX+'<!ENTITY x SYSTEM "file:///etc/passwd">',OFX.replace('<STMTRS>','<STMTRS><STMTRS>'),OFX.replace('20261002','20261399')])
def test_ofx_invalid(bad):
    with pytest.raises((ValueError,ArithmeticError)):cp.parse_ofx(bad)


def test_totp_rfc6238_six_digits():
    # RFC vector uses 8 digits 94287082; configured 6-digit output is 287082.
    assert cp.totp('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ',1)=='287082'


def test_document_rejects_invalid_returns_and_stage():
    for changes in ({'kind':'return'},{'stage':'bad'},{'business_date':'2099-01-01'},{'items':[{'brand':'x','sku':'x','units':'1','amount':'NaN'}]}):
        with pytest.raises(ValidationError):document(**changes)


def test_export_files_and_formula_injection():
    r=cp.calculate(CLIENTS,[normalized()],START,END,True);r['filters']={'brand':'','channel':'','region':''}
    r['portfolio'][0]['sku']='=HYPERLINK("https://example.com")'
    wb=load_workbook(io.BytesIO(cp.export_xlsx(r)))
    assert wb['Portfolio']['B2'].data_type=='s'
    assert wb['Indicadores']['A1'].value=='Indicador'
    pdf=cp.export_pdf(r)
    assert pdf.startswith(b'%PDF')
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(pdf))
    assert len(reader.pages)==4
    assert 'Drenagem' in reader.pages[1].extract_text()


def test_monthly_schedule_calendar():
    start,end=cp.schedule_window('monthly',datetime(2024,3,1,tzinfo=cp.TZ))
    assert (start,end)==(date(2024,2,1),date(2024,3,1))


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('L2_COMMERCIAL_PLANNING_ENABLED','true')
    original=cp.services.copy()
    def auth(header):
        if header!='Bearer manager':raise HTTPException(401,'Sessão inválida')
        return 'manager'
    cp.configure(auth=auth,sectors_for=lambda u:{'commercial','management'})
    app=FastAPI();app.include_router(cp.router)
    with TestClient(app) as client:yield client
    cp.services.clear();cp.services.update(original)


def test_api_auth_and_finance_rbac(api):
    assert api.get('/api/strategy/commercial/status').status_code==401
    assert api.get('/api/strategy/commercial/finance/receivables',headers={'Authorization':'Bearer manager'}).status_code==403
    assert api.post('/api/strategy/commercial/documents',json={'stage':'x'},headers={'Authorization':'Bearer manager'}).status_code==422


def test_disabled_is_fail_closed(api,monkeypatch):
    monkeypatch.setenv('L2_COMMERCIAL_PLANNING_ENABLED','false')
    r=api.get('/api/strategy/commercial/ports',headers={'Authorization':'Bearer manager'})
    assert r.status_code==503


def test_webhook_signature_and_replay_window(api,monkeypatch):
    key='x'*40;monkeypatch.setenv('L2_BANK_WEBHOOK_SECRET',key)
    body=json.dumps({'account':'001','external_id':'e1','business_date':'2026-10-01','amount':'100.00','reference':'NF1'}).encode()
    assert api.post('/api/strategy/commercial/finance/webhook',content=body).status_code==401
    stamp=str(int(time.time())-600)
    sig=hmac.new(key.encode(),stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    r=api.post('/api/strategy/commercial/finance/webhook',content=body,headers={'X-L2-Timestamp':stamp,'X-L2-Signature':sig})
    assert r.status_code==401


def test_valid_webhook_contract(api,monkeypatch):
    from contextlib import nullcontext
    key='x'*40;monkeypatch.setenv('L2_BANK_WEBHOOK_SECRET',key)
    body=json.dumps({'account':'001','external_id':'e1','business_date':'2026-10-01','amount':'100.00','reference':'NF1'}).encode()
    stamp=str(int(time.time()));sig=hmac.new(key.encode(),stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
    with patch.dict(cp.services,db=lambda:nullcontext(object())),patch.object(cp,'bank_transaction',return_value={'status':'Conciliado'}) as handler:
        r=api.post('/api/strategy/commercial/finance/webhook',content=body,headers={'X-L2-Timestamp':stamp,'X-L2-Signature':sig})
        assert r.status_code==200
        assert handler.call_args.args[1]=='bank-webhook'
