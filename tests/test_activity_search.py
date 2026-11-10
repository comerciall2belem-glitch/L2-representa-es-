import contextlib
import unittest
from unittest.mock import MagicMock
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import activity_search as module

class ActivitySearchTests(unittest.TestCase):
    def setUp(self):
        self.con=MagicMock();self.con.execute.return_value.fetchall.return_value=[]
    def test_filters_are_bound_and_accent_prefix_is_normalized(self):
        module.search(self.con,'Ana',True,{'commercial'},q='reposição São',owner="Ana'",status='Aberta',start='2026-11-01',end='2026-11-30')
        sql,args=self.con.execute.call_args.args
        self.assertNotIn("Ana'",sql);self.assertIn("Ana'",args);self.assertIn('reposicao:* & sao:*',args)
        self.assertIn('client_seller_authorizations',sql);self.assertNotIn('office_action',args[0])
    def test_invalid_filters_never_query_database(self):
        for filters in [dict(q='a'*81),dict(start='2026-13-01'),dict(start='2026-11-02',end='2026-11-01'),dict(kind='office_finance'),dict(offset=-1)]:
            with self.subTest(filters=filters):
                with self.assertRaises(ValueError):module.search(self.con,'Ana',False,{'commercial'},**filters)
        self.con.execute.assert_not_called()
    def test_pagination_has_ten_results_and_continuation(self):
        self.con.execute.return_value.fetchall.return_value=[('task',str(i),{'text':'Ligar'},'Cliente','2026-11-01','Ana','Aberta') for i in range(11)]
        result=module.search(self.con,'Ana',False,{'commercial'})
        self.assertEqual(len(result['activities']),10);self.assertTrue(result['hasMore'])
    def test_detail_not_found_or_denied(self):
        self.con.execute.return_value.fetchone.return_value=None
        with self.assertRaises(HTTPException) as ctx:module.detail(self.con,'Ana',True,{'commercial'},'task','t1')
        self.assertEqual(ctx.exception.status_code,404)
        sql,args=self.con.execute.call_args.args
        self.assertIn('client_seller_authorizations',sql);self.assertIn('Ana',args)
    def test_authentication_blocks_all_reads(self):
        app=FastAPI()
        def auth(header):raise HTTPException(401)
        @contextlib.contextmanager
        def db():yield self.con
        module.install(app,db,auth,lambda *args:None,lambda user:{'commercial'},lambda *args:True)
        c=TestClient(app)
        self.assertEqual(c.get('/api/activities/search').status_code,401)
        self.assertEqual(c.get('/api/activities/task/t1').status_code,401)
        self.con.execute.assert_not_called()

if __name__=='__main__':unittest.main()
