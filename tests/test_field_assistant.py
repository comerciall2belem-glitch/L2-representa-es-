import unittest
from contextlib import ExitStack
from datetime import datetime,timedelta,date
from unittest.mock import patch
from fastapi import HTTPException
import field_assistant as f
import strategic_crm as crm
import routing_engine as routing

class Cursor:
    def __init__(self,rows=()):self.rows=list(rows)
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows
class Store:
    def __init__(self):self.session=None;self.logged=set();self.sql=[];self.mapped=True;self.active=True
    def execute(self,sql,args=()):
        self.sql.append((sql,args))
        if 'SELECT username FROM field_operators' in sql:return Cursor([('Erika',)] if self.mapped else [])
        if 'SELECT role,sectors FROM app_users' in sql:return Cursor([('Vendedor',['commercial'])] if self.active else [])
        if 'SELECT 1 FROM field_events' in sql:return Cursor([(1,)] if args[0] in self.logged else [])
        if 'SELECT payload,updated_at FROM field_sessions' in sql:return Cursor([(self.session,datetime.now(crm.TZ))] if self.session else [])
        if 'INSERT INTO field_sessions' in sql:self.session=args[1].obj
        if 'DELETE FROM field_sessions' in sql:self.session=None
        if 'INSERT INTO field_events' in sql:self.logged.add(args[0])
        return Cursor()

class FieldTests(unittest.TestCase):
    def test_intents_and_accents(self):
        self.assertEqual(f.intent('AGENDA AMANHÃ'),{'kind':'agenda','day':'amanha'})
        self.assertEqual(f.intent('visita Drogaria Alfa: cliente ausente')['result'],'cliente ausente')
        self.assertEqual(f.intent('cliente quente Alfa')['query'],'alfa')
        self.assertEqual(f.intent('excluir todos os pedidos')['kind'],'help')
    def test_phone_and_safe_links(self):
        self.assertEqual(f.phone_number('(91) 99999-9999'),'5591999999999')
        with self.assertRaises(HTTPException):f.phone_number('123')
        links=f.links({'latitude':-1.4,'longitude':-48.4,'phone':'91999999999'})
        self.assertIn('api=1',links['maps']);self.assertTrue(links['waze'].startswith('https://waze.com/ul?'))
        self.assertEqual(links['call'],'tel:+5591999999999')
    def test_ambiguous_names_require_choice(self):
        rows=[{'id':'1','name':'Drogaria Alfa','city':'Belém'},{'id':'2','name':'Drogaria Alfa','city':'Macapá'}]
        self.assertEqual(len(f.match_clients(rows,'Drogaria Alfa')),2)
        self.assertEqual(f.match_clients(rows,'Drogaria Alfa Belém')[0]['id'],'1')
    def test_authenticated_confirmed_write_and_dedup(self):
        store=Store();clients=[{'id':'c1','name':'Alfa','city':'Belém','state':'PA','owner':'Erika'}];writes=[]
        def scoped(con,kind,user):return clients if kind=='client' else []
        with ExitStack() as stack:
            stack.enter_context(patch.dict(crm._services,{'scoped_rows':scoped,'check_client_scope':lambda *x:None,'project_attendance':lambda *x:None}))
            stack.enter_context(patch.object(crm,'record',return_value=clients[0]))
            stack.enter_context(patch.object(crm,'put',side_effect=lambda con,kind,item:writes.append((kind,item))))
            event=stack.enter_context(patch.object(crm,'event'));stack.enter_context(patch.object(crm,'changed'))
            self.assertIn('CONFIRMAR',f.handle_message(store,'5591999999999','message1','visita Alfa: pedido realizado'))
            self.assertEqual(writes,[])
            reply=f.handle_message(store,'5591999999999','long-message'*30,'confirmar')
            self.assertIn('faturamento e comissão',reply)
            self.assertEqual([k for k,v in writes],['visit','task'])
            self.assertLess(len(writes[1][1]['id']),128)
            self.assertEqual(writes[0][1]['result'],'Visitado - pedido');event.assert_called_once()
            self.assertIn('já foi',f.handle_message(store,'5591999999999','long-message'*30,'confirmar'))
            self.assertEqual(len(writes),2)
    def test_unknown_or_disabled_operator_cannot_write(self):
        store=Store();store.mapped=False
        self.assertIsNone(f.handle_message(store,'5591999999999','x','confirmar'))
        store.mapped=True;store.active=False
        self.assertIn('suspenso',f.handle_message(store,'5591999999999','x','confirmar'))
    def test_expiry_and_cancel(self):
        store=Store()
        with patch.dict(crm._services,{'scoped_rows':lambda *x:[]}):
            self.assertIn('Nenhum registro',f.handle_message(store,'5591999999999','x','confirmar'))
            store.session={'clientId':'c1'}
            self.assertIn('cancelada',f.handle_message(store,'5591999999999','y','cancelar'))
            self.assertIsNone(store.session)
    def test_daily_summary_scoped_and_purchase_is_only_visit(self):
        rows={'visit':[{'user':'Erika','date':'2026-10-08','result':'Visitado - pedido','clientId':'a'},{'user':'Ana Paula','date':'2026-10-08','result':'Visitado - pedido','clientId':'b'}],'route':[{'user':'Erika','date':'2026-10-08','clientId':'a'},{'user':'Erika','date':'2026-10-08','clientId':'c'}]}
        with patch.dict(crm._services,{'scoped_rows':lambda con,k,u:rows[k]}):
            text=f.daily_summary(None,'Erika',date(2026,10,8))
            self.assertIn('Visitas registradas: 1',text);self.assertIn('sem registro: 1',text)
    def test_notification_disabled_without_credentials(self):
        with patch('whatsapp_media.provider_config',return_value=None),patch.dict(crm._services,{'db':lambda: self.fail('DB must not be touched')}):f.notification_tick()

