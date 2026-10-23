"""One bounded NF-e validation on isolated staging; synthetic fixtures are removed."""
import io
import json
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from psycopg.types.json import Jsonb


def validate(module):
    import os
    from fastapi.testclient import TestClient
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader
    if os.getenv('L2_EXPECTED_DATABASE')!='l2one_homologacao':
        raise RuntimeError('NF-e QA requires explicitly isolated staging')
    tag='nfe-qa-'+secrets.token_hex(8)
    client_id=tag+'-client';brand=tag+'-brand'
    cnpj='99'+str(secrets.randbelow(10**10)).zfill(10)
    for weights in ([5,4,3,2,9,8,7,6,5,4,3,2],[6,5,4,3,2,9,8,7,6,5,4,3,2]):
        remainder=sum(int(n)*w for n,w in zip(cnpj,weights))%11
        cnpj+=str(0 if remainder<2 else 11-remainder)
    orders=[tag+'-order-'+str(i) for i in range(3)]
    profiles=[(tag+'-manager','Gestão',['commercial','management']),
              (tag+'-office','Administrativo',['commercial','office']),
              (tag+'-seller','Vendedor',['commercial']),
              (tag+'-foreign','Vendedor',['commercial'])]
    passwords={u:secrets.token_urlsafe(24) for u,_,_ in profiles}
    checks=[];tokens={};invoice_ids=[]
    api=TestClient(module.app,raise_server_exceptions=False)
    def fingerprint(con):
        return con.execute("SELECT count(*),md5(coalesce(string_agg(md5(kind || ':' || id || ':' || payload::text),'' ORDER BY kind,id),'')) FROM entities").fetchone()
    with module.db() as con:
        if con.execute('SELECT current_database()').fetchone()[0]!='l2one_homologacao':raise RuntimeError('Wrong QA database')
        before=fingerprint(con)
    def get_order(oid):
        with module.db() as con:return con.execute("SELECT payload FROM entities WHERE kind='order' AND id=%s",(oid,)).fetchone()[0]
    def xml(qty=4,number='990001',recipient=None):
        recipient=recipient or cnpj
        return ('<NFe><infNFe Id="NFe'+tag+number+'"><ide><nNF>'+number+'</nNF><serie>999</serie><dhEmi>'+date.today().isoformat()+'T12:00:00-03:00</dhEmi></ide><emit><CNPJ>99999999000199</CNPJ></emit><dest><CNPJ>'+recipient+'</CNPJ></dest><det><prod><cProd>QA01</cProd><qCom>'+str(qty)+'</qCom><vProd>'+str(qty*20)+'</vProd></prod></det><total><ICMSTot><vNF>'+str(qty*20)+'</vNF></ICMSTot></total></infNFe></NFe>').encode()
    try:
        with module.db() as con:
            for user,role,sectors in profiles:
                con.execute('INSERT INTO app_users(username,password_hash,active,must_change_password,sectors,role) VALUES(%s,%s,true,false,%s,%s)',(user,module.password_hash(passwords[user]),Jsonb(sectors),role))
            customer={'id':client_id,'name':'HOMOLOGAÇÃO NF-e FICTÍCIA - NÃO ENVIAR','owner':profiles[2][0],'state':'PA','taxId':cnpj,'active':True}
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('client',%s,%s)",(client_id,Jsonb(customer)))
            con.execute('INSERT INTO clientes(id,razao_social,documento) VALUES(%s,%s,%s)',(client_id,customer['name'],customer['taxId']))
            for oid in orders:
                payload={'id':oid,'clientId':client_id,'brand':brand,'date':date.today().isoformat(),'status':'Confirmado','amount':'300.00','sellerResponsible':profiles[2][0],
                         'items':[{'brand':brand,'sku':'QA01','description':'Produto fictício parcial','quantity':10,'unitPrice':'20.00','subtotal':'200.00'},
                                  {'brand':brand,'sku':'QA02','description':'Produto fictício corte total','quantity':5,'unitPrice':'20.00','subtotal':'100.00'}]}
                con.execute("INSERT INTO entities(kind,id,payload) VALUES('order',%s,%s)",(oid,Jsonb(payload)))
        checks.append('isolated_postgres_synthetic_fixtures')
        for user,_,_ in profiles:
            r=api.post('/api/login',json={'user':user,'password':passwords[user]});assert r.status_code==200,('login',r.status_code)
            tokens[user]={'Authorization':'Bearer '+r.json()['token']}
        owner=tokens[profiles[2][0]];office=tokens[profiles[1][0]];manager=tokens[profiles[0][0]]
        root='/api/orders/'+orders[0]+'/invoices'
        assert api.get(root).status_code==401
        assert api.get(root,headers=tokens[profiles[3][0]]).status_code==403
        for header in (manager,office,owner):assert api.get(root,headers=header).status_code==200
        checks.append('real_session_and_manager_office_seller_scope')
        r=api.post(root,headers=owner,data={'brand':brand},files={'file':('nota.xml',xml(),'application/xml')})
        assert r.status_code==200,('xml',r.status_code,r.text[:180])
        saved=r.json();invoice_ids.append(saved['id'])
        assert not saved['confirmed'] and get_order(orders[0])['amount']=='300.00'
        assert {i['classification'] for i in saved['cuts']}=={'Corte Parcial','Corte Total'}
        checks.append('xml_attachment_without_text_and_no_early_billing')
        endpoint=root+'/'+saved['id']+'/confirm'
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(lambda _:api.post(endpoint,headers=office).status_code,range(2)))
        assert statuses==[200,200],('concurrency',statuses)
        current=get_order(orders[0]);assert current['amount']=='80.00' and current['originalAmount']=='300.00'
        assert current['items'][0]['quantity']==10
        with module.db() as con:
            assert con.execute("SELECT count(*) FROM audit_log WHERE kind='order_invoice' AND entity_id=%s AND action='billing_confirmed'",(saved['id'],)).fetchone()[0]==1
            assert str(con.execute('SELECT valor_total FROM faturamento_pedidos WHERE id=%s',(orders[0],)).fetchone()[0]) in ('80.00','80')
        checks.append('concurrent_confirmation_one_audit_actual_sales_projection')
        import strategic_reports
        start=date.today().replace(day=1);end=date.today()
        report_data={'order':[current],'client':[],'settlement':[],'goal':[]}
        sales=strategic_reports.build(report_data,'brands',start,end,end)
        assert sum(row[2] for row in sales['rows'])==80
        billed=strategic_reports.build(report_data,'billing',start,end,end)
        assert sum(row[1] for row in billed['rows'])==80
        report_data['settlement']=[dict(current['invoiceBilling']['documents'][0])]
        billed=strategic_reports.build(report_data,'billing',start,end,end)
        assert sum(row[1] for row in billed['rows'])==80
        checks.append('sales_and_billing_reports_actual_amount_without_duplicate_settlement')
        pdf=api.get(root+'/cuts.pdf',headers=manager);assert pdf.status_code==200 and pdf.content.startswith(b'%PDF-')
        text='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
        assert all(s in text for s in ('QA01','QA02','120,00','100,00','Qtd. faturada','Qtd. cortada'))
        checks.append('real_pdf_download_contains_partial_and_total_cuts')
        assert api.post(root,headers=owner,data={'brand':brand},files={'file':('nota.xml',xml(),'application/xml')}).status_code==409
        checks.append('duplicate_upload_rejected')
        bad=api.post(root,headers=owner,data={'brand':brand},files={'file':('nota.xml',xml(number='990002',recipient='99999999000199'),'application/xml')})
        assert bad.status_code==400
        checks.append('wrong_recipient_no_write')
        r=api.post(root,headers=owner,data={'brand':brand},files={'file':('complementar.xml',xml(6,'990003'),'application/xml')});assert r.status_code==200
        second=r.json();invoice_ids.append(second['id']);assert api.post(root+'/'+second['id']+'/confirm',headers=office).status_code==200
        assert get_order(orders[0])['amount']=='200.00'
        assert [i['sku'] for i in get_order(orders[0])['invoiceBilling']['cuts']]==['QA02']
        checks.append('complementary_invoice_recalculates_remaining_cuts')
        # A real PDF is uploaded with no text form field.
        buf=io.BytesIO();doc=canvas.Canvas(buf)
        for y,line in zip((800,780,760,740),('NF: 990004','CNPJ destinatário: '+cnpj,'Código; Quantidade; Valor total','QA01; 4; 80,00')):doc.drawString(40,y,line)
        doc.save();pdfroot='/api/orders/'+orders[1]+'/invoices'
        r=api.post(pdfroot,headers=office,data={'brand':brand},files={'file':('ficticia.pdf',buf.getvalue(),'application/pdf')})
        assert r.status_code==200,('pdf_upload',r.status_code,r.text[:180])
        pdfreport=r.json();invoice_ids.append(pdfreport['id']);assert api.post(pdfroot+'/'+pdfreport['id']+'/confirm',headers=office).status_code==200
        assert get_order(orders[1])['amount']=='80.00'
        checks.append('real_pdf_extraction_and_confirmation_no_manual_text')
        # Force an error after the invoice/order update to prove request rollback.
        rollbackroot='/api/orders/'+orders[2]+'/invoices'
        r=api.post(rollbackroot,headers=office,data={'brand':brand},files={'file':('rollback.xml',xml(number='990005'),'application/xml')});assert r.status_code==200
        rollbackreport=r.json();invoice_ids.append(rollbackreport['id']);original=module.project_order
        def fail_projection(*args):raise RuntimeError('Synthetic QA rollback probe')
        module.project_order=fail_projection
        try:assert api.post(rollbackroot+'/'+rollbackreport['id']+'/confirm',headers=office).status_code==500
        finally:module.project_order=original
        assert get_order(orders[2])['amount']=='300.00'
        with module.db() as con:
            assert not con.execute('SELECT reconciliation FROM order_invoices WHERE id=%s',(rollbackreport['id'],)).fetchone()[0]['confirmed']
        checks.append('postgres_rollback_on_projection_failure')
    finally:
        # Delete only IDs generated by this test; no real orders, sessions or users are targeted.
        with module.db() as con:
            con.execute('DELETE FROM audit_log WHERE username=ANY(%s)',([p[0] for p in profiles],))
            con.execute('DELETE FROM order_invoices WHERE order_id=ANY(%s)',(orders,))
            con.execute('DELETE FROM faturamento_pedidos WHERE id=ANY(%s)',(orders,))
            con.execute('DELETE FROM entities WHERE id=ANY(%s)',(orders+[client_id],))
            con.execute('DELETE FROM clientes WHERE id=%s',(client_id,))
            con.execute('DELETE FROM sessions WHERE username=ANY(%s)',([p[0] for p in profiles],))
            con.execute('DELETE FROM app_users WHERE username=ANY(%s)',([p[0] for p in profiles],))
        with module.db() as con:
            if fingerprint(con)!=before:raise RuntimeError('Source fingerprint differs after NF-e QA cleanup')
    checks.append('synthetic_data_removed_source_fingerprint_unchanged')
    result={'ok':True,'checks':checks,'passed':len(checks),'database':'l2one_homologacao','transport':'ASGI','realPostgres':True,'physicalBrowser':False,'realCustomerDocuments':False}
    logging.getLogger('uvicorn.error').info('nfe_staging_validated %s',json.dumps(result))
    return result
