from decimal import Decimal, ROUND_HALF_UP
import re,unicodedata
def key(v):return re.sub(r"[^a-z0-9]+","-",unicodedata.normalize("NFD",str(v or "")).encode("ascii","ignore").decode().lower()).strip("-")
D=lambda v:Decimal(str(v or 0))
def rounded(v):return float(v.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))
def analyze_finance(inputs,orders,ledger,rates,month,goals=()):
    end=month+'-31'
    def latest(kind,key,asof=None):
        rows=[x for x in inputs if x['kind']==kind and x['effectiveDate']<=(asof or end) and key(x)]
        return max(rows,key=lambda x:(x['effectiveDate'],x.get('updatedAt','')),default=None)
    billed=[o for o in orders if o.get('status')=='Faturado' and str(o.get('billedAt') or o.get('date','')).startswith(month)]
    sales=sum((D(o.get('amount')) for o in billed),Decimal(0));margin=Decimal(0);products={};missing=[]
    for o in billed:
        seller=o.get('sellerResponsible') or o.get('commissionSeller') or o.get('user')
        contract=latest('seller_contract',lambda x:x['name']==seller,str(o.get('date') or end))
        volume=sum((D(x.get('amount')) for x in billed if (x.get('sellerResponsible') or x.get('commissionSeller') or x.get('user'))==seller),Decimal(0))
        rate=D(contract['commissionRate']) if contract else D(o.get('sellerCommissionRate')) if o.get('sellerCommissionRate') is not None else None
        if contract:
            for tier in sorted(contract.get('tiers',[]),key=lambda t:D(t['minimum'])):
                if volume>=D(tier['minimum']):rate=D(tier['rate'])
        if contract and contract.get('basis')=='Recebido':missing.append('Regra por recebimento requer liquidação vinculada: '+seller);rate=None
        for item in o.get('items',[]):
            brand=item.get('brand') or o.get('brand');sku=item.get('sku');cost=latest('product_cost',lambda x:x.get('brand')==brand and x.get('sku')==sku,str(o.get('date') or end))
            industry=next((r for r in rates if key(r.get('id',''))==key(brand) or r.get('brand')==brand),None)
            amount=D(item.get('unitPrice'))*D(item.get('quantity'))
            if not cost or not industry or rate is None:missing.append(str(brand)+' / '+str(sku));continue
            contribution=amount*(D(industry['rate'])-rate)/100-D(cost['amount'])*D(item['quantity']);margin+=contribution
            product_key=(brand,sku);r=products.setdefault(product_key,{'brand':brand,'sku':sku,'sales':Decimal(0),'contribution':Decimal(0)})
            r['sales']+=amount;r['contribution']+=contribution
    bonusRules=[x for name in {i['name'] for i in inputs if i['kind']=='seller_contract'} if (x:=latest('seller_contract',lambda i:i['name']==name)) and x.get('bonusRate') is not None]
    target=sum((D(g.get('amount')) for g in goals if g.get('month')==month and not g.get('brand')),Decimal(0))
    bonuses=[]
    for rule in bonusRules:
        amount= sales*D(rule['bonusRate'])/100 if target>0 and sales>=target*D(rule.get('targetPercent',100))/100 else Decimal(0)
        pending=target<=0 or rule.get('basis')=='Recebido'
        bonuses.append({'seller':rule['name'],'amount':None if pending else rounded(amount)})
        if pending:missing.append('Meta ou recebimentos pendentes para bônus: '+rule['name'])
        else:margin-=amount
    complete=bool(billed) and not missing
    fixedNames={x['name'] for x in inputs if x['kind']=='operating_cost'}
    fixed=sum((D(r['amount']) for name in fixedNames if (r:=latest('operating_cost',lambda x:x['name']==name))),Decimal(0))
    ratio=margin/sales if complete and sales else None
    ticket=fixed/len(billed)/ratio if ratio and ratio>0 and fixedNames else None
    accounts={x['name'] for x in inputs if x['kind']=='opening_balance'}
    balances=[latest('opening_balance',lambda x:x['name']==name) for name in accounts];balances=[x for x in balances if x]
    cash=sum((D(x['amount']) for x in balances),Decimal(0));flows={}
    # Saldo inicial + liquidações posteriores; previstos são segregados.
    start=min((x['effectiveDate'] for x in balances),default=month+'-01')
    unknown=0
    for r in ledger:
        paid=r.get('Situação')=='Pago/Recebido';day=r.get('Liquidação') or r.get('Data') if paid else r.get('Vencimento')
        if not day:unknown+=1;continue
        if day<start:continue
        sign=1 if r.get('Tipo')=='Receita' else -1
        flows[day]=flows.get(day,Decimal(0))+sign*D(r.get('Valor'))
    low=cash;points=[]
    for day,delta in sorted(flows.items()):
        cash+=delta;low=min(low,cash);points.append({'date':day,'balance':rounded(cash)})
    return {'bonuses':bonuses,'sales':rounded(sales),'contribution':rounded(margin) if complete else None,'missing':sorted(set(missing)),'ticketBreakEven':rounded(ticket) if ticket is not None else None,'workingCapitalGap':rounded(max(-low,Decimal(0))) if balances and not unknown and len({x['effectiveDate'] for x in balances})==1 else None,'projection':points if balances else [],'fixedCost':rounded(fixed) if fixedNames else None,'products':[dict(brand=r['brand'],sku=r['sku'],sales=rounded(r['sales']),contribution=rounded(r['contribution'])) for r in sorted(products.values(),key=lambda r:r['contribution'],reverse=True)],'undatedFlows':unknown,'note':'Projeção limitada aos títulos cadastrados; estoque inicial não é receita nem saída de caixa.'}
