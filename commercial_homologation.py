"""Explicit synthetic smoke data, permitted ONLY in the dedicated Next service/DB."""
import os
import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from psycopg.types.json import Jsonb
from commercial_planning import Document

DATABASE = 'l2_one_next_staging_db'
SERVICE = 'srv-dbgcn4mgekts738hi3ig'
MANAGER = '__homolog_planning_manager'
REP = '__homolog_planning_rep'
BRAND = 'HOMOLOGAÇÃO L2 Planejamento'
DEPARTMENT = 'Homologação técnica Planejamento (dados fictícios)'


def seed(connect, hash_password):
    if os.getenv('L2_COMMERCIAL_SMOKE_SEED') != 'yes':
        return False
    if os.getenv('L2_EXPECTED_DATABASE') != DATABASE or os.getenv('RENDER_SERVICE_ID') != SERVICE:
        raise RuntimeError('Seed comercial recusado fora do serviço/banco isolado')
    passwords = [os.getenv('L2_SMOKE_MANAGER_PASSWORD', ''), os.getenv('L2_SMOKE_REP_PASSWORD', '')]
    if any(len(x) < 32 for x in passwords):
        raise RuntimeError('Credenciais técnicas de homologação ausentes/inválidas')
    today = datetime.now(ZoneInfo('America/Belem')).date()
    day = today.replace(day=1)
    with connect() as con:
        if con.execute('SELECT current_database()').fetchone()[0] != DATABASE:
            raise RuntimeError('Seed comercial recusado: banco real diverge do esperado')
        for user, role, sectors, password in [
            (MANAGER,'Gestão',['commercial','clients_edit','management','finance'],passwords[0]),
            (REP,'Vendedor',['commercial','clients_edit'],passwords[1])]:
            old = con.execute('SELECT department FROM app_users WHERE username=%s FOR UPDATE',(user,)).fetchone()
            if old and old[0] != DEPARTMENT:
                raise RuntimeError('Conta técnica colide com cadastro existente')
            # Never overwrite an existing password or remove sessions on a restart.
            con.execute('INSERT INTO app_users(username,password_hash,role,sectors,department,must_change_password) VALUES(%s,%s,%s,%s,%s,false) ON CONFLICT(username) DO NOTHING',
                        (user,hash_password(password),role,Jsonb(sectors),DEPARTMENT))
        for identifier,owner,tax in [('planning-smoke-port-a',REP,'11222333000181'),('planning-smoke-port-b',MANAGER,'52998224725')]:
            payload={'id':identifier,'name':'HOMOLOGAÇÃO L2 '+identifier[-1].upper(),'owner':owner,'taxId':tax,'state':'PA','city':'Belém','sourceDocument':'commercial_homologation'}
            con.execute("INSERT INTO entities(kind,id,payload) VALUES('client',%s,%s) ON CONFLICT(kind,id) DO NOTHING",(identifier,Jsonb(payload)))
        for suffix,client,stage,amount in [('st-a','planning-smoke-port-a','sell_through','100.00'),('st-b','planning-smoke-port-b','sell_through','200.00'),('si-a','planning-smoke-port-a','sell_in','50.00'),('so-a','planning-smoke-port-a','sell_out','150.00')]:
            d=Document(source='HOMOLOGACAO-L2',external_id=str(day)+'-'+suffix,client_id=client,stage=stage,business_date=day,items=[{'brand':BRAND,'sku':'HOMOLOG-SKU','amount':amount,'units':'10'}])
            key=str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps([d.source,d.stage,d.external_id])))
            con.execute('INSERT INTO l2_commercial_documents(id,source,external_id,stage,client_id,business_date,kind,payload,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',
                        (key,d.source,d.external_id,d.stage,d.client_id,d.business_date,'sale',Jsonb(d.model_dump(mode='json')),MANAGER))
    return True
