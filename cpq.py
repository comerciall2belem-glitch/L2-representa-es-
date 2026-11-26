"""Server-owned quotes, approval matrix and immutable commercial snapshots."""
import hashlib
import io
import json
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from psycopg.types.json import Jsonb
from commercial_tables import validate_table, price_id, bella_policy, BELLA

TZ = ZoneInfo('America/Belem')
router = APIRouter(prefix='/api/cpq', tags=['CPQ'])
_services = {}
MANAGERS = {'Ana Paula', 'Euler'}
DIRECTORS = {'Ana Paula', 'Euler'}

def configure(**services):
    _services.update(services)

def now(): return datetime.now(TZ)
def money(value):
    try: number = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation): raise HTTPException(422, 'Valor comercial inválido')
    if not number.is_finite() or number < 0 or number > Decimal('10000000000'):
        raise HTTPException(422, 'Valor comercial inválido')
    return number.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)

def level(discount):
    discount = money(discount)
    if discount > 100: raise HTTPException(422, 'Desconto deve ficar entre 0 e 100%')
    return 'Automática' if discount <= 5 else 'Gestor Comercial' if discount <= 10 else 'Diretoria Executiva'

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str).encode()).hexdigest()

def put(con, kind, value):
    con.execute('INSERT INTO entities(kind,id,payload) VALUES(%s,%s,%s) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,updated_at=now()', (kind, value['id'], Jsonb(value)))

def audit(con, user, identifier, action):
    con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)', (user, 'cpq_quote', identifier, action))
    con.execute("SELECT pg_notify('l2_records_changed','')")

def lock(con): con.execute('SELECT pg_advisory_xact_lock(%s)', (8239020,))

def get(con, kind, identifier, for_update=False):
    row = con.execute('SELECT payload FROM entities WHERE kind=%s AND id=%s'+(' FOR UPDATE' if for_update else ''), (kind, identifier)).fetchone()
    if not row: raise HTTPException(404, 'Registro comercial não encontrado')
    return row[0]

def access(header):
    user = _services['auth'](header)
    _services['require_sector'](user, 'commercial', 'office')
    return user

def scoped_quote(con, identifier, user):
    quote = get(con, 'cpq_quote', identifier, True)
    if quote['clientId']:
        _services['check_client_scope'](con, user, quote['clientId'])
    elif quote['sellerResponsible'] != user and user not in MANAGERS and quote['createdBy'] != user:
        raise HTTPException(403, 'Proposta avulsa fora da sua carteira')
    if _services['is_seller'](con, user) and quote['sellerResponsible'] != user:
        raise HTTPException(403, 'Proposta pertence a outro vendedor')
    return quote

class Strict(BaseModel): model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
class Line(Strict):
    brand: str = Field(min_length=1, max_length=120)
    tableId: str = Field(min_length=1, max_length=250)
    sku: str = Field(min_length=1, max_length=120)
    quantity: int = Field(gt=0, le=100000, strict=True)
class Guest(Strict):
    name: str = Field(min_length=2, max_length=200)
    taxId: str = Field(max_length=20)
    city: str = Field(min_length=2, max_length=120)
    state: str = Field(pattern='^(PA|AP)$')


class QuoteRequest(Strict):
    clientId: str = Field(default='', max_length=128)
    guest: Guest | None = None
    leadId: str = Field(default='', max_length=128)
    notes: str = Field(default='', max_length=1000)
    sellerResponsible: str = Field(min_length=1, max_length=120)
    priceTable: str = Field(pattern='^(PA|AP)$')
    channel: str = Field(min_length=1, max_length=40)
    paymentTerms: str = Field(pattern=r'^\d+(?:/\d+)*$', max_length=80)
    discountPercent: Decimal = Field(default=Decimal('0'), ge=0, le=100, decimal_places=2, allow_inf_nan=False)
    items: list[Line] = Field(min_length=1, max_length=200)
    opportunityId: str = Field(default='', max_length=128)
    validityDays: int = Field(default=14, ge=1, le=30)

class Decision(Strict):
    approve: bool
    reason: str = Field(min_length=3, max_length=1000)
class Dispatch(Strict):
    channel: str = Field(pattern='^(WhatsApp|E-mail|Link)$')
    evidence: str = Field(min_length=3, max_length=1000)
class Acceptance(Strict):
    evidence: str = Field(min_length=3, max_length=1000)


