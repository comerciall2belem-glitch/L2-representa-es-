"""Independent commercial tables and Bella Brazil rules."""
from decimal import Decimal, InvalidOperation
from fastapi import HTTPException
from psycopg.types.json import Jsonb
import json

BELLA = 'Bella Brazil'
BELLA_POLICY_VERSION = 'bella-2026-11-08'
BELLA_POLICY = {'version':BELLA_POLICY_VERSION, 'minimum':'1500.00', 'paymentTerms':['28'], 'paymentMethod':'Boleto Bancário', 'cifAbove':'3500.00', 'freightBelowOrEqual':None}
CHANNELS = {'VAREJO': 8, 'REVENDA': 8, 'ATACADO': 5}
AUDIENCES = {
    'VAREJO': 'Exclusiva para Perfumarias',
    'REVENDA': 'Grupos de lojas, redes de farmácia, redes de supermercados e distribuidores de salões de beleza',
    'ATACADO': 'Atacados e distribuidores que compram e revendem para lojas, farmácias e mercados',
}

def bella_policy(subtotal, terms, state, policy=None):
    policy = policy or BELLA_POLICY
    if state not in ('PA','AP'): raise HTTPException(422,'Bella Brazil: UF deve ser PA ou AP')
    minimum = Decimal(policy['minimum'])
    if subtotal < minimum:
        raise HTTPException(422,'Bella Brazil: pedido mínimo R$ 1.500,00 por marca, após descontos')
    if terms not in policy['paymentTerms']:
        raise HTTPException(422,'Bella Brazil: pagamento em 28 dias por boleto bancário')
    if subtotal <= Decimal(policy['cifAbove']):
        raise HTTPException(422,'Bella Brazil: frete pendente até R$ 3.500,00 inclusive; defina a condição antes de emitir')
    return {'minimum':str(minimum),'freight':'CIF','paymentTerms':terms,'paymentMethod':policy['paymentMethod'],'policyVersion':policy['version']}


def migrate_bella_policy(con):
    """One-time revision of commercial settings; never rewrites orders or prices."""
    row=con.execute("SELECT payload FROM entities WHERE kind='industry' AND id=%s FOR UPDATE",(BELLA,)).fetchone()
    if not row or row[0].get('cpqPolicy',{}).get('version')==BELLA_POLICY_VERSION:return False
    industry=dict(row[0])
    history=list(industry.get('commercialPolicyHistory') or [])
    history.append({key:industry.get(key) for key in ('commercialPolicy','minimumByState','freight','paymentOptions','notes','cpqPolicy')})
    industry.update(cpqPolicy=dict(BELLA_POLICY),minimumByState={'PA':'1500.00','AP':'1500.00'},
                    paymentOptions=['28'],freight='Condicional',commercialPolicy=[],commercialPolicyHistory=history,
                    notes='Política informada por Ana Paula em 08/11/2026: mínimo R$ 1.500,00 em PA/AP; pagamento em 28 dias por boleto. CIF somente acima de R$ 3.500,00. Até R$ 3.500,00 inclusive, frete ainda não definido: emissão bloqueada. Embalagens e comissões preservadas.')
    con.execute("UPDATE entities SET payload=%s,updated_at=now() WHERE kind='industry' AND id=%s",(Jsonb(industry),BELLA))
    con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES('Ana Paula','industry',%s,%s)",(BELLA,'policy_'+BELLA_POLICY_VERSION))
    return True


def table_id(payload):
    return payload.get('tableId') or f"{payload.get('brand', '')}|{payload.get('state', '')}"

def price_id(payload):
    return f"{table_id(payload)}|{payload.get('sku', '')}"

def validate_table(con, payload, brand, state=None, allowed_states=None):
    row = con.execute("SELECT payload FROM entities WHERE kind='price_table' AND id=%s", (table_id(payload),)).fetchone()
    if not row or not row[0].get('active') or row[0].get('brand') != brand:
        raise HTTPException(400, 'Selecione uma tabela comercial ativa da indústria')
    if state and row[0].get('state') not in (*((allowed_states or [state])), 'ALL'):
        raise HTTPException(400, 'Tabela comercial incompatível com a UF do cliente')
    return row[0]

