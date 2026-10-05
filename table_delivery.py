"""Server-generated commercial PDFs and accountable single-client delivery."""
import io
import hashlib
import re
from datetime import datetime, timezone
from xml.sax.saxutils import escape
from fastapi import Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
from whatsapp_media import send_media, provider_config, MediaError


def catalog_pdf(client, table, prices):
    out = io.BytesIO()
    styles = getSampleStyleSheet()
    para = lambda value: Paragraph(escape(str(value or '')), styles['BodyText'])
    rows = [['SKU', 'Descrição', 'EAN', 'Preço (R$)']]
    for p in sorted(prices, key=lambda p: (str(p.get('description', '')), str(p.get('sku', '')))):
        rows.append([para(p.get('sku')), para(p.get('description')), para(p.get('ean')),
                     para(f"{float(p['price']):,.2f}".replace(',', '#').replace('.', ',').replace('#', '.'))])
    grid = Table(rows, colWidths=[65, 265, 100, 85], repeatRows=1)
    grid.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#164957')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f1f5f5')]),
        ('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story = [Paragraph('Tabela comercial', styles['Title']), para(table.get('title') or table['brand']),
             para(f"Cliente: {client.get('name')} · UF da tabela: {table.get('state')}"),
             para('Atualizada no sistema · Gerada em ' + datetime.now(timezone.utc).isoformat()), Spacer(1, 15), grid]
    def footer(canvas, doc):
        canvas.setFont('Helvetica',8)
        canvas.drawString(40,22,'L2 Representações · Consulte as condições comerciais com seu representante')
        canvas.drawRightString(555,22,str(doc.page))
    SimpleDocTemplate(out, topMargin=35, bottomMargin=40).build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()


class Delivery(BaseModel):
    clientId: str = Field(min_length=1, max_length=128)
    tableIds: list[str] = Field(min_length=1, max_length=10)
    message: str = Field(default='Olá [Nome do Cliente], segue a tabela atualizada da [Nome da Indústria] conforme solicitado.', max_length=1024)
    requestId: str = Field(min_length=16, max_length=80, pattern=r'^[A-Za-z0-9-]+$')


def register(app, db, auth, require_sector, check_scope, normalize_uf, matches):
    def load(con, user, cid, tid=None):
        check_scope(con,user,cid)
        row=con.execute("SELECT payload FROM entities WHERE kind='client' AND id=%s",(cid,)).fetchone()
        if not row: raise HTTPException(404,'Cliente não encontrado')
        client=row[0]
        uf=normalize_uf(client.get('state'))
        if not uf: raise HTTPException(400,'Corrija a UF do cliente')
        tables=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='price_table' ORDER BY id")
                if r[0].get('active') and (r[0].get('state')=='ALL' or matches(uf,r[0].get('state')))]
        if tid is None: return client,tables
        table=next((t for t in tables if t.get('id')==tid),None)
        if not table: raise HTTPException(400,'Tabela inativa ou incompatível com o cliente')
        prices=[r[0] for r in con.execute("SELECT payload FROM entities WHERE kind='price' AND (payload->>'tableId'=%s OR (coalesce(payload->>'tableId','')='' AND (payload->>'brand') || '|' || (payload->>'state')=%s))",(tid,tid))
                if r[0].get('brand')==table['brand'] and r[0].get('state') in (table['state'],'ALL')]
        if not prices: raise HTTPException(400,'Tabela sem produtos cadastrados')
        return client,table,prices

    @app.get('/api/materials/{client_id}')
    def options(client_id: str, authorization: str | None = Header(default=None)):
        user=auth(authorization);require_sector(user,'commercial','office')
        with db() as con: client,tables=load(con,user,client_id)
        return {'tables':[{k:t.get(k) for k in ('id','brand','title','state','channel')} for t in tables],
                'apiReady':bool(provider_config())}

    @app.get('/api/materials/{client_id}/pdf')
    def pdf(client_id: str, table_id: str, authorization: str | None = Header(default=None)):
        user=auth(authorization);require_sector(user,'commercial','office')
        with db() as con: client,table,prices=load(con,user,client_id,table_id)
        return Response(catalog_pdf(client,table,prices),media_type='application/pdf',headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="L2_tabela.pdf"'})

    @app.post('/api/materials/send')
    def send(data: Delivery, authorization: str | None = Header(default=None)):
        user=auth(authorization);require_sector(user,'commercial','office')
        if not provider_config(): raise HTTPException(503,'Configure a API oficial do WhatsApp no servidor')
        with db() as con:
            catalogs=[load(con,user,data.clientId,tid) for tid in dict.fromkeys(data.tableIds)]
        phone=re.sub(r'\D','',str(catalogs[0][0].get('phone') or ''))
        if len(phone) in (10,11): phone='55'+phone
        if not re.fullmatch(r'55\d{10,11}',phone): raise HTTPException(400,'Cadastre o WhatsApp do cliente com DDD')
        results=[]
        for client,table,prices in catalogs:
            caption=data.message.replace('[Nome do Cliente]',client.get('name') or '').replace('[Nome da Indústria]',table['brand'])
            if len(caption)>1024: raise HTTPException(400,'Mensagem final acima de 1024 caracteres')
            content=catalog_pdf(client,table,prices)
            identity=hashlib.sha256((user+'|'+data.requestId+'|'+table['id']).encode()).hexdigest()
            fingerprint=hashlib.sha256((data.clientId+'|'+table['id']+'|'+caption).encode()).hexdigest()
            record={'id':identity,'clientId':data.clientId,'tableId':table['id'],'brand':table['brand'],
                    'at':datetime.now(timezone.utc).isoformat(),'owner':user,'type':'WhatsApp',
                    'status':'sending','fingerprint':fingerprint,'pdfSha256':hashlib.sha256(content).hexdigest(),
                    'text':f"Tabela {table['brand']} · {table.get('title')} · envio em processamento"}
            with db() as con:
                claimed=con.execute("INSERT INTO entities(kind,id,payload) VALUES('material_send',%s,%s) ON CONFLICT DO NOTHING RETURNING id",(identity,Jsonb(record))).fetchone()
                if not claimed:
                    old=con.execute("SELECT payload FROM entities WHERE kind='material_send' AND id=%s",(identity,)).fetchone()[0]
                    if old.get('fingerprint')!=fingerprint: raise HTTPException(409,'Identificador reutilizado com conteúdo diferente')
                    results.append(old);continue
                con.execute("INSERT INTO entities(kind,id,payload) VALUES('interaction',%s,%s)",(identity,Jsonb(record)))
            try:
                record['messageId']=send_media(phone,'L2_tabela.pdf','application/pdf',content,caption)
                record['status']='accepted'
                record['text']=f"Tabela {table['brand']} · {table.get('title')} · aceita pela API (entrega não confirmada)"
            except MediaError as exc:
                record['status']='unconfirmed'
                record['text']=f"Tabela {table['brand']} · envio não confirmado: {exc}"
            with db() as con:
                for kind in ('material_send','interaction'):
                    con.execute('UPDATE entities SET payload=%s,updated_at=now() WHERE kind=%s AND id=%s',(Jsonb(record),kind,identity))
                con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'material_send',%s,%s)",(user,identity,record['status']))
            results.append(record)
        return {'results':results}
