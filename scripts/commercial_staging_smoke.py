"""Authenticated acceptance on the dedicated isolated Render staging service only.
Credentials are read from a private file, never command arguments or stdout.
"""
import hashlib
import io
import json
import os
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
from openpyxl import load_workbook
from pypdf import PdfReader

BASE='https://l2-one-next-staging.onrender.com'
BRAND='HOMOLOGAÇÃO L2 Planejamento'
ROOT='/api/strategy/commercial'

def main():
    credentials=json.loads(Path(os.environ['L2_SMOKE_CREDS_FILE']).read_text())
    checks=[]
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append(name)
    with httpx.Client(base_url=BASE,timeout=60) as c:
        def call(method,path,token=None,**kw):
            response=c.request(method,path,headers={'Authorization':'Bearer '+token} if token else {},**kw)
            response.raise_for_status();return response
        check('health',call('GET','/health').json()['status']=='ok')
        def login(user,key):
            data=call('POST','/api/login',json={'user':user,'password':credentials[key]}).json()
            check('login_'+key,not data.get('mustChangePassword') and bool(data.get('token')))
            return data['token']
        manager=login('__homolog_planning_manager','manager_password')
        rep=login('__homolog_planning_rep','rep_password')
        rep_second=login('__homolog_planning_rep','rep_password')
        try:
            capabilities=call('GET',ROOT+'/status',manager).json()
            check('module_enabled',capabilities['enabled'])
            check('bank_provider_not_activated',not capabilities['bank_api'] and not capabilities['boleto_issuance'])
            day=datetime.now(ZoneInfo('America/Belem')).date()
            filters={'start':str(day.replace(day=1)),'end':str(day+timedelta(days=1)),'brand':BRAND}
            summary=call('GET',ROOT+'/indicators',manager,params=filters).json()
            own=call('GET',ROOT+'/indicators',rep,params=filters).json()
            check('manager_sell_through',summary['metrics']['sell_through']==300)
            check('representative_scope',own['metrics']['sell_through']==100)
            check('gmv_no_double_count',summary['stages']['gmv_sell_out_net']==150)
            check('history_not_fabricated',summary['metrics']['starts_12m'] is None)
            check('financial_rbac',c.get(ROOT+'/finance/receivables',headers={'Authorization':'Bearer '+rep}).status_code==403)
            check('authentication_required',c.get(ROOT+'/indicators',params=filters).status_code==401)
            check('private_port_hidden',c.get(ROOT+'/interactions/planning-smoke-port-b',headers={'Authorization':'Bearer '+rep}).status_code==404)
            ports=call('GET',ROOT+'/ports',rep,params={'q':'planning-smoke-port-a'}).json()
            # Query the exact technical label; all client identifiers remain server-authorized.
            if not ports['ports']:
                ports=call('GET',ROOT+'/ports',rep,params={'q':'HOMOLOGAÇÃO L2 A'}).json()
            port=next(p for p in ports['ports'] if p['id']=='planning-smoke-port-a')
            profile={'expected_version':port['version'],'tax_id':'11222333000181','channel':'Salão de Beleza','profile':'Premium','management':'Familiar','focus':'Tratamento','portfolio':'Estratégico','needs':'TESTE: reposição e retenção'}
            saved=call('PUT',ROOT+'/ports/planning-smoke-port-a/profile',rep,json=profile).json()
            second=call('GET',ROOT+'/ports',rep_second,params={'q':'HOMOLOGAÇÃO L2 A'}).json()['ports'][0]
            check('two_sessions_profile_visibility',second['version']==saved['version'])
            check('two_sessions_conflict_protected',c.put(ROOT+'/ports/planning-smoke-port-a/profile',headers={'Authorization':'Bearer '+rep_second},json=profile).status_code==409)
            item={'id':'smoke-consultative-'+str(day),'client_id':'planning-smoke-port-a','needs':'TESTE: reposição consultiva','expectations':'Melhorar disponibilidade','next_action':'TESTE: apresentar mix','due':str(day),'confirmed':True}
            call('POST',ROOT+'/interactions',rep,json=item)
            history=call('GET',ROOT+'/interactions/planning-smoke-port-a',rep_second).json()
            check('two_sessions_interaction_visibility',any(x['data']['id']==item['id'] for x in history))
            call('POST',ROOT+'/interactions',rep,json=item)
            history_again=call('GET',ROOT+'/interactions/planning-smoke-port-a',manager).json()
            check('interaction_idempotency',sum(x['data']['id']==item['id'] for x in history_again)==1)
            pdf=call('GET',ROOT+'/reports/pdf',rep,params=filters)
            reader=PdfReader(io.BytesIO(pdf.content))
            check('pdf_download',pdf.headers['content-type'].startswith('application/pdf') and len(reader.pages)>=4)
            check('pdf_content', 'Planejamento Comercial' in reader.pages[0].extract_text())
            xlsx=call('GET',ROOT+'/reports/xlsx',rep,params=filters)
            workbook=load_workbook(io.BytesIO(xlsx.content))
            check('xlsx_download',{'Indicadores','Portfolio','Retencao'}<=set(workbook.sheetnames))
            financial=call('GET',ROOT+'/finance/receivables',manager).json()
            check('manager_finance_access',isinstance(financial,list))
            js=call('GET','/commercial_planning.js')
            check('frontend_asset', 'window.L2Commercial' in js.text)
            print(json.dumps({'passed':len(checks),'checks':checks,'sell_through_manager':300,'sell_through_representative':100,'gmv_sell_out':150,'pdf_pages':len(reader.pages),'status':'approved'},ensure_ascii=False))
        finally:
            for token in (manager,rep,rep_second):
                c.post('/api/logout',headers={'Authorization':'Bearer '+token})

if __name__=='__main__':main()
