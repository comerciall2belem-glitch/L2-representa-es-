import unittest
from unittest.mock import patch
from fastapi import HTTPException
import field_assistant as field
import strategic_crm as crm
class Result:
    def fetchone(self):return ('Vendedor',['commercial'])
class Con:
    def execute(self,*a):return Result()
class AgendaValidation(unittest.TestCase):
    def item(self):return {'source':'field_agenda','user':'Erika','date':'2026-10-09','time':'09:30','text':'Apresentar lançamentos','type':'Visita','status':'Aberta'}
    def test_valid_agenda_has_belem_due_time(self):
        item=self.item()
        with patch.dict(crm._services,{'is_seller':lambda *a:True}):field.validate_agenda_task(Con(),'Erika',item)
        self.assertEqual(item['dueAt'],'2026-10-09T09:30:00-03:00')
    def test_seller_cannot_assign_or_edit_another_users_task(self):
        with patch.dict(crm._services,{'is_seller':lambda *a:True}):
            for item,old in [(dict(self.item(),user='Euler'),None),(self.item(),{'user':'Euler'})]:
                with self.assertRaises(HTTPException) as raised:field.validate_agenda_task(Con(),'Erika',item,old)
                self.assertEqual(raised.exception.status_code,403)
    def test_invalid_dates_times_and_states_cannot_persist(self):
        with patch.dict(crm._services,{'is_seller':lambda *a:False}):
            for key,value in [('date','2026-02-30'),('time','25:30'),('time','09:99'),('status','Faturado'),('type','Excluir pedidos'),('text','aa')]:
                with self.subTest(key=key,value=value),self.assertRaises(HTTPException):field.validate_agenda_task(Con(),'Ana Paula',dict(self.item(),**{key:value}))
