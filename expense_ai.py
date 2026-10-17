"""Receipt intake with explicit ledger selection and atomic attachment/posting."""
import hashlib
import re
import unicodedata
import uuid
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from psycopg.types.json import Jsonb
from personal_finance import HOUSEHOLD, MEMBERS

MAX_BYTES = 5 * 1024 * 1024

def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower()) if not unicodedata.combining(c))

def authorize_scope(user, scope, sectors):
    if scope not in ('empresa', 'pessoal'):
        raise HTTPException(422, 'Escolha Empresa ou Pessoal antes de enviar o comprovante')
    if scope == 'pessoal' and user not in MEMBERS:
        raise HTTPException(403, 'Financeiro pessoal exclusivo de Ana Paula e Euler')
    if scope == 'empresa' and 'finance' not in sectors(user):
        raise HTTPException(403, 'Acesso ao financeiro da empresa necessário')

def parse_receipt(text, sent_at=None):
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    supplier = next((x for x in lines[:6] if len(x) > 3 and not re.match(r'^(CNPJ|CPF|DANFE|DOCUMENTO|CUPOM)', x, re.I)), '')[:200]
    dates = re.findall(r'\b(\d{2}/\d{2}/\d{4})\b', text)
    when = (sent_at or datetime.now(ZoneInfo('America/Belem'))).date()
    if dates:
        try: when = datetime.strptime(dates[0], '%d/%m/%Y').date()
        except ValueError: raise ValueError('Data ilegível; confira o comprovante')
    totals = []
    for i, line in enumerate(lines):
        label = normalize(line)
        if re.search(r'\b(total|valor pago|valor da transacao)\b', label) and not re.search(r'subtotal|desconto|tribut|troco|item', label):
            values = re.findall(r'(?<!\d)(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})(?!\d)', line)
            if not values and i + 1 < len(lines):
                values = re.findall(r'^(?:R\$\s*)?(\d[\d.]*,\d{2})$', lines[i+1])
            totals.extend(Decimal(v.replace('.', '').replace(',', '.')) for v in values)
    totals = set(totals)
    if not supplier or len(totals) != 1 or not 0 < next(iter(totals)) <= 1000000000:
        raise ValueError('Fornecedor ou valor total incerto; nenhum lançamento foi criado')
    category = 'Outros'
    rules = {'Combustível':('gasolina','etanol','diesel','combustivel','posto '), 'Alimentação':('restaurante','lanchonete','refeicao','alimentacao'), 'Hospedagem':('hotel','pousada','hospedagem'), 'Manutenção':('oficina','manutencao','borracharia'), 'Material de Escritório':('papelaria','material de escritorio')}
    for name, words in rules.items():
        if any(word in normalize(text) for word in words): category = name; break
    return dict(supplier=supplier, date=when.isoformat(), amount=str(next(iter(totals))), category=category, dateSource='comprovante' if dates else 'envio')

def extract(content):
    import pymupdf
    from pdf_price_import import _OCR_LOCK, ocr_page
    kind = 'pdf' if content.startswith(b'%PDF-') else 'png' if content.startswith(b'\x89PNG\r\n\x1a\n') else 'jpeg' if content.startswith(b'\xff\xd8\xff') else None
    if not kind or not content or len(content) > MAX_BYTES: raise ValueError('Envie PDF, JPG ou PNG válido de até 5 MB')
    if not _OCR_LOCK.acquire(blocking=False): raise ValueError('OCR ocupado; tente novamente em instantes')
    try:
        with pymupdf.open(stream=content, filetype=kind) as original:
            if original.is_encrypted or original.page_count > 3: raise ValueError('Envie comprovante sem senha com até três páginas')
            pdf = content if kind == 'pdf' else original.convert_to_pdf()
        with pymupdf.open(stream=pdf, filetype='pdf') as doc:
            text = []
            for i, page in enumerate(doc):
                value = page.get_text()
                if len(value.strip()) < 40:
                    rows = ocr_page(pdf, i)
                    if any(score < .90 for _, score in rows): raise ValueError('Leitura de baixa confiança; nenhum lançamento foi criado')
                    value = '\n'.join(row for row, _ in rows)
                text.append(value)
        return parse_receipt('\n'.join(text)), {'pdf':'application/pdf','png':'image/png','jpeg':'image/jpeg'}[kind]
    finally: _OCR_LOCK.release()