def commercial_policy(industry, state, channel, net, terms):
    """No fabricated freight/minimum when an industry has not configured them."""
    if industry.get('name') == BELLA:
        return bella_policy(net,terms,state,industry.get('cpqPolicy'))
    tiers = [t for t in industry.get('commercialPolicy', []) if
             (not t.get('state') or t['state'] in (state, 'ALL')) and
             (not t.get('channel') or t['channel'].casefold() == channel.casefold())]
    if tiers:
        matching = [t for t in tiers if money(t['minimum']) <= net and t['paymentTerms'] == terms]
        if not matching: raise HTTPException(422, 'Pedido mínimo ou prazo fora da política de '+industry['name'])
        tier = max(matching, key=lambda t: money(t['minimum']))
        return {'minimum':str(money(tier['minimum'])), 'freight':tier['freight'], 'paymentTerms':terms}
    minimum = (industry.get('minimumByState') or {}).get(state)
    options = industry.get('paymentOptions')
    if minimum is None or industry.get('freight') not in ('CIF', 'FOB') or not options:
        raise HTTPException(422, 'Configure pedido mínimo, frete e prazos da marca '+industry['name'])
    if net < money(minimum) or terms not in options:
        raise HTTPException(422, 'Pedido mínimo ou prazo fora da política de '+industry['name'])
    return {'minimum':str(money(minimum)), 'freight':industry['freight'], 'paymentTerms':terms}


def calculate(con, data, client):
    state = _services['normalize_uf'](client.get('state'))
    if not state or not _services['price_table_matches_client'](state, data.priceTable):
        raise HTTPException(422, 'Tabela incompatível com PA/AP do cliente')
    if not all(str(client.get(k) or '').strip() for k in ('name', 'taxId', 'city')) or not _services['valid_cnpj'](client['taxId']):
        raise HTTPException(422, 'Complete razão social, CNPJ, cidade e UF do cliente')
    discount = money(data.discountPercent)
    approval = level(discount)
    lines, industries, tables, totals = [], {}, {}, defaultdict(lambda: Decimal(0))
    keys = set()
    for row in data.items:
        key = (row.brand, row.tableId, row.sku)
        if key in keys: raise HTTPException(422, 'SKU duplicado na mesma tabela; consolide a quantidade')
        keys.add(key)
        table = validate_table(con, row.model_dump(), row.brand, data.priceTable, allowed_states=['PA', 'AP'] if state == 'AP' else ['PA'])
        if table.get('channel') and table['channel'].casefold() != data.channel.casefold():
            raise HTTPException(422, 'Tabela incompatível com o canal B2B selecionado')
        industry = industries.setdefault(row.brand, get(con, 'industry', row.brand))
        if not industry.get('active'): raise HTTPException(422, 'Indústria inativa')
        tables[table['id']] = table
        price = get(con, 'price', price_id(row.model_dump()))
        if price.get('brand') != row.brand or price.get('sku') != row.sku or price.get('active') is False:
            raise HTTPException(422, 'SKU inválido ou inativo')
        unit = money(price.get('price'))
        if unit <= 0: raise HTTPException(422, 'Preço deve ser positivo')
        pack = int(price.get('packSize') or industry.get('packSize') or 1)
        if row.quantity % pack: raise HTTPException(422, 'Quantidade fora da embalagem de '+row.sku)
        gross = money(unit * row.quantity)
        net = money(gross * (100 - discount) / 100)
        lines.append({**row.model_dump(), 'description':price.get('description', row.sku), 'unitPrice':str(unit), 'grossSubtotal':str(gross), 'subtotal':str(net)})
        totals[row.brand] += net
    policies = {brand:commercial_policy(industry, state, data.channel, totals[brand], data.paymentTerms) for brand, industry in industries.items()}
    commercial = {'clientId':data.clientId, 'clientState':state, 'sellerResponsible':data.sellerResponsible,
                  'priceTable':data.priceTable, 'channel':data.channel, 'paymentTerms':data.paymentTerms,
                  'discountPercent':str(discount), 'items':lines, 'policies':policies,
                  'amount':str(sum(totals.values())), 'grossAmount':str(sum(money(x['grossSubtotal']) for x in lines)),
                  'requiredApproval':approval}
    if data.notes:
        commercial['notes'] = data.notes
    if data.guest:
        commercial['guest'] = data.guest.model_dump()
    if data.leadId:
        commercial['leadId'] = data.leadId
    # A policy/table edit invalidates a pending or granted approval, even if totals stay equal.
    revisions = {'commercial':commercial, 'industries':industries, 'tables':tables}
    return {**commercial, 'fingerprint':fingerprint(revisions)}


