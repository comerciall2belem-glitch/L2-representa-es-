"""L2 ONE: secure FastAPI/PostgreSQL application for Render."""
import os, json, time, hashlib, secrets, re, base64, gzip, unicodedata
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, UploadFile, File
from fastapi.responses import FileResponse, Response
from decimal import Decimal, InvalidOperation
from pydantic import BaseModel, Field
import psycopg
from psycopg.types.json import Jsonb
from client_cleanup import plan as client_cleanup_plan

BASE = Path(__file__).resolve().parent
USERS = ['Ana Paula', 'Euler', 'Laís', 'Marlene']
FINANCE_USERS = {'Ana Paula', 'Euler', 'Laís'}
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

def next_order_number(con):
    return con.execute('UPDATE order_counter SET value=value+1 WHERE id=1 RETURNING value').fetchone()[0]

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
        con.execute('CREATE TABLE IF NOT EXISTS archived_entities (kind TEXT NOT NULL, id TEXT NOT NULL, payload JSONB NOT NULL, reason TEXT NOT NULL, archived_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(kind,id))')
        con.execute('CREATE TABLE IF NOT EXISTS order_counter (id SMALLINT PRIMARY KEY CHECK(id=1), value BIGINT NOT NULL CHECK(value>=0))')
        con.execute('INSERT INTO order_counter(id,value) VALUES(1,0) ON CONFLICT(id) DO NOTHING')
        existing_max = con.execute("SELECT COALESCE(MAX((payload->>'orderNumber')::bigint),0) FROM (SELECT payload FROM entities WHERE kind='order' UNION ALL SELECT payload FROM archived_entities WHERE kind='order') AS orders WHERE payload->>'orderNumber' ~ '^[0-9]+$'").fetchone()[0]
        con.execute('UPDATE order_counter SET value=GREATEST(value,%s) WHERE id=1',(existing_max,))
        legacy_orders = con.execute("SELECT source,id FROM (SELECT 'active' AS source,id,payload FROM entities WHERE kind='order' AND payload->>'orderNumber' IS NULL UNION ALL SELECT 'archived' AS source,id,payload FROM archived_entities WHERE kind='order' AND payload->>'orderNumber' IS NULL) AS orders ORDER BY payload->>'date',id").fetchall()
        for source, order_id in legacy_orders:
            number=next_order_number(con)
            table='entities' if source=='active' else 'archived_entities'
            con.execute(f"UPDATE {table} SET payload=jsonb_set(payload,'{{orderNumber}}',to_jsonb(%s::bigint)) WHERE kind='order' AND id=%s",(number,order_id))
        con.execute('CREATE TABLE IF NOT EXISTS order_attachments (id TEXT PRIMARY KEY, order_id TEXT NOT NULL, filename TEXT NOT NULL, content_type TEXT NOT NULL, content BYTEA NOT NULL, uploaded_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE INDEX IF NOT EXISTS idx_order_attachments_order ON order_attachments(order_id)')
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
        initial_prices = os.getenv('L2_INITIAL_PRICES_B64', '')
        has_prices = con.execute("SELECT 1 FROM entities WHERE kind='price' LIMIT 1").fetchone()
        if initial_prices and not has_prices:
            try:
                prices = json.loads(gzip.decompress(base64.b64decode(initial_prices)).decode('utf-8'))
                if not isinstance(prices, list) or len(prices) != 3934:
                    raise ValueError('Carga de preços deve conter 3934 registros.')
                seen = set()
                for row in prices:
                    if not isinstance(row, dict):
                        raise ValueError('Produto inválido.')
                    brand, sku = str(row.get('brand') or '').strip(), str(row.get('sku') or '').strip()
                    state = str(row.get('state') or '').strip().upper()
                    amount = Decimal(str(row.get('price') or '0'))
                    if not brand or not sku or state not in ('PA', 'AP') or not amount.is_finite() or amount <= 0:
                        raise ValueError('Marca, SKU, UF ou preço inválido.')
                    key = f'{brand}|{state}|{sku}'
                    if key in seen:
                        raise ValueError('Preço duplicado na carga.')
                    seen.add(key)
                    payload = {'brand':brand, 'sku':sku, 'state':state,
                               'price':str(amount), 'description':str(row.get('description') or '')}
                    con.execute("INSERT INTO entities(kind,id,payload) VALUES('price',%s,%s)", (key, Jsonb(payload)))
            except Exception as exc:
                raise RuntimeError('Falha ao importar preços iniciais protegidos.') from exc
        office_seed = os.getenv('L2_OFFICE_SEED_B64', '')
        office_marker = 'office-management-seed-v1'
        already_imported = con.execute('SELECT 1 FROM applied_changes WHERE change_id=%s', (office_marker,)).fetchone()
        if office_seed and not already_imported:
            try:
                office = json.loads(gzip.decompress(base64.b64decode(office_seed)).decode('utf-8'))
                sections = {
                    'processes': ('office_process', 'ID'),
                    'budget': ('office_budget', None),
                    'rituals': ('office_ritual', 'Momento'),
                    'roles': ('office_role', 'Pessoa'),
                    'actions': ('office_action', 'ID'),
                    'commercial': ('office_commercial', 'ID'),
                    'administrative': ('office_administrative', 'ID'),
                    'finance': ('office_finance', 'ID'),
                    'monthlyClose': ('office_monthly_close', 'Mês'),
                }
                for section, (kind, id_field) in sections.items():
                    rows = office.get(section, [])
                    if not isinstance(rows, list):
                        raise ValueError(f'Seção inválida: {section}')
                    for index, row in enumerate(rows):
                        if not isinstance(row, dict):
                            raise ValueError(f'Linha inválida: {section}')
                        row_id = str(row.get(id_field, '') if id_field else '').strip()
                        if not row_id:
                            row_id = f'{section}-{index + 1}'
                        payload = dict(row)
                        payload['id'] = row_id
                        payload['source'] = 'L2 — Gestão Integrada do Escritório'
                        con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()', (kind, row_id, Jsonb(payload)))
                con.execute('INSERT INTO applied_changes(change_id) VALUES(%s)', (office_marker,))
            except Exception as exc:
                raise RuntimeError('Falha ao importar a gestão integrada do escritório.') from exc

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

