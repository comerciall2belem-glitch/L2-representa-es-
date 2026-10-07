import unittest,copy
from cnpj_registry import normalize_company,RegistryError

class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.raw={'razao_social':'Empresa de Teste','simples':{'simples':'Sim'},'estabelecimento':{'cnpj':'27865757000102','nome_fantasia':'Teste','estado':{'sigla':'PA'},'cidade':{'nome':'Belém'},'bairro':'Centro','tipo_logradouro':'Rua','logradouro':'A','numero':'1','ddd1':'91','telefone1':'999999999','inscricoes_estaduais':[{'inscricao_estadual':'15.123.456-7','ativo':True,'estado':{'sigla':'PA'}},{'inscricao_estadual':'999999999','ativo':True,'estado':{'sigla':'AP'}},{'inscricao_estadual':'111111111','ativo':False,'estado':{'sigla':'PA'}}]}}
    def test_company_and_active_registration_only_same_state(self):
        result=normalize_company(self.raw,'27865757000102')
        self.assertEqual(result['client']['stateRegistration'],'151234567')
        self.assertEqual(result['client']['taxRegime'],'SIMPLES')
        self.assertEqual(result['client']['address'],'Rua A 1')
        self.assertEqual(result['missing'],[])
        self.assertNotIn('socios',result)
    def test_no_registration_never_becomes_exempt(self):
        self.raw['estabelecimento']['inscricoes_estaduais']=[]
        result=normalize_company(self.raw,'27865757000102')
        self.assertEqual(result['client']['stateRegistration'],'')
        self.assertIn('stateRegistration',result['missing'])
    def test_multiple_active_registrations_require_confirmation(self):
        self.raw['estabelecimento']['inscricoes_estaduais'].append({'inscricao_estadual':'159999999','ativo':True,'estado':{'sigla':'PA'}})
        self.assertEqual(normalize_company(self.raw,'27865757000102')['client']['stateRegistration'],'')
    def test_returned_company_must_match_requested_identifier(self):
        with self.assertRaises(RegistryError):normalize_company(self.raw,'00000000000000')
