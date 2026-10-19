import json
import logging
import os
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import drive_backup as drive

ENV={'GOOGLE_DRIVE_CLIENT_ID':'test-client','GOOGLE_DRIVE_CLIENT_SECRET':'test-secret','GOOGLE_DRIVE_REDIRECT_URI':'https://testserver/api/integrations/google-drive/callback'}

class DriveTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,ENV,clear=True);self.env.start()
        self.con=MagicMock();self.con.__enter__.return_value=self.con
        self.app=FastAPI()
        def auth(header):
            if not header: raise HTTPException(401,'login')
            return 'Marlene' if header=='Bearer other' else 'Ana Paula'
        drive.configure(self.app,lambda:self.con,auth,lambda user:{'records':{'order':[{'id':'1','amount':20}]}})
        self.client=TestClient(self.app,base_url='https://testserver')
        self.headers={'Authorization':'Bearer session'}
    def tearDown(self): self.env.stop()
    def test_missing_configuration(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(self.client.get(drive.PREFIX+'/configuration').json()['configured'])
            self.assertEqual(self.client.post(drive.PREFIX+'/connect',headers=self.headers).status_code,503)
        self.assertEqual(self.client.post(drive.PREFIX+'/connect').status_code,401)
        self.assertEqual(self.client.post(drive.PREFIX+'/connect',headers={'Authorization':'Bearer other'}).status_code,403)
    def test_start_limited_scope_and_secure_cookie(self):
        response=self.client.post(drive.PREFIX+'/connect',headers=self.headers)
        self.assertEqual(response.status_code,200)
        query=parse_qs(urlsplit(response.json()['url']).query)
        self.assertEqual(query['scope'],[drive.SCOPE]);self.assertEqual(query['code_challenge_method'],['S256'])
        self.assertIn('HttpOnly',response.headers['set-cookie']);self.assertIn('Secure',response.headers['set-cookie'])
        self.assertNotIn('test-secret',response.text)
    def test_callback_rejects_wrong_cookie_and_inactive_session(self):
        self.con.execute.return_value.fetchone.return_value=('Ana Paula','session-hash',drive.digest('right-cookie'),drive.cipher().encrypt(b'verifier').decode())
        self.assertEqual(self.client.get(drive.PREFIX+'/callback?state=x&code=y').status_code,400)
        self.client.cookies.set(drive.COOKIE,'right-cookie')
        state=MagicMock();state.fetchone.return_value=('Ana Paula','session-hash',drive.digest('right-cookie'),drive.cipher().encrypt(b'verifier').decode())
        inactive=MagicMock();inactive.fetchone.return_value=None
        self.con.execute.side_effect=[state,inactive]
        self.assertEqual(self.client.get(drive.PREFIX+'/callback?state=x&code=y').status_code,401)
    def test_success_callback_encrypts_refresh_and_resets_old_file(self):
        self.client.cookies.set(drive.COOKIE,'right-cookie')
        self.con.execute.return_value.fetchone.return_value=('Ana Paula','session-hash',drive.digest('right-cookie'),drive.cipher().encrypt(b'verifier').decode())
        with patch.object(drive.httpx,'Client') as client:
            client.return_value.__enter__.return_value.post.return_value=MagicMock(status_code=200,json=lambda:{'refresh_token':'private-refresh','scope':drive.SCOPE})
            response=self.client.get(drive.PREFIX+'/callback?state=x&code=y')
        self.assertEqual(response.status_code,200);self.assertNotIn('private-refresh',response.text)
        sql,params=self.con.execute.call_args.args
        self.assertIn('file_id=NULL',sql);self.assertNotIn('private-refresh',params[1]);self.assertEqual(drive.cipher().decrypt(params[1].encode()),b'private-refresh')
    def test_failed_upload_does_not_claim_success(self):
        encrypted=drive.cipher().encrypt(b'refresh').decode()
        self.con.execute.return_value.fetchone.return_value=(encrypted,None,None,None)
        with patch.object(drive.httpx,'Client') as client:
            client.return_value.__enter__.return_value.post.return_value=MagicMock(status_code=401)
            result=drive.backup('Ana Paula')
        self.assertFalse(result['sent']);self.assertIn('error',result)
        self.assertFalse(any('last_backup=now()' in call.args[0] for call in self.con.execute.call_args_list))
    def test_unchanged_snapshot_skips_network(self):
        fingerprint=drive.hashlib.sha256(drive.json_bytes(drive.export_snapshot('Ana Paula'))).hexdigest()
        self.con.execute.return_value.fetchone.return_value=('encrypted','folder','file',fingerprint)
        with patch.object(drive.httpx,'Client') as client:
            self.assertTrue(drive.backup('Ana Paula')['unchanged']);client.assert_not_called()
    def test_successful_upload_confirms_only_after_drive_response(self):
        encrypted=drive.cipher().encrypt(b'refresh').decode()
        self.con.execute.return_value.fetchone.return_value=(encrypted,None,None,None)
        with patch.object(drive.httpx,'Client') as factory:
            client=factory.return_value.__enter__.return_value
            client.post.side_effect=[MagicMock(status_code=200,json=lambda:{'access_token':'access'}),MagicMock(status_code=200,json=lambda:{'id':'folder'})]
            client.request.return_value=MagicMock(status_code=200,json=lambda:{'id':'file'})
            result=drive.backup('Ana Paula')
        self.assertTrue(result['sent'])
        self.assertIn('last_backup=now()',self.con.execute.call_args.args[0])
        self.assertEqual(self.con.execute.call_args.args[1][0:2],('folder','file'))
        body=client.request.call_args.kwargs['content']
        self.assertIn(b'L2_One_Registros.json.gz',body)
        self.assertNotIn(b'private-refresh',body)
    def test_callback_code_redacted_from_access_log(self):
        record=logging.LogRecord('uvicorn.access',20,'',1,'%s %s %s',('host','GET',drive.PREFIX+'/callback?code=private'),None)
        drive.RedactOAuthQuery().filter(record)
        self.assertNotIn('private',record.getMessage())

if __name__=='__main__': unittest.main()
