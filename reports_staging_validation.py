"""Staging-only, read-only validation. No fixtures, tokens or financial details logged."""
from contextlib import contextmanager
from datetime import date
from time import perf_counter
from types import FunctionType
import os
import json
from fastapi import HTTPException
import strategic_reports as reports


def validate(module):
    if os.getenv('L2_EXPECTED_DATABASE')!='l2one_homologacao':
        raise RuntimeError('Report validation requires explicitly isolated staging')
    started=perf_counter()
    with module.db() as con:
        con.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        database,readonly=con.execute("SELECT current_database(),current_setting('transaction_read_only')").fetchone()
        if database!='l2one_homologacao' or readonly!='on':
            raise RuntimeError('Reports validation refused database or writable transaction')
        def fingerprint():
            return con.execute("SELECT count(*),md5(coalesce(string_agg(md5(kind || ':' || id || ':' || payload::text),'' ORDER BY kind,id),'')) FROM entities").fetchone()
        before=fingerprint()
        data={k:[] for k in reports.KINDS}
        for kind,payload in con.execute('SELECT kind,payload FROM entities WHERE kind=ANY(%s)',(list(reports.KINDS),)):
            data[kind].append(payload)
        @contextmanager
        def read_db():yield con
        users=[]
        for username,role,sectors in con.execute('SELECT username,role,sectors FROM app_users WHERE active ORDER BY username'):
            users.append((username,module.effective_sectors(username,role,sectors)))
        today=module.datetime.now(module.TZ).date();start=date(today.year,today.month,1)
        all_ids=[key for entries in reports.CATALOG.values() for key,_ in entries]
        def handler_for(username,permissions):
            def require(_user,*allowed):
                if not set(allowed)&permissions:raise HTTPException(403,'Sem acesso')
            env=dict(module.strategic_report.__globals__,auth=lambda h:username,db=read_db,
                     sectors_for=lambda u:permissions,require_sector=require)
            return FunctionType(module.strategic_report.__code__,env)
        selected=[]
        for predicate in (lambda p:'management' in p,lambda p:'commercial' in p and not p&{'management','finance'},lambda p:'finance' in p):
            match=next((u for u in users if predicate(u[1])),None)
            if match and match not in selected:selected.append(match)
        checks=denied=0;timings=[];counts={};coverage={'management':False,'field':False,'finance':False}
        for username,permissions in selected:
            coverage['management']|='management' in permissions
            coverage['field']|='commercial' in permissions and not bool(permissions&{'management','finance'})
            coverage['finance']|='finance' in permissions
            handler=handler_for(username,permissions)
            allowed_clients={c.get('id') for c in module.scoped_rows(con,'client',username) if c.get('owner')==username}
            allowed_clients.update(r[0] for r in con.execute('SELECT client_id FROM client_seller_authorizations WHERE seller_username=%s AND active',(username,)))
            for report in all_ids:
                for format in ('json','csv','pdf'):
                    permitted=('management' in permissions if report=='commissions' else 'finance' in permissions) if report in reports.FINANCIAL else bool(permissions&{'commercial','office','management'})
                    tick=perf_counter()
                    try:response=handler(report,start,today,today,60,format,'validation-internal')
                    except HTTPException as error:
                        if permitted or error.status_code!=403:raise RuntimeError('Reports RBAC validation failed') from None
                        denied+=1;continue
                    if not permitted or response.status_code!=200:raise RuntimeError('Reports authorization failed')
                    if response.headers.get('cache-control')!='no-store':raise RuntimeError('Report cache control invalid')
                    if format=='json':
                        output=json.loads(response.body);counts.setdefault(report,output['count'])
                        if output['count']!=len(output['rows']):raise RuntimeError('Report row count mismatch')
                        if 'management' not in permissions and report not in reports.FINANCIAL:
                            scoped={kind:module.scoped_rows(con,kind,username) for kind in reports.KINDS if kind not in ('office_finance','financial_input')}
                            scoped['client']=[c for c in scoped['client'] if c.get('id') in allowed_clients]
                            for kind in ('order','opportunity','interaction','visit','fulfillment','lead'):
                                scoped[kind]=[r for r in scoped[kind] if r.get('clientId') in allowed_clients or (kind=='lead' and r.get('owner')==username)]
                            scoped['goal']=[];scoped['settlement']=[]
                            expected=reports.build(scoped,report,start,today,today,60)
                            if output['rows']!=expected['rows']:raise RuntimeError('Report portfolio leaked')
                    elif format=='pdf' and not response.body.startswith(b'%PDF'):raise RuntimeError('Invalid PDF')
                    elif format=='csv' and not response.body.startswith(b'\xef\xbb\xbf'):raise RuntimeError('Invalid CSV')
                    timings.append((perf_counter()-tick)*1000);checks+=1
        if not coverage['management'] or not coverage['field']:raise RuntimeError('Missing management or field profile to validate reporting')
        if fingerprint()!=before:raise RuntimeError('Source data changed during read-only reporting validation')
        # Anonymous requests exercise real authentication, without creating a session.
        for report in all_ids:
            try:module.strategic_report(report,start,today,today,60,'json',None)
            except HTTPException as error:
                if error.status_code!=401:raise RuntimeError('Anonymous report access not rejected') from None
                denied+=1
            else:raise RuntimeError('Anonymous report access accepted')
        # Bounded concurrent smoke on real data. Separate read-only connections per request.
        from concurrent.futures import ThreadPoolExecutor
        from statistics import median
        manager=next(u for u in selected if 'management' in u[1])
        @contextmanager
        def isolated_read_db():
            with module.db() as read_con:
                read_con.execute('SET TRANSACTION READ ONLY')
                yield read_con
        def concurrent_read(report):
            username,permissions=manager
            env=dict(module.strategic_report.__globals__,auth=lambda h:username,db=isolated_read_db,
                     sectors_for=lambda u:permissions)
            handler=FunctionType(module.strategic_report.__code__,env)
            tick=perf_counter();response=handler(report,start,today,today,60,'json','validation-internal')
            if response.status_code!=200:raise RuntimeError('Concurrent report read failed')
            return (perf_counter()-tick)*1000
        with ThreadPoolExecutor(max_workers=2) as pool:
            load_times=list(pool.map(concurrent_read,['brands','deliveries','inactive']*2))
        mapping={
            'ordersWithoutDate':sum(not reports.day(r.get('date')) for r in data['order']),
            'ordersWithoutClient':sum(not r.get('clientId') for r in data['order']),
            'documentedInvoices':sum(bool(r.get('invoiceNumber') and reports.day(r.get('billedDate'))) for r in data['settlement']),
            'receivablesWithoutDueDate':sum(r.get('Tipo')=='Receita' and not reports.day(r.get('Vencimento')) for r in data['office_finance']),
            'logisticsWithoutOrder':sum(not r.get('orderId') for r in data['fulfillment']),
            'opportunitiesWithoutCreationDate':sum(not reports.day(r.get('createdAt')) for r in data['opportunity']),
            'leadsWithoutCreationDate':sum(not reports.day(r.get('createdAt')) for r in data['lead'])}
        return {'ok':True,'readOnly':True,'sourceUnchanged':True,'reportCount':len(all_ids),
                'checks':checks,'denials':denied,'profileCoverage':coverage,
                'entityCounts':{k:len(v) for k,v in data.items()},'reportRows':counts,'mapping':mapping,
                'maxRequestMs':round(max(timings,default=0),1),'elapsedMs':round((perf_counter()-started)*1000,1),
                'sessionAuth':'anonymous denied; authorized checks use internal identity with real profile permissions',
                'loadTest':{'concurrency':2,'requests':6,'medianMs':round(median(load_times),1),'maxMs':round(max(load_times),1),'scope':'bounded smoke on staging data; not a capacity certification'}}
