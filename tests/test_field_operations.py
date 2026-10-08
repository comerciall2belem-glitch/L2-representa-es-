import unittest
from contextlib import ExitStack
from datetime import datetime,timedelta
from unittest.mock import patch
from fastapi import HTTPException
import field_operations as ops
import field_assistant as field
import strategic_crm as crm
import test_field_assistant as fixtures

class Store(fixtures.Store):
    def __init__(self):super().__init__();self.entities={}
    def execute(self,sql,args=()):
        if sql.startswith('INSERT INTO entities'):self.entities[(args[0],args[1])]=args[2].obj;return fixtures.Cursor()
        if "kind='field_location' AND id=" in sql:
            item=self.entities.get(('field_location',args[0]));return fixtures.Cursor([(item,)] if item else [])
        if sql.startswith('SELECT payload FROM entities WHERE kind=%s AND id='):
            item=self.entities.get((args[0],args[1]));return fixtures.Cursor([(item,)] if item else [])
        if "SELECT 1 FROM entities WHERE kind='field_send'" in sql:return fixtures.Cursor([(1,)] if ('field_send',args[0]) in self.entities else [])
        return super().execute(sql,args)

class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,8,9,0,tzinfo=crm.TZ)
        self.store=Store();self.clients=[{'id':'c','owner':'Erika','name':'Alfa','city':'Belém','state':'PA','latitude':-1.4,'longitude':-48.4,'tdc':{'segment':'Cosméticos'}}]
    def context(self,scope=None):
        stack=ExitStack()
        stack.enter_context(patch.dict(crm._services,{'scoped_rows':lambda con,k,u:self.clients if k=='client' else [x for (ki,i),x in self.store.entities.items() if ki==k],'check_client_scope':scope or (lambda *x:None)}))
        return stack
    def test_brazilian_currency_validation(self):
        self.assertEqual(ops.money('1.234,56'),1234.56);self.assertEqual(ops.money('183.00'),183)
        for bad in ('0','-1','NaN','100001','1.234'):
            with self.assertRaises(HTTPException):ops.money(bad)
        self.assertEqual(field.intent('Despesa combustível R$ 183,00')['amount'],183)
    def test_relative_and_absolute_times_and_invalid_past(self):
        self.assertEqual(ops.due_time('em 30 min',self.now),self.now+timedelta(minutes=30))
        self.assertEqual(ops.due_time('amanha 09:00',self.now).day,9)
        for bad in ('hoje 08:00','amanha 25:00','2026-02-30 09:00'):
            with self.assertRaises(HTTPException):ops.due_time(bad,self.now)
    def test_expense_confirmed_once_and_review_not_cash(self):
        with self.context():
            self.assertIn('CONFIRMAR',field.handle_message(self.store,'5591999999999','m1','despesa combustível R$ 183,00'))
            self.assertEqual(self.store.entities,{})
            self.assertIn('conferência',field.handle_message(self.store,'5591999999999','m2','confirmar'))
            self.assertEqual(sum(k=='route_expense' for k,i in self.store.entities),1)
            self.assertEqual(sum(k=='office_action' for k,i in self.store.entities),1)
            self.assertFalse(any(k.startswith('cash') for k,i in self.store.entities))
            field.handle_message(self.store,'5591999999999','m2','confirmar')
            self.assertEqual(sum(k=='route_expense' for k,i in self.store.entities),1)
    def test_location_is_private_until_confirmed_and_scope_rechecked(self):
        with self.context():
            reply=field.handle_message(self.store,'5591999999999','loc','location',location={'latitude':-1.401,'longitude':-48.401})
            self.assertIn('não altera',reply);self.assertFalse(any(k=='client' for k,i in self.store.entities))
            self.assertIn('Alfa',field.handle_message(self.store,'5591999999999','near','perto cosméticos'))
            self.assertIn('CONFIRMAR',field.handle_message(self.store,'5591999999999','prepare','localizar Alfa'))
        def deny(*x):raise HTTPException(403,'Carteira restrita')
        with self.context(deny):
            with self.assertRaises(HTTPException):field.handle_message(self.store,'5591999999999','confirm','confirmar')
        self.assertFalse(any(k=='client' for k,i in self.store.entities))
    def test_reminder_is_visible_as_task_without_creating_order(self):
        with self.context(),patch.object(ops,'datetime') as clock:
            clock.now.return_value=self.now;clock.fromisoformat.side_effect=datetime.fromisoformat
            self.assertIn('CONFIRMAR',field.handle_message(self.store,'5591999999999','r1','lembrete em 30 min: ligar para cliente Alfa'))
            self.assertIn('Agenda',field.handle_message(self.store,'5591999999999','r2','confirmar'))
            task=next(x for (k,i),x in self.store.entities.items() if k=='task')
            self.assertEqual(task['time'],'09:30');self.assertEqual(task['clientId'],'c')
            self.assertFalse(any(k=='order' for k,i in self.store.entities))
    def test_reminder_phases_cancellation_and_reschedule(self):
        task={'id':'t','user':'Erika','clientId':'c','date':'2026-10-08','time':'09:30','dueAt':'2026-10-08T09:30:00-03:00','text':'ligar Alfa','status':'Aberta'}
        self.store.entities[('task','t')]=task
        with self.context():
            first=ops.reminder_candidate(self.store,'Erika',self.now);self.assertIn('30 min',first[2])
            self.store.entities[('field_send',first[0])]={'status':'Conferir envio'}
            self.assertIsNone(ops.reminder_candidate(self.store,'Erika',self.now))
            second=ops.reminder_candidate(self.store,'Erika',self.now+timedelta(minutes=30));self.assertNotEqual(first[0],second[0]);self.assertIn('Agora',second[2])
            task['time']='10:00';self.assertIsNone(ops.reminder_candidate(self.store,'Erika',self.now))
            self.assertIsNotNone(ops.reminder_candidate(self.store,'Erika',self.now+timedelta(minutes=30)))
            task['status']='Concluída';self.assertIsNone(ops.reminder_candidate(self.store,'Erika',self.now+timedelta(minutes=30)))
    def test_productivity_uses_observations_and_no_fictional_loss(self):
        visits=[{'date':'2026-10-08','result':'Visitado - pedido'},{'date':'2026-10-08','result':'Cliente ausente'},{'date':'2026-10-08','result':'Contato remoto'}]
        result=ops.productivity([],visits,[{'date':'2026-10-08','distanceKm':3,'travelMinutes':10}],[{'date':'2026-10-08','amount':183}],'2026-10-01','2026-10-08')
        self.assertEqual(result['fieldVisits'],2);self.assertEqual(result['orderVisitRate'],50);self.assertEqual(result['actualSubmittedExpense'],183)
        self.assertIsNone(result['observedVisitMinutes']);self.assertNotIn('lostRevenue',result)
    def test_location_invalid_coordinates_and_far_region(self):
        for location in ({'latitude':True,'longitude':0},{'latitude':float('nan'),'longitude':0},{'latitude':91,'longitude':0}):self.assertFalse(ops.location_valid(location))
        self.assertEqual(ops.nearby(self.clients,{'latitude':0,'longitude':0}),[])

