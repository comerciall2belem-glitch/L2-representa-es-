import unittest
from unittest.mock import patch
from pydantic import ValidationError
from fastapi import HTTPException
import geo_prospecting as g
class GeoTests(unittest.TestCase):
    def data(self,**extra):return g.Search(state='PA',latitude=-1.45,longitude=-48.48,**extra)
    def setUp(self):g._cache.clear();g._searches.clear()
    def test_invalid_radius_coordinates_and_state(self):
        for values in ({'radiusKm':0},{'radiusKm':11},{'latitude':float('nan')},{'longitude':181},{'state':'SP'}):
            with self.assertRaises(ValidationError):g.Search(**dict(state='PA',latitude=-1.45,longitude=-48.48,**{})|values)
    def test_query_uses_exact_enum_filters_and_state_area(self):
        query=g.query_for(self.data(channel='Farma'))
        self.assertIn('"BR-PA"',query);self.assertIn('"pharmacy"',query);self.assertIn('around:5000',query)
        with self.assertRaises(HTTPException):g.query_for(self.data(channel='Farma"];out;'))
    def test_normalized_segmentation_distance_and_missing_coordinates(self):
        clients=[{'id':'one','name':'Perfumaria','state':'PA','city':'Belém','channel':'Cosméticos','latitude':-1.45,'longitude':-48.48},{'id':'missing','name':'Cosméticos Alfa','state':'PA','city':'Belém'},{'id':'far','name':'Perfumaria Fora','state':'PA','city':'Belém','latitude':-2.5,'longitude':-48.48},{'id':'ap','name':'Cosméticos AP','state':'AP','latitude':-1.45,'longitude':-48.48}]
        rows,missing=g.base_search(clients,[],self.data(city='belem'))
        self.assertEqual([r['clientId'] for r in rows],['one']);self.assertEqual(rows[0]['distanceKm'],0);self.assertEqual(missing,1)
    def test_external_rows_bounded_deduped_and_cached(self):
        payload={'elements':[{'type':'node','id':1,'lat':-1.45,'lon':-48.48,'tags':{'name':'Loja'}},{'type':'way','id':2,'center':{'lat':-1.45,'lon':-48.48},'tags':{'name':'Loja'}},{'type':'node','id':3,'lat':10,'lon':-48.48,'tags':{'name':'Fora'}}]}
        with patch.object(g,'_fetch',return_value=payload) as fetch:
            rows=g.external_search(self.data());self.assertEqual(len(rows),1);self.assertIsNone(rows[0]['clientId']);self.assertNotIn('taxId',rows[0]);g.external_search(self.data());fetch.assert_called_once()
    def test_partial_external_response_not_treated_as_success(self):
        with patch.object(g,'_fetch',return_value={'remark':'runtime timeout','elements':[]}):
            with self.assertRaises(ValueError):g.external_search(self.data())
    def test_save_search_not_owned_or_expired_rejected(self):
        with patch.object(g.crm,'access',return_value='Erika'):
            for context in ({'user':'Euler','expires':9999999999,'rows':[]},{'user':'Erika','expires':0,'rows':[]}):
                g._searches['a'*32]=context
                with self.assertRaises(HTTPException) as error:g.save_prospect(g.Save(searchId='a'*32,sourceId='osm:node:1'),'Bearer x')
                self.assertEqual(error.exception.status_code,409)
    def test_geocode_rejects_other_states_and_caches_user_request(self):
        payload=[{'lat':'-1.45','lon':'-48.48','display_name':'Belém, Pará','address':{'state':'Pará','country_code':'br'}},{'lat':'-23','lon':'-46','display_name':'São Paulo','address':{'state':'São Paulo','country_code':'br'}}]
        with patch.object(g,'_fetch',return_value=payload) as fetch,patch.object(g.time,'sleep'):
            rows=g.geocode('Belém','PA');self.assertEqual(len(rows),1);g.geocode('Belém','PA');fetch.assert_called_once()
