from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from lead_capture import LeadIntake, normalize_intake, next_seller, ingest_lead, lead_sla

os.environ.setdefault('DATABASE_URL','postgresql://localhost/lead_capture_test')
os.environ.setdefault('L2_INITIAL_PASSWORD','test-password-long-enough')
import server


class Cursor:
    def __init__(self,row=None,rows=None):self.row,self.rows=row,rows or []
    def fetchone(self):return self.row
    def fetchall(self):return self.rows


class IntakeDB:
    def __init__(self):self.leads={};self.last=None;self.events=[];self.sellers=['Erika','Euler']
    def execute(self,sql,params=None):
        if sql.startswith('SELECT pg_advisory'):return Cursor()
        if sql.startswith('SELECT id FROM capture_leads'):
            return Cursor(rows=[(key,) for key,item in self.leads.items()
                                if (params[0] and item['email']==params[0]) or
                                   (params[2] and item['phone']==params[2])])
        if sql.startswith('SELECT id FROM capture_accounts'):return Cursor()
        if sql.startswith('INSERT INTO capture_accounts'):return Cursor()
        if sql.startswith('INSERT INTO capture_contacts'):return Cursor()
        if sql.startswith('INSERT INTO capture_leads'):
            self.leads[params[0]]={'email':params[-2],'phone':params[-1],'owner':None}
            return Cursor()
        if sql.startswith('INSERT INTO capture_lead_events'):
            self.events.append(params[1]);return Cursor()
        if sql.startswith('SELECT last_username'):return Cursor((self.last,))
        if sql.startswith('SELECT username FROM app_users'):return Cursor(rows=[(x,) for x in self.sellers])
        if sql.startswith('UPDATE capture_leads'):
            self.leads[params[1]]['owner']=params[0];return Cursor()
        if sql.startswith('UPDATE capture_routing_state'):
            self.last=params[0];return Cursor()
        raise AssertionError(sql)


class LeadCaptureTests(unittest.TestCase):
    def test_normalization_and_invalid_input(self):
        item=normalize_intake(LeadIntake(name='  Maria   Silva ',email=' MARIA@EXAMPLE.COM ',phone='(91) 99999-0000',utmSource='campanha'),lambda _:True)
        self.assertEqual((item['name'],item['email'],item['phone'],item['utmSource']),('Maria Silva','maria@example.com','91999990000','campanha'))
        with self.assertRaises(HTTPException):normalize_intake(LeadIntake(name='Maria',email='invalid'),lambda _:True)
        with self.assertRaises(HTTPException):normalize_intake(LeadIntake(name='Maria',phone='91999990000',state='SP'),lambda _:True)

    def test_round_robin_and_duplicate_event(self):
        con=IntakeDB()
        first=normalize_intake(LeadIntake(name='Maria',email='maria@example.com',utmCampaign='outubro'),lambda _:True)
        second=normalize_intake(LeadIntake(name='José',email='jose@example.com'),lambda _:True)
        one=ingest_lead(con,first);two=ingest_lead(con,second);duplicate=ingest_lead(con,first)
        self.assertEqual((one['owner'],two['owner']),('Erika','Euler'))
        self.assertEqual(duplicate['id'],one['id'])
        self.assertEqual(len(con.leads),2)
        self.assertEqual(con.events,['created','distributed','created','distributed','duplicate_received'])
        self.assertEqual(next_seller(['Euler'], 'Erika'),'Euler')
        con.sellers=[]
        unassigned=ingest_lead(con,normalize_intake(LeadIntake(name='Bia',email='bia@example.com'),lambda _:True))
        self.assertEqual(unassigned['status'],'Novo / Não Distribuído')

    def test_sla(self):
        now=datetime(2026,9,28,12,tzinfo=timezone.utc)
        self.assertEqual(lead_sla(now-timedelta(minutes=121),None,now),'Vencido')
        self.assertEqual(lead_sla(now-timedelta(minutes=15),None,now),'A vencer')
        self.assertEqual(lead_sla(now-timedelta(minutes=121),now,now),'Atendido')

    def test_webhook_requires_configured_secret(self):
        data=LeadIntake(name='Maria',email='maria@example.com')
        with patch.dict(os.environ,{'L2_LEAD_WEBHOOK_TOKEN':''}):
            with self.assertRaises(HTTPException) as error:server.capture_lead_webhook(data,'invalid')
            self.assertEqual(error.exception.status_code,503)
        with patch.dict(os.environ,{'L2_LEAD_WEBHOOK_TOKEN':'correct-secret'}):
            with self.assertRaises(HTTPException) as error:server.capture_lead_webhook(data,'invalid')
            self.assertEqual(error.exception.status_code,401)

if __name__=='__main__':unittest.main()
