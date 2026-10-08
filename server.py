"""L2 ONE: secure FastAPI/PostgreSQL application for Render."""
import logging
import os, json, time, hashlib, secrets, re, base64, gzip, unicodedata, io, zipfile, asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
from pydantic import BaseModel, Field
import psycopg
from psycopg.types.json import Jsonb
from client_cleanup import plan as client_cleanup_plan
from daily_report import build_pdf, read_data, TZ
from speedio_integration import lookup_cnpj, SpeedioError
from cnpj_registry import lookup_registry, RegistryError
from senscience_seed import seed_senscience
from davines_seed import seed_davines
from magic_beauty_seed import seed_magic_beauty
from order_reconciliation import reconcile_invoice, InvoiceError
from lead_capture import LeadIntake, normalize_intake, ingest_lead, lead_sla
from login_identity import resolve_identity
from catalog_document import build_catalog_pdf
from whatsapp_media import send_media, validate_media, provider_config, MediaError, send_text

from commercial_tables import table_id, price_id, validate_table, seed_bella, validate_bella_order
from access_policy import effective_sectors, attribute_order, COMMERCIAL_SECTORS
from seller_commission import validate_rate, apply_seller_commission
from financial_visibility import hide_industry_commissions, preserve_industry_commissions

import zara
import webchat
import strategic_crm
import field_assistant
import geo_prospecting

BASE = Path(__file__).resolve().parent
USERS = ['Ana Paula', 'Euler', 'Laís', 'Marlene']
SECTORS = {'commercial','clients_edit','routes','office','management','finance','catalog','admin'}
DEFAULT_SECTORS = {
 'Ana Paula':sorted(SECTORS),
 'Euler':['commercial','routes','management','finance','catalog'],
 'Laís':['commercial','clients_edit','office','management','finance','catalog','admin'],
 'Marlene':['commercial','clients_edit','routes','office','management','catalog','admin'],
}
CATALOG_EDITORS = {'Ana Paula', 'Laís', 'Marlene'}
INITIAL_PASSWORD = os.getenv('L2_INITIAL_PASSWORD', '')
PASSWORDS = {u: os.getenv(f'L2_PASSWORD_{i}', '') or INITIAL_PASSWORD for i, u in enumerate(USERS, 1)}
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


MARCO_ZERO = date(2026, 9, 28)

