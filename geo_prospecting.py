"""User-triggered nearby discovery from scoped CRM and OpenStreetMap; no fabricated ICP."""
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from fastapi import APIRouter, Header, HTTPException
from pydantic import Field
import strategic_crm as crm

router=APIRouter(prefix='/api/strategy/tdc',tags=['Prospecção geográfica'])
FILTERS={'Cosméticos':[('shop','cosmetics'),('shop','beauty'),('shop','perfumery')],'Farma':[('amenity','pharmacy'),('shop','chemist')],'Salão':[('shop','hairdresser'),('shop','beauty')],'Alimentar':[('shop','supermarket'),('shop','convenience'),('shop','grocery')],'Departamento':[('shop','department_store'),('shop','mall')],'Atacado':[('shop','wholesale')],'Especializada':[('shop','cosmetics'),('shop','beauty')]}
_cache={};_searches={};_lock=threading.Lock();_geo_lock=threading.Lock();_last_geo=0.0

def norm(value):return ' '.join(''.join(c for c in unicodedata.normalize('NFD',str(value or '')).casefold() if unicodedata.category(c)!='Mn').split())
def distance(lat,lon,item):
    point=crm.coords(item)
    if not point:return None
    a,b,c,d=map(math.radians,(lat,lon,*point));h=math.sin((c-a)/2)**2+math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return round(6371*2*math.asin(math.sqrt(min(1,h))),3)

def _fetch(url,data=None,timeout=20):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise ValueError('Provedor inválido')
    request=urllib.request.Request(url,data=data,headers={'Accept':'application/json','User-Agent':'L2-One/1.0 (https://l2-one.onrender.com; user-triggered prospecting)'})
    with urllib.request.urlopen(request,timeout=timeout) as response:raw=response.read(6_000_001)
    if len(raw)>6_000_000:raise ValueError('Resposta excedeu limite')
    return json.loads(raw)

def geocode(query,state):
    global _last_geo
    key=('geocode',norm(query),state)
    with _geo_lock:
        cached=_cache.get(key)
        if cached and cached[0]>time.time():return cached[1]
        delay=1.1-(time.monotonic()-_last_geo)
        if delay>0:time.sleep(delay)
        _last_geo=time.monotonic()
        params=urllib.parse.urlencode({'q':query+', '+('Pará' if state=='PA' else 'Amapá')+', Brasil','format':'jsonv2','addressdetails':1,'countrycodes':'br','limit':5})
        data=_fetch(os.getenv('L2_GEOCODING_URL','https://nominatim.openstreetmap.org/search')+'?'+params,timeout=15)
        rows=[]
        for x in data:
            address=x.get('address',{})
            if address.get('country_code')!='br' or norm(address.get('state'))!=norm('Pará' if state=='PA' else 'Amapá'):continue
            lat,lon=float(x['lat']),float(x['lon'])
            if not crm.coords({'latitude':lat,'longitude':lon}):continue
            rows.append({'name':x['display_name'][:500],'latitude':lat,'longitude':lon,'state':state})
        _cache[key]=(time.time()+86400,rows)
        return rows

class Search(crm.StrictModel):
    state:str=Field(pattern='^(PA|AP)$')
    city:str=Field(default='',max_length=120)
    channel:str=Field(default='Cosméticos',max_length=40)
    segment:str=Field(default='',max_length=120)
    latitude:float=Field(ge=-90,le=90,allow_inf_nan=False)
    longitude:float=Field(ge=-180,le=180,allow_inf_nan=False)
    radiusKm:float=Field(default=5,ge=.1,le=10,allow_inf_nan=False)
    external:bool=True

def query_for(data):
    if data.channel not in FILTERS:raise HTTPException(422,'Selecione um segmento de comércio')
    criteria=FILTERS[data.channel];around=f'(around:{int(data.radiusKm*1000)},{data.latitude:.6f},{data.longitude:.6f})'
    # Resolve the small nearby set first, then intersect with the requested UF.
    # Scanning a whole state separately for each category caused provider timeouts.
    groups={}
    for key,value in criteria:groups.setdefault(key,[]).append(value)
    pieces=''.join(f'nwr{around}["{key}"~"^({"|".join(values)})$"]["name"];' for key,values in groups.items())
    return f'[out:json][timeout:15];({pieces})->.nearby;area["ISO3166-2"="BR-{data.state}"]->.region;nwr.nearby(area.region);out tags center 150;'