class AdminPasswordReset(BaseModel):
    newPassword: str = Field(min_length=12, max_length=1024)

@app.post('/api/admin/reset-my-password')
def admin_reset_my_password(data: AdminPasswordReset, authorization: str | None = Header(default=None)):
    user = auth(authorization)
    if user != 'Ana Paula':
        raise HTTPException(403, 'Acesso restrito à administradora')
    if data.newPassword in PASSWORDS.values():
        raise HTTPException(400, 'Escolha uma senha pessoal diferente da provisória')
    with db() as con:
        row = con.execute('SELECT password_hash FROM app_users WHERE username=%s AND active FOR UPDATE', (user,)).fetchone()
        if not row:
            raise HTTPException(404, 'Conta indisponível')
        if password_ok(data.newPassword, row[0]):
            raise HTTPException(400, 'Escolha uma senha diferente da atual')
        con.execute('UPDATE app_users SET password_hash=%s, must_change_password=false, updated_at=now() WHERE username=%s', (password_hash(data.newPassword), user))
        con.execute('DELETE FROM sessions WHERE username=%s', (user,))
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)', (user,'app_user',user,'password_reset'))
    return {'ok': True, 'loginRequired': True}

class TeamAccessReset(BaseModel):
    user: str

@app.get('/api/admin/team-access')
def team_access(authorization: str | None = Header(default=None)):
    if auth(authorization) != 'Ana Paula':
        raise HTTPException(403, 'Acesso restrito à administradora')
    with db() as con:
        rows = con.execute('SELECT username,active,must_change_password FROM app_users ORDER BY username').fetchall()
    return [{'user': name, 'active': active, 'mustChangePassword': first_access} for name,active,first_access in rows]

