import json
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from client_cleanup import fingerprint, plan

class ClientCleanupTests(unittest.TestCase):
    def record(self, id, **fields): return {'id':id,'payload':fields}

    def test_archives_outside_pa_ap(self):
        rows=[self.record('1',name='A',city='Belém',state='PA'),self.record('2',name='B',city='Manaus',state='AM'),self.record('3',name='C',city='Macapá',state='AP')]
        result=plan(rows)
        self.assertEqual([x['id'] for x in result['outside']],['2'])

    def test_preserves_separate_branches(self):
        rows=[self.record('1',name='Formosa',city='Belém',state='PA',district='Marco',address='Av Duque 165'),self.record('2',name='Formosa',city='Belém',state='PA',district='Parque Verde',address='Rod Augusto km 7')]
        self.assertEqual(plan(rows)['duplicates'],[])

    def test_merges_same_store_without_losing_richer_record(self):
        rows=[self.record('a',name='Loja Única',city='Belém',state='PA',district='Centro',address='Rua A 123'),self.record('b',name='LOJA UNICA',city='Belem',state='Pará',district='Centro',address='Rua A, 123',phone='91999999999')]
        duplicates=plan(rows)['duplicates']
        self.assertEqual(len(duplicates),1)
        self.assertEqual((duplicates[0]['old']['id'],duplicates[0]['keep']['id']),('a','b'))

    def test_phone_alone_never_merges_different_addresses(self):
        rows=[self.record('1',name='Rede',city='Belém',state='PA',phone='91999999999',address='Rua 1'),self.record('2',name='Rede',city='Belém',state='PA',phone='91999999999',address='Rua 2')]
        self.assertEqual(plan(rows)['duplicates'],[])

if __name__=='__main__':unittest.main()
