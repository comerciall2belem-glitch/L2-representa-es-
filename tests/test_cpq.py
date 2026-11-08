from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
import io
import unittest
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from pypdf import PdfReader
import cpq
from pipeline_rules import validate_opportunity

DAY=date(2026,11,8)

class LogicTests(unittest.TestCase):
    def test_discount_boundaries(self):
        for value,expected in [('0','Automática'),('5','Automática'),('5.01','Gestor Comercial'),('10','Gestor Comercial'),('10.01','Diretoria Executiva'),('100','Diretoria Executiva')]:
            self.assertEqual(cpq.level(value),expected)
        for value in ('NaN','-1','101'):
            with self.assertRaises(HTTPException):cpq.level(value)
    def test_money_is_exact_and_model_rejects_fractional_quantity(self):
        self.assertEqual(cpq.money(Decimal('19.99')*3),Decimal('59.97'))
        with self.assertRaises(ValidationError):cpq.Line(brand='Marca',tableId='t',sku='x',quantity=1.5)
    def test_policy_requires_configured_rules_and_net_minimum(self):
        industry={'name':'Marca','commercialPolicy':[{'minimum':'1000','paymentTerms':'30/60','freight':'CIF'}]}
        self.assertEqual(cpq.commercial_policy(industry,'PA','B2B',Decimal('1000'),'30/60')['freight'],'CIF')
        for net,terms in ((Decimal('999.99'),'30/60'),(Decimal('1000'),'30/90')):
            with self.assertRaises(HTTPException):cpq.commercial_policy(industry,'PA','B2B',net,terms)
        with self.assertRaises(HTTPException):cpq.commercial_policy({'name':'Sem regra'},'PA','B2B',Decimal('9999'),'30')
    def test_no_external_discount_bypass(self):
        with self.assertRaises(HTTPException):cpq.enforce_order(None,{'status':'Confirmado','discountPercent':'0'})
        with self.assertRaises(HTTPException):cpq.enforce_order(None,{'status':'Pendente','discountPercent':'10'})
        cpq.enforce_order(None,{'status':'Pendente','discountPercent':'0'})
    def test_reorder_exactly_120_percent_is_not_overdue(self):
        from strategic_crm import cadence
        orders=[{'clientId':'a','status':'Faturado','date':str(DAY-timedelta(days=n))} for n in (32,22,12)]
        self.assertFalse(cadence({'id':'a'},orders,today=DAY)['reorderAlert'])
        self.assertTrue(cadence({'id':'a'},orders,today=DAY+timedelta(days=1))['reorderAlert'])
        self.assertIsNone(cadence({'id':'a'},[],today=DAY)['cycleMeanDays'])
    def test_conversion_rates_exclude_unknown_stages_future_and_duplicate_orders(self):
        history=[{'stage':'Negociação','at':str(DAY-timedelta(days=2))}]
        losses=[{'stage':'Perdido','stageHistory':history+[{'stage':'Perdido','at':str(DAY-timedelta(days=1))}]} for _ in range(4)]
        winner={'clientId':'a','orderId':'o','stage':'Pedido confirmado','stageHistory':history}
        order={'id':'o','clientId':'a','status':'Confirmado','date':str(DAY)}
        rates=cpq.conversion_rates(losses+[winner,winner],[order],DAY)
        self.assertEqual(rates['Negociação']['rate'],.2)
        self.assertEqual(rates['Negociação']['sample'],5)
        self.assertIsNone(rates['Prospectado']['rate'])
    def test_mix_excludes_bought_cancelled_future_and_ineligible_table(self):
        clients=[{'id':'a','state':'PA','channel':'Farma'},{'id':'b','state':'PA','channel':'Farma'}]
        orders=[{'clientId':'b','date':str(DAY),'status':'Faturado','items':[{'brand':'Marca','sku':'new','quantity':5},{'brand':'Marca','sku':'bought','quantity':9}]}, {'clientId':'a','date':str(DAY),'status':'Faturado','items':[{'brand':'Marca','sku':'bought','quantity':1}]}]
        prices=[{'brand':'Marca','sku':sku,'tableId':'t'} for sku in ('new','bought')]
        tables=[{'id':'t','state':'PA','active':True}]
        with patch.dict(cpq._services,normalize_uf=lambda x:x,price_table_matches_client=lambda a,b:a==b):
            out=cpq.mix_suggestions(clients[0],clients,orders,prices,tables,DAY)
            self.assertEqual([x['sku'] for x in out['products']],['new'])
            orders[0]['status']='Cancelado'
            self.assertEqual(cpq.mix_suggestions(clients[0],clients,orders,prices,tables,DAY)['products'],[])
    def test_pdf_contains_net_price_and_terms(self):
        quote={'id':'test','validUntil':str(DAY),'items':[{'brand':'Marca','sku':'x','description':'Produto','quantity':3,'unitPrice':'10.00','subtotal':'28.50'}],'discountPercent':'5.00','amount':'28.50','paymentTerms':'30/60','policies':{'Marca':{'freight':'CIF','minimum':'20.00'}},'sellerResponsible':'Erika'}
        content=cpq.quote_pdf(quote,{'name':'Loja','taxId':'11222333000181'})
        self.assertTrue(content.startswith(b'%PDF-'))
        text=''.join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)
        self.assertIn('28.50',text);self.assertIn('30/60',text);self.assertIn('CIF',text)