@app.post('/api/admin/team-access/reset')
def reset_team_access(data: TeamAccessReset, authorization: str | None = Header(default=None)):
    if auth(authorization) != 'Ana Paula':
        raise HTTPException(403, 'Acesso restrito à administradora')
    if data.user not in USERS or data.user == 'Ana Paula':
        raise HTTPException(400, 'Selecione um integrante da equipe')
    provisional = secrets.token_urlsafe(24)
    with db() as con:
        row = con.execute('SELECT active FROM app_users WHERE username=%s FOR UPDATE', (data.user,)).fetchone()
        if not row or not row[0]:
            raise HTTPException(404, 'Conta não está ativa')
        con.execute('UPDATE app_users SET password_hash=%s,must_change_password=true,updated_at=now() WHERE username=%s', (password_hash(provisional),data.user))
        con.execute('DELETE FROM sessions WHERE username=%s', (data.user,))
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)', ('Ana Paula','app_user',data.user,'team_access_reset'))
    return {'user': data.user, 'temporaryPassword': provisional, 'mustChangePassword': True}

def valid_cnpj(value):
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    def check(length):
        weights = list(range(length - 7, 1, -1)) + list(range(9, 1, -1))
        total = sum(int(digits[i]) * weights[i] for i in range(length))
        remainder = total % 11
        return 0 if remainder < 2 else 11 - remainder
    return int(digits[12]) == check(12) and int(digits[13]) == check(13)

def normalize_uf(value):
    code = str(value or '').strip().upper()
    return {'PA':'PA','PARA':'PA','PARÁ':'PA','AP':'AP','AMAPA':'AP','AMAPÁ':'AP'}.get(code)

def price_table_matches_client(customer_state, table_state):
    client_uf = normalize_uf(customer_state)
    return client_uf is not None and client_uf == normalize_uf(table_state)

class PriceRow(BaseModel):
    brand: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    state: str
    price: Decimal = Field(ge=0)
    description: str = ''