def seed_bella(con, base):
    con.execute('CREATE TABLE IF NOT EXISTS catalog_seeds (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
    if not con.execute("INSERT INTO catalog_seeds(name) VALUES('bella-brazil-2026-v1') ON CONFLICT DO NOTHING RETURNING name").fetchone():
        return
    catalog = json.loads((base / 'bella_brazil_2026.json').read_text(encoding='utf-8'))
    industry = {'id': BELLA, 'name': BELLA, 'active': True, 'contact': '',
        'commercialPolicy': [], 'coverage': ['PA','AP'], 'packSize': 6,
        'minimumByState': {'PA':'2000.00','AP':'3000.00'}, 'freight':'CIF',
        'paymentOptions':['28','28/35','30/45/60'], 'deliveryDays':[6,9],
        'commissionByChannel': CHANNELS, 'commissionPaymentDay':15,
        'notes': 'Cobertura: Pará e Amapá. Venda e faturamento em caixas fechadas de 6 unidades de cada cor. Pedido mínimo: PA R$ 2.000,00; AP R$ 3.000,00; frete CIF. Entrega de 6 a 9 dias após confirmação. Prazos: 28; 28/35; 30/45/60 dias (máximo 60). Comissão no faturamento: Varejo 8%, Revenda 8%, Atacado 5%; pagamento todo dia 15, fechamento do dia 1 ao 30. Bonificação em produtos no primeiro pedido: 5% a 15%; demais solicitações sob consulta prévia.'}
    con.execute("INSERT INTO entities(kind,id,payload) VALUES('industry',%s,%s) ON CONFLICT DO NOTHING",(BELLA,Jsonb(industry)))
    for channel, rate in CHANNELS.items():
        rows = catalog[channel]
        if len(rows) != 160 or len({x['sku'] for x in rows}) != 160:
            raise RuntimeError('Catálogo Bella Brazil incompleto ou duplicado')
        tid = f'{BELLA}|{channel}'
        table = {'id':tid,'brand':BELLA,'state':'ALL','title':f'Bella Brazil — {channel} — AGO/2026','active':True,'channel':channel,'audience':AUDIENCES[channel],'commissionRate':rate}
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('price_table',%s,%s) ON CONFLICT DO NOTHING",(tid,Jsonb(table)))
        for row in rows:
            payload = {'brand':BELLA,'state':'ALL','tableId':tid,'packSize':6,**row}
            payload['id'] = price_id(payload)
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('price',%s,%s) ON CONFLICT DO NOTHING",(payload['id'],Jsonb(payload)))

def validate_bella_order(con, order, state, user, previous=None):
    items = [x for x in order['items'] if x['brand'] == BELLA]
    if not items:
        return
    subtotal = sum(Decimal(x['subtotal']) for x in items)
    channels = {validate_table(con,x,BELLA,state).get('channel') for x in items}
    if len(channels)!=1 or not channels.issubset(CHANNELS):
        raise HTTPException(400,'Selecione uma única tabela Bella Brazil: Varejo, Revenda ou Atacado')
    channel = next(iter(channels))
    order['bellaChannel'] = channel
    order['bellaCommissionRate'] = CHANNELS[channel]
    for item in items:
        if Decimal(str(item['quantity'])) % 6:
            raise HTTPException(400,'Bella Brazil: quantidade deve ser múltipla de 6 por cor/SKU')
        item['packSize'] = 6
        item['commissionRate'] = CHANNELS[channel]
    if order.get('status') in ('Confirmado','Faturado'):
        # Converted CPQ orders and already confirmed legacy orders keep approved conditions.
        frozen=(order.get('freightByBrand') or {}).get(BELLA) if order.get('cpqQuoteId') and order.get('cpqFingerprint') else None
        legacy=previous if previous and previous.get('status') in ('Confirmado','Faturado','Entregue') else None
        if frozen:
            order['bellaFreight']=frozen['freight']
        elif legacy:
            order['bellaFreight']=legacy.get('bellaFreight','CIF')
        else:
            rule=bella_policy(subtotal,str(order.get('paymentTerms','')).replace(' ',''),state)
            order.update(bellaFreight=rule['freight'],bellaPaymentMethod=rule['paymentMethod'],bellaPolicyVersion=rule['policyVersion'])
    try:
        bonus = Decimal(str(order.get('bellaBonusPercent',0)))
    except (InvalidOperation,ValueError,TypeError):
        raise HTTPException(400,'Percentual de bonificação inválido')
    if not bonus.is_finite() or bonus < 0 or bonus > 100:
        raise HTTPException(400,'Percentual de bonificação inválido')
    if bonus and order.get('status') in ('Confirmado','Faturado'):
        previous = con.execute("SELECT payload FROM entities WHERE kind='order' AND payload->>'clientId'=%s AND id<>%s UNION ALL SELECT payload FROM archived_entities WHERE kind='order' AND payload->>'clientId'=%s AND id<>%s",(order['clientId'],order['id'],order['clientId'],order['id'])).fetchall()
        repeat = any(x[0].get('status') in ('Confirmado','Faturado') and (x[0].get('brand')==BELLA or any(i.get('brand')==BELLA for i in x[0].get('items',[]))) for x in previous)
        if repeat:
            if user!='Ana Paula' or not str(order.get('bellaBonusApproval','')).strip():
                raise HTTPException(400,'Bonificação após o primeiro pedido exige consulta e registro da aprovação pela administradora')
        elif not Decimal('5') <= bonus <= Decimal('15'):
            raise HTTPException(400,'Bonificação de introdução: informe de 5% a 15%, ou zero sem bonificação')
    order['bellaBonusPercent'] = str(bonus)
    order['bellaBonusValue'] = str((subtotal * bonus / 100).quantize(Decimal('0.01')))
    order.setdefault('bellaFreight','A definir' if subtotal<=Decimal('3500') else 'CIF')
    order['bellaDeliveryDays'] = [6,9]
