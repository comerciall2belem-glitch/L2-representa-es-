import io
import json
import unittest
import urllib.error
from unittest.mock import patch
from speedio_integration import lookup_cnpj, SpeedioError, ENDPOINT


class Reply(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class SpeedioTests(unittest.TestCase):
    def setUp(self):
        self.network_guard = patch('speedio_integration.urllib.request.urlopen',
                                   side_effect=AssertionError('Teste não pode acessar a rede'))
        self.network_guard.start()
        self.addCleanup(self.network_guard.stop)

    def test_authenticated_lookup_and_pa_mapping(self):
        def opener(request, timeout):
            self.assertEqual(timeout, 10)
            self.assertTrue(request.full_url.startswith(ENDPOINT + '?'))
            self.assertEqual(request.get_header('Authorization'), 'Basic dXNlcjpwYXNz')
            return Reply(json.dumps([{'cnpj': '21071712000171',
                                      'razao_social': 'Empresa PA',
                                      'location': {'uf': 'PA', 'city': 'Belém'},
                                      'telefones': {'telefones_validados': ['91999990000']}}]).encode())
        result = lookup_cnpj('21071712000171', 'user', 'pass', opener)
        self.assertEqual(result['id'], 'speedio:21071712000171')
        self.assertEqual(result['state'], 'PA')
        self.assertEqual(result['phone'], '91999990000')

    def test_missing_credentials_and_other_state(self):
        with self.assertRaises(SpeedioError) as error:
            lookup_cnpj('21071712000171', '', '')
        self.assertEqual(error.exception.status, 503)
        with self.assertRaises(SpeedioError) as error:
            lookup_cnpj('21071712000171', 'user', 'pass',
                        lambda req, timeout: Reply(b'[{"cnpj":"21071712000171","razao_social":"Empresa","location":{"uf":"SP"}}]'))
        self.assertEqual(error.exception.status, 422)

    def test_upstream_auth_error_is_safe(self):
        def unauthorized(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', None, None)
        with self.assertRaises(SpeedioError) as error:
            lookup_cnpj('21071712000171', 'user', 'pass', unauthorized)
        self.assertEqual(error.exception.status, 502)
        self.assertNotIn('pass', str(error.exception))


if __name__ == '__main__': unittest.main()
