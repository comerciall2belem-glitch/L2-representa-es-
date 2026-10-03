import unittest, json, copy, ast
from pathlib import Path
from decimal import Decimal, InvalidOperation

class HTTPException(Exception):
    def __init__(self,status_code,detail): self.status_code,self.detail=status_code,detail

source=ast.parse(Path(__file__).resolve().parents[1].joinpath('commercial_tables.py').read_text())
ns={'Decimal':Decimal,'InvalidOperation':InvalidOperation,'HTTPException':HTTPException,'Jsonb':lambda x:x,'json':json}
exec(compile(ast.Module(body=[x for x in source.body if isinstance(x,(ast.Assign,ast.FunctionDef))],type_ignores=[]),'<commercial>','exec'),ns)

class Cursor:
    def __init__(self,row=None,rows=()): self.row,self.rows=row,rows
    def fetchone(self):return self.row
    def fetchall(self):return self.rows

class DB:
    def __init__(self):self.tables={};self.history=[];self.entities={};self.seeded=False
    def execute(self,sql,args=()):
        if "SELECT payload FROM entities WHERE kind='price_table'" in sql:
            return Cursor((self.tables[args[0]],) if args[0] in self.tables else None)
        if 'UNION ALL' in sql:return Cursor(rows=[(x,) for x in self.history if x['id']!=args[1]])
        if 'INSERT INTO catalog_seeds' in sql:
            if self.seeded:return Cursor()
            self.seeded=True;return Cursor(('marker',))
        if 'INSERT INTO entities' in sql:
            kind=sql.split("VALUES('")[1].split("'")[0];self.entities[(kind,args[0])]=copy.deepcopy(args[1])
        return Cursor()

class CommercialTests(unittest.TestCase):
    def setUp(self):
        self.db=DB()
        for channel,rate in ns['CHANNELS'].items():
            tid='Bella Brazil|'+channel
            self.db.tables[tid]={'id':tid,'brand':'Bella Brazil','state':'ALL','active':True,'channel':channel,'commissionRate':rate}
        self.order={'id':'new','clientId':'customer','status':'Confirmado','paymentTerms':'28/35','items':[{'brand':'Bella Brazil','tableId':'Bella Brazil|REVENDA','sku':'800','quantity':420,'subtotal':'2032.80'}]}
    def test_three_prices_for_same_sku_do_not_collide(self):
        keys={ns['price_id']({'brand':'Bella Brazil','tableId':f'Bella Brazil|{ch}','sku':'800'}) for ch in ns['CHANNELS']}
        self.assertEqual(len(keys),3)
        self.assertEqual(ns['price_id']({'brand':'BT','state':'PA','sku':'800'}),'BT|PA|800')
    def test_shared_table_works_in_both_states(self):
        for uf in ('PA','AP'):self.assertEqual(ns['validate_table'](self.db,self.order['items'][0],'Bella Brazil',uf)['channel'],'REVENDA')
    def test_cross_brand_and_wrong_state_rejected(self):
        with self.assertRaises(HTTPException):ns['validate_table'](self.db,self.order['items'][0],'BT','PA')
        self.db.tables['local']={'id':'local','brand':'BT','state':'PA','active':True}
        with self.assertRaises(HTTPException):ns['validate_table'](self.db,{'tableId':'local'},'BT','AP')
    def test_revenda_eight_percent_and_cif(self):
        ns['validate_bella_order'](self.db,self.order,'PA','Ana Paula')
        self.assertEqual(self.order['bellaCommissionRate'],8)
        self.assertEqual(self.order['items'][0]['commissionRate'],8)
        self.assertEqual(self.order['bellaFreight'],'CIF')
    def test_ap_minimum_applies_only_to_bella_subtotal(self):
        self.order['items'].append({'brand':'BT','subtotal':'10000.00'})
        with self.assertRaises(HTTPException):ns['validate_bella_order'](self.db,self.order,'AP','Ana Paula')
    def test_draft_can_be_below_minimum_but_units_are_multiples_of_six(self):
        self.order['status']='Pendente';self.order['items'][0]['subtotal']='29.04';self.order['items'][0]['quantity']=6
        ns['validate_bella_order'](self.db,self.order,'AP','Ana Paula')
        self.order['items'][0]['quantity']=7
        with self.assertRaises(HTTPException):ns['validate_bella_order'](self.db,self.order,'AP','Ana Paula')
    def test_payment_terms_and_bonus_bounds(self):
        for terms in ('28','28/35','30/45/60'):
            self.order['paymentTerms']=terms;ns['validate_bella_order'](self.db,self.order,'PA','Ana Paula')
        self.order['paymentTerms']='30/60/90'
        with self.assertRaises(HTTPException):ns['validate_bella_order'](self.db,self.order,'PA','Ana Paula')
        self.order['paymentTerms']='28';self.order['bellaBonusPercent']=15
        ns['validate_bella_order'](self.db,self.order,'PA','Ana Paula')
        self.assertEqual(self.order['bellaBonusValue'],'304.92')
        self.order['bellaBonusPercent']=16
        with self.assertRaises(HTTPException):ns['validate_bella_order'](self.db,self.order,'PA','Ana Paula')
    def test_repeat_bonus_needs_consultation_even_for_archived_history(self):
        self.db.history=[{'id':'prior','brand':'Bella Brazil','status':'Faturado'}]
        self.order['bellaBonusPercent']=10
        with self.assertRaises(HTTPException):ns['validate_bella_order'](self.db,self.order,'PA','Ana Paula')
        self.order['bellaBonusApproval']='Consulta aprovada pela indústria'
        ns['validate_bella_order'](self.db,self.order,'PA','Ana Paula')
        with self.assertRaises(HTTPException):ns['validate_bella_order'](self.db,self.order,'PA','Euler')
    def test_seed_is_complete_and_does_not_overwrite_edits(self):
        ns['seed_bella'](self.db,Path(__file__).resolve().parents[1])
        self.assertEqual(sum(k[0]=='price' for k in self.db.entities),480)
        self.assertEqual(sum(k[0]=='price_table' for k in self.db.entities),3)
        key=('price','Bella Brazil|VAREJO|800');self.db.entities[key]['price']='6.00'
        ns['seed_bella'](self.db,Path(__file__).resolve().parents[1])
        self.assertEqual(self.db.entities[key]['price'],'6.00')

if __name__=='__main__':unittest.main()