def query_provider(query):
    # GET avoids the POST transport failures observed on the public endpoint.
    configured=os.getenv('L2_PROSPECTING_URL')
    providers=[configured] if configured else ['https://maps.mail.ru/osm/tools/overpass/api/interpreter','https://overpass.private.coffee/api/interpreter']
    last=None
    for index,url in enumerate(providers):
        try:
            result=_fetch(url+'?'+urllib.parse.urlencode({'data':query}),timeout=32 if index==0 else 12)
            if not isinstance(result,dict) or not isinstance(result.get('elements'),list) or result.get('remark'):
                raise ValueError('Consulta do provedor incompleta')
            return result
        except (OSError,ValueError,TypeError,KeyError) as error:
            last=error
            logging.getLogger('uvicorn.error').warning('prospecting_provider_failed host=%s error=%s code=%s',urllib.parse.urlparse(url).hostname,type(error).__name__,getattr(error,'code',''))
    raise last

def external_search(data):
    query=query_for(data);key=('places',query)
    with _lock:
        if len(_cache)>300:
            for k in list(_cache)[:150]:_cache.pop(k,None)
        cached=_cache.get(key)
        if cached and cached[0]>time.time():return cached[1]
        result=query_provider(query)
        rows=[];seen=set()
        for element in result.get('elements',[]):
            tags=element.get('tags',{});center=element.get('center') or element
            lat,lon=center.get('lat'),center.get('lon')
            item={'latitude':lat,'longitude':lon}
            km=distance(data.latitude,data.longitude,item)
            if km is None or km>data.radiusKm or not tags.get('name'):continue
            signature=(norm(tags['name']),round(lat,4),round(lon,4))
            if signature in seen:continue
            seen.add(signature)
            phone=str(tags.get('contact:phone') or tags.get('phone') or '')[:30]
            rows.append({'sourceId':'osm:'+str(element.get('type'))+':'+str(element.get('id')),'name':str(tags['name'])[:180],
                'state':data.state,'city':str(tags.get('addr:city') or '')[:120],'channel':data.channel,'latitude':lat,'longitude':lon,
                'address':' '.join(str(tags.get(k) or '') for k in ('addr:street','addr:housenumber')).strip()[:300],
                'district':str(tags.get('addr:suburb') or tags.get('addr:neighbourhood') or '')[:120],
                'phone':phone,'distanceKm':km,'source':'OpenStreetMap','status':'Necessidade e CNPJ a confirmar','clientId':None})
        rows.sort(key=lambda x:x['distanceKm']);_cache[key]=(time.time()+3600,rows[:50]);return rows[:50]

def base_search(clients,leads,data):
    rows=[];missing=0;seen=set()
    for item in [*clients,*leads]:
        if item.get('state')!=data.state:continue
        if data.city and item.get('city') and norm(data.city) not in norm(item.get('city')):continue
        info=item.get('tdc') or {};channel=item.get('channel') or info.get('channel') or item.get('segment') or ''
        text=' '.join(str(item.get(k) or '') for k in ('channel','segment','business','name','tags','brands'))+' '+str(info.get('segment',''))
        aliases={'Cosméticos':['cosmetic','perfum','maquiag'],'Salão':['salao','cabeleir','hair'],'Farma':['farma','drog','pharm'],'Alimentar':['aliment','supermerc','mercad'],'Departamento':['departamento','magazine'],'Atacado':['atacad'],'Especializada':['especializ','cosmetic','perfum']}
        if norm(channel)!=norm(data.channel) and not any(v in norm(text) for v in aliases[data.channel]):continue
        if data.segment and norm(data.segment) not in norm(text):continue
        km=distance(data.latitude,data.longitude,item)
        if km is None:missing+=1;continue
        if km>data.radiusKm:continue
        cid=item.get('clientId') or (item['id'] if item in clients else None)
        key=cid or item['id']
        if key in seen:continue
        seen.add(key)
        rows.append({**{k:item.get(k) for k in ('id','name','taxId','city','state','latitude','longitude','phone','address','district','tdc')},'channel':channel or data.channel,'distanceKm':km,'clientId':cid,'source':'Minha Carteira' if cid else 'Prospecção L2','status':item.get('stage') or item.get('status') or 'Cadastro existente'})
    return sorted(rows,key=lambda x:x['distanceKm'])[:100],missing

@router.get('/geocode')
def locate(q:str,state:str,authorization:str|None=Header(default=None)):
    crm.access(authorization)
    if state not in ('PA','AP') or not 3<=len(q.strip())<=200:raise HTTPException(422,'Informe cidade, bairro ou endereço do PA/AP')
    try:return {'locations':geocode(q.strip(),state),'source':'OpenStreetMap/Nominatim'}
    except (OSError,ValueError,KeyError,TypeError):raise HTTPException(503,'Localização indisponível. Use GPS ou informe as coordenadas.')

def source_cache_id(data):return 'osm-'+hashlib.sha256(query_for(data).encode()).hexdigest()

def read_source_cache(data):
    with crm._services['db']() as con:
        record=con.execute("SELECT payload FROM entities WHERE kind='geo_source_cache' AND id=%s",(source_cache_id(data),)).fetchone()
    if not record:return None
    cached=record[0]
    try:
        age=time.time()-float(cached['capturedAt'])
        if not 0<=age<=86400 or not isinstance(cached['rows'],list):return None
        return cached,age
    except (ValueError,KeyError,TypeError):return None

