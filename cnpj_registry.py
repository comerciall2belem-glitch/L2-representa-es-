"""CNPJ.ws registry adapter: company data and explicitly active state registrations."""
import json,time,threading,urllib.request,urllib.error,re
from collections import deque

class RegistryError(Exception):
    def __init__(self,status,message):self.status=status;super().__init__(message)
_lock=threading.Lock()
_recent=deque()

def normalize_company(raw,cnpj):
    est=raw.get('estabelecimento') or {}
    if est.get('cnpj')!=cnpj:raise RegistryError(502,'A consulta retornou um CNPJ diferente. Confira o cadastro manualmente.')
    uf=str((est.get('estado') or {}).get('sigla') or '').upper()
    registrations=[re.sub(r'\D','',str(x.get('inscricao_estadual') or '')) for x in est.get('inscricoes_estaduais') or [] if x.get('ativo') is True and (x.get('estado') or {}).get('sigla')==uf]
    registrations=list(dict.fromkeys(x for x in registrations if re.fullmatch(r'\d{7,14}',x)))
    simples=(raw.get('simples') or {}).get('simples')
    data={'name':str(raw.get('razao_social') or ''),'tradeName':str(est.get('nome_fantasia') or ''),'taxId':cnpj,'state':uf,'city':str((est.get('cidade') or {}).get('nome') or ''),'district':str(est.get('bairro') or ''),'address':' '.join(str(est.get(k) or '') for k in ('tipo_logradouro','logradouro','numero','complemento')).strip(),'phone':str(est.get('ddd1') or '')+str(est.get('telefone1') or ''),'email':str(est.get('email') or ''),'stateRegistration':registrations[0] if len(registrations)==1 else '', 'taxRegime':'SIMPLES' if simples in ('Sim','SIM','sim',True) else 'NORMAL' if simples in ('Não','NAO','Nao','não',False) else '', 'postalCode':str(est.get('cep') or '')}
    missing=[k for k in ('name','city','state','stateRegistration') if not data[k]]
    return {'client':data,'source':'CNPJ.ws · Receita Federal / cadastros estaduais','registryUpdatedAt':est.get('atualizado_em') or raw.get('atualizado_em'),'missing':missing,'registrations':registrations}

def lookup_registry(cnpj,opener=None):
    with _lock:
        now=time.monotonic()
        while _recent and now-_recent[0]>=60:_recent.popleft()
        if len(_recent)>=3:raise RegistryError(429,'Limite temporário de consultas. Aguarde um minuto e consulte novamente.')
        _recent.append(now)
    request=urllib.request.Request('https://publica.cnpj.ws/cnpj/'+cnpj,headers={'Accept':'application/json','User-Agent':'L2-One/1.0'})
    try:
        with (opener or urllib.request.urlopen)(request,timeout=15) as response:raw=json.loads(response.read(1024*1024).decode())
    except urllib.error.HTTPError as exc:
        status=404 if exc.code==404 else 429 if exc.code==429 else 502
        raise RegistryError(status,'CNPJ não encontrado no provedor.' if status==404 else 'Limite temporário de consultas. Aguarde um minuto.' if status==429 else 'Consulta cadastral indisponível. Tente novamente ou preencha manualmente.') from exc
    except (OSError,ValueError) as exc:raise RegistryError(502,'Consulta cadastral indisponível. Tente novamente ou preencha manualmente.') from exc
    if not isinstance(raw,dict):raise RegistryError(502,'Resposta cadastral inválida.')
    return normalize_company(raw,cnpj)