class NotificationTests(unittest.TestCase):
    def test_reserved_summary_never_retries_ambiguous_send(self):
        import test_strategic_crm as fixtures
        store=fixtures.MemoryCon();original=store.execute
        def execute(sql,args=()):
            if sql.startswith('SELECT pg_try_advisory'):return Cursor([(True,)])
            if "kind='strategy_settings' AND id='field'" in sql:return Cursor([({'enabled':True,'template':'l2_field','language':'pt_BR'},)])
            if 'SELECT f.phone,f.username' in sql:return Cursor([('5591999999999','Erika')])
            if "kind='field_notice'" in sql:return Cursor()
            if "SELECT 1 FROM entities WHERE kind='field_send'" in sql:return Cursor([(1,)] if ('field_send',args[0]) in store.entities else [])
            return original(sql,args)
        store.execute=execute
        with patch.dict(crm._services,{'db':lambda:store,'scoped_rows':lambda *x:[]}),patch.object(f,'datetime') as clock,patch('whatsapp_media.provider_config',return_value=('token','123','v23.0')),patch('whatsapp_media._post',side_effect=TimeoutError()) as send:
            clock.now.return_value=datetime(2026,10,8,18,tzinfo=crm.TZ)
            f.notification_tick();f.notification_tick()
            self.assertEqual(send.call_count,1)
            records=[x for (k,i),x in store.entities.items() if k=='field_send']
            self.assertEqual(records[0]['status'],'Conferir envio')

class RoutingTests(unittest.TestCase):
    def test_road_matrix_uses_minutes_and_handles_unreachable(self):
        nodes=[{'_node':'origin','latitude':-1.4,'longitude':-48.4},{'_node':'c1','latitude':-1.5,'longitude':-48.5}]
        with patch.object(routing,'_get',return_value={'durations':[[0,121],[None,0]],'distances':[[0,1500],[None,0]]}):
            fn=routing.matrix(nodes);self.assertEqual(fn(nodes[0],nodes[1]),{'minutes':3,'km':1.5});self.assertIsNone(fn(nodes[1],nodes[0]))
    def test_bad_matrix_or_geometry_rejected(self):
        nodes=[{'_node':'o','latitude':0,'longitude':0},{'_node':'c','latitude':1,'longitude':1}]
        with patch.object(routing,'_get',return_value={'durations':[[0]],'distances':[[0]]}):
            with self.assertRaises(routing.RoutingError):routing.matrix(nodes)
        with patch.object(routing,'_get',return_value={'routes':[{'geometry':{'type':'LineString','coordinates':[[999,0],[1,1]]}}]}):
            with self.assertRaises(routing.RoutingError):routing.geometry(nodes)
    def test_volume_signal_requires_baseline(self):
        today=date(2026,10,8);orders=[{'clientId':'c','status':'Faturado','date':(today-timedelta(days=n)).isoformat(),'amount':1000} for n in (40,70)]
        self.assertTrue(crm.cadence({'id':'c'},orders,today=today)['volumeDrop'])
        self.assertFalse(crm.cadence({'id':'c'},orders[:1],today=today)['volumeDrop'])

if __name__=='__main__':unittest.main()
