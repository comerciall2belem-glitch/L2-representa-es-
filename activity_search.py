"""Server-side activity search with the same portfolio scope as record sync."""
from datetime import date
from fastapi import Header, HTTPException
from global_search_server import CLIENT_DOCUMENT, document, query_tokens

CORE_KINDS = ('task', 'visit', 'interaction', 'opportunity', 'route')
OFFICE_KINDS = ('office_action', 'office_commercial', 'office_administrative')
ACTIVITY_DOCUMENT = document('a', ('text','notes','next','result','type','status','stage','user','owner','brand','title','Demanda','Demanda ou problema','Responsável','Status','Observações','Próxima ação'))
ACTIVITY_DATE = "coalesce(nullif(a.payload->>'date',''),nullif(left(a.payload->>'at',10),''),nullif(a.payload->>'closeDate',''),nullif(a.payload->>'Data abertura',''),nullif(a.payload->>'Prazo',''),left(a.updated_at::text,10))"
ACTIVITY_OWNER = "coalesce(nullif(a.payload->>'user',''),a.payload->>'owner',a.payload->>'Responsável','')"
ACTIVITY_STATUS = "coalesce(nullif(a.payload->>'status',''),a.payload->>'stage',a.payload->>'result',a.payload->>'Status','')"

def conditions(user, seller, permissions):
    kinds = list(CORE_KINDS)
    if not seller and 'office' in permissions: kinds.extend(OFFICE_KINDS)
    sql = ["a.kind=ANY(%s)"]
    args = [kinds]
    if seller:
        sql.append("""((c.id IS NOT NULL AND (c.payload->>'owner'=%s OR EXISTS
          (SELECT 1 FROM client_seller_authorizations ca WHERE ca.client_id=c.id AND ca.seller_username=%s AND ca.active)))
          OR (a.kind='task' AND coalesce(a.payload->>'clientId','')='' AND a.payload->>'user'=%s))""")
        args.extend([user,user,user])
        sql.append("(a.kind<>'route' OR a.payload->>'user'=%s)");args.append(user)
    if 'finance' not in permissions:
        sql.append("(a.kind NOT IN ('office_action','office_commercial','office_administrative') OR lower(btrim(coalesce(a.payload->>'Área','')))<>'financeiro')")
    return sql,args

def search(con, user, seller, permissions, q='', kind='', owner='', status='', start='', end='', offset=0):
    tokens = query_tokens(q)
    if q.strip() and not tokens: return {'activities':[], 'hasMore':False}
    for value in (owner,status):
        if len(value)>120:raise ValueError('Filtro muito extenso')
    for value in (start,end):
        if value:
            try:
                if date.fromisoformat(value).isoformat()!=value:raise ValueError()
            except ValueError:raise ValueError('Data de filtro inválida') from None
    if start and end and start>end:raise ValueError('A data inicial deve ser anterior à final')
    if not 0<=offset<=100000:raise ValueError('Página inválida')
    sql,args=conditions(user,seller,permissions)
    if kind:
        if kind not in CORE_KINDS+OFFICE_KINDS:raise ValueError('Tipo de atividade inválido')
        sql.append('a.kind=%s');args.append(kind)
    if tokens:sql.append(f'({ACTIVITY_DOCUMENT} || {CLIENT_DOCUMENT}) @@ to_tsquery(\'simple\'::regconfig, %s)');args.append(tokens)
    for expression,value in ((ACTIVITY_OWNER,owner),(ACTIVITY_STATUS,status)):
        if value:sql.append(expression+'=%s');args.append(value)
    for operator,value in (('>=',start),('<=',end)):
        if value:sql.append(ACTIVITY_DATE+operator+'%s');args.append(value)
    args.extend([11,offset])
    rows=con.execute(f"""SELECT a.kind,a.id,a.payload,c.payload->>'name', {ACTIVITY_DATE}, {ACTIVITY_OWNER}, {ACTIVITY_STATUS}
      FROM entities a LEFT JOIN entities c ON c.kind='client' AND c.id=a.payload->>'clientId'
      WHERE {' AND '.join(sql)} ORDER BY a.updated_at DESC,a.kind,a.id LIMIT %s OFFSET %s""",tuple(args)).fetchall()
    return {'activities':[{'kind':k,'id':id,'title':str(p.get('text') or p.get('title') or p.get('Demanda ou problema') or p.get('Demanda') or p.get('next') or p.get('notes') or p.get('result') or p.get('type') or p.get('brand') or 'Atividade')[:240], 'clientName':client or p.get('Cliente') or 'Geral','date':day,'owner':owner,'status':status} for k,id,p,client,day,owner,status in rows[:10]],'hasMore':len(rows)>10}

def detail(con,user,seller,permissions,kind,entity_id):
    if kind not in CORE_KINDS+OFFICE_KINDS or not 1<=len(entity_id)<=128:raise HTTPException(404,'Atividade não encontrada')
    sql,args=conditions(user,seller,permissions)
    sql.extend(['a.kind=%s','a.id=%s']);args.extend([kind,entity_id])
    row=con.execute(f"""SELECT a.payload AS activity_payload,c.payload AS client_payload FROM entities a
      LEFT JOIN entities c ON c.kind='client' AND c.id=a.payload->>'clientId'
      WHERE {' AND '.join(sql)}""",tuple(args)).fetchone()
    if not row:raise HTTPException(404,'Atividade não encontrada ou sem permissão')
    return {'kind':kind,'activity':{**row[0],'id':entity_id},'client':row[1]}

def install(app,db,auth,require_sector,sectors_for,is_seller):
    @app.get('/api/activities/search')
    def activity_search(q: str='',kind: str='',owner: str='',status: str='',start: str='',end: str='',offset: int=0,authorization: str|None=Header(default=None)):
        user=auth(authorization);require_sector(user,'commercial','office')
        try:
            with db() as con:return search(con,user,is_seller(con,user),sectors_for(user),q,kind,owner,status,start,end,offset)
        except ValueError as e:raise HTTPException(400,str(e)) from e
    @app.get('/api/activities/{kind}/{entity_id}')
    def activity_detail(kind: str,entity_id: str,authorization: str|None=Header(default=None)):
        user=auth(authorization);require_sector(user,'commercial','office')
        with db() as con:return detail(con,user,is_seller(con,user),sectors_for(user),kind,entity_id)
