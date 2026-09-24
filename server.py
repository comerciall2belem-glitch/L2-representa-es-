"""L2 ONE: secure FastAPI/PostgreSQL application for Render."""
import os, json, time, hashlib, secrets, re, base64, gzip
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from decimal import Decimal, InvalidOperation
from pydantic import BaseModel, Field
import psycopg
from psycopg.types.json import Jsonb

BASE = Path(__file__).resolve().parent
USERS = ['Ana Paula', 'Euler', 'Laís', 'Marlene']
INITIAL_PASSWORD = os.getenv('L2_INITIAL_PASSWORD', '')
PASSWORDS = {u: INITIAL_PASSWORD or os.getenv(f'L2_PASSWORD_{i}', '') for i, u in enumerate(USERS, 1)}
DATABASE_URL = os.getenv('DATABASE_URL', '')
SESSION_HOURS = int(os.getenv('L2_SESSION_HOURS', '24'))
config_errors = []
if not DATABASE_URL:
    config_errors.append('DATABASE_URL ausente')
for i, user in enumerate(USERS, 1):
    if not PASSWORDS[user]:
        config_errors.append(f'L2_PASSWORD_{i} ausente')
    elif len(PASSWORDS[user]) < 12:
        config_errors.append(f'L2_PASSWORD_{i} deve ter pelo menos 12 caracteres')
if config_errors:
    raise RuntimeError('Configuracao invalida: ' + '; '.join(config_errors))

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
        con.execute('ALTER TABLE app_users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false')
        con.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES app_users(username), expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at)')
        for user, password in PASSWORDS.items():
            exists = con.execute('SELECT 1 FROM app_users WHERE username=%s',(user,)).fetchone()
            if not exists:
                con.execute('INSERT INTO app_users(username,password_hash,must_change_password) VALUES(%s,%s,true)',(user,password_hash(password)))
        # Importação inicial opcional por segredo do Render. O valor é gzip+base64,
        # nunca fica no repositório, e só é aplicado enquanto a carteira estiver vazia.
        initial_clients = os.getenv('L2_INITIAL_CLIENTS_B64', '')
        has_clients = con.execute("SELECT 1 FROM entities WHERE kind='client' LIMIT 1").fetchone()
        if initial_clients and not has_clients:
            try:
                decoded = gzip.decompress(base64.b64decode(initial_clients)).decode('utf-8')
                clients = json.loads(decoded)
                if not isinstance(clients, list) or len(clients) != 432:
                    raise ValueError('A carga inicial deve conter 432 clientes.')
                seen = set()
                for client in clients:
                    entity_id = client.get('id') if isinstance(client, dict) else None
                    if not isinstance(entity_id, str) or not entity_id or entity_id in seen:
                        raise ValueError('ID de cliente inválido ou duplicado.')
                    if not isinstance(client.get('name'), str) or not client['name'].strip():
                        raise ValueError('Nome de cliente obrigatório.')
                    seen.add(entity_id)
                    payload = dict(client)
                    payload['updatedBy'] = 'importacao-inicial'
                    con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s)', ('client', entity_id, Jsonb(payload)))
            except Exception as exc:
                raise RuntimeError('Falha ao importar a carteira inicial protegida.') from exc
        # Nunca excluir clientes automaticamente por divergência de UF.
        # O cadastro permanece disponível para correção; pedidos exigem PA/AP.

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

def auth(header, allow_password_change=False):
    if not header or not header.startswith('Bearer '):
        raise HTTPException(401, 'Autenticação necessária')
    token = header[7:]
    if len(token) < 32:
        raise HTTPException(401, 'Sessão inválida')
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    with db() as con:
        row = con.execute("SELECT s.username, u.must_change_password FROM sessions s JOIN app_users u ON u.username=s.username WHERE s.token_hash=%s AND s.expires_at>now() AND u.active",(token_digest,)).fetchone()
    if not row:
        raise HTTPException(401, 'Sessão inválida ou expirada')
    if row[1] and not allow_password_change:
        raise HTTPException(403, 'Troque a senha provisória antes de usar o sistema')
    return row[0]

