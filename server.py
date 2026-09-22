"""L2 ONE: secure FastAPI/PostgreSQL application for Render."""
import os, json, time, hashlib, secrets, re
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import psycopg
from psycopg.types.json import Jsonb

BASE = Path(__file__).resolve().parent
USERS = ['Ana Paula', 'Euler', 'Laís', 'Marlene']
PASSWORDS = {u: os.getenv(f'L2_PASSWORD_{i}', '') for i, u in enumerate(USERS, 1)}
DATABASE_URL = os.getenv('DATABASE_URL', '')
SESSION_HOURS = int(os.getenv('L2_SESSION_HOURS', '24'))
if not DATABASE_URL or not all(len(p) >= 12 for p in PASSWORDS.values()):
    raise RuntimeError('Configure DATABASE_URL e L2_PASSWORD_1..4 (12+ caracteres).')

def db():
    return psycopg.connect(DATABASE_URL)

def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310_000)
    return salt.hex() + ':' + digest.hex()

def password_ok(password, stored):
    try:
        salt_hex, _ = stored.split(':', 1)
        return secrets.compare_digest(password_hash(password, bytes.fromhex(salt_hex)), stored)
    except (ValueError, TypeError):
        return False

def initialize():
    with db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS entities (kind TEXT NOT NULL, id TEXT NOT NULL, payload JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(kind,id))')
        con.execute('CREATE TABLE IF NOT EXISTS applied_changes (change_id TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE TABLE IF NOT EXISTS audit_log (id BIGSERIAL PRIMARY KEY, username TEXT NOT NULL, kind TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE TABLE IF NOT EXISTS app_users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT true, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES app_users(username), expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at)')
        for user, password in PASSWORDS.items():
            exists = con.execute('SELECT 1 FROM app_users WHERE username=%s',(user,)).fetchone()
            if not exists:
                con.execute('INSERT INTO app_users(username,password_hash) VALUES(%s,%s)',(user,password_hash(password)))
        # A carteira comercial é importada pela API autenticada após a publicação.
        # Nenhum dado de cliente é armazenado no repositório de código.

@asynccontextmanager
async def lifespan(app):
    initialize()
    yield

app = FastAPI(title='L2 ONE API', lifespan=lifespan, docs_url=None, redoc_url=None)

class Login(BaseModel):
    user: str
    password: str

class Change(BaseModel):
    type: str
    data: dict
    changeId: str = Field(min_length=8, max_length=128)

class Sync(BaseModel):
    changes: list[Change] = Field(max_length=500)

def auth(header):
    if not header or not header.startswith('Bearer '):
        raise HTTPException(401, 'Autenticação necessária')
    token = header[7:]
    if len(token) < 32:
        raise HTTPException(401, 'Sessão inválida')
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    with db() as con:
        row = con.execute("SELECT s.username FROM sessions s JOIN app_users u ON u.username=s.username WHERE s.token_hash=%s AND s.expires_at>now() AND u.active",(token_digest,)).fetchone()
    if not row:
        raise HTTPException(401, 'Sessão inválida ou expirada')
    return row[0]

@app.post('/api/login')
def login(data: Login):
    with db() as con:
        row = con.execute('SELECT password_hash FROM app_users WHERE username=%s AND active',(data.user,)).fetchone()
    if not row or not password_ok(data.password, row[0]):
        time.sleep(0.25)
        raise HTTPException(401, 'Credenciais inválidas')
    token = secrets.token_urlsafe(48)
    with db() as con:
        con.execute("DELETE FROM sessions WHERE expires_at<=now()")
        con.execute("INSERT INTO sessions(token_hash,username,expires_at) VALUES(%s,%s,now()+(%s || ' hours')::interval)",(hashlib.sha256(token.encode()).hexdigest(),data.user,SESSION_HOURS))
    return {'token': token, 'user': data.user, 'expiresInHours': SESSION_HOURS}

@app.post('/api/logout')
def logout(authorization: str | None = Header(default=None)):
    auth(authorization)
    token_digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with db() as con: con.execute('DELETE FROM sessions WHERE token_hash=%s',(token_digest,))
    return {'ok': True}

@app.post('/api/sync')
def sync(data: Sync, authorization: str | None = Header(default=None)):
    user = auth(authorization)
    with db() as con:
        for change in data.changes:
            kind, obj = change.type, dict(change.data)
            entity_id = obj.get('id')
            if kind not in ('client','visit','order','task','route','goal','delete_route') or not isinstance(entity_id,str) or not 1 <= len(entity_id) <= 128:
                raise HTTPException(400, 'Alteração inválida')
            if kind == 'client' and (not isinstance(obj.get('name'),str) or not obj['name'].strip()):
                raise HTTPException(400, 'Nome do cliente obrigatório')
            if kind == 'order':
                try: amount = float(obj.get('amount',0))
                except (TypeError, ValueError): raise HTTPException(400,'Valor inválido')
                if not 0 <= amount <= 1e10: raise HTTPException(400,'Valor inválido')
            if kind == 'goal':
                try: amount = float(obj.get('amount',0))
                except (TypeError, ValueError): raise HTTPException(400,'Meta inválida')
                if not re.fullmatch(r'\d{4}-\d{2}',str(obj.get('month',''))) or not 0 <= amount <= 1e10:
                    raise HTTPException(400,'Meta inválida')
            if kind == 'route' and user in ('Laís','Marlene'):
                raise HTTPException(403,'Roteirização restrita a representantes')
            if kind == 'delete_route' and user in ('Laís','Marlene'):
                raise HTTPException(403,'Roteirização restrita a representantes')
            if kind == 'client' and user == 'Euler' and con.execute('SELECT 1 FROM entities WHERE kind=%s AND id=%s',('client',entity_id)).fetchone():
                # A repeated, previously applied change is accepted below.
                if not con.execute('SELECT 1 FROM applied_changes WHERE change_id=%s',(change.changeId,)).fetchone():
                    raise HTTPException(403,'Sem permissão para editar cadastro existente')
            if con.execute('SELECT 1 FROM applied_changes WHERE change_id=%s',(change.changeId,)).fetchone():
                continue
            if kind == 'delete_route':
                con.execute('DELETE FROM entities WHERE kind=%s AND id=%s',('route',entity_id))
            else:
                obj['updatedBy'] = user
                con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()', (kind,entity_id,Jsonb(obj)))
            con.execute('INSERT INTO applied_changes(change_id) VALUES(%s)',(change.changeId,))
            con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,kind,entity_id,'delete' if kind=='delete_route' else 'upsert'))
        result = {}
        for kind, name in [('client','clients'),('visit','visits'),('order','orders'),('task','tasks'),('route','routes'),('goal','goals')]:
            result[name] = [row[0] for row in con.execute('SELECT payload FROM entities WHERE kind=%s ORDER BY updated_at,id',(kind,))]
        return result

@app.get('/health')
def health():
    with db() as con: con.execute('SELECT 1')
    return {'status':'ok'}

@app.middleware('http')
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' https:; img-src 'self' data:; frame-ancestors 'none'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.get('/')
@app.get('/index.html')
def home(): return FileResponse(BASE/'index.html',headers={'Cache-Control':'no-store'})

@app.get('/{filename}')
def asset(filename: str):
    if filename not in ('app.js','sw.js','manifest.json'):
        raise HTTPException(404)
    return FileResponse(BASE/filename,headers={'Cache-Control':'no-store'})