class PriceImport(BaseModel):
    prices: list[PriceRow] = Field(min_length=1, max_length=5000)

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
            deletable = {'delete_whatsapp_template':'whatsapp_template','delete_client':'client','delete_visit':'visit','delete_task':'task','delete_goal':'goal','delete_route':'route','delete_order':'order','delete_price':'price','delete_cash_entry':'cash_entry','delete_commission_rate':'commission_rate','delete_commission_receipt':'commission_receipt','delete_office_process':'office_process','delete_office_action':'office_action','delete_office_commercial':'office_commercial','delete_office_administrative':'office_administrative','delete_office_ritual':'office_ritual','delete_office_role':'office_role','delete_office_finance':'office_finance','delete_office_budget':'office_budget','delete_office_monthly_close':'office_monthly_close'}
            if kind not in ('whatsapp_template','client','visit','order','task','route','goal','price','commission_rate','commission_receipt','office_action','office_commercial','office_administrative','office_ritual','office_role','office_finance','office_budget','office_monthly_close','office_process','cash_day','cash_entry',*deletable) or not isinstance(entity_id,str) or not 1 <= len(entity_id) <= 128:
                raise HTTPException(400, 'Alteração inválida')
            if kind in deletable:
                target = deletable[kind]
                if target in ('office_finance','office_budget','office_monthly_close','cash_entry','commission_rate','commission_receipt') and user not in FINANCE_USERS:
                    raise HTTPException(403, 'Acesso financeiro restrito')
                if target == 'cash_entry':
                    previous = con.execute("SELECT payload FROM entities WHERE kind='cash_entry' AND id=%s",(entity_id,)).fetchone()
                    if previous:
                        day = con.execute("SELECT payload FROM entities WHERE kind='cash_day' AND payload->>'date'=%s",(previous[0].get('date'),)).fetchone()
                        if not day or day[0].get('status') != 'Aberto':
                            raise HTTPException(409, 'Caixa fechado não permite excluir lançamentos')
                if target == 'route' and user in ('Laís','Marlene'):
                    raise HTTPException(403, 'Roteirização restrita a representantes')
                if target in ('price','client') and user != 'Ana Paula':
                    raise HTTPException(403, 'Exclusão restrita à administradora')
                if target == 'office_process' and user not in FINANCE_USERS:
                    previous = con.execute("SELECT payload FROM entities WHERE kind=%s AND id=%s", (target,entity_id)).fetchone()
                    if previous and previous[0].get('Área') == 'Financeiro':
                        raise HTTPException(403, 'Acesso financeiro restrito')
                if target == 'client':
                    for dependent in ('order','visit','route','task'):
                        if con.execute("SELECT 1 FROM entities WHERE kind=%s AND payload->>'clientId'=%s LIMIT 1",(dependent,entity_id)).fetchone():
                            raise HTTPException(409, 'Cliente possui histórico vinculado; preserve o cadastro')
                if target == 'order':
                    previous = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s",(entity_id,)).fetchone()
                    if previous and previous[0].get('status') not in ('Pendente','Cancelado') and user != 'Ana Paula':
                        raise HTTPException(403, 'Somente Ana Paula pode arquivar pedido confirmado ou faturado')
                if con.execute('SELECT 1 FROM applied_changes WHERE change_id=%s',(change.changeId,)).fetchone():
                    continue
                if target == 'order' and previous:
                    con.execute("INSERT INTO archived_entities(kind,id,payload,reason) VALUES('order',%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,reason=excluded.reason,archived_at=now()",(entity_id,Jsonb(previous[0]),'arquivado por '+user))
                con.execute('DELETE FROM entities WHERE kind=%s AND id=%s',(target,entity_id))
                con.execute('INSERT INTO applied_changes(change_id) VALUES(%s)',(change.changeId,))
                con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,target,entity_id,'delete'))
                continue
            if kind == 'price':
                if user != 'Ana Paula':
                    raise HTTPException(403, 'Preço restrito à administradora')
                brand,sku,state = str(obj.get('brand','')).strip(),str(obj.get('sku','')).strip(),normalize_uf(obj.get('state'))
                try: price=Decimal(str(obj.get('price','')))
                except (ValueError,InvalidOperation): raise HTTPException(400,'Preço inválido')
                if not brand or not sku or not state or entity_id != f'{brand}|{state}|{sku}' or not price.is_finite() or price <= 0 or price.as_tuple().exponent < -2:
                    raise HTTPException(400,'Preço ou identificação inválida')
                obj.update(brand=brand,sku=sku,state=state,price=str(price))
            if (kind in ('office_finance','office_budget','office_monthly_close','cash_day','cash_entry','commission_rate','commission_receipt') or (kind == 'office_process' and str(obj.get('Área','')) == 'Financeiro')) and user not in FINANCE_USERS:
                raise HTTPException(403, 'Acesso financeiro restrito')
            if kind in ('commission_rate','commission_receipt'):
                brand = str(obj.get('brand','')).strip()
                normalized = re.sub(r'[^a-z0-9]+','-',unicodedata.normalize('NFKD',brand).encode('ascii','ignore').decode().lower()).strip('-')
                if not normalized or len(brand)>120 or len(normalized)>120:
                    raise HTTPException(400, 'Indústria inválida')
                from decimal import Decimal as _Decimal
                try:
                    value = _Decimal(str(obj['rate' if kind=='commission_rate' else 'received']))
                except (KeyError, ValueError, InvalidOperation):
                    raise HTTPException(400, 'Valor de comissão inválido')
                if not value.is_finite() or value<0 or value.as_tuple().exponent < -2 or (kind=='commission_rate' and value>100) or (kind=='commission_receipt' and value>10000000000):
                    raise HTTPException(400, 'Valor de comissão inválido')
                expected_id = normalized if kind=='commission_rate' else str(obj.get('month',''))+'|'+normalized
                if kind=='commission_receipt' and not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',str(obj.get('month',''))):
                    raise HTTPException(400, 'Mês de comissão inválido')
                if entity_id != expected_id:
                    raise HTTPException(400, 'Identificação de comissão inválida')
            if kind == 'office_process' and user not in FINANCE_USERS:
                previous_process = con.execute("SELECT payload FROM entities WHERE kind='office_process' AND id=%s",(entity_id,)).fetchone()
                if previous_process and str(previous_process[0].get('Área','')) == 'Financeiro':
                    raise HTTPException(403, 'Acesso financeiro restrito')
            if kind in ('cash_day','cash_entry'):
                from datetime import date as _date
                from decimal import Decimal as _Decimal
                try:
                    _date.fromisoformat(str(obj.get('date','')))
                except (ValueError, TypeError):
                    raise HTTPException(400, 'Data do caixa inválida')
                if kind == 'cash_day':
                    try:
                        opening = _Decimal(str(obj['opening']))
                        if not opening.is_finite() or opening < 0 or opening.as_tuple().exponent < -2:
                            raise ValueError()
                    except (KeyError, ValueError, InvalidOperation):
                        raise HTTPException(400, 'Saldo inicial inválido')
                    if obj.get('status') not in ('Aberto','Fechado'):
                        raise HTTPException(400, 'Status de caixa inválido')
                    existing_day = con.execute("SELECT payload FROM entities WHERE kind='cash_day' AND id=%s", (entity_id,)).fetchone()
                    existing_date = con.execute("SELECT id FROM entities WHERE kind='cash_day' AND payload->>'date'=%s AND id<>%s", (obj['date'],entity_id)).fetchone()
                    if existing_date or (existing_day and existing_day[0].get('date') != obj['date']):
                        raise HTTPException(409, 'Caixa da data já existe')
                    if existing_day and existing_day[0].get('status') == 'Fechado':
                        raise HTTPException(409, 'Caixa fechado não pode ser alterado')
                    if existing_day and str(existing_day[0].get('opening')) != str(obj['opening']):
                        raise HTTPException(409, 'Saldo inicial não pode ser alterado após abertura')
                    if obj['status'] == 'Fechado':
                        if not existing_day:
                            raise HTTPException(409, 'Abra o caixa antes de fechar')
                        try:
                            closing = _Decimal(str(obj['closing']))
                            if not closing.is_finite() or closing < 0 or closing.as_tuple().exponent < -2:
                                raise ValueError()
                        except (KeyError, ValueError, InvalidOperation):
                            raise HTTPException(400, 'Saldo de fechamento inválido')
                else:
                    if obj.get('type') not in ('Entrada','Saída') or not str(obj.get('category','')).strip() or not str(obj.get('description','')).strip():
                        raise HTTPException(400, 'Movimentação incompleta')
                    try:
                        amount = _Decimal(str(obj['amount']))
                        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
                            raise ValueError()
                    except (KeyError, ValueError, InvalidOperation):
                        raise HTTPException(400, 'Valor da movimentação inválido')
                    existing_entry = con.execute("SELECT payload FROM entities WHERE kind='cash_entry' AND id=%s",(entity_id,)).fetchone()
                    if existing_entry and existing_entry[0].get('date') != obj['date']:
                        raise HTTPException(409, 'Movimentação não pode mudar de data')
                    day_record = con.execute("SELECT payload FROM entities WHERE kind='cash_day' AND payload->>'date'=%s",(obj['date'],)).fetchone()
                    if not day_record or day_record[0].get('status') != 'Aberto':
                        raise HTTPException(409, 'Caixa não está aberto para esta data')
            if kind == 'client' and (not isinstance(obj.get('name'),str) or not obj['name'].strip()):
                raise HTTPException(400, 'Nome do cliente obrigatório')
            if kind == 'client':
                state = str(obj.get('state','')).strip().upper()
                if state and state not in ('PA','PARA','PARÁ','AP','AMAPA','AMAPÁ'):
                    raise HTTPException(400, 'A carteira aceita somente clientes do Pará e Amapá')
            if kind == 'client':
                if con.execute("SELECT 1 FROM archived_entities WHERE kind='client' AND id=%s",(entity_id,)).fetchone():
                    raise HTTPException(409, 'Cliente arquivado; não é permitido recriar o mesmo cadastro')
                # Cadastros legados continuam editáveis; novos exigem identificação fiscal.
                existing = con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s", (entity_id,)).fetchone()
                if not existing and not normalize_uf(obj.get('state')):
                    raise HTTPException(400, 'UF PA ou AP obrigatória para novo cliente')
                tax_id = ''.join(ch for ch in str(obj.get('taxId') or '') if ch.isdigit())
                registration = str(obj.get('stateRegistration') or '').strip().upper()
                if not existing or tax_id or registration:
                    if not valid_cnpj(tax_id):
                        raise HTTPException(400, 'CNPJ inválido; informe os 14 dígitos corretos')
                    if registration != 'ISENTO' and not (registration.isdigit() and 7 <= len(registration) <= 14):
                        raise HTTPException(400, 'Informe inscrição estadual numérica ou ISENTO')
                    obj['taxId'] = tax_id
                    obj['stateRegistration'] = registration
                    duplicates = con.execute("SELECT id,payload FROM entities WHERE kind='client' AND id<>%s AND payload->>'taxId' IS NOT NULL", (entity_id,)).fetchall()
                    if any(''.join(ch for ch in str(row[1].get('taxId') or '') if ch.isdigit()) == tax_id for row in duplicates):
                        raise HTTPException(409, 'CNPJ já cadastrado em outro cliente')
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
                if not price_table_matches_client(customer[0].get('state'), obj.get('priceTable')):
                    raise HTTPException(400, 'A tabela de preços deve corresponder à UF do cliente')
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
                existing_order = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s",(entity_id,)).fetchone()
                if not existing_order and con.execute("SELECT 1 FROM archived_entities WHERE kind='order' AND id=%s",(entity_id,)).fetchone():
                    raise HTTPException(409, 'Pedido arquivado não pode ser recriado; restaure o original')
                obj['orderNumber'] = existing_order[0].get('orderNumber') if existing_order and existing_order[0].get('orderNumber') else next_order_number(con)
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
        public_kinds = [('whatsapp_template','whatsappTemplates'),('client','clients'),('visit','visits'),('order','orders'),('task','tasks'),('route','routes'),('goal','goals'),('price','prices'),('office_process','officeProcesses'),('office_action','officeActions'),('office_commercial','officeCommercial'),('office_administrative','officeAdministrative'),('office_ritual','officeRituals'),('office_role','officeRoles')]
        for kind, name in public_kinds:
            if kind == 'office_process' and user not in FINANCE_USERS:
                result[name] = [row[0] for row in con.execute("SELECT payload FROM entities WHERE kind=%s AND coalesce(payload->>'Área','')<>'Financeiro' ORDER BY updated_at,id", (kind,))]
            else:
                result[name] = [row[0] for row in con.execute('SELECT payload FROM entities WHERE kind=%s ORDER BY updated_at,id',(kind,))]
        for kind, name in [('office_finance','officeFinance'),('office_budget','officeBudget'),('office_monthly_close','officeMonthlyClose'),('cash_day','cashDays'),('cash_entry','cashEntries'),('commission_rate','commissionRates'),('commission_receipt','commissionReceipts')]:
            result[name] = [row[0] for row in con.execute('SELECT payload FROM entities WHERE kind=%s ORDER BY updated_at,id',(kind,))] if user in FINANCE_USERS else []
        return result