def quote_customer(con, quote):
    return get(con, 'client', quote['clientId']) if quote['clientId'] else quote['guest']


def current(con, quote):
    if date.fromisoformat(quote['validUntil']) < now().date(): raise HTTPException(409, 'Proposta expirada; gere uma nova versão')
    data = QuoteRequest(**quote['request'])
    calculated = calculate(con, data, quote_customer(con, quote))
    if calculated['fingerprint'] != quote['fingerprint']:
        raise HTTPException(409, 'Preços ou política mudaram; gere nova proposta e aprovação')
    return calculated


def released(quote):
    if quote.get('approvalStatus') != 'Aprovada': raise HTTPException(409, 'Alçada pendente ou recusada')

def save_quote(con, data, user, previous=None):
    from proposal_tracking import ensure_metadata

    if data.leadId:
        if data.clientId:
            raise HTTPException(422, 'Selecione um cliente ou um lead')
        lead = get(con, 'lead', data.leadId)
        if (lead.get('owner') != data.sellerResponsible
                or (_services['is_seller'](con, user) and lead.get('owner') != user)):
            raise HTTPException(403, 'Lead fora da carteira do vendedor')
        try:
            guest = Guest(**{key:lead.get(key) or '' for key in ('name','taxId','city','state')})
        except ValueError as exc:
            raise HTTPException(422, 'Complete os dados do lead antes de gerar a proposta') from exc
        data = data.model_copy(update={'guest': guest})
    if bool(data.clientId) == bool(data.guest):
        raise HTTPException(422, 'Selecione um cliente ou preencha os dados da proposta avulsa')
    if data.clientId:
        _services['check_client_scope'](con, user, data.clientId)
        _services['check_client_scope'](con, data.sellerResponsible, data.clientId)
    if _services['is_seller'](con, user) and data.sellerResponsible != user:
        raise HTTPException(403, 'Vendedor não pode cotar em nome de outro vendedor')
    row = con.execute('SELECT role FROM app_users WHERE username=%s AND active', (data.sellerResponsible,)).fetchone()
    if not row or row[0] != 'Vendedor' or data.sellerResponsible == 'Laís':
        raise HTTPException(422, 'Selecione um vendedor ativo')
    if data.opportunityId:
        opp = get(con, 'opportunity', data.opportunityId)
        if not data.clientId or opp.get('clientId') != data.clientId or opp.get('owner') != data.sellerResponsible:
            raise HTTPException(422, 'Oportunidade incompatível com o cliente ou vendedor')
    customer = get(con, 'client', data.clientId) if data.clientId else data.guest.model_dump()
    result = calculate(con, data, customer)
    quote = {'id':previous['id'] if previous else str(uuid.uuid4()), **result,
             'request':data.model_dump(mode='json'), 'createdAt':previous['createdAt'] if previous else now().isoformat(),
             'createdBy':previous['createdBy'] if previous else user, 'approvalRequestedBy':user,
             'validUntil':str(now().date()+timedelta(days=data.validityDays)), 'opportunityId':data.opportunityId,
             'approvalStatus':'Aprovada' if result['requiredApproval']=='Automática' else 'Pendente',
             'approvalBy':'Sistema' if result['requiredApproval']=='Automática' else None}
    put(con, 'cpq_quote', quote)
    ensure_metadata(con, quote)
    audit(con, user, quote['id'], 'updated' if previous else 'created')
    return quote