def write_source_cache(data,rows):
    with crm._services['db']() as con:
        crm.put(con,'geo_source_cache',{'id':source_cache_id(data),'capturedAt':time.time(),'rows':rows})
        con.execute("DELETE FROM entities WHERE kind='geo_source_cache' AND updated_at < now()-interval '7 days'")

@router.post('/search')
def search(data:Search,authorization:str|None=Header(default=None)):
    user=crm.access(authorization);query_for(data)
    with crm._services['db']() as con:
        clients=crm._services['scoped_rows'](con,'client',user)
        all_users=not crm._services['is_seller'](con,user)
        leads=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='lead' AND (%s OR payload->>'owner'=%s) ORDER BY updated_at DESC LIMIT 1000",(all_users,user)).fetchall()]
    rows,missing=base_search(clients,leads,data);warning='';external=[];external_status='not_requested'
    if data.external:
        cached=None
        try:cached=read_source_cache(data)
        except Exception as error:
            logging.getLogger('uvicorn.error').warning('prospecting_cache_read_failed error=%s',type(error).__name__)
        if cached and cached[1]<3600:
            external=cached[0]['rows'];external_status='cached'
        else:
            try:
                external=external_search(data);external_status='ok'
                try:write_source_cache(data,external)
                except Exception as error:
                    logging.getLogger('uvicorn.error').warning('prospecting_cache_write_failed error=%s',type(error).__name__)
            except (OSError,ValueError,TypeError,KeyError):
                if cached:
                    external=cached[0]['rows'];external_status='cached';warning='Provedor temporariamente indisponível. Exibindo a última consulta pública válida, realizada há '+str(round(cached[1]/3600,1))+' horas.'
                else:
                    external_status='unavailable';warning='A consulta de novos estabelecimentos não foi concluída. O provedor está indisponível ou ocupado. Os resultados internos abaixo não representam a busca externa; tente novamente.'
    if external_status=='cached' and not warning:warning='Resultados da consulta pública recente, armazenada há '+str(round(cached[1]/60))+' minutos.'
    for candidate in external:
        match=next((c for c in clients if norm(c.get('name'))==norm(candidate['name']) and distance(candidate['latitude'],candidate['longitude'],c) is not None and distance(candidate['latitude'],candidate['longitude'],c)<.05),None)
        if match:
            if not any(r.get('clientId')==match['id'] for r in rows):rows.append({**candidate,'clientId':match['id'],'source':'Minha Carteira','status':'Cliente já cadastrado'})
        else:
            saved=next((x for x in leads if x.get('sourceId')==candidate['sourceId'] and x.get('owner')==user),None)
            rows.append({**candidate,'saved':bool(saved),'id':saved.get('id') if saved else None})
    rows.sort(key=lambda x:x['distanceKm']);token=uuid.uuid4().hex
    if len(_searches)>200:
        for k in list(_searches)[:100]:_searches.pop(k,None)
    _searches[token]={'user':user,'expires':time.time()+1800,'rows':external}
    return {'results':rows,'missingCoordinates':missing,'warning':warning,'externalStatus':external_status,'searchId':token,'attribution':'© OpenStreetMap contributors · ODbL','sourceNotice':'Distância em linha reta. Dados públicos não comprovam CNPJ, porte, funcionamento ou intenção de compra.'}

class Save(crm.StrictModel):
    searchId:str=Field(min_length=32,max_length=32)
    sourceId:str=Field(min_length=5,max_length=80)

@router.post('/prospects')
def save_prospect(data:Save,authorization:str|None=Header(default=None)):
    user=crm.access(authorization);search=_searches.get(data.searchId)
    if not search or search['user']!=user or search['expires']<time.time():raise HTTPException(409,'Refaça a busca para salvar o prospect')
    item=next((r for r in search['rows'] if r['sourceId']==data.sourceId),None)
    if not item:raise HTTPException(422,'Estabelecimento não encontrado nesta busca')
    identifier='geo-'+hashlib.sha256((user+'|'+data.sourceId).encode()).hexdigest()[:40]
    with crm._services['db']() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
        stored=con.execute("SELECT payload FROM entities WHERE kind='lead' AND id=%s",(identifier,)).fetchone()
        old=stored[0] if stored else None
        payload={**item,'id':identifier,'owner':user,'origin':'OpenStreetMap','status':'Prospect a conferir','createdAt':datetime.now(crm.TZ).isoformat(),**(old or {})}
        crm.put(con,'lead',payload);crm.changed(con)
        con.execute('INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,%s,%s,%s)',(user,'lead',identifier,'capture_geo'))
    return {'leadId':identifier,'saved':True,'status':'Prospect salvo; complete CNPJ e evidência para qualificar e incluir na carteira'}
