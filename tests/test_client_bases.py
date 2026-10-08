import ast
import base64
import gzip
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock
import client_bases as b
m=ast.parse(Path('server.py').read_text());f=next(n for n in m.body if isinstance(n,ast.FunctionDef) and n.name=='valid_cnpj');scope={};exec(compile(ast.Module(body=[f],type_ignores=[]),'validator','exec'),scope);valid=scope['valid_cnpj']
def source(**kw):return dict(name='Loja de teste',document='11222333000181',city='Belém',state='PA',address='Rua A, 1',phone='91999999999',sourcePurchaseDate='2026-09-01',sourcePurchaseValue=100,sourceSeller='Vendedor externo',sourceRow=3,**kw)
class BaseTests(unittest.TestCase):
 def test_duplicate_document_merges_source_rows(self):
  a=source();c={**a,'sourceRow':4,'sourcePurchaseDate':'2026-08-01'}
  grouped=b.prepare({'rows':[a,c]},valid);self.assertEqual(len(grouped),1);self.assertEqual(len(next(iter(grouped.values()))),2)
 def test_invalid_data_rejected_and_cpf_preserved_without_invented_cnpj(self):
  for change in ({'document':'11222333000182'},{'state':'SP'},{'name':''}):
   with self.assertRaises(ValueError):b.prepare({'rows':[{**source(),**change}]},valid)
  item=b.merge_client([{**source(),'document':'12345678909'}]);self.assertEqual(item['taxId'],'');self.assertEqual(item['sourceDocument']['type'],'CPF');self.assertEqual(item['stateRegistration'],'')
 def test_existing_history_owner_and_contact_are_preserved(self):
  old={'id':'native','name':'Nome nativo','owner':'Euler','taxId':'11222333000181','phone':'native','last_purchase':'2026-10-07','clientBases':['Outra base'],'brands':'Outra indústria'}
  item=b.merge_client([source()],old);self.assertEqual(item['id'],'native');self.assertEqual(item['owner'],'Euler');self.assertEqual(item['phone'],'native');self.assertEqual(item['last_purchase'],'2026-10-07');self.assertEqual(item['brands'],'Outra indústria');self.assertEqual(item['clientBases'],['Bella Brazil','Outra base'])
 def test_external_purchase_does_not_become_native_sale_or_cadence(self):
  item=b.merge_client([source()]);self.assertEqual(item['last_purchase'],'');self.assertNotIn('orders',item);self.assertNotIn('allowedBrands',item);self.assertEqual(item['baseImports']['bella_brazil']['sourceRows'][0]['sourcePurchaseValue'],100)
 def test_matching_by_document_and_precise_legacy_address(self):
  item={**source(),'id':'one','taxId':'11.222.333/0001-81'};self.assertEqual(b.matching_client(source(),[item])['id'],'one')
  legacy={**source(),'id':'legacy','taxId':''};self.assertEqual(b.matching_client(source(),[legacy])['id'],'legacy')
  self.assertIsNone(b.matching_client(source(),[{**legacy,'address':'Rua B'}]))
  with self.assertRaises(ValueError):b.matching_client(source(),[item,{**item,'id':'two'}])
 def test_repeat_import_returns_marker_without_creating_clients(self):
  payload=base64.b64encode(gzip.compress(json.dumps({'rows':[source()]}).encode())).decode()
  con=MagicMock();con.execute.return_value.fetchone.return_value=({'uniqueClients':1},);db=MagicMock();db.return_value.__enter__.return_value=con
  result=b.import_bella(db,valid,payload);self.assertTrue(result['alreadyApplied']);self.assertFalse(any('INSERT' in c.args[0] for c in con.execute.call_args_list))
 def test_missing_configuration_does_not_import(self):
  db=MagicMock();self.assertIsNone(b.import_bella(db,valid,''));db.assert_not_called()