def setup(con):
    con.execute('''CREATE TABLE IF NOT EXISTS expense_receipts (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL CHECK(scope IN ('empresa','pessoal')),
        digest TEXT UNIQUE NOT NULL, entry_id TEXT NOT NULL, filename TEXT NOT NULL,
        content_type TEXT NOT NULL, content BYTEA NOT NULL, created_by TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')

def post_receipt(con, user, scope, content, filename, extracted, mime, account='', cost_center=''):
    digest = hashlib.sha256(content).hexdigest()
    # One document cannot silently create expenses in both ledgers, including concurrent retries.
    con.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('receipt:'+digest,))
    if con.execute('SELECT 1 FROM expense_receipts WHERE digest=%s', (digest,)).fetchone():
        raise HTTPException(409, 'Este comprovante já foi recebido; nenhum lançamento duplicado foi criado')
    eid = 'expense-'+uuid.uuid4().hex
    if scope == 'empresa':
        historical=con.execute("SELECT payload->>'category' FROM entities WHERE kind='cash_entry' AND payload->>'type'='Saída' AND lower(payload->>'description')=lower(%s) ORDER BY updated_at DESC LIMIT 1",(extracted['supplier'],)).fetchone()
        if historical and historical[0]: extracted=dict(extracted,category=historical[0])
        con.execute("SELECT pg_advisory_xact_lock(hashtextextended('l2-cash',0))")
        day = con.execute("SELECT payload FROM entities WHERE kind='cash_day' AND payload->>'date'=%s FOR UPDATE", (extracted['date'],)).fetchone()
        if not day or day[0].get('status') != 'Aberto': raise HTTPException(409, 'Abra o caixa da data do comprovante antes de lançar')
        payload = dict(id=eid, type='Saída', date=extracted['date'], amount=float(Decimal(extracted['amount'])), category=extracted['category'], description=extracted['supplier'], account=account or 'Empresa', costCenter=cost_center or 'L2 Representações', receiptId=eid, createdBy=user, createdAt=datetime.now(ZoneInfo('America/Belem')).isoformat())
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('cash_entry',%s,%s)", (eid, Jsonb(payload)))
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'cash_entry',%s,'expense_ocr')", (user,eid))
        con.execute("SELECT pg_notify('l2_records_changed','')")
    else:
        con.execute("SELECT set_config('l2.personal_actor',%s,true)", (user,))
        account = account or 'account-cash'
        if not con.execute('SELECT 1 FROM personal_finance.accounts WHERE id=%s AND household_id=%s AND active', (account,HOUSEHOLD)).fetchone(): raise HTTPException(422,'Escolha uma conta pessoal ativa')
        category = 'expense-'+normalize(extracted['category']).replace(' ','-')
        name = extracted['category']+' Pessoal'
        historical=con.execute("SELECT c.name FROM personal_finance.transactions t JOIN personal_finance.categories c ON c.id=t.category_id WHERE t.household_id=%s AND t.type='Despesa Pessoal' AND t.deleted_at IS NULL AND lower(t.notes)=lower(%s) AND c.active ORDER BY t.created_at DESC LIMIT 1",(HOUSEHOLD,extracted['supplier'])).fetchone()
        if historical: name=historical[0]; extracted=dict(extracted,category=name)
        con.execute('INSERT INTO personal_finance.categories(id,household_id,name) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING', (category,HOUSEHOLD,name))
        category_row = con.execute('SELECT id FROM personal_finance.categories WHERE household_id=%s AND name=%s AND active', (HOUSEHOLD,name)).fetchone()
        if not category_row: raise HTTPException(422,'Categoria pessoal inativa; revise o cadastro')
        con.execute('''INSERT INTO personal_finance.transactions(id,household_id,date,type,category_id,account_id,amount,status,paid_date,person,notes,created_by,updated_by)
            VALUES(%s,%s,%s,'Despesa Pessoal',%s,%s,%s,'Pago',%s,'Casal',%s,%s,%s)''', (eid,HOUSEHOLD,extracted['date'],category_row[0],account,Decimal(extracted['amount']),extracted['date'],extracted['supplier'],user,user))
        con.execute('INSERT INTO personal_finance.attachments(id,household_id,transaction_id,filename,content_type,content,uploaded_by) VALUES(%s,%s,%s,%s,%s,%s,%s)', (eid,HOUSEHOLD,eid,filename,mime,content,user))
        con.execute("INSERT INTO personal_finance.audit_events(household_id,actor,entity_id,action) VALUES(%s,%s,%s,'expense_ocr')", (HOUSEHOLD,user,eid))
    con.execute('INSERT INTO expense_receipts(id,scope,digest,entry_id,filename,content_type,content,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)', (eid,scope,digest,eid,filename,mime,content,user))
    return dict(extracted,id=eid,scope=scope,message='Despesa lançada automaticamente no '+('fluxo de caixa da empresa!' if scope=='empresa' else 'financeiro pessoal!'))

def install(app, db, auth, sectors):
    router = APIRouter(prefix='/api/expense-ai')
    @router.post('/receipts')
    def upload(scope: str = Form(...), file: UploadFile = File(...), account: str = Form(''), cost_center: str = Form(''), authorization: str | None = Header(None)):
        user = auth(authorization); authorize_scope(user,scope,sectors)
        content = file.file.read(MAX_BYTES+1)
        try: extracted,mime = extract(content)
        except (ValueError, RuntimeError) as exc: raise HTTPException(422,str(exc)) from None
        user = auth(authorization)
        authorize_scope(user,scope,sectors)
        with db() as con:
            setup(con)
            return post_receipt(con,user,scope,content,(file.filename or 'comprovante')[:200],extracted,mime,account[:100],cost_center[:100])
    @router.get('/receipts/{receipt_id}')
    def download(receipt_id: str, authorization: str | None = Header(None)):
        user=auth(authorization)
        with db() as con:
            setup(con)
            row=con.execute('SELECT scope,content,content_type FROM expense_receipts WHERE id=%s',(receipt_id,)).fetchone()
            if not row: raise HTTPException(404,'Comprovante não encontrado')
            authorize_scope(user,row[0],sectors)
            return Response(bytes(row[1]),media_type=row[2],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    @router.post('/receipts/{receipt_id}/reverse')
    def reverse(receipt_id: str, authorization: str | None = Header(None)):
        user=auth(authorization)
        with db() as con:
            setup(con)
            row=con.execute('SELECT scope,entry_id FROM expense_receipts WHERE id=%s FOR UPDATE',(receipt_id,)).fetchone()
            if not row: raise HTTPException(404,'Comprovante não encontrado')
            scope,eid=row; authorize_scope(user,scope,sectors)
            if scope=='pessoal':
                con.execute("SELECT set_config('l2.personal_actor',%s,true)",(user,))
                changed=con.execute('UPDATE personal_finance.transactions SET deleted_at=now(),updated_by=%s,updated_at=now(),version=version+1 WHERE id=%s AND household_id=%s AND deleted_at IS NULL RETURNING id',(user,eid,HOUSEHOLD)).fetchone()
                if not changed: raise HTTPException(409,'Despesa já estornada')
                con.execute("INSERT INTO personal_finance.audit_events(household_id,actor,entity_id,action) VALUES(%s,%s,%s,'expense_reversed')",(HOUSEHOLD,user,eid))
            else:
                con.execute("SELECT pg_advisory_xact_lock(hashtextextended('l2-cash',0))")
                rid=eid+'-reversal'
                if con.execute("SELECT 1 FROM entities WHERE kind='cash_entry' AND id=%s",(rid,)).fetchone(): raise HTTPException(409,'Despesa já estornada')
                entry=con.execute("SELECT payload FROM entities WHERE kind='cash_entry' AND id=%s",(eid,)).fetchone()
                if not entry: raise HTTPException(409,'Despesa removida; confira o histórico')
                when=datetime.now(ZoneInfo('America/Belem')).date().isoformat()
                day=con.execute("SELECT payload FROM entities WHERE kind='cash_day' AND payload->>'date'=%s FOR UPDATE",(when,)).fetchone()
                if not day or day[0].get('status')!='Aberto': raise HTTPException(409,'Abra o caixa de hoje para registrar o estorno')
                payload=dict(entry[0],id=rid,type='Entrada',date=when,description='Estorno: '+entry[0]['description'],reversalOf=eid,createdBy=user,createdAt=datetime.now(ZoneInfo('America/Belem')).isoformat())
                con.execute("INSERT INTO entities(kind,id,payload) VALUES('cash_entry',%s,%s)",(rid,Jsonb(payload)))
                con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'cash_entry',%s,'expense_reversed')",(user,eid))
                con.execute("SELECT pg_notify('l2_records_changed','')")
            return {'message':'Despesa estornada no destino original'}
    app.include_router(router)

async def whatsapp_receipt(msg, phone, db, setting, sectors):
    """Called only after Meta webhook signature and business phone validation."""
    from starlette.concurrency import run_in_threadpool
    from field_audio import trusted_url
    import httpx
    kind=msg.get('type')
    if kind not in ('image','document'): return None
    media=msg.get(kind,{})
    caption=normalize(str(media.get('caption','')))
    if not re.search(r'\bdespesa\b',caption): return None
    with db() as con:
        operator=con.execute('SELECT o.username FROM field_operators o JOIN app_users u ON u.username=o.username WHERE o.phone=%s AND o.enabled AND u.active',(phone,)).fetchone()
    if not operator: return 'Número sem autorização para registrar despesas. Solicite o vínculo do operador no L2 One.'
    scopes=[value for value in ('empresa','pessoal') if re.search(r'\b'+value+r'\b',caption)]
    if len(scopes)!=1: return 'Informe o destino na legenda: DESPESA EMPRESA ou DESPESA PESSOAL. Nenhuma despesa foi lançada.'
    user=operator[0];scope=scopes[0]
    try:
        await run_in_threadpool(authorize_scope,user,scope,sectors)
        mid=str(media.get('id',''));version=setting('WA_GRAPH_VERSION')
        if not re.fullmatch(r'\d{1,40}',mid) or not re.fullmatch(r'v\d+\.\d+',version): raise ValueError('Arquivo inválido')
        headers={'Authorization':'Bearer '+setting('WA_ACCESS_TOKEN')}
        async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
            response=await client.get(f'https://graph.facebook.com/{version}/{mid}',headers=headers)
            response.raise_for_status();meta=response.json();url=meta.get('url','')
            if not trusted_url(url) or int(meta.get('file_size',MAX_BYTES+1))>MAX_BYTES: raise ValueError('Comprovante inválido ou acima de 5 MB')
            content=bytearray()
            async with client.stream('GET',url,headers=headers) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content)>MAX_BYTES: raise ValueError('Comprovante acima de 5 MB')
        extracted,mime=await run_in_threadpool(extract,bytes(content))
        def persist():
            # Recheck permissions after download/OCR, before committing a financial change.
            authorize_scope(user,scope,sectors)
            with db() as con:
                active=con.execute('SELECT 1 FROM field_operators o JOIN app_users u ON u.username=o.username WHERE o.phone=%s AND o.username=%s AND o.enabled AND u.active',(phone,user)).fetchone()
                if not active: raise HTTPException(403,'Operador desativado')
                setup(con)
                return post_receipt(con,user,scope,bytes(content),'comprovante-whatsapp',extracted,mime)
        result=await run_in_threadpool(persist)
        return ('📄 Comprovante identificado · '+scope.upper()+'\nFornecedor: '+result['supplier']+'\nCategoria: '+result['category']+'\nValor: R$ '+result['amount'].replace('.',',')+'\nData: '+datetime.fromisoformat(result['date']).strftime('%d/%m/%Y')+'\nComprovante anexado com sucesso.\n'+result['message']+'\nEdite ou estorne no L2 One, no financeiro correspondente.')
    except HTTPException as exc: return str(exc.detail)
    except (ValueError,RuntimeError) as exc: return str(exc)+' — Confira no L2 One.'
    except Exception: return 'Processamento indisponível. Confira os lançamentos no L2 One antes de reenviar.'