class ReminderDeliveryTests(unittest.TestCase):
    def test_weekend_reminder_advance_and_due_have_separate_reservations(self):
        import test_strategic_crm as existing
        store=existing.MemoryCon();original=store.execute
        task={'id':'t','user':'Erika','date':'2026-10-10','time':'09:30','dueAt':'2026-10-10T09:30:00-03:00','text':'Conferir combustível','status':'Aberta'}
        def execute(sql,args=()):
            if sql.startswith('SELECT pg_try_advisory'):return fixtures.Cursor([(True,)])
            if "kind='strategy_settings' AND id='field'" in sql:return fixtures.Cursor([({'enabled':True,'template':'l2_field','language':'pt_BR'},)])
            if 'SELECT f.phone,f.username' in sql:return fixtures.Cursor([('5591999999999','Erika')])
            if "SELECT 1 FROM entities WHERE kind='field_send'" in sql:return fixtures.Cursor([(1,)] if ('field_send',args[0]) in store.entities else [])
            return original(sql,args)
        store.execute=execute
        with patch.dict(crm._services,{'db':lambda:store,'scoped_rows':lambda con,k,u:[task] if k=='task' else []}),patch.object(field,'datetime') as clock,patch('whatsapp_media.provider_config',return_value=('token','123','v23.0')),patch('whatsapp_media._post',return_value={'messages':[{'id':'wamid.test'}]}) as send:
            clock.now.return_value=datetime(2026,10,10,9,tzinfo=crm.TZ)
            field.notification_tick();field.notification_tick();self.assertEqual(send.call_count,1)
            clock.now.return_value=datetime(2026,10,10,9,30,tzinfo=crm.TZ)
            field.notification_tick();self.assertEqual(send.call_count,2)
            self.assertEqual(sum(k=='field_send' for k,i in store.entities),2)

class LocationWebhookTests(unittest.TestCase):
    def test_signed_location_reaches_field_and_invalid_location_rejected(self):
        from scripts.zara_e2e_mock import Harness
        from scripts.simulate_zara import payload
        with Harness() as harness,patch.object(field,'handle_message',return_value='Localização atual recebida') as handler:
            data=payload();message=data['entry'][0]['changes'][0]['value']['messages'][0]
            message['type']='location';message.pop('text');message['location']={'latitude':-1.4,'longitude':-48.4}
            self.assertEqual(harness.post(data).status_code,200)
            self.assertEqual(handler.call_args.kwargs['location']['latitude'],-1.4)
            handler.reset_mock();message['location']['latitude']=float('nan')
            self.assertEqual(harness.post(data).status_code,400);handler.assert_not_called()

if __name__=='__main__':unittest.main()