@router.post('/quotes')
def create_quote(data: QuoteRequest, authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        lock(con)
        return save_quote(con, data, user)


def editable(quote):
    if any(quote.get(key) for key in ('documentIssuedAt','publicCustomer','sentAt','acceptedAt','convertedOrderId')):
        raise HTTPException(409, 'Proposta já emitida. Crie uma nova versão para mudar as condições')


@router.put('/quotes/{quote_id}')
def update_quote(quote_id: str, data: QuoteRequest, authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        lock(con)
        quote = scoped_quote(con, quote_id, user)
        editable(quote)
        return save_quote(con, data, user, quote)


@router.delete('/quotes/{quote_id}')
def delete_quote(quote_id: str, authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        lock(con)
        quote = scoped_quote(con, quote_id, user)
        editable(quote)
        con.execute("DELETE FROM entities WHERE kind='cpq_quote' AND id=%s", (quote_id,))
        audit(con, user, quote_id, 'deleted')
    return {'deleted': True}


class Attach(Strict):
    clientId: str = Field(min_length=1, max_length=128)


@router.post('/quotes/{quote_id}/attach')
def attach_client(quote_id: str, data: Attach, authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        lock(con)
        quote = scoped_quote(con, quote_id, user)
        if quote['clientId'] == data.clientId:
            return quote
        if quote['clientId']:
            raise HTTPException(409, 'Proposta já vinculada')
        _services['check_client_scope'](con, user, data.clientId)
        _services['check_client_scope'](con, quote['sellerResponsible'], data.clientId)
        customer = get(con, 'client', data.clientId)
        digits = lambda value: ''.join(c for c in str(value or '') if c.isdigit())
        if (digits(customer.get('taxId')) != digits(quote['guest']['taxId'])
                or _services['normalize_uf'](customer.get('state')) != quote['clientState']):
            raise HTTPException(422, 'O cliente deve ter o mesmo CNPJ e UF da proposta avulsa')
        current(con, quote)
        request = QuoteRequest(**{**quote['request'], 'clientId':data.clientId, 'guest':None, 'leadId':''})
        calculated = calculate(con, request, customer)
        for key in ('items','amount','policies'):
            if calculated[key] != quote[key]:
                raise HTTPException(409, 'O cadastro altera as condições aprovadas')
        if quote.get('leadId'):
            quote['sourceLeadId'] = quote.pop('leadId')
        quote.pop('guest', None)
        quote.update(calculated, request=request.model_dump(mode='json'))
        put(con, 'cpq_quote', quote)
        audit(con, user, quote_id, 'client_attached')
        return quote


@router.get('/quotes/{quote_id}')
def read_quote(quote_id: str, authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        return scoped_quote(con, quote_id, user)


@router.get('/quotes')
def list_quotes(authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        ids = {c['id'] for c in _services['scoped_rows'](con, 'client', user)}
        seller = _services['is_seller'](con, user)
        return [r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='cpq_quote' ORDER BY updated_at DESC").fetchall() if (r[0]['clientId'] in ids and (not seller or r[0]['sellerResponsible']==user)) or (not r[0]['clientId'] and (r[0]['sellerResponsible']==user or user in MANAGERS or (not seller and r[0]['createdBy']==user)))]

@router.post('/quotes/{quote_id}/decision')
def decide(quote_id: str, data: Decision, authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        lock(con)
        quote = scoped_quote(con, quote_id, user)
        required = quote['requiredApproval']
        allowed = DIRECTORS if required == 'Diretoria Executiva' else MANAGERS
        if user not in allowed or (quote.get('approvalRequestedBy') or quote['createdBy']) == user: raise HTTPException(403, 'Aprovação exige alçada competente e outro usuário solicitante')
        if quote.get('convertedOrderId') or quote.get('acceptedAt') or quote.get('approvalStatus') != 'Pendente': raise HTTPException(409, 'Decisão já encerrada')
        current(con, quote)
        quote.update(approvalStatus='Aprovada' if data.approve else 'Recusada', approvalBy=user, approvalAt=now().isoformat(), approvalReason=data.reason.strip())
        put(con, 'cpq_quote', quote)
        audit(con, user, quote_id, 'approved' if data.approve else 'rejected')
    return quote


def quote_pdf(quote, client, public_url=None):
    from pathlib import Path
    from reportlab.platypus import Image, SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    styles = getSampleStyleSheet()
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, rightMargin=32, leftMargin=32)
    p = lambda text: Paragraph(escape(str(text)), styles['BodyText'])
    styles['Title'].textColor = colors.HexColor('#123d44')
    logo = Image(str(Path(__file__).with_name('logo-l2.jpeg')), width=106, height=46, kind='proportional')
    story = [logo, Paragraph('L2 ONE · Proposta comercial', styles['Title']), p(client['name']), p(' · '.join(filter(None, (client.get('taxId'), client.get('city'), client.get('state'))))), p('Proposta: '+str(quote.get('number') or quote['id'])), p('Válida até '+quote['validUntil']), Spacer(1,12)]
    rows = [[p(x) for x in ('Marca / SKU', 'Descrição', 'Qtd.', 'Tabela R$', 'Líquido R$')]]
    rows += [[p(x['brand']+' / '+x['sku']),p(x['description']),p(x['quantity']),p(x['unitPrice']),p(x['subtotal'])] for x in quote['items']]
    table = Table(rows, colWidths=[125,180,38,72,72], repeatRows=1)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dbe8e8')),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.3,colors.grey)]))
    gross = quote.get('grossAmount') or str(sum(money(line['unitPrice']) * line['quantity'] for line in quote['items']))
    story += [table, Spacer(1,12),p('Subtotal R$ '+gross+' · Desconto '+quote['discountPercent']+'% · Total líquido R$ '+quote['amount']),p('Pagamento: '+quote['paymentTerms'])]
    for brand, policy in quote['policies'].items(): story.append(p(brand+' · Frete '+policy['freight']+' · Pedido mínimo R$ '+policy['minimum']+' · '+policy.get('paymentMethod','')))
    story.append(p('Vendedor responsável: '+quote['sellerResponsible']))
    if quote.get('notes'):
        story.append(p('Observações: '+quote['notes']))
    if public_url:
        story += [Spacer(1, 12), Paragraph(f'<link href="{escape(public_url, quote=True)}" color="#123d44"><u>Responder ou aceitar proposta</u></link>', styles['BodyText'])]
    doc.build(story)
    return out.getvalue()

@router.get('/quotes/{quote_id}/pdf')
def emit_pdf(quote_id: str, request: Request, authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        lock(con)
        quote = scoped_quote(con, quote_id, user)
        if not quote.get('convertedOrderId'): current(con, quote)
        released(quote)
        from proposal_tracking import ensure_metadata, make_link
        number = ensure_metadata(con, quote)
        customer = quote_customer(con, quote)
        quote.setdefault('publicCustomer', {key: customer.get(key) or '' for key in ('name','taxId','state','city')})
        public_url = str(request.base_url).rstrip('/') + make_link(con, quote, 'link_direto')
        content = quote_pdf({**quote, 'number': number}, quote['publicCustomer'], public_url)
        quote.update(documentIssuedAt=now().isoformat(), documentHash=hashlib.sha256(content).hexdigest())
        put(con, 'cpq_quote', quote); audit(con, user, quote_id, 'pdf_issued')
    return Response(content, media_type='application/pdf', headers={'Content-Disposition':f'attachment; filename="L2_Proposta_{quote_id}.pdf"', 'Cache-Control':'no-store'})

@router.post('/quotes/{quote_id}/dispatch')
def record_dispatch(quote_id: str, data: Dispatch, authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        lock(con); quote = scoped_quote(con, quote_id, user)
        current(con, quote); released(quote)
        if not quote.get('documentIssuedAt') and not quote.get('publicCustomer'): raise HTTPException(409, 'Emita a proposta em PDF antes de registrar o envio')
        if quote.get('convertedOrderId'): raise HTTPException(409, 'Proposta já convertida')
        quote.update(sentAt=now().isoformat(), sentBy=user, sentChannel=data.channel, sentEvidence=data.evidence.strip())
        put(con, 'cpq_quote', quote); audit(con, user, quote_id, 'dispatch_recorded')
        from proposal_tracking import record_contact
        channel = {'WhatsApp':'whatsapp','E-mail':'email','Link':'link_direto'}[data.channel]
        record_contact(con, 'cpq_quote', quote_id, user, 'proposta_enviada', channel, data.evidence)
        if quote.get('opportunityId') and con.execute(
            "SELECT 1 FROM entities WHERE kind='opportunity' AND id=%s AND payload->>'clientId'=%s AND payload->>'owner'=%s",
            (quote['opportunityId'], quote['clientId'], quote['sellerResponsible'])
        ).fetchone():
            record_contact(con, 'opportunity', quote['opportunityId'], user, 'proposta_enviada', channel, data.evidence)
    return quote

@router.post('/quotes/{quote_id}/accept')
def accept_quote(quote_id: str, data: Acceptance, authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        lock(con); quote = scoped_quote(con, quote_id, user)
        current(con, quote); released(quote)
        if not quote.get('sentAt'): raise HTTPException(409, 'Registre o envio formal antes do aceite')
        if quote.get('convertedOrderId'): return quote
        quote.update(acceptedAt=now().isoformat(), acceptedBy=user, acceptanceEvidence=data.evidence.strip())
        put(con, 'cpq_quote', quote); audit(con, user, quote_id, 'accepted')
    return quote

@router.post('/quotes/{quote_id}/convert')
def convert(quote_id: str, authorization: str | None = Header(default=None)):
    user = access(authorization)
    with _services['db']() as con:
        quote = scoped_quote(con, quote_id, user)
        if quote.get('convertedOrderId'): return {'orderId':quote['convertedOrderId'], 'alreadyConverted':True}
        current(con, quote); released(quote)
        if not quote.get('acceptedAt'): raise HTTPException(409, 'Registre o aceite antes de gerar o pedido')
        if not quote['clientId']: raise HTTPException(409, 'Vincule a proposta avulsa a um cliente antes de converter')
        order = {'id':'cpq-'+quote_id, 'clientId':quote['clientId'], 'sellerResponsible':quote['sellerResponsible'],
                 'cpqQuoteId':quote_id, 'date':str(now().date()), 'status':'Confirmado', 'brand':'Multimarcas',
                 'priceTable':quote['priceTable'], 'channel':quote['channel'], 'paymentTerms':quote['paymentTerms'],
                 'discountPercent':quote['discountPercent'], 'items':quote['items']}
    # The existing ERP transaction locks and revalidates the snapshot, then records both writes.
    _services['sync_order'](order, authorization)
    return {'orderId':order['id'], 'alreadyConverted':False}


def enforce_order(con, order, previous=None):
    """Shared by the ERP sync endpoint; client-controlled discounts never bypass approval."""
    previous = previous or {}
    quote_id = order.get('cpqQuoteId')
    discount = money(order.get('discountPercent', 0))
    # Confirmed commercial conditions cannot be rewritten through a status update.
    if previous.get('status') in ('Confirmado','Faturado','Entregue'):
        for key in ('clientId','sellerResponsible','priceTable','paymentTerms','items'):
            if key in previous and order.get(key) != previous.get(key):
                raise HTTPException(409, 'Pedido confirmado: condições comerciais imutáveis')
    if previous.get('cpqQuoteId') and quote_id != previous['cpqQuoteId']:
        raise HTTPException(409, 'O vínculo de origem CPQ não pode ser removido')
    if not quote_id:
        # Pedidos diretos do ERP não exigem proposta CPQ. O fluxo CPQ é obrigatório
        # apenas para pedidos vinculados a uma cotação ou com desconto.
        # Não flexibilizar a imutabilidade nem o vínculo de pedidos CPQ existentes.
        if discount: raise HTTPException(409, 'Descontos exigem proposta CPQ aprovada')
        return
    if not isinstance(quote_id, str) or len(quote_id)>128: raise HTTPException(422, 'Proposta CPQ inválida')
    quote = get(con, 'cpq_quote', quote_id, True)
    if order.get('status') not in ('Confirmado','Faturado','Entregue','Cancelado'):
        raise HTTPException(409, 'Pedido CPQ confirmado não pode voltar a rascunho')
    if not quote.get('convertedOrderId'): current(con, quote)
    released(quote)
    if not quote.get('acceptedAt'): raise HTTPException(409, 'Aceite obrigatório')
    if quote.get('convertedOrderId') and quote['convertedOrderId'] != order['id']: raise HTTPException(409, 'Proposta já convertida em outro pedido')
    expected = [(x['brand'],x['tableId'],x['sku'],int(x['quantity']),money(x['unitPrice'])) for x in quote['items']]
    actual = [(x['brand'],x['tableId'],x['sku'],int(x['quantity']),money(x['unitPrice'])) for x in order['items']]
    if expected != actual or any(order.get(k) != quote.get(k) for k in ('clientId','sellerResponsible','priceTable','channel','paymentTerms')) or discount != money(quote['discountPercent']):
        raise HTTPException(409, 'Pedido difere das condições aprovadas; gere nova proposta')
    for line, original in zip(order['items'], quote['items']):
        line.update(subtotal=original['subtotal'], grossSubtotal=original['grossSubtotal'], discountPercent=quote['discountPercent'])
    order.update(amount=float(quote['amount']), grossAmount=quote['grossAmount'], discountPercent=quote['discountPercent'], cpqFingerprint=quote['fingerprint'], freightByBrand=quote['policies'])
    quote['convertedOrderId'] = order['id']
    quote['convertedAt'] = quote.get('convertedAt') or now().isoformat()
    put(con, 'cpq_quote', quote)


def attach_order(con, order, user):
    if not order.get('cpqQuoteId'): return
    quote = get(con, 'cpq_quote', order['cpqQuoteId'])
    if not quote.get('opportunityId'): return
    opp = get(con, 'opportunity', quote['opportunityId'], True)
    if opp.get('clientId') != order['clientId']: raise HTTPException(409, 'Cliente da oportunidade mudou')
    stage = 'Pós-venda' if order.get('status') in ('Faturado','Entregue') else 'Pedido confirmado'
    if opp.get('stage') != stage:
        opp['stageHistory'] = list(opp.get('stageHistory') or []) + [{'stage':stage,'at':now().isoformat(),'by':user}]
    opp.update(stage=stage, orderId=order['id'], cpqQuoteId=quote['id'], updatedAt=now().isoformat(),
               next='Acompanhar recompra e mix' if stage=='Pós-venda' else 'Conferir faturamento e entrega',
               followUp=str(now().date()+timedelta(days=7)))
    put(con,'opportunity',opp)
    put(con,'task',{'id':'cpq-followup-'+quote['id'],'clientId':opp['clientId'],'user':opp['owner'],
                   'sourceOpportunityId':opp['id'],'text':opp['next'],'date':opp['followUp'],'status':'Aberta','source':'CPQ'})


def mix_suggestions(client, clients, orders, prices, tables, today):
    segment = client.get('tdc',{}).get('segment') or client.get('channel')
    if not segment: return {'products':[], 'reason':'Cadastre o segmento/canal para comparar o mix'}
    peers = {c['id'] for c in clients if (c.get('tdc',{}).get('segment') or c.get('channel')) == segment}
    bought, scores = set(), defaultdict(lambda: {'quantity':0, 'clients':set()})
    for order in orders:
        try: purchased = date.fromisoformat(str(order.get('date',''))[:10])
        except ValueError: continue
        if order.get('status') not in ('Faturado','Entregue') or not today-timedelta(days=365) <= purchased <= today: continue
        for line in order.get('items') or []:
            key = (line.get('brand') or order.get('brand'), line.get('sku'))
            if not all(key): continue
            if order.get('clientId') == client['id']: bought.add(key)
            if order.get('clientId') not in peers: continue
            try: quantity = int(line.get('quantity',0))
            except (ValueError,TypeError): continue
            if quantity > 0:
                scores[key]['quantity'] += quantity
                scores[key]['clients'].add(order['clientId'])
    active = {t['id']:t for t in tables if t.get('active')}
    products = {}
    for price in prices:
        key=(price.get('brand'),price.get('sku')); table=active.get(price.get('tableId') or (str(price.get('brand'))+'|'+str(price.get('state'))))
        if key in bought or key not in scores or not table or price.get('active') is False: continue
        state = _services['normalize_uf'](client.get('state'))
        if table.get('state')!='ALL' and not _services['price_table_matches_client'](state,table.get('state')): continue
        if table.get('channel') and str(client.get('b2bChannel') or client.get('channel') or '').casefold()!=table['channel'].casefold(): continue
        products.setdefault(key, {'brand':key[0],'sku':key[1],'description':price.get('description',''), 'quantitySold':scores[key]['quantity'], 'buyingClients':len(scores[key]['clients']), 'segment':segment})
    return {'products':sorted(products.values(),key=lambda x:(-x['quantitySold'],x['brand'],x['sku']))[:10], 'reason':'Mais vendidos no segmento nos últimos 12 meses disponíveis, ainda não comprados pelo cliente; não indica estoque disponível'}

@router.get('/mix/{client_id}')
def mix(client_id: str, authorization: str | None = Header(default=None)):
    user=access(authorization)
    with _services['db']() as con:
        _services['check_client_scope'](con,user,client_id)
        records=defaultdict(list)
        for kind,payload in con.execute("SELECT kind,payload FROM entities WHERE kind IN ('client','order','price','price_table')"):
            records[kind].append(payload)
        return mix_suggestions(get(con,'client',client_id),records['client'],records['order'],records['price'],records['price_table'],now().date())


def conversion_rates(opportunities, orders, today, minimum=5):
    from pipeline_rules import canonical_stage
    orders={o['id']:o for o in orders}
    rates={}
    for stage in ('Prospectado','Proposta enviada','Negociação'):
        wins=losses=0; seen=set()
        for opp in opportunities:
            order=orders.get(opp.get('orderId'))
            lost=opp.get('stage')=='Perdido'
            if lost:
                terminal=[h.get('at','')[:10] for h in opp.get('stageHistory',[]) if h.get('stage')=='Perdido' and h.get('at','')[:10]<=str(today)]
                end=max(terminal,default='')
            else:
                if not order or order.get('clientId')!=opp.get('clientId') or order.get('status') not in ('Confirmado','Faturado','Entregue') or order['id'] in seen: continue
                end=str(order.get('date',''))[:10]
            try: end_date=date.fromisoformat(end)
            except ValueError: continue
            if end_date>today: continue
            visited=False
            for h in opp.get('stageHistory',[]):
                try: when=date.fromisoformat(str(h.get('at',''))[:10])
                except ValueError: continue
                if canonical_stage(h.get('stage'))==stage and when<=end_date: visited=True
            if not visited: continue
            if lost: losses+=1
            else: wins+=1;seen.add(order['id'])
        sample=wins+losses
        rates[stage]={'sample':sample,'wins':wins,'rate':wins/sample if sample>=minimum else None}
    return rates

@router.get('/forecast')
def forecast(authorization: str | None = Header(default=None)):
    user=access(authorization)
    with _services['db']() as con:
        all_opp=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='opportunity'")]
        all_orders=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='order'")]
        own=_services['scoped_rows'](con,'opportunity',user)
        local=conversion_rates(own,all_orders,now().date()); global_rates=conversion_rates(all_opp,all_orders,now().date())
        result={}
        for stage, rate in local.items():
            fallback=rate['rate'] is None
            selected=global_rates[stage] if fallback else rate
            result[stage]={**selected,'source':'global' if fallback else 'carteira', 'message':('Dados insuficientes para histórico — calculando pela média global' if selected['rate'] is not None else 'Dados insuficientes na carteira e na média global') if fallback else 'Histórico da carteira autorizada'}
    return {'rates':result,'basis':'Conversão em pedido confirmado; sem horizonte de faturamento mensal'}

@router.get('/readiness')
def readiness(authorization: str | None = Header(default=None)):
    import os
    user=access(authorization)
    if user not in MANAGERS: raise HTTPException(403,'Diagnóstico restrito à gestão')
    with _services['db']() as con:
        industries=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='industry'")]
        missing=[i.get('name',i['id']) for i in industries if i.get('active') and not i.get('commercialPolicy') and not (i.get('minimumByState') and i.get('freight') and i.get('paymentOptions'))]
        history=con.execute("SELECT min(payload->>'date'),max(payload->>'date'),count(*) FROM entities WHERE kind='order'").fetchone()
    return {'missingCommercialPolicies':sorted(missing),'orderHistory':{'first':history[0],'last':history[1],'count':history[2]},
            'speedioConfigured':bool(os.getenv('SPEEDIO_USERNAME') and os.getenv('SPEEDIO_PASSWORD')),
            'emailIntegration':'Gmail/Outlook ainda requerem OAuth do CRM; conexão de e-mail do ChatGPT não conecta o ERP',
            'productionReady':False, 'pending':['Homologação autenticada em staging com carteira real','Conexões OAuth Gmail/Outlook','Preenchimento das políticas comerciais ausentes']}


def qualify_pipeline(con, client, evidence, next_date, brand='Multimarcas'):
    """Idempotent first-stage entry after verified lead qualification, never an orphan card."""
    identifier='qualified-'+hashlib.sha256(client['id'].encode()).hexdigest()[:48]
    if con.execute("SELECT 1 FROM entities WHERE kind='opportunity' AND id=%s",(identifier,)).fetchone(): return identifier
    action={'id':identifier,'clientId':client['id'],'owner':client['owner'],'brand':brand,'stage':'Prospectado',
            'amount':'0.00','next':'Confirmar mix, condições e agendar apresentação','followUp':str(next_date),
            'contactEvidence':evidence,'createdAt':now().isoformat(),'stageHistory':[{'stage':'Prospectado','at':now().isoformat(),'by':'Qualificação automática'}]}
    from pipeline_rules import validate_opportunity
    validate_opportunity(action,{},now().date(),client=client)
    put(con,'opportunity',action)
    put(con,'task',{'id':'task-'+identifier,'clientId':client['id'],'user':client['owner'],'sourceOpportunityId':identifier,
                    'text':action['next'],'date':str(next_date),'status':'Aberta','source':'Qualificação'})
    audit(con,client['owner'],identifier,'qualified_pipeline_created')
    return identifier
