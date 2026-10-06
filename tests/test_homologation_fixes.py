import ast
import hashlib
import logging
import secrets
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from login_identity import resolve_identity

class Cursor:
    def __init__(self, rows=()): self.rows=rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def __iter__(self): return iter(self.rows)

class Connection:
    def __init__(self, provisional=False, payload=None):
        self.provisional=provisional; self.payload=payload; self.actions=[]
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def execute(self,sql,params=None):
        self.actions.append((sql,params))
        if sql=='SELECT username FROM app_users WHERE active': return Cursor([('Ana Paula',)])
        if sql.startswith('SELECT password_hash'): return Cursor([('personal-hash',self.provisional,['finance'],'Administradora')])
        if sql.startswith('UPDATE app_users'): return Cursor([('Ana Paula',)])
        if sql.startswith('SELECT payload FROM entities'): return Cursor([(self.payload,)]) if self.payload is not None else Cursor()
        if sql.startswith('UPDATE entities'): self.payload=params[0]
        if sql.startswith("SELECT payload->>'date'"): return Cursor([(self.payload['date'],)])
        return Cursor()

class Failure(Exception):
    def __init__(self,status_code,detail): self.status_code=status_code

tree=ast.parse(Path(__file__).resolve().parents[1].joinpath('server.py').read_text())
nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('login','correct_order_18_date')]
for n in nodes: n.decorator_list=[]

class HomologationFixTests(unittest.TestCase):
    def scope(self, con):
        scope={'db':lambda:con, 'Login':object,'resolve_identity':resolve_identity,'password_ok':lambda p,h:p=='personal-password',
               'PASSWORDS':{'Ana Paula':'bootstrap-password'},'password_hash':lambda p:'new-hash','secrets':secrets,
               'time':SimpleNamespace(sleep=lambda _:None),'HTTPException':Failure,'hashlib':hashlib,'SESSION_HOURS':24,
               'effective_sectors':lambda *args:['finance'],'Jsonb':lambda x:x,'logging':logging,'os':SimpleNamespace(getenv=lambda name,*a:'srv-daqk8ifavr4c738m78f0' if name=='RENDER_SERVICE_ID' else ''),
               'project_order':lambda con,id,p:con.actions.append(('projection',p['date']))}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<fixes>','exec'),scope)
        return scope

    def test_normalized_login_uses_canonical_session_identity(self):
        con=Connection(); scope=self.scope(con)
        result=scope['login'](SimpleNamespace(user=' ANA   PAULA ', password='personal-password'))
        self.assertEqual(result['user'],'Ana Paula')

    def test_bootstrap_cannot_replace_personal_password(self):
        con=Connection(); scope=self.scope(con)
        with self.assertRaises(Failure): scope['login'](SimpleNamespace(user='Ana Paula',password='bootstrap-password'))
        self.assertFalse(any(sql.startswith('UPDATE app_users') for sql,_ in con.actions))

    def test_bootstrap_only_recovers_provisional_account(self):
        con=Connection(provisional=True); scope=self.scope(con)
        self.assertTrue(scope['login'](SimpleNamespace(user='Ana Paula',password='bootstrap-password'))['mustChangePassword'])
        self.assertTrue(any(sql.startswith('UPDATE app_users') for sql,_ in con.actions))

    def test_migration_verified_and_idempotent(self):
        con=Connection(payload={'date':'2026-11-30'}); scope=self.scope(con)
        scope['correct_order_18_date'](); scope['correct_order_18_date']()
        self.assertEqual(con.payload['date'],'2026-09-29')
        self.assertEqual(con.payload['dateCorrection']['original'],'2026-11-30')
        self.assertEqual(sum(sql.startswith('UPDATE entities') for sql,_ in con.actions),1)

    def test_migration_does_not_create_missing_order(self):
        con=Connection(); self.scope(con)['correct_order_18_date']()
        self.assertFalse(any(sql.startswith('UPDATE entities') for sql,_ in con.actions))

    def test_production_startup_never_corrects_homologation_order(self):
        con=Connection(payload={'date':'2026-11-30'}); scope=self.scope(con)
        scope['os']=SimpleNamespace(getenv=lambda *a:'srv-production')
        scope['correct_order_18_date']()
        self.assertFalse(con.actions)
