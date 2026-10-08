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
        self.assertIn('"BR-PA"',query);self.assertIn('^(pharmacy)$',query);self.assertIn('around:5000',query);self.assertLess(query.index('around:'),query.index('area['));self.assertIn('nwr.nearby(area.region)',query)
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
    def test_get_transport_and_provider_failover(self):
        payload={'elements':[]}
        with patch.dict(g.os.environ,{},clear=True),patch.object(g,'_fetch',side_effect=[TimeoutError('timeout'),payload]) as fetch:
            self.assertEqual(g.query_provider('query'),payload)
            self.assertEqual(fetch.call_count,2)
            args,kwargs=fetch.call_args_list[0]
            self.assertIn('?data=query',args[0]);self.assertEqual(len(args),1)
    def test_configured_provider_is_not_replaced_and_invalid_payload_rejected(self):
        with patch.dict(g.os.environ,{'L2_PROSPECTING_URL':'https://example.org/interpreter'}),patch.object(g,'_fetch',return_value={'unexpected':[]}) as fetch:
            with self.assertRaises(ValueError):g.query_provider('query')
            fetch.assert_called_once()
    def test_persistent_cache_fallback_does_not_turn_into_empty_results(self):
        from unittest.mock import MagicMock
        con=MagicMock();con.execute.return_value.fetchall.return_value=[]
        db=MagicMock();db.return_value.__enter__.return_value=con
        row={'sourceId':'osm:node:1','name':'Loja','state':'PA','channel':'Cosméticos','latitude':-1.45,'longitude':-48.48,'distanceKm':0,'source':'OpenStreetMap','clientId':None}
        cached=({'capturedAt':g.time.time()-7200,'rows':[row]},7200)
        with patch.dict(g.crm._services,{'db':db,'scoped_rows':lambda con,kind,user:[], 'is_seller':lambda con,user:True}),patch.object(g.crm,'access',return_value='Erika'),patch.object(g,'read_source_cache',return_value=cached),patch.object(g,'external_search',side_effect=TimeoutError()):
            response=g.search(self.data(),'Bearer x');self.assertEqual(response['externalStatus'],'cached');self.assertEqual(len(response['results']),1);self.assertIn('2.0 horas',response['warning'])
    def test_recent_persistent_cache_skips_provider_and_stale_cache_expires(self):
        from unittest.mock import MagicMock
        con=MagicMock();con.execute.return_value.fetchall.return_value=[]
        db=MagicMock();db.return_value.__enter__.return_value=con
        with patch.dict(g.crm._services,{'db':db,'scoped_rows':lambda con,kind,user:[], 'is_seller':lambda con,user:True}),patch.object(g.crm,'access',return_value='Erika'),patch.object(g,'read_source_cache',return_value=({'rows':[]},60)),patch.object(g,'external_search') as fetch:
            response=g.search(self.data(),'Bearer x');self.assertEqual(response['externalStatus'],'cached');fetch.assert_not_called()
        con.execute.return_value.fetchone.return_value=({'capturedAt':g.time.time()-86401,'rows':[]},)
        with patch.dict(g.crm._services,{'db':db}):self.assertIsNone(g.read_source_cache(self.data()))
    def test_external_failure_is_distinct_from_zero_matches(self):
        from unittest.mock import MagicMock
        con=MagicMock();con.execute.return_value.fetchall.return_value=[]
        db=MagicMock();db.return_value.__enter__.return_value=con
        with patch.dict(g.crm._services,{'db':db,'scoped_rows':lambda con,kind,user:[], 'is_seller':lambda con,user:True}),patch.object(g.crm,'access',return_value='Erika'),patch.object(g,'external_search',side_effect=TimeoutError()):
            response=g.search(self.data(),'Bearer x');self.assertEqual(response['externalStatus'],'unavailable');self.assertEqual(response['results'],[]);self.assertTrue(response['warning'])
        with patch.dict(g.crm._services,{'db':db,'scoped_rows':lambda con,kind,user:[], 'is_seller':lambda con,user:True}),patch.object(g.crm,'access',return_value='Erika'),patch.object(g,'external_search',return_value=[]):
            response=g.search(self.data(),'Bearer x');self.assertEqual(response['externalStatus'],'ok');self.assertEqual(response['warning'],'')
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