def operational_date(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed if parsed.date() >= MARCO_ZERO else None
    except (ValueError, TypeError):
        return None

def project_attendance(con,kind,entity_id,payload,created_at=None,updated_at=None):
    client_id=payload.get('clientId')
    if not client_id or not con.execute('SELECT 1 FROM clientes WHERE id=%s',(client_id,)).fetchone(): return
    user=payload.get('user') if kind=='visit' else payload.get('owner')
    if not con.execute('SELECT 1 FROM app_users WHERE username=%s',(user,)).fetchone(): user=None
    contact='visita' if kind=='visit' else {'Ligação':'ligacao','WhatsApp':'whatsapp','E-mail':'email'}.get(payload.get('type'))
    at=operational_date(payload.get('date') if kind=='visit' else payload.get('at'))
    note=(payload.get('notes') or payload.get('result') or '') if kind=='visit' else payload.get('text','')
    con.execute("""INSERT INTO atendimentos(id,cliente_id,usuario_id,tipo_contato,observacao,data_atendimento,created_at,updated_at)
        VALUES(%s,%s,%s,%s,%s,%s,coalesce(%s,now()),coalesce(%s,now()))
        ON CONFLICT(id) DO UPDATE SET cliente_id=excluded.cliente_id,usuario_id=excluded.usuario_id,
          tipo_contato=excluded.tipo_contato,observacao=excluded.observacao,data_atendimento=excluded.data_atendimento,updated_at=now()""",
        (kind+':'+entity_id,client_id,user,contact,note,at,created_at,updated_at))

def project_order(con,entity_id,payload,created_at=None,updated_at=None):
    client_id=payload.get('clientId')
    if not client_id or not con.execute('SELECT 1 FROM clientes WHERE id=%s',(client_id,)).fetchone(): return
    try:
        value=Decimal(str(payload.get('amount'))) if payload.get('amount') not in (None,'') else None
        if value is not None and (not value.is_finite() or value<0): value=None
    except (ValueError,TypeError,InvalidOperation): value=None
    con.execute("""INSERT INTO faturamento_pedidos(id,cliente_id,pedido_erp_id,valor_total,data_pedido,status_pedido,created_at,updated_at)
        VALUES(%s,%s,%s,%s,%s,%s,coalesce(%s,now()),coalesce(%s,now()))
        ON CONFLICT(id) DO UPDATE SET cliente_id=excluded.cliente_id,pedido_erp_id=excluded.pedido_erp_id,
          valor_total=excluded.valor_total,data_pedido=excluded.data_pedido,status_pedido=excluded.status_pedido,updated_at=now()""",
        (entity_id,client_id,str(payload.get('orderNumber') or '') or None,value,operational_date(payload.get('date')),str(payload.get('status') or 'Sem status'),created_at,updated_at))

def initialize():
    with db() as con:
        zara.setup(con)
        webchat.setup(con)
        con.execute('CREATE TABLE IF NOT EXISTS entities (kind TEXT NOT NULL, id TEXT NOT NULL, payload JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(kind,id))')
        con.execute('ALTER TABLE entities ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ')
        con.execute('ALTER TABLE entities ALTER COLUMN created_at SET DEFAULT now()')
        con.execute('CREATE TABLE IF NOT EXISTS commercial_drafts (username TEXT NOT NULL, draft_key TEXT NOT NULL, payload JSONB, client_timestamp BIGINT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(username,draft_key))')
        con.execute('CREATE TABLE IF NOT EXISTS cnpj_registry_cache (cnpj TEXT PRIMARY KEY, payload JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE TABLE IF NOT EXISTS applied_changes (change_id TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE TABLE IF NOT EXISTS audit_log (id BIGSERIAL PRIMARY KEY, username TEXT NOT NULL, kind TEXT NOT NULL, entity_id TEXT NOT NULL, action TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        strategic_crm.setup(con)
        field_assistant.setup(con)
        seed_bella(con, BASE)
        seed_senscience(con, BASE)
        seed_davines(con, BASE)
        seed_magic_beauty(con, BASE)
        # Cadastro inicial idempotente; preserva alterações feitas pela equipe no sistema.
        bth = {'id':'Brotherhood BTH','name':'Brotherhood BTH','active':True,
               'contact':'','notes':'Tabela BTH Varejo - Brotherhood 2026. Mesmos preços para PA e AP.',
               'commercialPolicy':[
                   {'minimum':'2000.00','paymentTerms':'30/45/60','freight':'CIF'},
                   {'minimum':'3000.00','paymentTerms':'30/60/90','freight':'CIF'}]}
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('industry',%s,%s) ON CONFLICT(kind,id) DO NOTHING",
                    (bth['id'], Jsonb(bth)))
        bth_catalog=json.loads((BASE/'bth_catalog_2026.json').read_text(encoding='utf-8'))
        if len(bth_catalog)!=53 or len({row['sku'] for row in bth_catalog})!=53:
            raise RuntimeError('Catálogo BTH incompleto ou com SKU duplicado')
        for uf in ('PA','AP'):
            table={'id':f"{bth['name']}|{uf}",'brand':bth['name'],'state':uf,
                   'title':f'Tabela BTH Varejo 2026 - {uf}','active':True}
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('price_table',%s,%s) ON CONFLICT(kind,id) DO NOTHING",
                        (table['id'],Jsonb(table)))
            for row in bth_catalog:
                payload={'brand':bth['name'],'state':uf,**row}
                price_id=f"{bth['name']}|{uf}|{row['sku']}"
                con.execute("INSERT INTO entities(kind,id,payload) VALUES('price',%s,%s) ON CONFLICT(kind,id) DO NOTHING",
                            (price_id,Jsonb(payload)))
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
        con.execute('''CREATE TABLE IF NOT EXISTS order_invoices (
            id TEXT PRIMARY KEY, order_id TEXT NOT NULL, brand TEXT NOT NULL,
            invoice_number TEXT NOT NULL, filename TEXT NOT NULL, content BYTEA NOT NULL,
            reconciliation JSONB NOT NULL, uploaded_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(order_id,brand,invoice_number))''')
        con.execute('CREATE TABLE IF NOT EXISTS visit_photos (id TEXT PRIMARY KEY, visit_id TEXT NOT NULL, filename TEXT NOT NULL, content_type TEXT NOT NULL, content BYTEA NOT NULL, uploaded_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE INDEX IF NOT EXISTS idx_visit_photos_visit ON visit_photos(visit_id)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_order_attachments_order ON order_attachments(order_id)')
        con.execute('CREATE TABLE IF NOT EXISTS app_users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT true, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('ALTER TABLE app_users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false')
        con.execute('ALTER TABLE app_users ADD COLUMN IF NOT EXISTS sectors JSONB')
        con.execute('ALTER TABLE app_users ADD COLUMN IF NOT EXISTS role TEXT')
        con.execute('ALTER TABLE app_users ADD COLUMN IF NOT EXISTS department TEXT')
        con.execute('''CREATE TABLE IF NOT EXISTS seller_profiles (
            username TEXT PRIMARY KEY REFERENCES app_users(username) ON DELETE RESTRICT,
            full_name TEXT NOT NULL, document TEXT NOT NULL, email TEXT NOT NULL,
            phone TEXT NOT NULL, bank TEXT NOT NULL, account_type TEXT NOT NULL,
            branch TEXT NOT NULL, account_number TEXT NOT NULL, pix_key TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('ALTER TABLE app_users ADD COLUMN IF NOT EXISTS seller_commission_rate NUMERIC(5,2) CHECK (seller_commission_rate BETWEEN 0 AND 100)')
        con.execute("UPDATE app_users SET department=CASE username WHEN 'Ana Paula' THEN 'Direção comercial' WHEN 'Euler' THEN 'Comercial' WHEN 'Laís' THEN 'Suporte administrativo' WHEN 'Marlene' THEN 'Operações administrativas' ELSE coalesce(role,'Equipe') END WHERE department IS NULL")
        con.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES app_users(username), expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        con.execute('CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at)')
        con.execute('''CREATE TABLE IF NOT EXISTS capture_accounts (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, tax_id TEXT UNIQUE, state TEXT, city TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('''CREATE TABLE IF NOT EXISTS capture_contacts (
            id TEXT PRIMARY KEY, account_id TEXT REFERENCES capture_accounts(id), name TEXT NOT NULL,
            email_norm TEXT, phone_norm TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('''CREATE TABLE IF NOT EXISTS capture_leads (
            id TEXT PRIMARY KEY, account_id TEXT REFERENCES capture_accounts(id),
            contact_id TEXT NOT NULL REFERENCES capture_contacts(id), owner TEXT REFERENCES app_users(username),
            status TEXT NOT NULL, origin TEXT NOT NULL, campaign TEXT, landing_page TEXT,
            utm_source TEXT, utm_medium TEXT, utm_campaign TEXT, utm_content TEXT, utm_term TEXT,
            custom_fields JSONB NOT NULL DEFAULT '{}'::jsonb, email_norm TEXT, phone_norm TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            assigned_at TIMESTAMPTZ, first_response_at TIMESTAMPTZ)''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_capture_leads_email ON capture_leads(email_norm) WHERE email_norm IS NOT NULL')
        con.execute('CREATE INDEX IF NOT EXISTS idx_capture_leads_phone ON capture_leads(phone_norm) WHERE phone_norm IS NOT NULL')
        con.execute('CREATE INDEX IF NOT EXISTS idx_capture_leads_owner ON capture_leads(owner,created_at DESC)')
        con.execute('''CREATE TABLE IF NOT EXISTS capture_lead_events (
            id BIGSERIAL PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES capture_leads(id),
            event_type TEXT NOT NULL, actor TEXT, details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('CREATE INDEX IF NOT EXISTS idx_capture_events_lead ON capture_lead_events(lead_id,created_at)')
        con.execute('''CREATE TABLE IF NOT EXISTS capture_routing_state (
            id SMALLINT PRIMARY KEY CHECK(id=1), last_username TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('INSERT INTO capture_routing_state(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
        con.execute("""CREATE TABLE IF NOT EXISTS client_seller_authorizations (
            client_id TEXT NOT NULL, seller_username TEXT NOT NULL REFERENCES app_users(username),
            approved_by TEXT NOT NULL REFERENCES app_users(username), active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY(client_id,seller_username))""")
        con.execute('CREATE INDEX IF NOT EXISTS idx_client_seller_active ON client_seller_authorizations(seller_username,client_id) WHERE active')
        for user, password in PASSWORDS.items():
            exists = con.execute('SELECT 1 FROM app_users WHERE username=%s',(user,)).fetchone()
            if not exists:
                con.execute('INSERT INTO app_users(username,password_hash,must_change_password,sectors) VALUES(%s,%s,true,%s)',(user,password_hash(password),Jsonb(DEFAULT_SECTORS[user])))
            else:
                con.execute('UPDATE app_users SET sectors=%s WHERE username=%s AND sectors IS NULL',(Jsonb(DEFAULT_SECTORS[user]),user))
            con.execute('UPDATE app_users SET role=%s WHERE username=%s AND role IS NULL',('Administradora' if user=='Ana Paula' else 'Vendedor' if user=='Euler' else 'Administrativo',user))
        # Marlene: carteira, campo, operações, escritório e gestão; financeiro permanece separado.
        con.execute('UPDATE app_users SET sectors=%s,updated_at=now() WHERE username=%s AND sectors IS DISTINCT FROM %s',
                    (Jsonb(DEFAULT_SECTORS['Marlene']),'Marlene',Jsonb(DEFAULT_SECTORS['Marlene'])))
        # Política de acesso para contas existentes, inclusive sessões antigas.
        for name, role, sectors in con.execute('SELECT username,role,sectors FROM app_users').fetchall():
            updated_role = 'Administrativo' if name == 'Laís' else role
            allowed = sorted(effective_sectors(name, updated_role, set(sectors or []) | ({'admin'} if name=='Laís' else set())))
            if updated_role != role or set(allowed) != set(sectors or []):
                con.execute('UPDATE app_users SET role=%s,sectors=%s,updated_at=now() WHERE username=%s', (updated_role, Jsonb(allowed), name))
                con.execute('DELETE FROM sessions WHERE username=%s', (name,))
                con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES('Ana Paula','app_user',%s,'commercial_access_policy')", (name,))
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
        # Catálogos legados recebem indústria e tabela PA/AP sem alterar preços.
        for (brand,) in con.execute("SELECT DISTINCT payload->>'brand' FROM entities WHERE kind='price' AND payload->>'brand' IS NOT NULL"):
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('industry',%s,%s) ON CONFLICT(kind,id) DO NOTHING",(brand,Jsonb({'id':brand,'name':brand,'active':True})))
        for brand,state in con.execute("SELECT DISTINCT payload->>'brand',payload->>'state' FROM entities WHERE kind='price' AND payload->>'state' IN ('PA','AP') AND nullif(payload->>'tableId','') IS NULL"):
            key=f'{brand}|{state}'
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('price_table',%s,%s) ON CONFLICT(kind,id) DO NOTHING",(key,Jsonb({'id':key,'brand':brand,'state':state,'title':f'{brand} {state}','active':True})))
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

        # Relational projection of the existing sync entities; IDs remain stable across exports.
        con.execute("""CREATE TABLE IF NOT EXISTS clientes (
            id TEXT PRIMARY KEY, razao_social VARCHAR(180) NOT NULL,
            nome_fantasia VARCHAR(180), documento VARCHAR(20) UNIQUE,
            curva_abc CHAR(1) CHECK(curva_abc IN ('A','B','C')),
            status VARCHAR(12) NOT NULL DEFAULT 'ativo' CHECK(status IN ('ativo','inativo','em_risco')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        con.execute("""CREATE TABLE IF NOT EXISTS atendimentos (
            id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL REFERENCES clientes(id) ON DELETE RESTRICT,
            usuario_id TEXT REFERENCES app_users(username) ON DELETE SET NULL,
            tipo_contato VARCHAR(12) CHECK(tipo_contato IN ('ligacao','whatsapp','visita','email')),
            observacao TEXT, data_atendimento TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        con.execute("""CREATE TABLE IF NOT EXISTS faturamento_pedidos (
            id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL REFERENCES clientes(id) ON DELETE RESTRICT,
            pedido_erp_id VARCHAR(128), valor_total NUMERIC(15,2),
            data_pedido TIMESTAMPTZ, status_pedido VARCHAR(50) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        con.execute("""CREATE TABLE IF NOT EXISTS documentos_cliente (
            id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clientes(id) ON DELETE RESTRICT,
            category TEXT NOT NULL CHECK(category IN ('nfe','finance','contract','note')),
            filename TEXT, content_type TEXT, content BYTEA, note TEXT,
            numero_documento VARCHAR(80), caminho_arquivo TEXT, data_emissao DATE,
            uploaded_by TEXT NOT NULL REFERENCES app_users(username) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        con.execute('CREATE INDEX IF NOT EXISTS idx_client_docs_client ON documentos_cliente(client_id,category,created_at DESC)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_atendimentos_client_date ON atendimentos(cliente_id,data_atendimento DESC)')
        con.execute('CREATE INDEX IF NOT EXISTS idx_faturamento_client_date ON faturamento_pedidos(cliente_id,data_pedido DESC)')
        # Idempotent projection; legacy missing dates remain NULL (no backfilled business history).
        con.execute("""INSERT INTO clientes(id,razao_social,nome_fantasia,documento,curva_abc,created_at,updated_at)
            SELECT id,coalesce(nullif(payload->>'name',''),id),nullif(payload->>'tradeName',''),
                   nullif(payload->>'taxId',''),CASE WHEN payload->>'abc' IN ('A','B','C') THEN payload->>'abc' ELSE NULL END,
                   coalesce(created_at,now()),updated_at FROM entities WHERE kind='client'
            ON CONFLICT(id) DO UPDATE SET razao_social=excluded.razao_social,nome_fantasia=excluded.nome_fantasia,
              documento=excluded.documento,curva_abc=excluded.curva_abc,updated_at=excluded.updated_at""")
        for kind in ('visit','interaction'):
            for entity_id,payload,created_at,updated_at in con.execute('SELECT id,payload,created_at,updated_at FROM entities WHERE kind=%s',(kind,)).fetchall():
                project_attendance(con,kind,entity_id,payload,created_at,updated_at)
        for entity_id,payload,created_at,updated_at in con.execute("SELECT id,payload,created_at,updated_at FROM entities WHERE kind='order'").fetchall():
            project_order(con,entity_id,payload,created_at,updated_at)

        # Nunca excluir clientes automaticamente por divergência de UF.
        # O cadastro permanece disponível para correção; pedidos exigem PA/AP.

def restore_bt_pa_table(con):
    # AP replacement must not disable the independent PA catalog.
    if not con.execute("INSERT INTO catalog_seeds(name) VALUES('restore-bt-pa-after-ap-replacement-20261007') ON CONFLICT DO NOTHING RETURNING name").fetchone():
        return
    restored = con.execute("""UPDATE entities SET payload=jsonb_set(payload,'{active}','true'::jsonb),updated_at=now()
        WHERE kind='price_table' AND id='Bruna Tavares|PA'
        AND payload->>'brand'='Bruna Tavares' AND payload->>'state'='PA'
        AND payload->>'active'='false'
        AND EXISTS (SELECT 1 FROM entities p WHERE p.kind='price' AND p.payload->>'brand'='Bruna Tavares' AND p.payload->>'state'='PA')
        RETURNING id""").fetchone()
    if restored:
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES('Ana Paula','price_table',%s,'restore_pa_after_ap_replacement')",(restored[0],))
        con.execute("SELECT pg_notify('l2_records_changed','')")

@asynccontextmanager
async def lifespan(app):
    initialize()
    with db() as con:
        restore_bt_pa_table(con)
    initialize_personal(db)
    from homologation_access import recover
    recover(db, password_hash)
    zara.log_configuration()
    zara.verify_database_schema()
    correct_order_18_date()
    with db() as con:
        provisional = con.execute('SELECT count(*) FROM app_users WHERE active AND must_change_password').fetchone()[0]
    logging.getLogger('uvicorn.error').info('homologation_checks provisional_accounts=%s whatsapp_documents_configured=%s', provisional, bool(provider_config()))
    strategic_crm.configure(db=db, auth=auth, require_sector=require_sector, valid_cnpj=valid_cnpj, check_client_scope=check_client_scope, scoped_rows=scoped_rows, project_attendance=project_attendance, is_seller=is_seller)
    strategic_worker = asyncio.create_task(strategic_crm.worker())
    try:
        yield
    finally:
        strategic_worker.cancel()
        try: await strategic_worker
        except asyncio.CancelledError: pass

app = FastAPI(title='L2 ONE API', lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(zara.router)
app.include_router(webchat.router)
app.include_router(strategic_crm.router)
app.include_router(field_assistant.router)
app.include_router(geo_prospecting.router)

def sectors_for(user):
    with db() as con:
        row=con.execute('SELECT role,sectors FROM app_users WHERE username=%s AND active',(user,)).fetchone()
    return effective_sectors(user,row[0],row[1]) if row else set()

def admin_access(user):
    return user=='Ana Paula' or 'admin' in sectors_for(user)

def valid_client_responsible(con, username):
    member=con.execute('SELECT role,sectors FROM app_users WHERE username=%s AND active',(username,)).fetchone()
    return bool(member and (member[0]=='Vendedor' or 'admin' in (member[1] or [])))

def require_sector(user, *allowed):
    if not (set(allowed) & sectors_for(user)):
        raise HTTPException(403,'Setor sem permissão para pedidos')

def is_seller(con, user):
    row=con.execute('SELECT role FROM app_users WHERE username=%s AND active',(user,)).fetchone()
    return bool(row and row[0]=='Vendedor' and user!='Euler')

def check_client_scope(con, user, client_id):
    """A carteira de um vendedor é definida no servidor, inclusive para anexos."""
    if not is_seller(con,user): return
    row=con.execute("SELECT payload->>'owner' FROM entities WHERE kind='client' AND id=%s",(client_id,)).fetchone()
    authorized=con.execute('SELECT 1 FROM client_seller_authorizations WHERE client_id=%s AND seller_username=%s AND active',(client_id,user)).fetchone()
    if not row or (row[0]!=user and not authorized): raise HTTPException(403,'Cliente fora da sua carteira autorizada')

def scoped_rows(con, kind, user):
    if not is_seller(con,user):
        return [row[0] for row in con.execute('SELECT payload FROM entities WHERE kind=%s ORDER BY updated_at,id',(kind,))]
    allowed={row[0] for row in con.execute('SELECT client_id FROM client_seller_authorizations WHERE seller_username=%s AND active',(user,))}
    if kind=='client':
        return [row[1] for row in con.execute("SELECT id,payload FROM entities WHERE kind='client' ORDER BY updated_at,id") if row[0] in allowed or row[1].get('owner')==user]
    if kind in ('visit','order','task','opportunity','interaction','fulfillment','lead','settlement'):
        client_ids={row[0] for row in con.execute("SELECT id FROM entities WHERE kind='client' AND payload->>'owner'=%s",(user,))}|allowed
        return [row[0] for row in con.execute('SELECT payload FROM entities WHERE kind=%s ORDER BY updated_at,id',(kind,)) if row[0].get('clientId') in client_ids or (kind=='task' and not row[0].get('clientId') and row[0].get('user')==user)]
    if kind=='route':
        client_ids={row[0] for row in con.execute("SELECT id FROM entities WHERE kind='client' AND payload->>'owner'=%s",(user,))}|allowed
        return [row[0] for row in con.execute("SELECT payload FROM entities WHERE kind='route' ORDER BY updated_at,id") if row[0].get('clientId') in client_ids and row[0].get('user')==user]
    if kind=='goal':
        return [row[0] for row in con.execute("SELECT payload FROM entities WHERE kind='goal' AND payload->>'user'=%s ORDER BY updated_at,id",(user,))]
    if kind=='office_finance':
        client_ids={row[0] for row in con.execute("SELECT id FROM entities WHERE kind='client' AND payload->>'owner'=%s",(user,))}|allowed
        return [row[0] for row in con.execute("SELECT payload FROM entities WHERE kind='office_finance' ORDER BY updated_at,id") if row[0].get('clientId') in client_ids]
    return [row[0] for row in con.execute('SELECT payload FROM entities WHERE kind=%s ORDER BY updated_at,id',(kind,))]

class PortfolioAuthorization(BaseModel):
    approved: bool

@app.get('/api/portfolio/erika/pa')
def erika_pa_portfolio(authorization: str | None = Header(default=None)):
    user=auth(authorization)
    if user not in ('Erika','Marlene','Ana Paula'): raise HTTPException(403,'Carteira restrita')
    with db() as con:
        approved={row[0] for row in con.execute("SELECT client_id FROM client_seller_authorizations WHERE seller_username='Erika' AND active")}
        rows=con.execute("SELECT id,payload FROM entities WHERE kind='client' ORDER BY payload->>'city',payload->>'district',payload->>'name'")
        clients=[{'id':client_id,'name':p.get('name',''),'city':p.get('city',''),'district':p.get('district',''),
                  'approved':client_id in approved} for client_id,p in rows if normalize_uf(p.get('state'))=='PA']
    return {'total':len(clients),'approved':sum(c['approved'] for c in clients),'clients':clients}

@app.put('/api/portfolio/erika/pa/{client_id}')
def authorize_erika_pa_client(client_id: str, data: PortfolioAuthorization, authorization: str | None = Header(default=None)):
    actor=auth(authorization)
    if actor!='Marlene': raise HTTPException(403,'A autorização da carteira da Erika cabe à Marlene')
    with db() as con:
        seller=con.execute("SELECT 1 FROM app_users WHERE username='Erika' AND active AND role='Vendedor'").fetchone()
        if not seller: raise HTTPException(409,'Conta de Erika não está ativa como vendedora')
        row=con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s",(client_id,)).fetchone()
        if not row or normalize_uf(row[0].get('state'))!='PA': raise HTTPException(404,'Cliente do Pará não encontrado')
        con.execute('''INSERT INTO client_seller_authorizations(client_id,seller_username,approved_by,active)
          VALUES(%s,'Erika',%s,%s) ON CONFLICT(client_id,seller_username)
          DO UPDATE SET active=excluded.active,approved_by=excluded.approved_by,updated_at=now()''',
          (client_id,actor,data.approved))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'client_authorization',%s,%s)",
                    (actor,client_id,'approve_erika' if data.approved else 'revoke_erika'))
    return {'id':client_id,'approved':data.approved,'seller':'Erika','approvedBy':actor}

@app.post('/api/leads/webhook', status_code=201)
def capture_lead_webhook(data: LeadIntake, x_l2_webhook_key: str | None = Header(default=None)):
    configured=os.getenv('L2_LEAD_WEBHOOK_TOKEN','')
    if not configured:
        raise HTTPException(503,'Captação externa ainda não configurada')
    if not x_l2_webhook_key or not secrets.compare_digest(x_l2_webhook_key,configured):
        raise HTTPException(401,'Credencial de captação inválida')
    item=normalize_intake(data,valid_cnpj)
    with db() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
        result=ingest_lead(con,item)
        result=strategic_crm.intake_tdc(con,item,result)
    return result

@app.post('/api/leads/intake', status_code=201)
def capture_lead_internal(data: LeadIntake, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'commercial','office')
    item=normalize_intake(data,valid_cnpj)
    with db() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
        result=ingest_lead(con,item,source='backoffice:'+user)
        return strategic_crm.intake_tdc(con,item,result)

@app.get('/api/leads/inbox')
def capture_lead_inbox(authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'commercial','office')
    with db() as con:
        can_view_all=not is_seller(con,user)
        rows=con.execute('''SELECT l.id,c.name,c.email_norm,c.phone_norm,a.name,l.origin,l.campaign,
             l.utm_source,l.utm_medium,l.utm_campaign,l.utm_content,l.utm_term,l.landing_page,
             l.custom_fields,l.status,l.owner,l.created_at,l.assigned_at,l.first_response_at
             FROM capture_leads l JOIN capture_contacts c ON c.id=l.contact_id
             LEFT JOIN capture_accounts a ON a.id=l.account_id
             WHERE (%s OR l.owner=%s) ORDER BY l.created_at DESC LIMIT 200''',(can_view_all,user)).fetchall()
        ids=[r[0] for r in rows]
        history=con.execute('''SELECT lead_id,event_type,actor,details,created_at FROM capture_lead_events
             WHERE lead_id=ANY(%s) ORDER BY created_at,id''',(ids,)).fetchall() if ids else []
    events={lead_id:[] for lead_id in ids}
    for lead_id,kind,actor,details,at in history:
        events[lead_id].append({'type':kind,'actor':actor,'details':details,'at':at.isoformat()})
    sla_minutes=max(1,min(int(os.getenv('L2_LEAD_SLA_MINUTES','120')),10080))
    return [{'id':r[0],'name':r[1],'email':r[2],'phone':r[3],'company':r[4],
             'origin':r[5],'campaign':r[6],'utms':{'source':r[7],'medium':r[8],
             'campaign':r[9],'content':r[10],'term':r[11]},'landingPage':r[12],
             'customFields':r[13],'status':r[14],'owner':r[15],
             'createdAt':r[16].isoformat(),'assignedAt':r[17].isoformat() if r[17] else None,
             'firstResponseAt':r[18].isoformat() if r[18] else None,
             'sla':lead_sla(r[16],r[18],minutes=sla_minutes),'history':events[r[0]]} for r in rows]

@app.post('/api/leads/{lead_id}/contacted')
def capture_lead_contacted(lead_id: str, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'commercial','office')
    with db() as con:
        row=con.execute('SELECT owner,first_response_at FROM capture_leads WHERE id=%s FOR UPDATE',(lead_id,)).fetchone()
        if not row:raise HTTPException(404,'Lead não encontrado')
        if is_seller(con,user) and row[0]!=user:raise HTTPException(403,'Lead fora da sua fila')
        if row[1]:return {'id':lead_id,'alreadyContacted':True}
        con.execute("UPDATE capture_leads SET first_response_at=now(),status='Em Atendimento',updated_at=now() WHERE id=%s",(lead_id,))
        con.execute('INSERT INTO capture_lead_events(lead_id,event_type,actor) VALUES(%s,%s,%s)',(lead_id,'first_contact',user))
    return {'id':lead_id,'alreadyContacted':False}

class SpeedioQuery(BaseModel):
    cnpj: str = Field(min_length=14, max_length=18)

@app.post('/api/clients/lookup-cnpj')
def client_registry_lookup(query: SpeedioQuery, authorization: str | None = Header(default=None)):
    user = auth(authorization)
    require_sector(user,'commercial','office')
    cnpj = re.sub(r'\D','',query.cnpj)
    if not valid_cnpj(cnpj):raise HTTPException(400,'Informe um CNPJ válido')
    with db() as con:
        existing = con.execute("SELECT id FROM entities WHERE kind='client' AND regexp_replace(coalesce(payload->>'taxId',''),'[^0-9]','','g')=%s LIMIT 1",(cnpj,)).fetchone()
        if existing:
            visible = next((x for x in scoped_rows(con,'client',user) if x.get('id')==existing[0]),None)
            if not visible:raise HTTPException(409,'CNPJ já cadastrado. Solicite acesso à carteira para localizar o cliente.')
            return {'existingClientId':existing[0],'client':{key:visible.get(key,'') for key in ('name','tradeName','taxId','stateRegistration','state','city','district','address','phone','email','taxRegime')},'source':'Cadastro L2','missing':[]}
        cached = con.execute("SELECT payload FROM cnpj_registry_cache WHERE cnpj=%s AND updated_at>now()-interval '24 hours'",(cnpj,)).fetchone()
        if cached:return cached[0]
    try:result=lookup_registry(cnpj)
    except RegistryError as exc:raise HTTPException(exc.status,str(exc)) from exc
    with db() as con:
        con.execute('INSERT INTO cnpj_registry_cache(cnpj,payload) VALUES(%s,%s) ON CONFLICT(cnpj) DO UPDATE SET payload=excluded.payload,updated_at=now()',(cnpj,Jsonb(result)))
    return result

@app.post('/api/integrations/speedio/lookup')
def speedio_lookup(query: SpeedioQuery, authorization: str | None = Header(default=None)):
    user = auth(authorization)
    require_sector(user, 'commercial', 'office')
    cnpj = re.sub(r'\D', '', query.cnpj)
    if not valid_cnpj(cnpj):
        raise HTTPException(400, 'Informe um CNPJ válido')
    try:
        lead = lookup_cnpj(cnpj, os.getenv('SPEEDIO_USERNAME', ''), os.getenv('SPEEDIO_PASSWORD', ''))
    except SpeedioError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    with db() as con:
        existing = con.execute("SELECT 1 FROM clientes WHERE regexp_replace(coalesce(documento,''),'[^0-9]','','g')=%s LIMIT 1", (cnpj,)).fetchone()
        staged = con.execute("SELECT 1 FROM entities WHERE kind='lead' AND id=%s", ('speedio:' + cnpj,)).fetchone()
    return {'lead': lead, 'alreadyRegistered': bool(existing), 'alreadyStaged': bool(staged)}

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
        candidates = [r[0] for r in con.execute('SELECT username FROM app_users WHERE active')]
        username = resolve_identity(data.user, candidates)
        row = con.execute('SELECT password_hash, must_change_password, sectors, role FROM app_users WHERE username=%s AND active',(username,)).fetchone()
    valid = bool(row and password_ok(data.password, row[0]))
    # Só recupera o bootstrap de uma conta ainda provisória; nunca substitui senha pessoal.
    configured = PASSWORDS.get(username, '')
    if row and not valid and row[1] and configured and secrets.compare_digest(data.password, configured):
        with db() as con:
            updated = con.execute('UPDATE app_users SET password_hash=%s,updated_at=now() WHERE username=%s AND must_change_password AND password_hash=%s RETURNING username',
                                  (password_hash(data.password), username, row[0])).fetchone()
            if updated:
                con.execute("DELETE FROM sessions WHERE username=%s", (username,))
                con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'app_user',%s,'provisional_bootstrap_reconciled')", (username, username))
                valid = True
    if not valid:
        time.sleep(0.25)
        raise HTTPException(401, 'Usuário ou senha inválidos neste ambiente. Homologação possui contas e senhas próprias.')
    data.user = username
    token = secrets.token_urlsafe(48)
    with db() as con:
        con.execute("DELETE FROM sessions WHERE expires_at<=now()")
        con.execute("INSERT INTO sessions(token_hash,username,expires_at) VALUES(%s,%s,now()+(%s || ' hours')::interval)",(hashlib.sha256(token.encode()).hexdigest(),data.user,SESSION_HOURS))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'session',%s,'login')",(data.user,data.user))
    return {'token': token, 'user': data.user, 'expiresInHours': SESSION_HOURS, 'mustChangePassword': row[1], 'role':row[3], 'sectors':sorted(effective_sectors(data.user,row[3],row[2]))}

@app.post('/api/logout')
def logout(authorization: str | None = Header(default=None)):
    user=auth(authorization, allow_password_change=True)
    token_digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    with db() as con:
        con.execute('DELETE FROM sessions WHERE token_hash=%s',(token_digest,))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'session',%s,'logout')",(user,user))
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
    if not admin_access(auth(authorization)):
        raise HTTPException(403, 'Acesso restrito à administradora')
    with db() as con:
        rows = con.execute('SELECT username,active,must_change_password,sectors,role,department FROM app_users ORDER BY username').fetchall()
    with db() as con:
        rates={name:rate for name,rate in con.execute('SELECT username,seller_commission_rate FROM app_users')}
    return [{'commissionRate':str(rates[name]) if rates.get(name) is not None else None,'user': name, 'active': active, 'mustChangePassword': first_access,'sectors':sectors or [], 'role':role or '', 'department':department or ''} for name,active,first_access,sectors,role,department in rows]

@app.post('/api/admin/team-access/reset')
def reset_team_access(data: TeamAccessReset, authorization: str | None = Header(default=None)):
    if not admin_access(auth(authorization)):
        raise HTTPException(403, 'Acesso restrito à administradora')
    actor=auth(authorization)
    if data.user == 'Ana Paula' or (actor!='Ana Paula' and data.user=='Marlene'):
        raise HTTPException(400, 'Selecione um integrante da equipe')
    provisional = secrets.token_urlsafe(24)
    with db() as con:
        row = con.execute('SELECT active,sectors FROM app_users WHERE username=%s FOR UPDATE', (data.user,)).fetchone()
        if actor!='Ana Paula' and row and ('finance' in (row[1] or []) or 'admin' in (row[1] or [])): raise HTTPException(403,'Conta protegida')
        if not row or not row[0]:
            raise HTTPException(404, 'Conta não está ativa')
        con.execute('UPDATE app_users SET password_hash=%s,must_change_password=true,updated_at=now() WHERE username=%s', (password_hash(provisional),data.user))
        con.execute('DELETE FROM sessions WHERE username=%s', (data.user,))
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)', (actor,'app_user',data.user,'team_access_reset'))
    return {'user': data.user, 'temporaryPassword': provisional, 'mustChangePassword': True}


class TeamMember(BaseModel):
    user: str = Field(min_length=2,max_length=80)
    role: str
    department: str = Field(default='',max_length=80)
    sectors: list[str] = Field(min_length=1,max_length=8)
    profile: dict | None = None

class TeamMemberUpdate(BaseModel):
    role: str
    department: str = Field(default='',max_length=80)
    sectors: list[str] = Field(min_length=1,max_length=8)
    active: bool = True

class AssignPortfolio(BaseModel):
    seller: str
    state: str
    city: str = ''
    district: str = ''
    onlyUnassigned: bool = True
    dryRun: bool = True

@app.post('/api/admin/clients/assign')
def assign_portfolio(data: AssignPortfolio, authorization: str | None = Header(default=None)):
    actor=auth(authorization)
    if not admin_access(actor): raise HTTPException(403,'Atribuição restrita à administradora')
    uf=normalize_uf(data.state)
    if not uf: raise HTTPException(400,'Selecione PA ou AP para a divisão')
    with db() as con:
        if not con.execute("SELECT 1 FROM app_users WHERE username=%s AND active AND role='Vendedor'",(data.seller,)).fetchone():
            raise HTTPException(400,'Vendedor ativo não encontrado')
        rows=con.execute("SELECT id,payload FROM entities WHERE kind='client' ORDER BY id").fetchall()
        selected=[(cid,p) for cid,p in rows if normalize_uf(p.get('state'))==uf and (not data.city or str(p.get('city','')).strip().casefold()==data.city.strip().casefold())
                  and (not data.district or str(p.get('district','')).strip().casefold()==data.district.strip().casefold())
                  and (not data.onlyUnassigned or not p.get('owner'))]
        if not data.dryRun:
            for cid,p in selected:
                edited=dict(p,owner=data.seller,updatedBy=actor)
                con.execute("UPDATE entities SET payload=%s,updated_at=now() WHERE kind='client' AND id=%s",(Jsonb(edited),cid))
                con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'client',%s,'assign')",(actor,cid))
    return {'count':len(selected),'sample':[p.get('name','') for _,p in selected[:10]],'applied':not data.dryRun}

def validate_member(user, role, sectors):
    name = unicodedata.normalize('NFC', user).strip()
    if not re.fullmatch(r"[\wÀ-ÿ .'-]{2,80}",name) or role not in ('Vendedor','Administrativo','Gestão') or set(sectors)-SECTORS or len(set(sectors))!=len(sectors):
        raise HTTPException(400,'Nome, função ou setores inválidos')
    if role=='Vendedor' and 'commercial' not in sectors:
        raise HTTPException(400,'Vendedor precisa de acesso Comercial')
    if name=='Laís' and (role!='Administrativo' or 'admin' not in sectors):
        raise HTTPException(400,'Laís deve atuar como Administrativo com acesso Adm')
    if role=='Vendedor' and name!='Euler' and set(sectors)-COMMERCIAL_SECTORS:
        raise HTTPException(400,'Equipe comercial pode acessar somente Comercial, Carteira e Rotas')
    if name in ('Marlene','Erika') and 'finance' in sectors:
        raise HTTPException(400,'Marlene e Erika não têm acesso financeiro')
    return name

def validate_seller_profile(profile):
    required=('fullName','document','email','phone','bank','accountType','branch','accountNumber','pixKey')
    if not isinstance(profile,dict) or any(not str(profile.get(k,'')).strip() or len(str(profile[k]))>180 for k in required):
        missing=[{'fullName':'nome completo','document':'CPF/CNPJ','email':'e-mail','phone':'telefone','bank':'banco','accountType':'tipo de conta','branch':'agência','accountNumber':'conta','pixKey':'chave PIX'}[k] for k in required if not isinstance(profile,dict) or not str(profile.get(k,'')).strip()]
        raise HTTPException(400,'Complete o cadastro do vendedor: '+', '.join(missing or ['campos com até 180 caracteres']))
    if profile['accountType'] not in ('Corrente','Poupança','Pagamento') or '@' not in profile['email']:
        raise HTTPException(400,'E-mail ou tipo de conta inválido')
    validate_rate(profile.get('commissionRate'))
    return tuple(str(profile[k]).strip() for k in required)

@app.post('/api/admin/team-access')
def create_team_member(data: TeamMember, authorization: str | None = Header(default=None)):
    if not admin_access(auth(authorization)):
        raise HTTPException(403,'Acesso restrito à administradora')
    actor=auth(authorization)
    if actor!='Ana Paula' and ('finance' in data.sectors or 'admin' in data.sectors): raise HTTPException(403,'Setor protegido')
    name=validate_member(data.user,data.role,data.sectors)
    profile=validate_seller_profile(data.profile) if data.role=='Vendedor' else None
    provisional=secrets.token_urlsafe(24)
    with db() as con:
        if con.execute('SELECT 1 FROM app_users WHERE lower(username)=lower(%s)',(name,)).fetchone():
            raise HTTPException(409,'Usuário já cadastrado')
        con.execute('INSERT INTO app_users(username,password_hash,must_change_password,sectors,role,department) VALUES(%s,%s,true,%s,%s,%s)',(name,password_hash(provisional),Jsonb(data.sectors),data.role,data.department.strip()))
        if profile:
            con.execute('UPDATE app_users SET seller_commission_rate=%s WHERE username=%s',(validate_rate(data.profile.get('commissionRate')),name))
            con.execute('INSERT INTO seller_profiles(username,full_name,document,email,phone,bank,account_type,branch,account_number,pix_key) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(name,*profile))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'app_user',%s,'create')",(actor,name))
        con.execute("SELECT pg_notify('l2_records_changed', '')")
    return {'user':name,'temporaryPassword':provisional,'mustChangePassword':True}

@app.get('/api/admin/sellers/{member}/profile')
def seller_profile(member: str, response: Response, authorization: str | None = Header(default=None)):
    if not admin_access(auth(authorization)): raise HTTPException(403,'Dados bancários restritos à administração')
    response.headers['Cache-Control']='no-store'
    with db() as con:
        row=con.execute('SELECT full_name,document,email,phone,bank,account_type,branch,account_number,pix_key FROM seller_profiles WHERE username=%s',(member,)).fetchone()
    if not row: raise HTTPException(404,'Cadastro bancário ainda não preenchido')
    with db() as con:
        rate=con.execute('SELECT seller_commission_rate FROM app_users WHERE username=%s',(member,)).fetchone()
    return {**dict(zip(('fullName','document','email','phone','bank','accountType','branch','accountNumber','pixKey'),row)), 'commissionRate':str(rate[0]) if rate and rate[0] is not None else None}

@app.put('/api/admin/sellers/{member}/profile')
def update_seller_profile(member: str, profile: dict, authorization: str | None = Header(default=None)):
    if not admin_access(auth(authorization)): raise HTTPException(403,'Dados bancários restritos à administração')
    values=validate_seller_profile(profile)
    with db() as con:
        if not con.execute("SELECT 1 FROM app_users WHERE username=%s AND role='Vendedor'",(member,)).fetchone():
            raise HTTPException(404,'Vendedor não encontrado')
        con.execute('UPDATE app_users SET seller_commission_rate=%s,updated_at=now() WHERE username=%s',(validate_rate(profile.get('commissionRate')),member))
        con.execute('''INSERT INTO seller_profiles(username,full_name,document,email,phone,bank,account_type,branch,account_number,pix_key)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(username) DO UPDATE SET
            full_name=excluded.full_name,document=excluded.document,email=excluded.email,phone=excluded.phone,
            bank=excluded.bank,account_type=excluded.account_type,branch=excluded.branch,
            account_number=excluded.account_number,pix_key=excluded.pix_key,updated_at=now()''',(member,*values))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'seller_profile',%s,'update')",(auth(authorization),member))
    return {'ok':True}

@app.put('/api/admin/sellers/{member}/commission')
def update_seller_commission(member: str, profile: dict, authorization: str | None = Header(default=None)):
    actor=auth(authorization)
    if not admin_access(actor): raise HTTPException(403,'Comissão restrita à administração')
    rate=validate_rate(profile.get('commissionRate'))
    with db() as con:
        if member=='Laís' or not con.execute("SELECT 1 FROM app_users WHERE username=%s AND role='Vendedor'",(member,)).fetchone():
            raise HTTPException(404,'Vendedor não encontrado')
        con.execute('UPDATE app_users SET seller_commission_rate=%s,updated_at=now() WHERE username=%s',(rate,member))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'seller_commission',%s,'update')",(actor,member))
    return {'ok':True,'commissionRate':str(rate)}

@app.put('/api/admin/team-access/{member}')
def update_team_member(member: str, data: TeamMemberUpdate, authorization: str | None = Header(default=None)):
    if not admin_access(auth(authorization)):
        raise HTTPException(403,'Acesso restrito à administradora')
    actor=auth(authorization)
    if member=='Ana Paula' or (actor!='Ana Paula' and member=='Marlene'):
        raise HTTPException(400,'A conta administradora não pode ser alterada aqui')
    validate_member(member,data.role,data.sectors)
    if actor!='Ana Paula' and ('finance' in data.sectors or 'admin' in data.sectors): raise HTTPException(403,'Setor protegido')
    with db() as con:
        existing=con.execute('SELECT sectors FROM app_users WHERE username=%s',(member,)).fetchone()
        if not existing:
            raise HTTPException(404,'Usuário não encontrado')
        if actor!='Ana Paula' and ('finance' in (existing[0] or []) or 'admin' in (existing[0] or [])): raise HTTPException(403,'Conta protegida')
        if data.role=='Vendedor' and not con.execute('SELECT 1 FROM seller_profiles WHERE username=%s',(member,)).fetchone():
            raise HTTPException(409,'Preencha os dados bancários antes de ativar a função Vendedor')
        con.execute('UPDATE app_users SET sectors=%s,role=%s,department=%s,active=%s,updated_at=now() WHERE username=%s',(Jsonb(data.sectors),data.role,data.department.strip(),data.active,member))
        con.execute('DELETE FROM sessions WHERE username=%s',(member,))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'app_user',%s,'permissions_update')",(actor,member))
    return {'ok':True,'loginRequired':True}


@app.get('/api/admin/usage')
def admin_usage(authorization: str | None = Header(default=None)):
    if not admin_access(auth(authorization)):
        raise HTTPException(403,'Painel restrito à administradora')
    with db() as con:
        staff=con.execute('SELECT username,coalesce(role,\'\'),active,must_change_password,sectors FROM app_users ORDER BY username').fetchall()
        activity=con.execute("""
            SELECT username,
              count(*) FILTER (WHERE created_at>=now()-interval '7 days' AND action NOT IN ('login','logout')) AS actions_7d,
              count(*) FILTER (WHERE created_at>=now()-interval '30 days' AND action NOT IN ('login','logout')) AS actions_30d,
              count(*) FILTER (WHERE created_at>=now()-interval '30 days' AND kind='visit' AND action='upsert') AS visits_30d,
              count(*) FILTER (WHERE created_at>=now()-interval '30 days' AND kind='order' AND action='upsert') AS orders_30d,
              count(*) FILTER (WHERE created_at>=now()-interval '30 days' AND kind='opportunity' AND action='upsert') AS opportunities_30d,
              count(*) FILTER (WHERE created_at>=now()-interval '30 days' AND kind='interaction' AND action='upsert') AS interactions_30d,
              count(*) FILTER (WHERE created_at>=now()-interval '30 days' AND action='login') AS logins_30d,
              max(created_at) FILTER (WHERE action='login') AS last_login,
              max(created_at) FILTER (WHERE action NOT IN ('login','logout')) AS last_action
            FROM audit_log GROUP BY username
        """).fetchall()
        session_rows=con.execute('SELECT username,count(*) FROM sessions WHERE expires_at>now() GROUP BY username').fetchall()
        task_rows=con.execute("""
            SELECT payload->>'user',count(*),count(*) FILTER (WHERE nullif(payload->>'date','')<current_date::text)
            FROM entities WHERE kind='task' AND coalesce(payload->>'status','')<>'Concluída'
            GROUP BY payload->>'user'
        """).fetchall()
        op_rows=con.execute("""
            SELECT payload->>'owner',count(*),count(*) FILTER (WHERE nullif(payload->>'due','')<current_date::text)
            FROM entities WHERE kind='fulfillment' AND coalesce(payload->>'stage','') NOT IN ('Entregue','Pós-venda concluído')
            GROUP BY payload->>'owner'
        """).fetchall()
        recent=con.execute("SELECT username,kind,action,created_at FROM audit_log WHERE action NOT IN ('login','logout') ORDER BY created_at DESC LIMIT 40").fetchall()
    by_user={r[0]:r for r in activity}
    sessions=dict(session_rows)
    tasks={r[0]:(r[1],r[2]) for r in task_rows}
    operations={r[0]:(r[1],r[2]) for r in op_rows}
    rows=[]
    for name,role,active,first_access,sectors in staff:
        metrics=by_user.get(name)
        rows.append({'user':name,'role':role,'active':active,'firstAccessPending':first_access,'sectors':sectors or [],
            'actions7d':metrics[1] if metrics else 0,'actions30d':metrics[2] if metrics else 0,
            'visits30d':metrics[3] if metrics else 0,'orders30d':metrics[4] if metrics else 0,
            'opportunities30d':metrics[5] if metrics else 0,'interactions30d':metrics[6] if metrics else 0,
            'logins30d':metrics[7] if metrics else 0,
            'lastLogin':metrics[8].isoformat() if metrics and metrics[8] else None,
            'lastAction':metrics[9].isoformat() if metrics and metrics[9] else None,
            'activeSessions':sessions.get(name,0),'openTasks':tasks.get(name,(0,0))[0],
            'overdueTasks':tasks.get(name,(0,0))[1],'openOperations':operations.get(name,(0,0))[0],
            'overdueOperations':operations.get(name,(0,0))[1]})
    return {'users':rows,'recent':[{'user':r[0],'kind':r[1],'action':r[2],'at':r[3].isoformat()} for r in recent],
        'note':'Ações representam eventos auditados, não tempo conectado. Logins anteriores à implantação deste painel não foram registrados.'}

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
    table_uf = normalize_uf(table_state)
    return client_uf is not None and table_uf is not None and (client_uf == table_uf or (client_uf == 'AP' and table_uf == 'PA'))

class PriceRow(BaseModel):
    brand: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    state: str
    price: Decimal = Field(ge=0)
    description: str = ''
    tableId: str = ''

class PriceImport(BaseModel):
    prices: list[PriceRow] = Field(min_length=1, max_length=5000)

@app.post('/api/prices/import')
def import_prices(data: PriceImport, authorization: str | None = Header(default=None)):
    user = auth(authorization)
    if user not in CATALOG_EDITORS or 'catalog' not in sectors_for(user):
        raise HTTPException(403, 'Importação restrita às editoras do catálogo')
    prepared = {}
    for row in data.prices:
        uf = 'ALL' if row.tableId and row.state == 'ALL' else normalize_uf(row.state)
        if not uf:
            raise HTTPException(400, 'Tabela deve identificar PA ou AP em cada produto')
        if not row.price.is_finite() or row.price <= 0 or row.price.as_tuple().exponent < -2 or not 1 <= len(row.description.strip()) <= 250:
            raise HTTPException(400, 'Preço ou descrição inválida')
        key = price_id({"brand":row.brand.strip(),"state":uf,"sku":row.sku.strip(),"tableId":row.tableId})
        if key in prepared:
            raise HTTPException(400, f'SKU duplicado para marca e UF: {key}')
        prepared[key] = {'brand':row.brand.strip(),'sku':row.sku.strip(),'state':uf,
                         'price':str(row.price),'description':row.description,'tableId':row.tableId or f'{row.brand.strip()}|{uf}','id':key}
    with db() as con:
        for key, payload in prepared.items():
            table=validate_table(con,payload,payload['brand'],payload['state'])
            payload['state']=table['state']
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('price',%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()", (key,Jsonb(payload)))
    return {'imported':len(prepared)}

@app.get('/api/prices/{client_id}')
def prices_for_client(client_id: str, authorization: str | None = Header(default=None), price_table: str | None = None):
    user=auth(authorization)
    if not ({'catalog','commercial'} & sectors_for(user)): raise HTTPException(403,'Catálogo sem permissão')
    with db() as con:
        check_client_scope(con,user,client_id)
        row = con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s",(client_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Cliente não encontrado')
        uf = normalize_uf(row[0].get('state'))
        if not uf:
            raise HTTPException(400, 'UF do cliente ausente ou inválida')
        selected=normalize_uf(price_table) if price_table is not None else uf
        if not price_table_matches_client(uf,selected): raise HTTPException(400,'Tabela não permitida para o cliente')
        prices = [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='price' AND (payload->>'state'=%s OR payload->>'state'='ALL') ORDER BY id",(selected,))]
    return {'clientId':client_id,'state':uf,'priceTable':selected,'allowedTables':['AP','PA'] if uf=='AP' else ['PA'],'prices':prices if 'finance' in sectors_for(user) else hide_industry_commissions(prices)}

@app.post('/api/sync')
def sync(data: Sync, authorization: str | None = Header(default=None)):
    user = auth(authorization)
    with db() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
        account=con.execute('SELECT role,sectors FROM app_users WHERE username=%s AND active',(user,)).fetchone()
        permissions=effective_sectors(user,account[0],account[1]) if account else set()
        # Catalog dependencies must exist before prices and orders, even in offline queues.
        changed=False
        priority={'industry':0,'price_table':1,'client':2,'price':3}
        for change in sorted(data.changes,key=lambda c:priority.get(c.type,4)):
            try:
                kind, obj = change.type, dict(change.data)
                entity_id = obj.get('id')
                deletable = {'delete_industry':'industry','delete_price_table':'price_table','delete_fulfillment':'fulfillment','delete_settlement':'settlement','delete_opportunity':'opportunity','delete_lead':'lead','delete_interaction':'interaction','delete_whatsapp_template':'whatsapp_template','delete_client':'client','delete_visit':'visit','delete_task':'task','delete_goal':'goal','delete_route':'route','delete_order':'order','delete_price':'price','delete_cash_entry':'cash_entry','delete_commission_rate':'commission_rate','delete_commission_receipt':'commission_receipt','delete_office_process':'office_process','delete_office_action':'office_action','delete_office_commercial':'office_commercial','delete_office_administrative':'office_administrative','delete_office_ritual':'office_ritual','delete_office_role':'office_role','delete_office_finance':'office_finance','delete_office_budget':'office_budget','delete_office_monthly_close':'office_monthly_close'}
                if kind not in ('industry','price_table','fulfillment','settlement','opportunity','interaction','lead','whatsapp_template','client','visit','order','task','route','goal','price','commission_rate','commission_receipt','office_action','office_commercial','office_administrative','office_ritual','office_role','office_finance','office_budget','office_monthly_close','office_process','cash_day','cash_entry',*deletable) or not isinstance(entity_id,str) or not 1 <= len(entity_id) <= 128:
                    raise HTTPException(400, 'Alteração inválida')
                # Replays were already validated and committed; catalog changes must not
                # invalidate their acknowledgements or create duplicate audit entries.
                if con.execute('SELECT 1 FROM applied_changes WHERE change_id=%s',(change.changeId,)).fetchone():
                    continue
                target_kind=deletable.get(kind,kind)
                if target_kind=='client':
                    previous_client=con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s",(entity_id,)).fetchone()
                    if not admin_access(user) and previous_client and obj.get('owner',previous_client[0].get('owner'))!=previous_client[0].get('owner'):
                        raise HTTPException(403,'Somente a administradora pode transferir a carteira')
                    if not admin_access(user) and not previous_client and obj.get('owner')!=user:
                        raise HTTPException(403,'Novo cliente deve pertencer ao seu usuário')
                    if obj.get('owner') and not valid_client_responsible(con,obj['owner']) and obj['owner']!='Ana Paula' and not (previous_client and obj['owner']==previous_client[0].get('owner')):
                        raise HTTPException(400,'Responsável precisa ser vendedor ou administrador ativo')
                    for derived in ('tdc','mcr','mcrConsent','fieldTemperature'):
                        obj.pop(derived,None)
                        if previous_client and derived in previous_client[0]: obj[derived]=previous_client[0][derived]
                    if previous_client: check_client_scope(con,user,entity_id)
                elif target_kind in ('order','visit','task','route','opportunity','interaction','fulfillment','lead','settlement','office_finance'):
                    previous_entity=con.execute('SELECT payload FROM entities WHERE kind=%s AND id=%s',(target_kind,entity_id)).fetchone()
                    if previous_entity and previous_entity[0].get('clientId'):
                        check_client_scope(con,user,previous_entity[0]['clientId'])
                    if obj.get('clientId'): check_client_scope(con,user,obj['clientId'])
                    if kind=='task':field_assistant.validate_agenda_task(con,user,obj,previous_entity[0] if previous_entity else None)
                if kind.startswith('delete_') and kind not in ('delete_industry','delete_price_table','delete_price','delete_client','delete_order'):
                    base=kind[7:]
                    required='finance' if base in ('settlement','cash_entry','commission_rate','commission_receipt','office_finance','office_budget','office_monthly_close') else 'routes' if base=='route' else 'office' if base.startswith('office_') or base=='fulfillment' else 'commercial'
                    if required not in permissions and not (base=='fulfillment' and 'commercial' in permissions): raise HTTPException(403,'Setor sem permissão')
                if kind in deletable:
                    target = deletable[kind]
                    if target in ('industry','price_table') and not admin_access(user): raise HTTPException(403,'Catálogo restrito à administradora')
                    if target == 'industry' and con.execute("SELECT 1 FROM entities WHERE kind='price_table' AND payload->>'brand'=%s LIMIT 1",(entity_id,)).fetchone(): raise HTTPException(409,'Indústria possui tabelas cadastradas')
                    if target == 'price_table' and con.execute("SELECT 1 FROM entities WHERE kind='price' AND coalesce(nullif(payload->>'tableId',''),concat(payload->>'brand','|',payload->>'state'))=%s LIMIT 1",(entity_id,)).fetchone(): raise HTTPException(409,'Tabela possui produtos cadastrados')
                    if target in ('office_finance','office_budget','office_monthly_close','cash_entry','commission_rate','commission_receipt','settlement') and 'finance' not in permissions:
                        raise HTTPException(403, 'Acesso financeiro restrito')
                    if target == 'cash_entry':
                        previous = con.execute("SELECT payload FROM entities WHERE kind='cash_entry' AND id=%s",(entity_id,)).fetchone()
                        if previous:
                            day = con.execute("SELECT payload FROM entities WHERE kind='cash_day' AND payload->>'date'=%s",(previous[0].get('date'),)).fetchone()
                            if not day or day[0].get('status') != 'Aberto':
                                raise HTTPException(409, 'Caixa fechado não permite excluir lançamentos')
                    if target == 'route' and 'routes' not in permissions:
                        raise HTTPException(403, 'Roteirização restrita a representantes')
                    if target in ('price','client') and not admin_access(user):
                        raise HTTPException(403, 'Exclusão restrita à administradora')
                    if target == 'office_process' and 'finance' not in permissions:
                        previous = con.execute("SELECT payload FROM entities WHERE kind=%s AND id=%s", (target,entity_id)).fetchone()
                        if previous and previous[0].get('Área') == 'Financeiro':
                            raise HTTPException(403, 'Acesso financeiro restrito')
                    if target == 'client':
                        if con.execute('SELECT 1 FROM documentos_cliente WHERE client_id=%s LIMIT 1',(entity_id,)).fetchone():
                            raise HTTPException(409, 'Cliente possui documentos; preserve o cadastro')
                        for dependent in ('order','visit','route','task','opportunity','interaction','fulfillment','settlement'):
                            if con.execute("SELECT 1 FROM entities WHERE kind=%s AND payload->>'clientId'=%s LIMIT 1",(dependent,entity_id)).fetchone():
                                raise HTTPException(409, 'Cliente possui histórico vinculado; preserve o cadastro')
                    if target == 'order':
                        if con.execute("SELECT 1 FROM entities WHERE kind='settlement' AND payload->>'orderId'=%s LIMIT 1",(entity_id,)).fetchone():
                            raise HTTPException(409,'Pedido com faturamento vinculado deve ser conciliado antes de arquivar')
                        previous = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s",(entity_id,)).fetchone()
                        if previous and previous[0].get('status') not in ('Pendente','Cancelado') and not admin_access(user):
                            raise HTTPException(403, 'Somente Ana Paula pode arquivar pedido confirmado ou faturado')
                    if con.execute('SELECT 1 FROM applied_changes WHERE change_id=%s',(change.changeId,)).fetchone():
                        continue
                    if target in ('opportunity','interaction','fulfillment','settlement','lead'):
                        previous_crm = con.execute('SELECT payload FROM entities WHERE kind=%s AND id=%s',(target,entity_id)).fetchone()
                        if previous_crm:
                            con.execute('INSERT INTO archived_entities(kind,id,payload,reason) VALUES(%s,%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,reason=excluded.reason,archived_at=now()',(target,entity_id,Jsonb(previous_crm[0]),'arquivado por '+user))
                    if target == 'visit':
                        con.execute('DELETE FROM visit_photos WHERE visit_id=%s',(entity_id,))
                    if target == 'order' and previous:
                        con.execute("INSERT INTO archived_entities(kind,id,payload,reason) VALUES('order',%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,reason=excluded.reason,archived_at=now()",(entity_id,Jsonb(previous[0]),'arquivado por '+user))
                    if target in ('visit','interaction'):
                        con.execute('DELETE FROM atendimentos WHERE id=%s',(target+':'+entity_id,))
                    elif target=='order':
                        con.execute("UPDATE faturamento_pedidos SET status_pedido='Arquivado',updated_at=now() WHERE id=%s",(entity_id,))
                    elif target=='client':
                        con.execute('DELETE FROM clientes WHERE id=%s',(entity_id,))
                    con.execute('DELETE FROM entities WHERE kind=%s AND id=%s',(target,entity_id))
                    if target=='order':con.execute("DELETE FROM entities WHERE kind='tdc_signal' AND id=%s",('order-'+entity_id,))
                    if target in ('order','visit','interaction') and previous_entity and previous_entity[0].get('clientId'):
                        strategic_crm.refresh_client(con,previous_entity[0]['clientId'])
                    con.execute('INSERT INTO applied_changes(change_id) VALUES(%s)',(change.changeId,))
                    con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,target,entity_id,'delete'))
                    continue
                if kind in ('opportunity','interaction','lead','visit','order','task','goal','whatsapp_template','client') and 'commercial' not in permissions and 'office' not in permissions:
                    raise HTTPException(403,'Setor comercial sem permissão')
                if kind in ('fulfillment',) and not ({'office','commercial'} & permissions): raise HTTPException(403,'Setor operacional sem permissão')
                if kind.startswith('office_') and kind not in ('office_finance','office_budget','office_monthly_close') and 'office' not in permissions: raise HTTPException(403,'Setor escritório sem permissão')
                if kind in ('industry','price_table'):
                    if not admin_access(user) and not (kind == 'price_table' and user in CATALOG_EDITORS and 'catalog' in permissions): raise HTTPException(403,'Catálogo restrito à administração')
                    brand=str(obj.get('name' if kind=='industry' else 'brand','')).strip()
                    if not brand or len(brand)>120 or '|' in brand: raise HTTPException(400,'Indústria inválida')
                    if kind=='industry':
                        if con.execute("SELECT 1 FROM entities WHERE kind='industry' AND lower(id)=lower(%s) AND id<>%s LIMIT 1",(brand,entity_id)).fetchone(): raise HTTPException(409,'Indústria já cadastrada')
                        if entity_id!=brand: raise HTTPException(400,'Identificação da indústria inválida')
                        if not isinstance(obj.get('active'),bool): raise HTTPException(400,'Situação inválida')
                        policy=obj.get('commercialPolicy',[])
                        if not isinstance(policy,list) or len(policy)>12: raise HTTPException(400,'Política comercial inválida')
                        for tier in policy:
                            try:
                                minimum=Decimal(str(tier['minimum']))
                                terms=tier['paymentTerms']
                                freight=tier['freight']
                            except (KeyError,TypeError,InvalidOperation):
                                raise HTTPException(400,'Faixa comercial inválida')
                            if not minimum.is_finite() or minimum<=0 or minimum.as_tuple().exponent < -2 or not isinstance(terms,str) or not re.fullmatch(r'\d+(?:/\d+)*',terms) or freight not in ('CIF','FOB'):
                                raise HTTPException(400,'Faixa comercial inválida')
                    else:
                        state='ALL' if obj.get('state')=='ALL' else normalize_uf(obj.get('state'))
                        if not state: raise HTTPException(400,'Abrangência da tabela inválida')
                        previous_table=con.execute("SELECT payload FROM entities WHERE kind='price_table' AND id=%s",(entity_id,)).fetchone()
                        if previous_table and (previous_table[0].get('brand')!=brand or previous_table[0].get('state')!=state): raise HTTPException(400,'Tabela vinculada não pode mudar de indústria ou abrangência')
                        if not str(obj.get('title','')).strip(): raise HTTPException(400,'Nome da tabela obrigatório')
                        if not admin_access(user):
                            previous_table=con.execute("SELECT payload FROM entities WHERE kind='price_table' AND id=%s",(entity_id,)).fetchone()
                            if not previous_table or obj.get('active') != previous_table[0].get('active'):
                                raise HTTPException(403,'Somente a administração altera a situação da tabela')
                        industry=con.execute("SELECT payload FROM entities WHERE kind='industry' AND id=%s",(brand,)).fetchone()
                        if not industry or not industry[0].get('active'): raise HTTPException(400,'Cadastre uma indústria ativa antes da tabela')
                        if not isinstance(obj.get('active'),bool): raise HTTPException(400,'Situação inválida')
                        obj['state']=state
                if kind in ('fulfillment','settlement'):
                    order_ref = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s",(obj.get('orderId'),)).fetchone()
                    if not order_ref or obj.get('clientId') != order_ref[0].get('clientId'):
                        raise HTTPException(400,'Pedido e cliente não correspondem')
                    if kind == 'fulfillment':
                        if obj.get('stage') not in ('Conferência','Enviado à indústria','Confirmado pela indústria','Faturado','Em transporte','Entregue','Pós-venda concluído','Ocorrência') or not con.execute('SELECT 1 FROM app_users WHERE username=%s AND active',(obj.get('owner'),)).fetchone():
                            raise HTTPException(400,'Etapa operacional ou responsável inválido')
                        other = con.execute("SELECT 1 FROM entities WHERE kind='fulfillment' AND payload->>'orderId'=%s AND id<>%s LIMIT 1",(obj.get('orderId'),entity_id)).fetchone()
                        if other: raise HTTPException(409,'Pedido já tem acompanhamento')
                    else:
                        if 'finance' not in permissions: raise HTTPException(403,'Acesso financeiro restrito')
                        if not isinstance(obj.get('invoiceNumber'),str) or not 1<=len(obj['invoiceNumber'].strip())<=80:
                            raise HTTPException(400,'Documento de faturamento inválido')
                        other = con.execute("SELECT 1 FROM entities WHERE kind='settlement' AND payload->>'orderId'=%s AND payload->>'invoiceNumber'=%s AND id<>%s LIMIT 1",(obj.get('orderId'),obj['invoiceNumber'],entity_id)).fetchone()
                        if other: raise HTTPException(409,'Documento de faturamento já cadastrado')
                        values={}
                        for field in ('billed','commissionReceived','allocatedCost'):
                            try: value=Decimal(str(obj.get(field,'0')))
                            except (ValueError,TypeError,InvalidOperation): raise HTTPException(400,'Valor financeiro inválido')
                            if not value.is_finite() or value<0 or value>Decimal('10000000000') or value.as_tuple().exponent < -2: raise HTTPException(400,'Valor financeiro inválido')
                            values[field]=value
                        try: rate=Decimal(str(obj.get('rate','')))
                        except (ValueError,TypeError,InvalidOperation): raise HTTPException(400,'Percentual inválido')
                        if not rate.is_finite() or rate<0 or rate>100 or rate.as_tuple().exponent < -2: raise HTTPException(400,'Percentual inválido')
                        calculated=(values['billed']*rate/100).quantize(Decimal('0.01'))
                        if values['commissionReceived']>calculated: raise HTTPException(400,'Recebimento maior que comissão prevista')
                        if values['commissionReceived']>0 and not obj.get('receivedDate'): raise HTTPException(400,'Data de recebimento obrigatória')
                        obj['commissionDue']=str(calculated)
                        order_brands={item.get('brand') or order_ref[0].get('brand') for item in order_ref[0].get('items',[])}
                        if obj.get('brand') not in (order_brands or {order_ref[0].get('brand')}):
                            raise HTTPException(400,'Indústria do lançamento não corresponde ao pedido')
                        for field in ('billedDate','due','receivedDate'):
                            if obj.get(field):
                                try: date.fromisoformat(str(obj[field]))
                                except (ValueError,TypeError): raise HTTPException(400,'Data financeira inválida')
                        if not obj.get('billedDate'): raise HTTPException(400,'Data do faturamento obrigatória')
                if kind == 'lead':
                    tax=re.sub(r'\D','',str(obj.get('taxId','')))
                    uf=normalize_uf(obj.get('state'))
                    name=str(obj.get('name','')).strip()
                    if not valid_cnpj(tax) or entity_id != 'speedio:'+tax or not uf or not name or len(name)>180:
                        raise HTTPException(400,'Lead: CNPJ, UF PA/AP ou nome inválido')
                    if any(len(str(obj.get(field,'')))>length for field,length in (('city',120),('contact',120),('phone',30))):
                        raise HTTPException(400,'Dados do lead excedem o limite')
                    if not con.execute('SELECT 1 FROM app_users WHERE username=%s AND active',(obj.get('owner'),)).fetchone():
                        raise HTTPException(400,'Responsável inválido')
                    if con.execute("SELECT 1 FROM clientes WHERE regexp_replace(coalesce(documento,''),'[^0-9]','','g')=%s LIMIT 1",(tax,)).fetchone():
                        raise HTTPException(409,'CNPJ já cadastrado como cliente')
                    obj={key:obj.get(key) for key in ('id','name','taxId','state','city','contact','phone','owner','origin','createdAt')}
                    obj['taxId'],obj['state'],obj['origin']=tax,uf,'Speedio'
                if kind in ('opportunity','interaction'):
                    if not isinstance(obj.get('clientId'),str) or not con.execute("SELECT 1 FROM entities WHERE kind='client' AND id=%s",(obj.get('clientId'),)).fetchone():
                        raise HTTPException(400,'Cliente da oportunidade ou interação não encontrado')
                    if not con.execute('SELECT 1 FROM app_users WHERE username=%s AND active',(obj.get('owner'),)).fetchone():
                        raise HTTPException(400,'Responsável inválido')
                    if kind == 'interaction':
                        if obj.get('type') not in ('Conversa','Ligação','WhatsApp','E-mail','Reunião','Ocorrência','Pós-venda') or not isinstance(obj.get('text'),str) or not 1 <= len(obj['text'].strip()) <= 2000:
                            raise HTTPException(400,'Interação inválida')
                        try: datetime.fromisoformat(str(obj.get('at','')).replace('Z','+00:00'))
                        except (ValueError,TypeError): raise HTTPException(400,'Data da interação inválida')
                    else:
                        stages = ('Prospectado','Qualificado','Visita agendada','Proposta enviada','Negociação','Ganho','Pedido confirmado','Faturado','Entregue','Pós-venda','Perdido')
                        if obj.get('stage') not in stages or not isinstance(obj.get('brand'),str) or not 1 <= len(obj['brand'].strip()) <= 120:
                            raise HTTPException(400,'Etapa ou indústria inválida')
                        if obj.get('stage') == 'Perdido' and not str(obj.get('lossReason','')).strip():
                            raise HTTPException(400,'Informe o motivo da perda')
                        try: amount=Decimal(str(obj.get('amount','')))
                        except (ValueError,TypeError,InvalidOperation): raise HTTPException(400,'Valor previsto inválido')
                        if not amount.is_finite() or amount<0 or amount>Decimal('10000000000') or amount.as_tuple().exponent < -2:
                            raise HTTPException(400,'Valor previsto inválido')
                        for field in ('closeDate','followUp'):
                            value=obj.get(field)
                            if value:
                                try: date.fromisoformat(str(value))
                                except (ValueError,TypeError): raise HTTPException(400,'Data da oportunidade inválida')
                        if len(str(obj.get('notes','')))>2000: raise HTTPException(400,'Contexto muito extenso')
                if kind in ('opportunity','order','fulfillment'):
                    previous = con.execute('SELECT payload FROM entities WHERE kind=%s AND id=%s',(kind,entity_id)).fetchone()
                    old = previous[0] if previous else {}
                    field = 'status' if kind == 'order' else 'stage'
                    if kind == 'order':
                        if old.get('proposalSentAt'):
                            obj['proposalSentAt'], obj['proposalSentBy'] = old['proposalSentAt'], old.get('proposalSentBy','')
                        elif obj.get('proposalSentAt'):
                            obj['proposalSentAt'], obj['proposalSentBy'] = datetime.now(TZ).isoformat(), user
                    history = list(old.get('stageHistory') or [])
                    if old.get(field) != obj.get(field):
                        history.append({'stage':obj.get(field),'at':datetime.now(TZ).isoformat(),'by':user})
                    obj['stageHistory'] = history
                    if kind == 'opportunity' and obj.get('orderId'):
                        linked = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s",(obj['orderId'],)).fetchone()
                        if not linked or linked[0].get('clientId') != obj.get('clientId'):
                            raise HTTPException(400,'Pedido vinculado não corresponde ao cliente da oportunidade')
                    if kind == 'fulfillment':
                        if obj.get('postSaleDate'):
                            try: date.fromisoformat(str(obj['postSaleDate']))
                            except (ValueError,TypeError): raise HTTPException(400,'Data de pós-venda inválida')
                        if obj.get('stage') == 'Pós-venda concluído' and not str(obj.get('postSaleResult') or obj.get('notes') or '').strip():
                            raise HTTPException(400,'Registre o resultado do contato de pós-venda')
                if kind in ('visit','interaction'):
                    previous_action = con.execute('SELECT payload FROM entities WHERE kind=%s AND id=%s', (kind,entity_id)).fetchone()
                    # Preserve previously queued historical records; require next step on new manual attendance.
                    if not previous_action and (kind=='visit' or obj.get('returnDate') or obj.get('next')):
                        if not str(obj.get('next','')).strip() or not obj.get('returnDate'):
                            raise HTTPException(400,'Informe próxima ação e data de retorno do atendimento')
                        try: date.fromisoformat(str(obj['returnDate']))
                        except (ValueError,TypeError): raise HTTPException(400,'Data de retorno inválida')
                if kind == 'price':
                    brand=str(obj.get('brand','')).strip()
                    table=validate_table(con,obj,brand)
                    obj['state']=table['state']
                    obj['tableId']=table_id(obj)
                    if user not in CATALOG_EDITORS or 'catalog' not in permissions:
                        raise HTTPException(403, 'Preço restrito à administração')
                    brand,sku,state = str(obj.get('brand','')).strip(),str(obj.get('sku','')).strip(),('ALL' if obj.get('state')=='ALL' else normalize_uf(obj.get('state')))
                    try: price=Decimal(str(obj.get('price','')))
                    except (ValueError,InvalidOperation): raise HTTPException(400,'Preço inválido')
                    if not brand or not sku or not state or entity_id != price_id(obj) or not price.is_finite() or price <= 0 or price.as_tuple().exponent < -2:
                        raise HTTPException(400,'Preço ou identificação inválida')
                    description=str(obj.get('description','')).strip()
                    if not 1 <= len(description) <= 250: raise HTTPException(400,'Descrição inválida')
                    obj.update(brand=brand,sku=sku,state=state,price=str(price),description=description)
                if (kind in ('office_finance','office_budget','office_monthly_close','cash_day','cash_entry','commission_rate','commission_receipt') or (kind == 'office_process' and str(obj.get('Área','')) == 'Financeiro')) and 'finance' not in permissions:
                    raise HTTPException(403, 'Acesso financeiro restrito')
                if kind == 'office_finance' and obj.get('Data'):
                    try:
                        date.fromisoformat(str(obj['Data']))
                        if obj.get('Vencimento'): date.fromisoformat(str(obj['Vencimento']))
                        if obj.get('Liquidação'): date.fromisoformat(str(obj['Liquidação']))
                        amount=Decimal(str(obj['Valor']))
                    except (ValueError, TypeError, KeyError, InvalidOperation):
                        raise HTTPException(400, 'Data ou valor financeiro inválido')
                    if not amount.is_finite() or amount<=0 or amount.as_tuple().exponent < -2 or amount>Decimal('10000000000'):
                        raise HTTPException(400, 'Valor financeiro inválido')
                    if obj.get('Tipo') not in ('Receita','Despesa') or obj.get('Situação') not in ('Previsto','Pago/Recebido'):
                        raise HTTPException(400, 'Tipo ou situação financeira inválida')
                    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',str(obj.get('Competência') or obj['Data'][:7])):
                        raise HTTPException(400, 'Competência inválida')
                    if obj['Situação']=='Pago/Recebido' and obj.get('Grupo DRE') and not obj.get('Liquidação'):
                        raise HTTPException(400, 'Data de liquidação obrigatória')
                    if obj.get('Grupo DRE') and obj['Grupo DRE'] not in ('Receita operacional','Deduções','Custo direto','Despesa operacional','Resultado financeiro','Tributos sobre o resultado','Não classificado'):
                        raise HTTPException(400, 'Grupo DRE inválido')
                    if obj.get('clientId') and not con.execute("SELECT 1 FROM entities WHERE kind='client' AND id=%s",(obj['clientId'],)).fetchone():
                        raise HTTPException(400, 'Cliente do lançamento não encontrado')
                    if len(str(obj.get('Centro de custo','')))>120:
                        raise HTTPException(400, 'Centro de custo muito longo')
                    if obj.get('sourceOpportunityId'):
                        source=con.execute("SELECT payload FROM entities WHERE kind='opportunity' AND id=%s",(obj['sourceOpportunityId'],)).fetchone()
                        if not source or source[0].get('stage') not in ('Ganho','Pedido confirmado') or source[0].get('clientId')!=obj.get('clientId') or obj.get('Tipo')!='Receita':
                            raise HTTPException(400, 'Vínculo com oportunidade inválido')
                        duplicates=con.execute("SELECT 1 FROM entities WHERE kind='office_finance' AND id<>%s AND payload->>'sourceOpportunityId'=%s LIMIT 1",(entity_id,obj['sourceOpportunityId'])).fetchone()
                        if duplicates:
                            raise HTTPException(409, 'Oportunidade já vinculada a um recebível')
                if kind in ('commission_rate','commission_receipt'):
                    brand = str(obj.get('brand','')).strip()
                    normalized = re.sub(r'[^a-z0-9]+','-',''.join(c for c in unicodedata.normalize('NFD',brand) if not '\u0300' <= c <= '\u036f').lower()).strip('-')
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
                if kind == 'office_process' and 'finance' not in permissions:
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
                    # Aceita pontuação comum da IE sem converter palavras inválidas em números.
                    if registration != 'ISENTO' and re.fullmatch(r'[\d.\-/\s]+', registration):
                        registration = re.sub(r'\D', '', registration)
                    if not existing or tax_id or registration:
                        if not valid_cnpj(tax_id):
                            raise HTTPException(400, 'CNPJ inválido; informe os 14 dígitos corretos')
                        if registration != 'ISENTO' and not (registration.isdigit() and 7 <= len(registration) <= 14):
                            raise HTTPException(400, f"Cliente {obj['name'].strip()} ({entity_id}): informe inscrição estadual numérica de 7 a 14 dígitos ou ISENTO")
                        obj['taxId'] = tax_id
                        obj['stateRegistration'] = registration
                        duplicates = con.execute("SELECT id,payload FROM entities WHERE kind='client' AND id<>%s AND payload->>'taxId' IS NOT NULL", (entity_id,)).fetchall()
                        if any(''.join(ch for ch in str(row[1].get('taxId') or '') if ch.isdigit()) == tax_id for row in duplicates):
                            raise HTTPException(409, 'CNPJ já cadastrado em outro cliente')
                if kind == 'order':
                    # Uma fila offline antiga não pode desfazer a correção autorizada.
                    if entity_id == '858c6713-4712-4873-b716-8f5e76425468' and obj.get('date') == '2026-11-30':
                        obj['date'] = '2026-09-29'
                        obj['dateCorrection'] = {'original':'2026-11-30','corrected':'2026-09-29','reason':'Correção autorizada preservada na sincronização'}
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
                        raise HTTPException(400, 'Cliente do Pará usa PA; cliente do Amapá pode usar PA ou AP')
                    total = Decimal('0')
                    brands=set()
                    for item in obj['items']:
                        if not isinstance(item, dict) or not isinstance(item.get('sku'), str) or not item['sku'].strip():
                            raise HTTPException(400, 'SKU inválido')
                        item_brand=str(item.get('brand') or obj.get('brand') or '').strip()
                        if not item_brand or item_brand=='Multimarcas': raise HTTPException(400,'Indústria do item obrigatória')
                        brands.add(item_brand)
                        table=validate_table(con,{**item,'brand':item_brand,'state':price_table},item_brand,price_table)
                        item['tableId']=table['id']
                        price_key = price_id({**item,'brand':item_brand,'sku':item['sku'].strip()})
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
                        item['brand']=item_brand
                        total += qty * unit
                    obj['brand']='Multimarcas' if len(brands)>1 else next(iter(brands))
                    obj['clientState'] = client_uf
                    obj['priceTable'] = price_table
                    obj['state'] = price_table
                    obj['amount'] = float(total.quantize(Decimal('0.01')))
                    validate_bella_order(con,obj,client_uf,user)
                    existing_order = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s",(entity_id,)).fetchone()
                    attribute_order(con,user,obj,existing_order[0] if existing_order else None)
                    rate_row=con.execute('SELECT seller_commission_rate FROM app_users WHERE username=%s',(obj['sellerResponsible'],)).fetchone()
                    apply_seller_commission(obj,rate_row[0] if rate_row else None,existing_order[0] if existing_order else None)
                    if not existing_order and con.execute("SELECT 1 FROM archived_entities WHERE kind='order' AND id=%s",(entity_id,)).fetchone():
                        raise HTTPException(409, 'Pedido arquivado não pode ser recriado; restaure o original')
                    obj['orderNumber'] = existing_order[0].get('orderNumber') if existing_order and existing_order[0].get('orderNumber') else next_order_number(con)
                if kind == 'order':
                    try: amount = float(obj.get('amount',0))
                    except (TypeError, ValueError): raise HTTPException(400,'Valor inválido')
                    if not 0 <= amount <= 1e10: raise HTTPException(400,'Valor inválido')
                if kind == 'goal':
                    if obj.get('clientTarget') is not None:
                        value=obj['clientTarget']
                        if isinstance(value,bool) or not isinstance(value,(int,float)) or value!=int(value) or not 0<=value<=1000000: raise HTTPException(400,'Meta de clientes inválida')
                    try: amount = float(obj.get('amount',0))
                    except (TypeError, ValueError): raise HTTPException(400,'Meta inválida')
                    if not re.fullmatch(r'\d{4}-\d{2}',str(obj.get('month',''))) or not 0 <= amount <= 1e10:
                        raise HTTPException(400,'Meta inválida')
                if kind == 'route' and 'routes' not in permissions:
                    raise HTTPException(403,'Roteirização restrita a representantes')
                if kind == 'delete_route' and 'routes' not in permissions:
                    raise HTTPException(403,'Roteirização restrita a representantes')
                if kind == 'client' and 'clients_edit' not in permissions and con.execute('SELECT 1 FROM entities WHERE kind=%s AND id=%s',('client',entity_id)).fetchone():
                    # A repeated, previously applied change is accepted below.
                    if not con.execute('SELECT 1 FROM applied_changes WHERE change_id=%s',(change.changeId,)).fetchone():
                        raise HTTPException(403,'Sem permissão para editar cadastro existente')
                if con.execute('SELECT 1 FROM applied_changes WHERE change_id=%s',(change.changeId,)).fetchone():
                    continue
                if kind == 'delete_route':
                    con.execute('DELETE FROM entities WHERE kind=%s AND id=%s',('route',entity_id))
                else:
                    if kind in ('industry','price_table') and 'finance' not in permissions:
                        private_previous=con.execute('SELECT payload FROM entities WHERE kind=%s AND id=%s',(kind,entity_id)).fetchone()
                        obj=preserve_industry_commissions(private_previous[0] if private_previous else {},obj)
                    obj['updatedAt'] = datetime.now(TZ).isoformat()
                    obj['updatedBy'] = user
                    obj['updatedAs'] = 'Adm' if user=='Laís' or (account and account[0] in ('Administrativo','Administradora')) else 'Sócio' if user=='Euler' else 'Comercial'
                    con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()', (kind,entity_id,Jsonb(obj)))
                    if kind=='client':
                        con.execute("""INSERT INTO clientes(id,razao_social,nome_fantasia,documento,curva_abc)
                            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET
                            razao_social=excluded.razao_social,nome_fantasia=excluded.nome_fantasia,
                            documento=excluded.documento,curva_abc=excluded.curva_abc,updated_at=now()""",
                            (entity_id,obj['name'],obj.get('tradeName') or None,obj.get('taxId') or None,obj.get('abc') if obj.get('abc') in ('A','B','C') else None))
                    elif kind in ('visit','interaction'): project_attendance(con,kind,entity_id,obj)
                    elif kind=='order': project_order(con,entity_id,obj)
                    strategic_crm.event(con,kind,obj)
                    if kind in ('order','visit','interaction') and previous_entity and previous_entity[0].get('clientId') and previous_entity[0]['clientId']!=obj.get('clientId'):
                        strategic_crm.refresh_client(con,previous_entity[0]['clientId'])
                con.execute('INSERT INTO applied_changes(change_id) VALUES(%s)',(change.changeId,))
                con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,kind,entity_id,'delete' if kind=='delete_route' else 'upsert'))
                changed=True
            except HTTPException as exc:
                exc.headers = {**(getattr(exc,'headers',None) or {}), 'X-L2-Change-Id': change.changeId, 'X-L2-Change-Type': change.type}
                logger = globals().get('logging')
                if logger:
                    logger.warning('sync_rejected user=%s kind=%s change=%s status=%s reason=%s', user, change.type, change.changeId, exc.status_code, exc.detail)
                raise
        if changed:
            con.execute("SELECT pg_notify('l2_records_changed', '')")
        result = {'role':account[0] if account else '', 'sectors':sorted(permissions),'team':[row[0] for row in con.execute('SELECT username FROM app_users WHERE active ORDER BY username')],
                  'sellers':[row[0] for row in con.execute("SELECT username FROM app_users WHERE active AND role='Vendedor' ORDER BY username")]}
        result['sellerCommissions']=[{'user':name,'rate':str(rate) if rate is not None else None} for name,rate in con.execute("SELECT username,seller_commission_rate FROM app_users WHERE active AND role='Vendedor' ORDER BY username") if not is_seller(con,user) or name==user]
        public_kinds = [('industry','industries'),('price_table','priceTables'),('fulfillment','fulfillments'),('opportunity','opportunities'),('interaction','interactions'),('lead','leads'),('whatsapp_template','whatsappTemplates'),('client','clients'),('visit','visits'),('order','orders'),('task','tasks'),('route','routes'),('goal','goals'),('price','prices'),('office_process','officeProcesses'),('office_action','officeActions'),('office_commercial','officeCommercial'),('office_administrative','officeAdministrative'),('office_ritual','officeRituals'),('office_role','officeRoles')]
        for kind, name in public_kinds:
            if kind in ('office_process','office_action','office_commercial','office_administrative','office_ritual','office_role') and 'office' not in permissions:
                result[name]=[];continue
            if kind=='fulfillment' and not ({'office','commercial'} & permissions):
                result[name]=[];continue
            if kind in ('client','visit','order','task','goal','opportunity','interaction','lead','whatsapp_template') and not ({'commercial','office','finance','management'} & permissions):
                result[name]=[];continue
            if kind == 'route' and 'routes' not in permissions:
                result[name]=[];continue
            if kind in ('price','industry','price_table') and not ({'catalog','commercial'} & permissions):
                result[name]=[];continue
            if kind == 'office_process' and 'finance' not in permissions:
                result[name] = [row[0] for row in con.execute("SELECT payload FROM entities WHERE kind=%s AND coalesce(payload->>'Área','')<>'Financeiro' ORDER BY updated_at,id", (kind,))]
            else:
                result[name] = scoped_rows(con,kind,user)
        # Shared read-only commercial dashboard. Portfolio write checks remain unchanged.
        result['crmTeam'] = {}
        if {'commercial','office','management'} & permissions:
            fields = {
                'client': ('name','owner','taxId'),
                'goal': ('month','user','brand','amount','updatedAt'),
                'industry': ('name','active'),
                'lead': ('name','owner','createdAt','taxId'),
                'opportunity': ('clientId','brand','stage','owner','amount','followUp','closeDate','orderId','stageHistory','updatedAt','createdAt','notes'),
                'order': ('clientId','brand','status','sellerResponsible','user','amount','date','orderNumber','stageHistory','items','proposalSentAt','proposalSentBy'),
                'route': ('clientId','user','date','status','objective'),
                'visit': ('clientId','user','date','result','checkIn','checkOut','next','notes','returnDate','brand'),
                'fulfillment': ('clientId','orderId','stage','owner','due','at','stageHistory','notes','postSaleDate','postSaleResult')
            }
            names = {'goal':'goals','industry':'industries','client':'clients','lead':'leads','opportunity':'opportunities','order':'orders','route':'routes','visit':'visits','fulfillment':'fulfillments'}
            for kind, keys in fields.items():
                rows = []
                for entity_id, payload in con.execute('SELECT id,payload FROM entities WHERE kind=%s ORDER BY updated_at,id',(kind,)):
                    item = {key: payload[key] for key in keys if key in payload}
                    item['id'] = entity_id
                    if kind == 'order':
                        item['items'] = [{key: line[key] for key in ('brand','subtotal','quantity','unitPrice') if key in line} for line in payload.get('items',[])]
                    rows.append(item)
                result['crmTeam'][names[kind]] = rows
            result['crmTeam']['billedDocuments'] = [{key: payload[key] for key in ('orderId','brand','billed','billedDate') if key in payload} for (payload,) in con.execute("SELECT payload FROM entities WHERE kind='settlement' AND coalesce(payload->>'invoiceNumber','')<>''")]
            result['crmTeam']['billedOrderIds'] = list({x['orderId'] for x in result['crmTeam']['billedDocuments'] if x.get('orderId')})
            result['crmTeam']['at'] = datetime.now(TZ).isoformat()
        for kind, name in [('settlement','settlements'),('office_finance','officeFinance'),('office_budget','officeBudget'),('office_monthly_close','officeMonthlyClose'),('cash_day','cashDays'),('cash_entry','cashEntries'),('commission_rate','commissionRates'),('commission_receipt','commissionReceipts')]:
            result[name] = scoped_rows(con,kind,user) if 'finance' in permissions else []
        return result if 'finance' in permissions else hide_industry_commissions(result)

def order_exists(con, order_id: str):
    if not con.execute("SELECT 1 FROM entities WHERE kind='order' AND id=%s",(order_id,)).fetchone():
        raise HTTPException(404, 'Pedido não encontrado')

def check_order_scope(con,user,order_id):
    row=con.execute("SELECT payload->>'clientId' FROM entities WHERE kind='order' AND id=%s",(order_id,)).fetchone()
    if not row: raise HTTPException(404,'Pedido não encontrado')
    check_client_scope(con,user,row[0])

@app.get('/api/admin/orders/archived')
def archived_orders(authorization: str | None = Header(default=None)):
    user=auth(authorization)
    if not admin_access(user):
        raise HTTPException(403, 'Consulta restrita à administradora')
    with db() as con:
        rows=con.execute("SELECT id,payload,reason,archived_at FROM archived_entities WHERE kind='order' ORDER BY archived_at DESC LIMIT 200").fetchall()
    result=[{'id':id,'order':payload,'reason':reason,'archivedAt':when.isoformat()} for id,payload,reason,when in rows]
    return result if 'finance' in sectors_for(user) else hide_industry_commissions(result)

@app.post('/api/admin/orders/{order_id}/restore')
def restore_archived_order(order_id: str, authorization: str | None = Header(default=None)):
    actor=auth(authorization)
    if not admin_access(actor):
        raise HTTPException(403, 'Restauração restrita à administradora')
    with db() as con:
        row=con.execute("SELECT payload FROM archived_entities WHERE kind='order' AND id=%s FOR UPDATE",(order_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Pedido arquivado não encontrado')
        if con.execute("SELECT 1 FROM entities WHERE kind='order' AND id=%s",(order_id,)).fetchone():
            raise HTTPException(409, 'Pedido já está ativo')
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('order',%s,%s)",(order_id,Jsonb(row[0])))
        strategic_crm.event(con,'order',row[0])
        strategic_crm.changed(con)
        con.execute("DELETE FROM archived_entities WHERE kind='order' AND id=%s",(order_id,))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'order',%s,'restore')",(actor,order_id))
    return {'id':order_id,'restored':True}

@app.get('/api/whatsapp/capabilities')
def whatsapp_capabilities(authorization: str | None = Header(default=None)):
    user = auth(authorization)
    require_sector(user, 'commercial', 'office')
    return {'mediaWithCaption': bool(provider_config()), 'tableDocuments': bool(provider_config())}

def catalog_document_data(user, client_id, selected_table):
    require_sector(user, 'commercial', 'office', 'catalog')
    with db() as con:
        check_client_scope(con, user, client_id)
        customer = con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s", (client_id,)).fetchone()
        table = con.execute("SELECT payload FROM entities WHERE kind='price_table' AND id=%s", (selected_table,)).fetchone()
        if not customer: raise HTTPException(404, 'Cliente não encontrado')
        if not table or not table[0].get('active'): raise HTTPException(400, 'Selecione uma tabela ativa')
        customer, table = customer[0], table[0]
        uf = normalize_uf(customer.get('state'))
        if not uf or (table.get('state') != 'ALL' and not price_table_matches_client(uf, table.get('state'))):
            raise HTTPException(400, 'Tabela não permitida para a UF do cliente')
        prices = [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='price' AND coalesce(payload->>'tableId',(payload->>'brand')||'|'||(payload->>'state'))=%s ORDER BY id", (selected_table,))]
        if not prices: raise HTTPException(400, 'Tabela sem produtos cadastrados')
        industries = [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='industry' AND (id=%s OR payload->>'name'=%s)", (table['brand'], table['brand']))]
    industry = industries[0] if industries else {}
    content = build_catalog_pdf(table, industry, prices, BASE, datetime.now(TZ).strftime('%d/%m/%Y'))
    filename = 'tabela-'+re.sub(r'[^a-zA-Z0-9_-]', '-', selected_table)[:100]+'.pdf'
    return customer, table, content, filename

@app.get('/api/catalog/table.pdf')
def catalog_table_pdf(client_id: str, table_id: str, authorization: str | None = Header(default=None)):
    user = auth(authorization)
    _, _, content, filename = catalog_document_data(user, client_id, table_id)
    return Response(content=content, media_type='application/pdf', headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="'+filename+'"'})

class TableDocumentSend(BaseModel):
    clientId: str = Field(min_length=1, max_length=128)
    tableId: str = Field(min_length=1, max_length=200)
    caption: str = Field(default='', max_length=1024)

@app.post('/api/whatsapp/table-document')
async def whatsapp_table_document(data: TableDocumentSend, authorization: str | None = Header(default=None)):
    user = auth(authorization)
    require_sector(user, 'commercial', 'office')
    if not provider_config(): raise HTTPException(503, 'Configure a API oficial do WhatsApp para enviar PDFs')
    customer, table, content, filename = catalog_document_data(user, data.clientId, data.tableId)
    phone = re.sub(r'\D', '', str(customer.get('phone') or ''))
    if len(phone) in (10,11): phone = '55'+phone
    if not re.fullmatch(r'55\d{10,11}', phone): raise HTTPException(400, 'Cadastre telefone brasileiro com DDD')
    caption = data.caption or ('Olá '+str(customer.get('name',''))+', segue a tabela atualizada da '+str(table['brand'])+' conforme solicitado.')
    try:
        message_id = await asyncio.to_thread(send_media, phone, filename, 'application/pdf', content, caption)
    except MediaError as exc:
        raise HTTPException(502, str(exc)) from exc
    event_id = secrets.token_hex(16)
    payload = {'id':event_id,'clientId':data.clientId,'owner':user,'type':'WhatsApp','at':datetime.now(TZ).isoformat(),
               'text':'Tabela PDF enviada: '+str(table['brand'])+' · '+str(table.get('title',data.tableId)),
               'brand':table['brand'],'tableId':data.tableId,'messageId':message_id,'status':'Aceito pela API'}
    try:
        with db() as con:
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('interaction',%s,%s)", (event_id, Jsonb(payload)))
            project_attendance(con, 'interaction', event_id, payload)
            con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'whatsapp_document',%s,%s)", (user,data.clientId,'accepted_'+data.tableId))
    except Exception:
        logging.getLogger('uvicorn.error').error('whatsapp_document accepted history_failed')
        return {'status':'accepted','messageId':message_id,'historyRecorded':False}
    return {'status':'accepted','messageId':message_id,'historyRecorded':True}

@app.post('/api/whatsapp/media')
async def send_whatsapp_media(client_id: str = Form(...), caption: str = Form(''),
                              file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user = auth(authorization)
    require_sector(user, 'commercial', 'office')
    if not provider_config():
        raise HTTPException(503, 'Envio de mídia indisponível até configurar a API oficial do WhatsApp')
    content = await file.read(16 * 1024 * 1024 + 1)
    try:
        mime, kind = validate_media(file.filename, file.content_type, content, caption)
    except MediaError as exc:
        raise HTTPException(400, str(exc)) from exc
    with db() as con:
        check_client_scope(con, user, client_id)
        row = con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s", (client_id,)).fetchone()
        if not row: raise HTTPException(404, 'Cliente não encontrado')
        phone = re.sub(r'\D', '', str(row[0].get('phone') or ''))
        if len(phone) in (10, 11): phone = '55' + phone
        if not re.fullmatch(r'55\d{10,11}', phone):
            raise HTTPException(400, 'Cadastre um telefone brasileiro com DDD para o cliente')
    try:
        message_id = await asyncio.to_thread(send_media, phone, file.filename, mime, content, caption)
    except MediaError as exc:
        raise HTTPException(502, str(exc)) from exc
    try:
        with db() as con:
            con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'whatsapp_media',%s,%s)",
                        (user, client_id, 'accepted_'+kind))
    except Exception:
        logging.getLogger('uvicorn.error').error('whatsapp_media accepted history_failed')
        return {'status':'accepted','messageId':message_id,'historyRecorded':False}
    return {'status':'accepted','messageId':message_id,'historyRecorded':True}

@app.get('/api/visits/{visit_id}/photos')
def list_visit_photos(visit_id: str, authorization: str | None = Header(default=None)):
    user=auth(authorization);require_sector(user,'commercial','office')
    with db() as con:
        check_visit_scope(con,user,visit_id)
        if not con.execute("SELECT 1 FROM entities WHERE kind='visit' AND id=%s",(visit_id,)).fetchone(): raise HTTPException(404,'Atendimento não encontrado')
        rows=con.execute('SELECT id,filename,uploaded_by,octet_length(content) FROM visit_photos WHERE visit_id=%s ORDER BY created_at,id',(visit_id,)).fetchall()
    return [{'id':r[0],'name':r[1],'uploadedBy':r[2],'size':r[3]} for r in rows]

@app.post('/api/visits/{visit_id}/photos')
async def upload_visit_photo(visit_id: str, file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'commercial','office')
    name=Path(file.filename or '').name.strip()[:180]
    content=await file.read(5*1024*1024+1)
    if not name or not content or len(content)>5*1024*1024: raise HTTPException(400,'Foto inválida ou acima de 5 MB')
    if content.startswith(b'\xff\xd8\xff') and name.lower().endswith(('.jpg','.jpeg')): content_type='image/jpeg'
    elif content.startswith(b'\x89PNG\r\n\x1a\n') and name.lower().endswith('.png'): content_type='image/png'
    else: raise HTTPException(400,'Envie uma foto JPG ou PNG válida')
    photo_id=secrets.token_hex(16)
    with db() as con:
        check_visit_scope(con,user,visit_id)
        if not con.execute("SELECT 1 FROM entities WHERE kind='visit' AND id=%s",(visit_id,)).fetchone(): raise HTTPException(404,'Sincronize o atendimento antes de enviar fotos')
        if con.execute('SELECT count(*) FROM visit_photos WHERE visit_id=%s',(visit_id,)).fetchone()[0]>=3: raise HTTPException(409,'Limite de três fotos por atendimento')
        con.execute('INSERT INTO visit_photos(id,visit_id,filename,content_type,content,uploaded_by) VALUES(%s,%s,%s,%s,%s,%s)',(photo_id,visit_id,name,content_type,content,user))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'visit_photo',%s,'upload')",(user,photo_id))
    return {'id':photo_id,'name':name}

@app.get('/api/visits/{visit_id}/photos/{photo_id}')
def download_visit_photo(visit_id: str, photo_id: str, authorization: str | None = Header(default=None)):
    user=auth(authorization);require_sector(user,'commercial','office')
    with db() as con:
        check_visit_scope(con,user,visit_id)
        row=con.execute('SELECT filename,content_type,content FROM visit_photos WHERE id=%s AND visit_id=%s',(photo_id,visit_id)).fetchone()
    if not row: raise HTTPException(404,'Foto não encontrada')
    safe_name=''.join(c if (c.isascii() and c.isalnum()) or c in ' ._-()' else '_' for c in row[0])
    return Response(content=bytes(row[2]),media_type=row[1],headers={'Content-Disposition':f'attachment; filename="{safe_name}"'})

def check_visit_scope(con,user,visit_id):
    row=con.execute("SELECT payload->>'clientId' FROM entities WHERE kind='visit' AND id=%s",(visit_id,)).fetchone()
    if not row: raise HTTPException(404,'Atendimento não encontrado')
    check_client_scope(con,user,row[0])


# Client repositories are virtual: every active client automatically has these four sections.
# Binary files stay in PostgreSQL and are never included in offline sync or JSON backups.
def client_document_access(con, client_id, user, category=None):
    check_client_scope(con,user,client_id)
    if not con.execute("SELECT 1 FROM entities WHERE kind='client' AND id=%s",(client_id,)).fetchone():
        raise HTTPException(404,'Cliente não encontrado')
    if category == 'finance':
        if 'finance' not in sectors_for(user):
            raise HTTPException(403,'Acesso financeiro restrito')
    elif not ({'commercial','office','finance'} & sectors_for(user)):
        raise HTTPException(403,'Acesso documental restrito')


@app.get('/api/admin/export/portable')
def export_portable(authorization: str | None = Header(default=None)):
    if auth(authorization)!='Ana Paula': raise HTTPException(403,'Exportação restrita à administradora')
    out=io.BytesIO()
    with db() as con, zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        # Only business data; never export password hashes, tokens or active sessions.
        bundle={'marco_zero':'2026-09-28','tables':{},'entities':[],'files':[]}
        for table,columns in (
            ('clientes','id,razao_social,nome_fantasia,documento,curva_abc,status,created_at,updated_at'),
            ('atendimentos','id,cliente_id,usuario_id,tipo_contato,observacao,data_atendimento,created_at,updated_at'),
            ('faturamento_pedidos','id,cliente_id,pedido_erp_id,valor_total,data_pedido,status_pedido,created_at,updated_at'),
            ('documentos_cliente','id,client_id,category,filename,content_type,note,numero_documento,caminho_arquivo,data_emissao,uploaded_by,created_at,updated_at')):
            names=columns.split(',')
            records=con.execute(f'SELECT {columns} FROM {table} ORDER BY id').fetchall()
            bundle['tables'][table]=[dict(zip(names,(v.isoformat() if isinstance(v,(date,datetime)) else str(v) if isinstance(v,Decimal) else v for v in row))) for row in records]
        for kind,entity_id,payload in con.execute('SELECT kind,id,payload FROM entities ORDER BY kind,id'):
            bundle['entities'].append({'kind':kind,'id':entity_id,'payload':payload})
        for path,content in con.execute('SELECT caminho_arquivo,content FROM documentos_cliente WHERE content IS NOT NULL ORDER BY id'):
            if not path or not path.startswith('/storage/clientes/'): continue
            safe=path.lstrip('/')
            archive.writestr(safe,bytes(content))
            bundle['files'].append(safe)
        archive.writestr('dados.json',json.dumps(bundle,ensure_ascii=False,indent=2,default=str))
        archive.writestr('LEIA-ME.txt','L2 ONE - Exportacao portavel\nMarco zero operacional: 28/09/2026.\nDados estruturados: dados.json (tabelas relacionais e entidades legadas).\nEstrutura PostgreSQL de referencia: schema.sql.\nArquivos: storage/clientes/{documento}/{categoria}/{ano_mes}/.\nDatas anteriores indisponiveis permanecem nulas nos registros operacionais normalizados.\nCredenciais e sessoes nao fazem parte desta exportacao.\n')
        archive.writestr('schema.sql',"""-- L2 ONE: esquema relacional de referência (PostgreSQL)
CREATE TABLE clientes (id TEXT PRIMARY KEY, razao_social VARCHAR(180) NOT NULL,
 nome_fantasia VARCHAR(180), documento VARCHAR(20) UNIQUE, curva_abc CHAR(1),
 status VARCHAR(12) NOT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL);
CREATE TABLE atendimentos (id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL REFERENCES clientes(id),
 usuario_id TEXT, tipo_contato VARCHAR(12), observacao TEXT, data_atendimento TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL);
CREATE TABLE faturamento_pedidos (id TEXT PRIMARY KEY, cliente_id TEXT NOT NULL REFERENCES clientes(id),
 pedido_erp_id VARCHAR(128), valor_total NUMERIC(15,2), data_pedido TIMESTAMPTZ,
 status_pedido VARCHAR(50) NOT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL);
CREATE TABLE documentos_cliente (id TEXT PRIMARY KEY, client_id TEXT NOT NULL REFERENCES clientes(id),
 category TEXT NOT NULL, filename TEXT, content_type TEXT, note TEXT, numero_documento VARCHAR(80),
 caminho_arquivo TEXT, data_emissao DATE, uploaded_by TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL);
-- Registros estão em dados.json; anexos nos caminhos listados em files.
""")
    out.seek(0)
    return Response(content=out.getvalue(),media_type='application/zip',
                    headers={'Content-Disposition':'attachment; filename="l2-one-portabilidade.zip"','Cache-Control':'no-store'})

@app.get('/api/clients/{client_id}/documents')
def list_documentos_cliente(client_id: str, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    with db() as con:
        client_document_access(con,client_id,user)
        rows=con.execute('SELECT id,category,filename,content_type,octet_length(content),note,data_emissao,uploaded_by,created_at FROM documentos_cliente WHERE client_id=%s ORDER BY created_at DESC,id',(client_id,)).fetchall()
    if 'finance' not in sectors_for(user):
        rows=[row for row in rows if row[1]!='finance']
    return [{'id':r[0],'category':r[1],'name':r[2],'type':r[3],'size':r[4] or 0,'note':r[5],'documentDate':r[6].isoformat() if r[6] else None,'uploadedBy':r[7],'createdAt':r[8].isoformat()} for r in rows]

@app.post('/api/clients/{client_id}/documents')
async def upload_client_document(client_id: str, category: str, file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user=auth(authorization)
    if category not in ('nfe','finance','contract'): raise HTTPException(400,'Categoria inválida')
    name=Path(file.filename or '').name.strip()[:180]
    content=await file.read(10*1024*1024+1)
    if not name or not content or len(content)>10*1024*1024: raise HTTPException(400,'Arquivo vazio ou maior que 10 MB')
    if content.startswith(b'%PDF-') and name.lower().endswith('.pdf'): content_type='application/pdf'
    elif content.startswith(b'\xff\xd8\xff') and name.lower().endswith(('.jpg','.jpeg')): content_type='image/jpeg'
    elif content.startswith(b'\x89PNG\r\n\x1a\n') and name.lower().endswith('.png'): content_type='image/png'
    elif category=='nfe' and name.lower().endswith('.xml'):
        try:
            from defusedxml import ElementTree
            ElementTree.fromstring(content)
        except Exception:
            raise HTTPException(400,'XML inválido ou inseguro')
        content_type='application/xml'
    else: raise HTTPException(400,'Envie PDF, JPG ou PNG; XML é permitido apenas para NFE')
    doc_id=secrets.token_hex(16)
    with db() as con:
        client_document_access(con,client_id,user,category)
        row=con.execute("SELECT nullif(documento,'') FROM clientes WHERE id=%s",(client_id,)).fetchone()
        directory=re.sub(r'[^A-Za-z0-9_-]','',row[0] or client_id)
        category_dir={'nfe':'nfe','finance':'boleto','contract':'contrato'}[category]
        folder=f"/storage/clientes/{directory}/{category_dir}/{date.today().strftime('%Y_%m')}/"
        path=folder+doc_id+'_'+re.sub(r'[^A-Za-z0-9._-]','_',name)
        con.execute('INSERT INTO documentos_cliente(id,client_id,category,filename,content_type,content,caminho_arquivo,uploaded_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(doc_id,client_id,category,name,content_type,content,path,user))
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'client_document',doc_id,'upload'))
    return {'id':doc_id,'name':name,'category':category}

class ClientNote(BaseModel):
    text: str

@app.post('/api/clients/{client_id}/notes')
def add_client_note(client_id: str, data: ClientNote, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    note=data.text.strip()
    if not note or len(note)>4000: raise HTTPException(400,'Anotação deve conter de 1 a 4000 caracteres')
    doc_id=secrets.token_hex(16)
    with db() as con:
        client_document_access(con,client_id,user,'note')
        con.execute("INSERT INTO documentos_cliente(id,client_id,category,note,uploaded_by) VALUES(%s,%s,'note',%s,%s)",(doc_id,client_id,note,user))
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'client_note',doc_id,'create'))
    return {'id':doc_id,'category':'note'}

@app.get('/api/clients/{client_id}/documents/{doc_id}')
def download_client_document(client_id: str, doc_id: str, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    with db() as con:
        row=con.execute('SELECT category,filename,content_type,content FROM documentos_cliente WHERE id=%s AND client_id=%s',(doc_id,client_id)).fetchone()
        if not row or row[0]=='note': raise HTTPException(404,'Documento não encontrado')
        client_document_access(con,client_id,user,row[0])
    safe_name=''.join(c if (c.isascii() and c.isalnum()) or c in ' ._-()' else '_' for c in row[1])
    return Response(content=bytes(row[3]),media_type=row[2],headers={'Content-Disposition':f'attachment; filename="{safe_name}"','X-Content-Type-Options':'nosniff'})

@app.delete('/api/clients/{client_id}/documents/{doc_id}')
def delete_client_document(client_id: str, doc_id: str, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    with db() as con:
        row=con.execute('SELECT category,uploaded_by FROM documentos_cliente WHERE id=%s AND client_id=%s',(doc_id,client_id)).fetchone()
        if not row: raise HTTPException(404,'Registro não encontrado')
        client_document_access(con,client_id,user,row[0])
        if user!='Ana Paula' and user!=row[1]: raise HTTPException(403,'Somente o autor ou a administradora podem excluir')
        con.execute('DELETE FROM documentos_cliente WHERE id=%s AND client_id=%s',(doc_id,client_id))
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'client_document',doc_id,'delete'))
    return {'deleted':True}

@app.get('/api/orders/{order_id}/attachments')
def list_order_attachments(order_id: str, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'commercial','office')
    with db() as con:
        order_exists(con,order_id)
        check_order_scope(con,user,order_id)
        rows=con.execute('SELECT id,filename,content_type,octet_length(content),uploaded_by,created_at FROM order_attachments WHERE order_id=%s ORDER BY created_at,id',(order_id,)).fetchall()
    return [{'id':r[0],'name':r[1],'type':r[2],'size':r[3],'uploadedBy':r[4],'createdAt':r[5].isoformat()} for r in rows]

@app.get('/api/orders/{order_id}/invoices')
def list_order_invoices(order_id: str, authorization: str | None = Header(default=None)):
    user=auth(authorization);require_sector(user,'commercial','office')
    with db() as con:
        check_order_scope(con,user,order_id)
        rows=con.execute('SELECT id,reconciliation,uploaded_by,created_at FROM order_invoices WHERE order_id=%s ORDER BY created_at DESC',(order_id,)).fetchall()
    return [dict(row[1],id=row[0],uploadedBy=row[2],createdAt=row[3].isoformat()) for row in rows]

@app.post('/api/orders/{order_id}/invoices')
async def upload_order_invoice(order_id: str, file: UploadFile = File(...), brand: str = Form(...), authorization: str | None = Header(default=None)):
    user=auth(authorization);require_sector(user,'commercial','office')
    name=Path(file.filename or '').name.strip()[:180]
    if not name.lower().endswith('.xml'): raise HTTPException(400,'Envie o XML da NF-e para confronto automático')
    content=await file.read(5*1024*1024+1)
    if not content or len(content)>5*1024*1024: raise HTTPException(400,'XML vazio ou acima de 5 MB')
    with db() as con:
        check_order_scope(con,user,order_id)
        order=con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s",(order_id,)).fetchone()[0]
        customer=con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s",(order.get('clientId'),)).fetchone()
        recipient=re.sub(r'\D','',str(customer[0].get('taxId') or '')) if customer else ''
        try: report=reconcile_invoice(order,content,brand.strip(),recipient or None)
        except InvoiceError as exc: raise HTTPException(400,str(exc)) from exc
        if con.execute('SELECT 1 FROM order_invoices WHERE order_id=%s AND brand=%s AND invoice_number=%s',(order_id,report['brand'],report['invoiceNumber'])).fetchone():
            raise HTTPException(409,'Esta NF-e já foi confrontada com o pedido')
        invoice_id=secrets.token_hex(16)
        con.execute('INSERT INTO order_invoices(id,order_id,brand,invoice_number,filename,content,reconciliation,uploaded_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                    (invoice_id,order_id,report['brand'],report['invoiceNumber'],name,content,Jsonb(report),user))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'order_invoice',%s,'reconciled')",(user,invoice_id))
    return dict(report,id=invoice_id)

@app.post('/api/orders/{order_id}/attachments')
async def upload_order_attachment(order_id: str, file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'commercial','office')
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
        check_order_scope(con,user,order_id)
        count=con.execute('SELECT count(*) FROM order_attachments WHERE order_id=%s',(order_id,)).fetchone()[0]
        if count>=5: raise HTTPException(409,'Limite de cinco comprovantes por pedido')
        con.execute('INSERT INTO order_attachments(id,order_id,filename,content_type,content,uploaded_by) VALUES(%s,%s,%s,%s,%s,%s)',(attachment_id,order_id,name,content_type,content,user))
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'order_attachment',attachment_id,'upload'))
    return {'id':attachment_id,'name':name,'type':content_type,'size':len(content)}

@app.get('/api/orders/{order_id}/attachments/{attachment_id}')
def download_order_attachment(order_id: str, attachment_id: str, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'commercial','office')
    with db() as con:
        check_order_scope(con,user,order_id)
        row=con.execute('SELECT filename,content_type,content FROM order_attachments WHERE id=%s AND order_id=%s',(attachment_id,order_id)).fetchone()
    if not row: raise HTTPException(404,'Comprovante não encontrado')
    safe_name=''.join(c if (c.isascii() and c.isalnum()) or c in ' ._-()' else '_' for c in row[0])
    return Response(content=bytes(row[2]),media_type=row[1],headers={'Content-Disposition':f'attachment; filename="{safe_name}"'})

@app.delete('/api/orders/{order_id}/attachments/{attachment_id}')
def delete_order_attachment(order_id: str, attachment_id: str, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'commercial','office')
    with db() as con:
        check_order_scope(con,user,order_id)
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
    if not admin_access(auth(authorization)): raise HTTPException(403,'Acesso restrito à administradora')
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
    if not admin_access(user): raise HTTPException(403,'Acesso restrito à administradora')
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

@app.get('/api/reports/daily.pdf')
def daily_pdf(authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'finance')
    with db() as con:
        content=build_pdf(read_data(con),datetime.now(TZ).date())
    return Response(content=content,media_type='application/pdf',headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="L2_ONE_relatorio_diario.pdf"'})

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
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(self), microphone=(), geolocation=(self)'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self' https:; img-src 'self' data: https://tile.openstreetmap.org; frame-ancestors 'none'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.get('/')
@app.get('/index.html')
def home(): return FileResponse(BASE/'index.html',headers={'Cache-Control':'no-store'})

@app.get('/zara.html')
def zara_page():
    return FileResponse(BASE/'zara.html', headers={'Cache-Control':'no-store'})

@app.get('/{filename}')
def asset(filename: str):
    if filename not in ('strategy_ui.css','leaflet.js','leaflet.css','strategic_crm.js','app.js','live_records.js','crm_operations.js','goals_dashboard.js', 'commercial_drafts.js','workspace.js','finance360.js','cash.js','personal-finance.js','sw.js','manifest.json','logo-l2.jpeg','logo-l2-light.jpg','logo-l2-dark.jpg','logo-data.js','icon-192.png','icon-512.png','apple-touch-icon.png'):
        raise HTTPException(404)
    if filename in ('logo-l2-light.jpg','logo-l2-dark.jpg'):
        source={'logo-l2-light.jpg':'logo-light.jpg.b64','logo-l2-dark.jpg':'logo-dark.jpg.b64'}[filename]
        return Response(content=base64.b64decode((BASE/source).read_text()), media_type='image/jpeg', headers={'Cache-Control':'no-store'})
    if filename in ('icon-192.png','icon-512.png','apple-touch-icon.png'):
        return Response(content=base64.b64decode((BASE/(filename+'.b64')).read_text()), media_type='image/png', headers={'Cache-Control':'public, max-age=86400'})
    return FileResponse(BASE/filename,headers={'Cache-Control':'no-store'})

from personal_finance import initialize_personal, install_personal
install_personal(app, db, auth)


@app.get('/api/commercial/lead-conversion')
def commercial_lead_conversion(month: str, authorization: str | None = Header(default=None)):
    from lead_conversion import conversion_metrics
    import re
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):raise HTTPException(422,'Mês inválido')
    user=auth(authorization)
    require_sector(user,'commercial','office')
    with db() as con:
        all_users=not is_seller(con,user)
        leads=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='lead' AND (%s OR payload->>'owner'=%s)",(all_users,user))]
        rows=con.execute("SELECT l.id,a.tax_id,l.owner,l.created_at,l.first_response_at,l.status,l.custom_fields FROM capture_leads l LEFT JOIN capture_accounts a ON a.id=l.account_id WHERE (%s OR l.owner=%s)",(all_users,user)).fetchall()
        leads += [{'id':r[0],'taxId':r[1],'owner':r[2],'createdAt':r[3].isoformat(),'firstResponseAt':r[4].isoformat() if r[4] else None,'status':r[5],'clientId':(r[6] or {}).get('clientId')} for r in rows]
        return conversion_metrics(leads,scoped_rows(con,'client',user),scoped_rows(con,'order',user),month)

from financial_inputs import FinancialInput

@app.get('/api/finance/inputs')
def financial_inputs_list(authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'finance')
    with db() as con:
        return [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='financial_input' ORDER BY updated_at,id")]

@app.put('/api/finance/inputs')
def financial_inputs_save(data: FinancialInput, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'finance')
    payload=data.model_dump(mode='json')
    payload['updatedBy']=user
    payload['updatedAt']=datetime.now(TZ).isoformat()
    with db() as con:
        con.execute("INSERT INTO entities(kind,id,payload) VALUES ('financial_input',%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=EXCLUDED.payload,updated_at=now()",(data.id,Jsonb(payload)))
    return payload

@app.get('/api/finance/analysis')
def financial_analysis_read(month: str, authorization: str | None = Header(default=None)):
    from financial_analysis import analyze_finance
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):raise HTTPException(422,'Mês inválido')
    user=auth(authorization)
    require_sector(user,'finance')
    with db() as con:
        inputs=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='financial_input'")]
        return analyze_finance(inputs,scoped_rows(con,'order',user),scoped_rows(con,'office_finance',user),scoped_rows(con,'commission_rate',user),month,scoped_rows(con,'goal',user))


def correct_order_18_date():
    # This data correction belongs exclusively to the authorized homologation service.
    if os.getenv('RENDER_SERVICE_ID') != 'srv-daqk8ifavr4c738m78f0':
        return
    order_id = '858c6713-4712-4873-b716-8f5e76425468'
    with db() as con:
        row = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s FOR UPDATE", (order_id,)).fetchone()
        seed_text = os.getenv('L2_HOMOLOGATION_ORDER18_SEED', '')
        if not row and seed_text and os.getenv('L2_ENABLE_HOMOLOGATION_SEED') == '1':
            # Carga privada da operação para esta instância; não vai para Git.
            seed = json.loads(seed_text)
            order, customer = seed['order'], seed['client']
            if order.get('id') != order_id or str(order.get('orderNumber')) != '18' or customer.get('id') != order.get('clientId'):
                raise RuntimeError('Carga de homologação do pedido 18 incompatível')
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('client',%s,%s) ON CONFLICT DO NOTHING", (customer['id'], Jsonb(customer)))
            con.execute("""INSERT INTO clientes(id,razao_social,nome_fantasia,documento,curva_abc)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (customer['id'], customer['name'], customer.get('tradeName'), customer.get('taxId'), customer.get('abc') if customer.get('abc') in ('A','B','C') else None))
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('order',%s,%s) ON CONFLICT DO NOTHING", (order_id, Jsonb(order)))
            number = con.execute("SELECT coalesce(max((payload->>'orderNumber')::integer),0) FROM entities WHERE kind='order' AND payload->>'orderNumber' ~ '^[0-9]+$'").fetchone()[0]
            con.execute('UPDATE order_counter SET value=greatest(value,%s) WHERE id=1', (number,))
            row = con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s FOR UPDATE", (order_id,)).fetchone()
        if not row:
            logging.getLogger('uvicorn.error').warning('order18_migration status=absent')
            return
        payload = dict(row[0])
        if payload.get('date') != '2026-09-29':
            payload['dateCorrection'] = {'original': payload.get('date'), 'corrected':'2026-09-29', 'reason':'Correção expressamente autorizada pela administração'}
            payload['date'] = '2026-09-29'
            con.execute("UPDATE entities SET payload=%s,updated_at=now() WHERE kind='order' AND id=%s", (Jsonb(payload), order_id))
            con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES('Ana Paula','order',%s,'date_correction_2026_09_29')", (order_id,))
        project_order(con, order_id, payload)
        verified = con.execute("SELECT payload->>'date' FROM entities WHERE kind='order' AND id=%s", (order_id,)).fetchone()
        if not verified or verified[0] != '2026-09-29':
            raise RuntimeError('Falha na conferência interna da migração do pedido 18')
    logging.getLogger('uvicorn.error').info('order18_migration status=verified date=2026-09-29')


@app.post('/api/catalog/import-pdf/preview')
async def preview_price_pdf(file: UploadFile = File(...), authorization: str | None = Header(default=None)):
    user = auth(authorization)
    if user not in CATALOG_EDITORS or 'catalog' not in sectors_for(user):
        raise HTTPException(403, 'Importação de preços restrita aos editores do catálogo')
    content = await file.read(10 * 1024 * 1024 + 1)
    from pdf_price_import import extract_price_candidates
    try:
        return await asyncio.to_thread(extract_price_candidates, content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, 'Não foi possível extrair esta tabela. Confira o PDF ou use CSV.') from exc


@app.get('/api/commercial/activity')
def commercial_activity(authorization: str | None = Header(default=None)):
    user = auth(authorization)
    require_sector(user, 'commercial', 'office')
    monitoring = user in ('Ana Paula', 'Marlene')
    with db() as con:
        def visible(kind):
            if monitoring:
                return [{**row[1], 'id': row[0]} for row in con.execute('SELECT id,payload FROM entities WHERE kind=%s ORDER BY updated_at,id', (kind,))]
            return [{**x, 'id': x.get('id') or (price_id(x) if kind == 'price' else '')} for x in scoped_rows(con, kind, user)]
        clients = {x['id']: x for x in visible('client')}
        permissions = sectors_for(user)
        kinds = ['client', 'visit', 'interaction', 'task', 'opportunity', 'order', 'route', 'fulfillment']
        if 'office' in permissions: kinds += ['office_action', 'office_commercial', 'office_administrative']
        if 'catalog' in permissions: kinds += ['price_table', 'price', 'industry']
        allowed = set()
        for kind in kinds:
            for x in visible(kind):
                if kind.startswith('office_') and 'finance' not in permissions and str(x.get('Área', '')).casefold() == 'financeiro': continue
                if kind in ('client', 'price_table', 'price', 'industry', 'office_action', 'office_commercial', 'office_administrative') or x.get('clientId') in clients or (kind == 'task' and not x.get('clientId') and (monitoring or not is_seller(con, user) or x.get('user') == user)):
                    if x.get('id'): allowed.add((kind, x['id']))
        event_kinds = kinds + ['whatsapp_media', 'whatsapp_document']
        events, audited = [], set()
        query = "SELECT a.username,a.kind,a.entity_id,a.action,a.created_at,e.payload FROM audit_log a JOIN entities e ON e.kind=CASE WHEN a.kind IN ('whatsapp_media','whatsapp_document') THEN 'client' ELSE a.kind END AND e.id=a.entity_id WHERE a.kind=ANY(%s) ORDER BY a.id DESC LIMIT 2000"
        for actor, kind, entity_id, action, at, payload in con.execute(query, (event_kinds,)):
            access_kind = 'client' if kind in ('whatsapp_media', 'whatsapp_document') else kind
            if (access_kind, entity_id) not in allowed: continue
            audited.add((access_kind, entity_id))
            cid = entity_id if access_kind == 'client' else payload.get('clientId')
            events.append({'entityId': entity_id, 'user': actor, 'kind': kind, 'action': action, 'at': at.isoformat(), 'clientName': clients.get(cid, {}).get('name', '') or str(payload.get('Demanda ou problema') or payload.get('Demanda') or payload.get('title') or payload.get('brand') or '')})
        # Imported and older records may predate the audit feed. Show their actual
        # persisted update time, explicitly identified as existing records.
        for kind, entity_id, payload, at in con.execute('SELECT kind,id,payload,updated_at FROM entities WHERE kind=ANY(%s) ORDER BY updated_at DESC LIMIT 2000', (kinds,)):
            if (kind, entity_id) not in allowed or (kind, entity_id) in audited: continue
            cid = entity_id if kind == 'client' else payload.get('clientId')
            events.append({'entityId': entity_id, 'user': payload.get('updatedBy') or payload.get('enteredBy') or payload.get('user') or 'Autoria não informada', 'kind': kind, 'action': 'existing', 'at': at.isoformat(), 'clientName': clients.get(cid, {}).get('name', '') or str(payload.get('Demanda ou problema') or payload.get('Demanda') or payload.get('title') or payload.get('brand') or '')})
    return sorted(events, key=lambda x: x['at'], reverse=True)[:100]


class CommercialDraft(BaseModel):
    key: str
    payload: dict | None = None
    updatedAt: int

@app.get('/api/commercial/drafts')
def commercial_drafts_list(authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user, 'commercial', 'office')
    with db() as con:
        return [{'key':r[0],'payload':r[1],'updatedAt':r[2]} for r in con.execute('SELECT draft_key,payload,client_timestamp FROM commercial_drafts WHERE username=%s ORDER BY updated_at DESC LIMIT 100',(user,))]

@app.put('/api/commercial/drafts')
def commercial_drafts_save(data: CommercialDraft, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user, 'commercial', 'office')
    if not re.fullmatch(r'(order|visit|interaction):[A-Za-z0-9_.-]{1,128}',data.key) or not 0<data.updatedAt<=int(datetime.now(TZ).timestamp()*1000)+300000:
        raise HTTPException(422,'Identificação ou data do rascunho inválida')
    if len(json.dumps(data.payload,ensure_ascii=False).encode())>100000:
        raise HTTPException(413,'Rascunho acima de 100 KB')
    with db() as con:
        if data.payload:
            fields=data.payload.get('fields',{})
            if not isinstance(fields,dict) or any(re.search(r'token|password|secret|senha',k,re.I) for k in fields):
                raise HTTPException(422,'Campos não permitidos no rascunho')
            cid=fields.get('clientId') or (data.payload.get('order') or {}).get('clientId')
            if cid:check_client_scope(con,user,cid)
        con.execute("INSERT INTO commercial_drafts(username,draft_key,payload,client_timestamp) VALUES(%s,%s,%s,%s) ON CONFLICT(username,draft_key) DO UPDATE SET payload=excluded.payload,client_timestamp=excluded.client_timestamp,updated_at=now() WHERE commercial_drafts.client_timestamp<excluded.client_timestamp",(user,data.key,Jsonb(data.payload),data.updatedAt))
    return {'saved':True}


class WhatsAppText(BaseModel):
    clientId: str
    message: str

@app.post('/api/whatsapp/text')
async def send_whatsapp_text(data: WhatsAppText, authorization: str | None = Header(default=None)):
    user=auth(authorization)
    require_sector(user,'commercial','office')
    if not provider_config():raise HTTPException(503,'API oficial do WhatsApp não configurada')
    if not data.message.strip() or len(data.message)>4096:raise HTTPException(422,'Mensagem vazia ou acima de 4.096 caracteres')
    with db() as con:
        check_client_scope(con,user,data.clientId)
        row=con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s",(data.clientId,)).fetchone()
        if not row:raise HTTPException(404,'Cliente não encontrado')
        phone=re.sub(r'\D','',str(row[0].get('phone') or ''))
        if len(phone) in (10,11):phone='55'+phone
        if not re.fullmatch(r'55\d{10,11}',phone):raise HTTPException(422,'Cadastre um telefone brasileiro com DDD')
    try:
        mid=await asyncio.to_thread(send_text,phone,data.message)
    except MediaError as exc:
        raise HTTPException(502,str(exc)) from exc
    try:
        with db() as con:
            event_id=secrets.token_hex(16)
            payload={'id':event_id,'clientId':data.clientId,'type':'WhatsApp','text':data.message,'owner':user,'at':datetime.now(TZ).isoformat(),'messageId':mid,'deliveryStatus':'accepted'}
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('interaction',%s,%s)",(event_id,Jsonb(payload)))
            project_attendance(con,'interaction',event_id,payload)
            con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'interaction',%s,'whatsapp_text_accepted')",(user,event_id))
    except Exception:
        logging.getLogger('uvicorn.error').error('whatsapp_text accepted history_failed')
        return {'status':'accepted','messageId':mid,'historyRecorded':False}
    return {'status':'accepted','messageId':mid,'historyRecorded':True}


@app.get('/api/records/events')
async def records_events(request: Request, authorization: str | None = Header(default=None)):
    # The channel carries only an invalidation signal, never customer data or secrets.
    await asyncio.to_thread(auth, authorization)
    async def messages():
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as con:
            await con.execute('LISTEN l2_records_changed')
            yield 'event: ready\ndata: {}\n\n'
            while not await request.is_disconnected():
                received=False
                async for _ in con.notifies(timeout=25, stop_after=1):
                    received=True
                try:
                    await asyncio.to_thread(auth, authorization)
                except HTTPException:
                    break
                yield 'event: records\ndata: {}\n\n' if received else ': heartbeat\n\n'
    return StreamingResponse(messages(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


def notify_record_change():
    with db() as con:
        con.execute("SELECT pg_notify('l2_records_changed', '')")

@app.middleware('http')
async def record_change_notifications(request: Request, call_next):
    response=await call_next(request)
    # Draft saves are private, and cannot recursively invalidate business records.
    if request.method in ('POST','PUT','PATCH','DELETE') and response.status_code<400 and request.url.path.startswith('/api/') and request.url.path not in ('/api/sync','/api/login','/api/logout','/api/commercial/drafts'):
        try:
            await asyncio.to_thread(notify_record_change)
        except Exception:
            logging.warning('record_notification_unavailable')
    return response