@app.post('/api/login')
def login(data: Login):
    with db() as con:
        row = con.execute('SELECT password_hash, must_change_password FROM app_users WHERE username=%s AND active',(data.user,)).fetchone()
    if not row or not password_ok(data.password, row[0]):
        time.sleep(0.25)
        raise HTTPException(401, 'Credenciais inválidas')
    token = secrets.token_urlsafe(48)
    with db() as con:
        con.execute("DELETE FROM sessions WHERE expires_at<=now()")
        con.execute("INSERT INTO sessions(token_hash,username,expires_at) VALUES(%s,%s,now()+(%s || ' hours')::interval)",(hashlib.sha256(token.encode()).hexdigest(),data.user,SESSION_HOURS))
    return {'token': token, 'user': data.user, 'expiresInHours': SESSION_HOURS, 'mustChangePassword': row[1]}

@app.post('/api/logout')
def logout(authorization: str | None = Header(default=None)):
    auth(authorization, allow_password_change=True)
    token_digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with db() as con: con.execute('DELETE FROM sessions WHERE token_hash=%s',(token_digest,))
    return {'ok': True}


class PasswordChange(BaseModel):
    currentPassword: str = Field(max_length=1024)
    newPassword: str = Field(min_length=12, max_length=1024)

@app.post('/api/change-password')
def change_password(data: PasswordChange, authorization: str | None = Header(default=None)):
    user = auth(authorization, allow_password_change=True)
    with db() as con:
        row = con.execute('SELECT password_hash FROM app_users WHERE username=%s AND active FOR UPDATE', (user,)).fetchone()
        if not row or not password_ok(data.currentPassword, row[0]):
            raise HTTPException(401, 'Senha atual incorreta')
        if password_ok(data.newPassword, row[0]) or data.newPassword in PASSWORDS.values():
            raise HTTPException(400, 'Escolha uma senha diferente da provisória')
        con.execute('UPDATE app_users SET password_hash=%s, must_change_password=false, updated_at=now() WHERE username=%s', (password_hash(data.newPassword), user))
        con.execute('DELETE FROM sessions WHERE username=%s', (user,))
    return {'ok': True, 'loginRequired': True}

def normalize_uf(value):
    code = str(value or '').strip().upper()
    return {'PA':'PA','PARA':'PA','PARÁ':'PA','AP':'AP','AMAPA':'AP','AMAPÁ':'AP'}.get(code)

class PriceRow(BaseModel):
    brand: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    state: str
    price: Decimal = Field(ge=0)
    description: str = ''

class PriceImport(BaseModel):
    prices: list[PriceRow] = Field(min_length=1, max_length=1000)