def order_exists(con, order_id: str):
    if not con.execute("SELECT 1 FROM entities WHERE kind='order' AND id=%s",(order_id,)).fetchone():
        raise HTTPException(404, 'Pedido não encontrado')

@app.get('/api/admin/orders/archived')
def archived_orders(authorization: str | None = Header(default=None)):
    if auth(authorization) != 'Ana Paula':
        raise HTTPException(403, 'Consulta restrita à administradora')
    with db() as con:
        rows=con.execute("SELECT id,payload,reason,archived_at FROM archived_entities WHERE kind='order' ORDER BY archived_at DESC LIMIT 200").fetchall()
    return [{'id':id,'order':payload,'reason':reason,'archivedAt':when.isoformat()} for id,payload,reason,when in rows]

@app.post('/api/admin/orders/{order_id}/restore')
def restore_archived_order(order_id: str, authorization: str | None = Header(default=None)):
    if auth(authorization) != 'Ana Paula':
        raise HTTPException(403, 'Restauração restrita à administradora')
    with db() as con:
        row=con.execute("SELECT payload FROM archived_entities WHERE kind='order' AND id=%s FOR UPDATE",(order_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Pedido arquivado não encontrado')
        if con.execute("SELECT 1 FROM entities WHERE kind='order' AND id=%s",(order_id,)).fetchone():
            raise HTTPException(409, 'Pedido já está ativo')
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('order',%s,%s)",(order_id,Jsonb(row[0])))
        con.execute("DELETE FROM archived_entities WHERE kind='order' AND id=%s",(order_id,))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES('Ana Paula','order',%s,'restore')",(order_id,))
    return {'id':order_id,'restored':True}

@app.get('/api/orders/{order_id}/attachments')
def list_order_attachments(order_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    with db() as con:
        order_exists(con,order_id)
        rows=con.execute('SELECT id,filename,content_type,octet_length(content),uploaded_by,created_at FROM order_attachments WHERE order_id=%s ORDER BY created_at,id',(order_id,)).fetchall()
    return [{'id':r[0],'name':r[1],'type':r[2],'size':r[3],'uploadedBy':r[4],'createdAt':r[5].isoformat()} for r in rows]

@app.post('/api/orders/{order_id}/attachments')
async def upload_order_attachment(order_id: str, file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user=auth(authorization)
    name=Path(file.filename or '').name.strip()[:180]
    content=await file.read(5*1024*1024+1)
    if not name or not content or len(content)>5*1024*1024:
        raise HTTPException(400, 'Comprovante vazio ou maior que 5 MB')
    if content.startswith(b'%PDF-'): content_type='application/pdf'
    elif content.startswith(b'\xff\xd8\xff'): content_type='image/jpeg'
    elif content.startswith(b'\x89PNG\r\n\x1a\n'): content_type='image/png'
    else: raise HTTPException(400, 'Envie somente PDF, JPG ou PNG')
    allowed_extensions={'application/pdf':('.pdf',),'image/jpeg':('.jpg','.jpeg'),'image/png':('.png',)}
    if not name.lower().endswith(allowed_extensions[content_type]):
        raise HTTPException(400,'A extensão não corresponde ao conteúdo do arquivo')
    attachment_id=secrets.token_hex(16)
    with db() as con:
        order_exists(con,order_id)
        count=con.execute('SELECT count(*) FROM order_attachments WHERE order_id=%s',(order_id,)).fetchone()[0]
        if count>=5: raise HTTPException(409,'Limite de cinco comprovantes por pedido')
        con.execute('INSERT INTO order_attachments(id,order_id,filename,content_type,content,uploaded_by) VALUES(%s,%s,%s,%s,%s,%s)',(attachment_id,order_id,name,content_type,content,user))
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'order_attachment',attachment_id,'upload'))
    return {'id':attachment_id,'name':name,'type':content_type,'size':len(content)}

@app.get('/api/orders/{order_id}/attachments/{attachment_id}')
def download_order_attachment(order_id: str, attachment_id: str, authorization: str | None = Header(default=None)):
    auth(authorization)
    with db() as con:
        row=con.execute('SELECT filename,content_type,content FROM order_attachments WHERE id=%s AND order_id=%s',(attachment_id,order_id)).fetchone()
    if not row: raise HTTPException(404,'Comprovante não encontrado')
    safe_name=''.join(c if (c.isascii() and c.isalnum()) or c in ' ._-()' else '_' for c in row[0])
    return Response(content=bytes(row[2]),media_type=row[1],headers={'Content-Disposition':f'attachment; filename="{safe_name}"'})

@app.delete('/api/orders/{order_id}/attachments/{attachment_id}')
def delete_order_attachment(order_id: str, attachment_id: str, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    with db() as con:
        order=con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s",(order_id,)).fetchone()
        if not order: raise HTTPException(404,'Pedido não encontrado')
        if order[0].get('status') not in ('Pendente','Cancelado'):
            raise HTTPException(409,'Comprovantes de pedidos confirmados devem ser preservados')
        deleted=con.execute('DELETE FROM order_attachments WHERE id=%s AND order_id=%s RETURNING id',(attachment_id,order_id)).fetchone()
        if not deleted: raise HTTPException(404,'Comprovante não encontrado')
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'order_attachment',attachment_id,'delete'))
    return {'deleted':True}

def cleanup_snapshot(con):
    rows=[{'id':r[0],'payload':r[1]} for r in con.execute("SELECT id,payload FROM entities WHERE kind='client' ORDER BY id")]
    proposal=client_cleanup_plan(rows)
    changes=sorted([('outside',r['id']) for r in proposal['outside']]+
                   [('duplicate',r['old']['id'],r['keep']['id']) for r in proposal['duplicates']])
    digest=hashlib.sha256(json.dumps(changes,ensure_ascii=False).encode()).hexdigest()
    return proposal,digest,len(rows)

class CleanupConfirmation(BaseModel):
    previewHash: str = Field(min_length=64,max_length=64)

@app.get('/api/admin/clients/cleanup')
def preview_client_cleanup(authorization: str | None = Header(default=None)):
    if auth(authorization)!='Ana Paula': raise HTTPException(403,'Acesso restrito à administradora')
    with db() as con:
        proposal,digest,total=cleanup_snapshot(con)
    return {'total':total,'outside':len(proposal['outside']),'duplicates':len(proposal['duplicates']),
            'remaining':total-len(proposal['outside'])-len(proposal['duplicates']),
            'previewHash':digest,'duplicateExamples':[
                {'name':item['old']['payload'].get('name'),'id':item['old']['id'],'keepId':item['keep']['id'],'reason':item['reason']}
                for item in proposal['duplicates'][:20]]}

@app.post('/api/admin/clients/cleanup')
def apply_client_cleanup(data: CleanupConfirmation, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    if user!='Ana Paula': raise HTTPException(403,'Acesso restrito à administradora')
    with db() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)',(12422026,))
        proposal,digest,total=cleanup_snapshot(con)
        if data.previewHash!=digest: raise HTTPException(409,'A carteira mudou; consulte novamente a prévia')
        for item in proposal['duplicates']:
            old,keep=item['old'],item['keep']
            merged=dict(keep['payload'])
            for key,value in old['payload'].items():
                if key not in ('id','name','state','taxId') and not merged.get(key) and value:
                    merged[key]=value
            if merged!=keep['payload']:
                con.execute("UPDATE entities SET payload=%s,updated_at=now() WHERE kind='client' AND id=%s",(Jsonb(merged),keep['id']))
                keep['payload']=merged
            con.execute("UPDATE entities SET payload=jsonb_set(payload,'{clientId}',to_jsonb(%s::text)),updated_at=now() WHERE kind IN ('visit','order','task','route') AND payload->>'clientId'=%s",(keep['id'],old['id']))
            con.execute("INSERT INTO archived_entities(kind,id,payload,reason) VALUES('client',%s,%s,%s) ON CONFLICT DO NOTHING",(old['id'],Jsonb(old['payload']),'duplicado: '+keep['id']))
            con.execute("DELETE FROM entities WHERE kind='client' AND id=%s",(old['id'],))
            con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'client',%s,%s)",(user,old['id'],'merge: '+keep['id']))
        for row in proposal['outside']:
            record=row['payload']
            snapshot={'name':record.get('name',''),'city':record.get('city',''),'state':record.get('state','')}
            con.execute("UPDATE entities SET payload=jsonb_set(payload,'{clientSnapshot}',%s::jsonb),updated_at=now() WHERE kind IN ('visit','order','task','route') AND payload->>'clientId'=%s",(json.dumps(snapshot,ensure_ascii=False),row['id']))
            con.execute("INSERT INTO archived_entities(kind,id,payload,reason) VALUES('client',%s,%s,'fora de PA/AP') ON CONFLICT DO NOTHING",(row['id'],Jsonb(record)))
            con.execute("DELETE FROM entities WHERE kind='client' AND id=%s",(row['id'],))
            con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'client',%s,'archive: outside PA/AP')",(user,row['id']))
    return {'archivedOutside':len(proposal['outside']),'mergedDuplicates':len(proposal['duplicates']),
            'remaining':total-len(proposal['outside'])-len(proposal['duplicates'])}

@app.get('/health')
def health():
    with db() as con:
        users = con.execute('SELECT count(*) FROM app_users WHERE active').fetchone()[0]
        counts = {kind: con.execute('SELECT count(*) FROM entities WHERE kind=%s',(kind,)).fetchone()[0]
                  for kind in ('client','visit','order','task','route','goal','price','office_process','office_budget','office_ritual','office_role')}
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
    if filename not in ('app.js','cash.js','sw.js','manifest.json','logo-l2.jpeg','logo-data.js','icon-192.png','icon-512.png','apple-touch-icon.png'):
        raise HTTPException(404)
    if filename in ('icon-192.png','icon-512.png','apple-touch-icon.png'):
        return Response(content=base64.b64decode((BASE/(filename+'.b64')).read_text()), media_type='image/png', headers={'Cache-Control':'public, max-age=86400'})
    return FileResponse(BASE/filename,headers={'Cache-Control':'no-store'})
