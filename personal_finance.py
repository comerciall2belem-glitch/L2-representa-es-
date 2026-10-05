"""Online-only household finance. Never stored in corporate sync entities."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
import json
import re
import unicodedata
import uuid

from fastapi import APIRouter, Header, HTTPException, UploadFile, File, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator

MEMBERS = frozenset({'Ana Paula', 'Euler'})
HOUSEHOLD = 'ana-paula-euler'
DEFAULT_CATEGORIES = ['Moradia', 'Alimentação Pessoal', 'Saúde', 'Lazer',
                      'Veículo Particular', 'Educação', 'Investimentos Pessoais',
                      'Pró-labore', 'Distribuição de Lucros', 'Outras Receitas', 'Outras Despesas']

def normalize(value):
    return re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in unicodedata.normalize('NFD', str(value))
                  if not '\u0300' <= c <= '\u036f').lower()).strip()

def validate_money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Valor inválido')
    if not amount.is_finite() or amount <= 0 or amount > Decimal('1000000000') or amount.as_tuple().exponent < -2:
        raise ValueError('Informe valor positivo com até duas casas decimais')
    return amount

class NamedItem(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    active: bool = True
    kind: Literal['Banco', 'Dinheiro', 'Cartão'] = 'Banco'
    version: int = Field(default=0, ge=0)
    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value: raise ValueError('Nome obrigatório')
        return value

class Transaction(BaseModel):
    date: date
    type: Literal['Receita Pessoal', 'Despesa Pessoal']
    category_id: str = Field(min_length=1, max_length=100)
    account_id: str = Field(min_length=1, max_length=100)
    amount: Decimal
    status: Literal['Pago', 'Pendente']
    due_date: date | None = None
    paid_date: date | None = None
    person: Literal['Ana Paula', 'Euler', 'Casal'] = 'Casal'
    withdrawal_id: str | None = Field(default=None, max_length=128)
    notes: str = Field(default='', max_length=4000)
    version: int = Field(default=0, ge=0)
    @field_validator('amount')
    @classmethod
    def money(cls, value): return validate_money(value)
    @model_validator(mode='after')
    def validate_dates(self):
        if self.status == 'Pago' and self.paid_date is None: raise ValueError('Data de pagamento/recebimento obrigatória')
        if self.status == 'Pendente' and self.paid_date is not None: raise ValueError('Pendente não pode ter data de pagamento')
        if self.withdrawal_id and (self.type != 'Receita Pessoal' or self.status != 'Pago'):
            raise ValueError('Vincule retiradas somente a receitas recebidas')
        return self

def initialize_personal(db):
    with db() as con:
        con.execute('CREATE SCHEMA IF NOT EXISTS personal_finance')
        con.execute('REVOKE ALL ON SCHEMA personal_finance FROM PUBLIC')
        con.execute("SELECT set_config('l2.personal_actor','Ana Paula',true)")
        for table in ('categories', 'accounts'):
            con.execute(f'''CREATE TABLE IF NOT EXISTS personal_finance.{table} (
                id TEXT PRIMARY KEY, household_id TEXT NOT NULL, name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'Banco', active BOOLEAN NOT NULL DEFAULT true,
                version INTEGER NOT NULL DEFAULT 1, UNIQUE(household_id,name))''')
        con.execute('''CREATE TABLE IF NOT EXISTS personal_finance.transactions (
            id TEXT PRIMARY KEY, household_id TEXT NOT NULL, date DATE NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('Receita Pessoal','Despesa Pessoal')),
            category_id TEXT NOT NULL REFERENCES personal_finance.categories(id),
            account_id TEXT NOT NULL REFERENCES personal_finance.accounts(id),
            amount NUMERIC(18,2) NOT NULL CHECK(amount>0), status TEXT NOT NULL CHECK(status IN ('Pago','Pendente')),
            due_date DATE, paid_date DATE, person TEXT NOT NULL,
            withdrawal_id TEXT, withdrawal_snapshot JSONB, notes TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL, updated_by TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ, CHECK((status='Pago')=(paid_date IS NOT NULL)),
            CHECK(withdrawal_id IS NULL OR (type='Receita Pessoal' AND status='Pago')))''')
        con.execute('''CREATE TABLE IF NOT EXISTS personal_finance.attachments (
            id TEXT PRIMARY KEY, household_id TEXT NOT NULL,
            transaction_id TEXT NOT NULL REFERENCES personal_finance.transactions(id),
            filename TEXT NOT NULL, content_type TEXT NOT NULL, content BYTEA NOT NULL,
            uploaded_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('''CREATE TABLE IF NOT EXISTS personal_finance.audit_events (
            id BIGSERIAL PRIMARY KEY, household_id TEXT NOT NULL, actor TEXT NOT NULL,
            entity_id TEXT NOT NULL, action TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
        con.execute('CREATE INDEX IF NOT EXISTS personal_transaction_period ON personal_finance.transactions(household_id,paid_date,date) WHERE deleted_at IS NULL')
        con.execute('CREATE INDEX IF NOT EXISTS personal_withdrawal_links ON personal_finance.transactions(withdrawal_id) WHERE deleted_at IS NULL')
        for table in ('categories', 'accounts', 'transactions', 'attachments', 'audit_events'):
            con.execute(f'REVOKE ALL ON personal_finance.{table} FROM PUBLIC')
            con.execute(f'ALTER TABLE personal_finance.{table} ENABLE ROW LEVEL SECURITY')
            con.execute(f'ALTER TABLE personal_finance.{table} FORCE ROW LEVEL SECURITY')
            exists = con.execute('SELECT 1 FROM pg_policies WHERE schemaname=%s AND tablename=%s AND policyname=%s',
                                 ('personal_finance',table,'household_members')).fetchone()
            if not exists:
                con.execute(f'''CREATE POLICY household_members ON personal_finance.{table}
                    USING (household_id='ana-paula-euler' AND current_setting('l2.personal_actor',true) IN ('Ana Paula','Euler'))
                    WITH CHECK (household_id='ana-paula-euler' AND current_setting('l2.personal_actor',true) IN ('Ana Paula','Euler'))''')
        for name in DEFAULT_CATEGORIES:
            con.execute('INSERT INTO personal_finance.categories(id,household_id,name) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                        ('category-'+normalize(name).replace(' ','-'),HOUSEHOLD,name))
        con.execute("INSERT INTO personal_finance.accounts(id,household_id,name,kind) VALUES('account-cash',%s,'Dinheiro pessoal','Dinheiro') ON CONFLICT DO NOTHING",(HOUSEHOLD,))

def withdrawal_candidate(entity_id, payload):
    """Only explicitly classified, paid corporate cash withdrawals for this couple."""
    if payload.get('type') != 'Saída': return None
    category = normalize(payload.get('category',''))
    kind = 'Pró-labore' if category.startswith('pro labore') else 'Distribuição de Lucros' if category.startswith(('distribuicao de lucros','dividendos')) else None
    beneficiary = normalize(payload.get('beneficiary') or payload.get('person') or '')
    label = normalize(str(payload.get('description',''))+' '+category)
    if not kind or not (beneficiary in ('ana paula','euler','casal') or 'ana paula' in label or 'euler' in label or 'casal' in label): return None
    try:
        amount = validate_money(payload.get('amount'))
        when = date.fromisoformat(payload.get('date',''))
    except (ValueError, TypeError): return None
    return {'id':entity_id,'kind':kind,'date':when.isoformat(),'amount':str(amount),
            'description':str(payload.get('description',''))[:500]}

def period_summary(rows, withdrawals, start, end):
    paid = [x for x in rows if x['status']=='Pago' and start <= x['paid_date'] <= end]
    income = sum((Decimal(str(x['amount'])) for x in paid if x['type']=='Receita Pessoal'),Decimal(0))
    expenses = sum((Decimal(str(x['amount'])) for x in paid if x['type']=='Despesa Pessoal'),Decimal(0))
    pending = sum((Decimal(str(x['amount'])) for x in rows if x['type']=='Despesa Pessoal' and x['status']=='Pendente'
                   and start <= (x['due_date'] or x['date']) <= end),Decimal(0))
    corporate = sum((Decimal(x['amount']) for x in withdrawals if start <= x['date'] <= end),Decimal(0))
    by_category = {}
    for x in paid:
        if x['type']=='Despesa Pessoal': by_category[x['category_id']] = by_category.get(x['category_id'],Decimal(0))+Decimal(str(x['amount']))
    return {'income':str(income),'expenses':str(expenses),'pending':str(pending),'withdrawn':str(corporate),
            'net':str(income-expenses),'withdrawal_difference':str(corporate-expenses),
            'categories':{k:str(v) for k,v in by_category.items()}}

def install_personal(app, db, auth):
    router = APIRouter(prefix='/api/personal-finance')
    def authorize(header):
        user = auth(header)
        if user not in MEMBERS: raise HTTPException(403,'Financeiro Pessoal exclusivo de Ana Paula e Euler')
        return user
    @contextmanager
    def connection(user):
        with db() as con:
            con.execute("SELECT set_config('l2.personal_actor',%s,true)",(user,))
            yield con
    def audit(con,user,entity_id,action):
        con.execute('INSERT INTO personal_finance.audit_events(household_id,actor,entity_id,action) VALUES(%s,%s,%s,%s)',(HOUSEHOLD,user,entity_id,action))
    def get_transaction(con,entity_id,include_deleted=False):
        row = con.execute('SELECT row_to_json(t) FROM personal_finance.transactions t WHERE id=%s AND household_id=%s'+('' if include_deleted else ' AND deleted_at IS NULL'),(entity_id,HOUSEHOLD)).fetchone()
        if not row: raise HTTPException(404,'Lançamento não encontrado')
        return row[0]
    def withdrawals(con):
        return [v for i,p in con.execute("SELECT id,payload FROM entities WHERE kind='cash_entry'").fetchall() if (v:=withdrawal_candidate(i,p))]
    @router.get('/overview')
    def overview(start: date, end: date, category: str='', account: str='', authorization: str | None=Header(default=None)):
        user=authorize(authorization)
        if end<start or (end-start).days>3660: raise HTTPException(400,'Período inválido ou superior a dez anos')
        with connection(user) as con:
            categories=[r[0] for r in con.execute('SELECT row_to_json(t) FROM personal_finance.categories t ORDER BY name')]
            accounts=[r[0] for r in con.execute('SELECT row_to_json(t) FROM personal_finance.accounts t ORDER BY name')]
            rows=[r[0] for r in con.execute('''SELECT row_to_json(t) FROM personal_finance.transactions t
                WHERE household_id=%s AND deleted_at IS NULL AND (%s='' OR category_id=%s)
                AND (%s='' OR account_id=%s) AND coalesce(paid_date,due_date,date) BETWEEN %s AND %s ORDER BY date DESC,id''',
                (HOUSEHOLD,category,category,account,account, start,end))]
            source=withdrawals(con)
            links=[(r[0],r[1]) for r in con.execute('SELECT withdrawal_id,sum(amount) FROM personal_finance.transactions WHERE withdrawal_id IS NOT NULL AND deleted_at IS NULL GROUP BY withdrawal_id')]
            allocated=dict(links)
            available=[dict(x,available=str(max(Decimal(0),Decimal(x['amount'])-allocated.get(x['id'],Decimal(0))))) for x in source]
            for row in rows:
                if row['withdrawal_id']:
                    actual=next((x for x in source if x['id']==row['withdrawal_id']),None)
                    row['reconciliation_warning']=not actual or actual != row['withdrawal_snapshot'] or allocated.get(actual['id'],0)>Decimal(actual['amount'])
                row['attachments']=[r[0] for r in con.execute('SELECT json_build_object(\'id\',id,\'name\',filename) FROM personal_finance.attachments WHERE transaction_id=%s',(row['id'],))]
            audit(con,user,HOUSEHOLD,'view')
        return {'categories':categories,'accounts':accounts,'transactions':rows,'withdrawals':available,
                'summary':period_summary(rows,source,start.isoformat(),end.isoformat()),'shared_with':['Ana Paula','Euler']}
    def save_named(kind, data, entity_id, user):
        table='categories' if kind=='categories' else 'accounts'
        with connection(user) as con:
            if con.execute(f'SELECT 1 FROM personal_finance.{table} WHERE household_id=%s AND name=%s AND id<>%s',(HOUSEHOLD,data.name,entity_id or '')).fetchone(): raise HTTPException(409,'Nome já cadastrado')
            if entity_id:
                result=con.execute(f'UPDATE personal_finance.{table} SET name=%s,kind=%s,active=%s,version=version+1 WHERE id=%s AND household_id=%s AND version=%s RETURNING id',
                    (data.name,data.kind,data.active,entity_id,HOUSEHOLD,data.version)).fetchone()
                if not result: raise HTTPException(409,'Cadastro alterado por outro sócio; atualize a tela')
            else:
                entity_id=str(uuid.uuid4())
                if con.execute(f'SELECT 1 FROM personal_finance.{table} WHERE household_id=%s AND name=%s',(HOUSEHOLD,data.name)).fetchone(): raise HTTPException(409,'Nome já cadastrado')
                con.execute(f'INSERT INTO personal_finance.{table}(id,household_id,name,kind,active) VALUES(%s,%s,%s,%s,%s)',(entity_id,HOUSEHOLD,data.name,data.kind,data.active))
            audit(con,user,entity_id,'save_'+kind)
        return {'id':entity_id}
    @router.post('/categories')
    def add_category(data:NamedItem, authorization:str|None=Header(default=None)): return save_named('categories',data,None,authorize(authorization))
    @router.put('/categories/{entity_id}')
    def edit_category(entity_id:str,data:NamedItem, authorization:str|None=Header(default=None)): return save_named('categories',data,entity_id,authorize(authorization))
    @router.post('/accounts')
    def add_account(data:NamedItem, authorization:str|None=Header(default=None)): return save_named('accounts',data,None,authorize(authorization))
    @router.put('/accounts/{entity_id}')
    def edit_account(entity_id:str,data:NamedItem, authorization:str|None=Header(default=None)): return save_named('accounts',data,entity_id,authorize(authorization))
    def save_transaction(data, entity_id, user):
        with connection(user) as con:
            con.execute('SELECT pg_advisory_xact_lock(%s)',(2909202601,))
            previous=get_transaction(con,entity_id) if entity_id else None
            for table,key in [('categories',data.category_id),('accounts',data.account_id)]:
                row=con.execute(f'SELECT active FROM personal_finance.{table} WHERE id=%s AND household_id=%s',(key,HOUSEHOLD)).fetchone()
                if not row or (not row[0] and (not previous or previous['category_id' if table=='categories' else 'account_id']!=key)):
                    raise HTTPException(400,'Categoria ou conta inválida/inativa')
            snapshot=None
            if data.withdrawal_id:
                original=con.execute("SELECT payload FROM entities WHERE kind='cash_entry' AND id=%s FOR SHARE",(data.withdrawal_id,)).fetchone()
                snapshot=withdrawal_candidate(data.withdrawal_id,original[0]) if original else None
                if not snapshot: raise HTTPException(400,'Selecione retirada paga e identificada para Ana Paula, Euler ou Casal')
                allocated=con.execute('SELECT coalesce(sum(amount),0) FROM personal_finance.transactions WHERE withdrawal_id=%s AND deleted_at IS NULL AND id<>%s',(data.withdrawal_id,entity_id or '')).fetchone()[0]
                if allocated+data.amount>Decimal(snapshot['amount']): raise HTTPException(409,'Valor superior ao saldo disponível da retirada')
            values=(data.date,data.type,data.category_id,data.account_id,data.amount,data.status,data.due_date,data.paid_date,data.person,
                    data.withdrawal_id,json.dumps(snapshot) if snapshot else None,data.notes,user)
            if entity_id:
                row=con.execute('''UPDATE personal_finance.transactions SET date=%s,type=%s,category_id=%s,account_id=%s,
                    amount=%s,status=%s,due_date=%s,paid_date=%s,person=%s,withdrawal_id=%s,withdrawal_snapshot=%s::jsonb,
                    notes=%s,updated_by=%s,version=version+1,updated_at=now() WHERE id=%s AND household_id=%s AND version=%s AND deleted_at IS NULL RETURNING id''',
                    (*values,entity_id,HOUSEHOLD,data.version)).fetchone()
                if not row: raise HTTPException(409,'Lançamento alterado por outro sócio; atualize a tela')
            else:
                entity_id=str(uuid.uuid4())
                con.execute('''INSERT INTO personal_finance.transactions(date,type,category_id,account_id,amount,status,due_date,
                    paid_date,person,withdrawal_id,withdrawal_snapshot,notes,updated_by,id,household_id,created_by)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)''',(*values,entity_id,HOUSEHOLD,user))
            audit(con,user,entity_id,'update' if previous else 'create')
        return {'id':entity_id}
    @router.post('/transactions')
    def add_transaction(data:Transaction,authorization:str|None=Header(default=None)): return save_transaction(data,None,authorize(authorization))
    @router.put('/transactions/{entity_id}')
    def edit_transaction(entity_id:str,data:Transaction,authorization:str|None=Header(default=None)): return save_transaction(data,entity_id,authorize(authorization))
    @router.delete('/transactions/{entity_id}')
    def delete_transaction(entity_id:str,version:int=Query(ge=1),authorization:str|None=Header(default=None)):
        user=authorize(authorization)
        with connection(user) as con:
            get_transaction(con,entity_id)
            row=con.execute('UPDATE personal_finance.transactions SET deleted_at=now(),version=version+1,updated_by=%s WHERE id=%s AND version=%s AND deleted_at IS NULL RETURNING id',(user,entity_id,version)).fetchone()
            if not row: raise HTTPException(409,'Lançamento alterado por outro sócio')
            audit(con,user,entity_id,'soft_delete')
        return {'deleted':True,'recoverable':True}
    @router.post('/transactions/{entity_id}/restore')
    def restore_transaction(entity_id:str,authorization:str|None=Header(default=None)):
        user=authorize(authorization)
        with connection(user) as con:
            con.execute('SELECT pg_advisory_xact_lock(%s)',(2909202601,))
            row=get_transaction(con,entity_id,True)
            if row['withdrawal_id']:
                source=next((x for x in withdrawals(con) if x['id']==row['withdrawal_id']),None)
                allocated=con.execute('SELECT coalesce(sum(amount),0) FROM personal_finance.transactions WHERE withdrawal_id=%s AND deleted_at IS NULL AND id<>%s',(row['withdrawal_id'],entity_id)).fetchone()[0]
                if not source or allocated+Decimal(str(row['amount']))>Decimal(source['amount']): raise HTTPException(409,'Retirada indisponível; revise o vínculo antes de restaurar')
            con.execute('UPDATE personal_finance.transactions SET deleted_at=NULL,version=version+1,updated_by=%s WHERE id=%s',(user,entity_id))
            audit(con,user,entity_id,'restore')
        return {'restored':True}
    @router.get('/deleted')
    def deleted(authorization:str|None=Header(default=None)):
        user=authorize(authorization)
        with connection(user) as con:
            return [r[0] for r in con.execute('SELECT row_to_json(t) FROM personal_finance.transactions t WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT 500')]
    @router.post('/transactions/{entity_id}/attachments')
    async def upload(entity_id:str,file:UploadFile=File(...),authorization:str|None=Header(default=None)):
        user=authorize(authorization)
        name=Path(file.filename or '').name.strip()[:180]
        content=await file.read(5*1024*1024+1)
        if not name or not content or len(content)>5*1024*1024: raise HTTPException(400,'Envie arquivo de até 5 MB')
        media='application/pdf' if content.startswith(b'%PDF-') else 'image/jpeg' if content.startswith(b'\xff\xd8\xff') else 'image/png' if content.startswith(b'\x89PNG\r\n\x1a\n') else None
        extensions={'application/pdf':('.pdf',),'image/jpeg':('.jpg','.jpeg'),'image/png':('.png',)}
        if not media or not name.lower().endswith(extensions[media]): raise HTTPException(400,'Envie PDF, JPG ou PNG válido')
        attachment_id=str(uuid.uuid4())
        with connection(user) as con:
            con.execute('SELECT pg_advisory_xact_lock(%s)',(2909202601,))
            get_transaction(con,entity_id)
            if con.execute('SELECT count(*) FROM personal_finance.attachments WHERE transaction_id=%s',(entity_id,)).fetchone()[0]>=5: raise HTTPException(409,'Limite de cinco anexos')
            con.execute('INSERT INTO personal_finance.attachments(id,household_id,transaction_id,filename,content_type,content,uploaded_by) VALUES(%s,%s,%s,%s,%s,%s,%s)',(attachment_id,HOUSEHOLD,entity_id,name,media,content,user))
            audit(con,user,entity_id,'attachment_upload')
        return {'id':attachment_id}
    @router.get('/attachments/{attachment_id}')
    def download(attachment_id:str,authorization:str|None=Header(default=None)):
        user=authorize(authorization)
        with connection(user) as con:
            row=con.execute('SELECT transaction_id,filename,content_type,content FROM personal_finance.attachments WHERE id=%s AND household_id=%s',(attachment_id,HOUSEHOLD)).fetchone()
            if not row: raise HTTPException(404,'Anexo não encontrado')
            get_transaction(con,row[0])
            audit(con,user,attachment_id,'attachment_download')
        name=re.sub(r'[^A-Za-z0-9._-]','_',row[1])
        return Response(bytes(row[3]),media_type=row[2],headers={'Content-Disposition':f'attachment; filename="{name}"','Cache-Control':'no-store'})
    app.include_router(router)