@app.post('/api/prices/import')
def import_prices(data: PriceImport, authorization: str | None = Header(default=None)):
    if auth(authorization) != 'Ana Paula':
        raise HTTPException(403, 'Importação restrita à administradora')
    prepared = {}
    for row in data.prices:
        uf = normalize_uf(row.state)
        if not uf:
            raise HTTPException(400, 'Tabela deve identificar PA ou AP em cada produto')
        key = f"{row.brand.strip()}|{uf}|{row.sku.strip()}"
        if key in prepared:
            raise HTTPException(400, f'SKU duplicado para marca e UF: {key}')
        prepared[key] = {'brand':row.brand.strip(),'sku':row.sku.strip(),'state':uf,
                         'price':str(row.price),'description':row.description}
    with db() as con:
        for key, payload in prepared.items():
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('price',%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()", (key,Jsonb(payload)))
    return {'imported':len(prepared)}

@app.get('/api/prices/{client_id}')
def prices_for_client(client_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    with db() as con:
        row = con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s",(client_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Cliente não encontrado')
        uf = normalize_uf(row[0].get('state'))
        if not uf:
            raise HTTPException(400, 'UF do cliente ausente ou inválida')
        prices = [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='price' AND payload->>'state'=%s ORDER BY id",(uf,))]
    return {'clientId':client_id,'state':uf,'prices':prices}

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
            if kind == 'client':
                state = str(obj.get('state','')).strip().upper()
                if state and state not in ('PA','PARA','PARÁ','AP','AMAPA','AMAPÁ'):
                    raise HTTPException(400, 'A carteira aceita somente clientes do Pará e Amapá')
            if kind == 'order':
                if not isinstance(obj.get('items'), list) or not obj['items']:
                    raise HTTPException(400, 'Novo pedido exige itens e tabela de preços por UF; registros antigos permanecem somente para consulta')
                if not isinstance(obj.get('brand'), str) or not obj['brand'].strip():
                    raise HTTPException(400, 'Marca obrigatória')
                if not isinstance(obj.get('clientId'), str):
                    raise HTTPException(400, 'Cliente obrigatório')
                customer = con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s", (obj.get('clientId'),)).fetchone()
                if not customer:
                    raise HTTPException(400, 'Cliente não cadastrado')
                client_uf = normalize_uf(customer[0].get('state'))
                price_table = normalize_uf(obj.get('priceTable'))
                if not price_table:
                    raise HTTPException(400, 'Escolha a tabela de preços PA ou AP')
                total = Decimal('0')
                for item in obj['items']:
                    if not isinstance(item, dict) or not isinstance(item.get('sku'), str) or not item['sku'].strip():
                        raise HTTPException(400, 'SKU inválido')
                    price_key = f"{obj.get('brand','').strip()}|{price_table}|{item['sku'].strip()}"
                    price_row = con.execute("SELECT payload FROM entities WHERE kind='price' AND id=%s", (price_key,)).fetchone()
                    if not price_row:
                        raise HTTPException(400, f"Preço não cadastrado na tabela {price_table}: {item['sku']}")
                    try:
                        qty = Decimal(str(item['quantity']))
                        unit = Decimal(str(price_row[0]['price']))
                    except (KeyError, TypeError, ValueError, InvalidOperation):
                        raise HTTPException(400, 'Quantidade ou preço inválido')
                    if not qty.is_finite() or not unit.is_finite() or qty != qty.to_integral_value() or qty <= 0 or qty > 100000 or unit <= 0:
                        raise HTTPException(400, 'Quantidade ou preço inválido')
                    item['unitPrice'] = str(unit)
                    item['subtotal'] = str((qty * unit).quantize(Decimal('0.01')))
                    total += qty * unit
                obj['clientState'] = client_uf
                obj['priceTable'] = price_table
                obj['state'] = price_table
                obj['amount'] = float(total.quantize(Decimal('0.01')))
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
        for kind, name in [('client','clients'),('visit','visits'),('order','orders'),('task','tasks'),('route','routes'),('goal','goals'),('price','prices')]:
            result[name] = [row[0] for row in con.execute('SELECT payload FROM entities WHERE kind=%s ORDER BY updated_at,id',(kind,))]
        return result

@app.get('/health')
def health():
    with db() as con:
        users = con.execute('SELECT count(*) FROM app_users WHERE active').fetchone()[0]
        counts = {kind: con.execute('SELECT count(*) FROM entities WHERE kind=%s',(kind,)).fetchone()[0]
                  for kind in ('client','visit','order','task','route','goal')}
    return {'status':'ok', 'users':users, **counts}

@app.post('/api/self-test')
def self_test(authorization: str | None = Header(default=None)):
    user = auth(authorization)
    if user != 'Ana Paula':
        raise HTTPException(403, 'Autoteste restrito à administradora')
    sample = {
        'visit': {'id':'self-test-visit','clientId':'self-test','date':'2026-01-01','notes':'teste transacional'},
        'task': {'id':'self-test-task','title':'teste transacional','date':'2026-01-01','done':False},
        'route': {'id':'self-test-route','date':'2026-01-01','clientIds':[]},
        'goal': {'id':'self-test-goal','month':'2026-01','amount':1.0},
    }
    checks = {}
    with db() as con:
        for kind, payload in sample.items():
            con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()', (kind,payload['id'],Jsonb(payload)))
            checks[kind] = bool(con.execute('SELECT 1 FROM entities WHERE kind=%s AND id=%s',(kind,payload['id'])).fetchone())
        con.rollback()
    return {'status':'ok' if all(checks.values()) else 'failed', 'transactionRolledBack':True, 'checks':checks}

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
