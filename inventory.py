"""Persistent unit inventory and auditable entries/withdrawals."""
from decimal import Decimal
from datetime import date
import secrets
from pydantic import BaseModel, Field
from fastapi import HTTPException

LOCATION = 'euler-zappa'
INITIAL = [["BJO02BBT","JUICYOIL ÓLEO LABIAL PINEAPPLE",60],["","BT COCA COLA-OIL TINT ICONIC",80],["BTS04BBT","LIPSHAPE MOCHA",60],["BTS07B-BT","LIPSHAPE TERRACOTA",60],["BTS02BBT","LIPSHAPE BEIGE ROSE",40],["","BT COCA COLA-LIP GLOSS BLACK BUBBLES",40],["","BT LIPSHAPE WARM BROWN",40],["","BT COCA COLA-BLUSH CLASSIC BROWN",40],["","BT COCA COLA-BALM LABIAL POP CHARM PROV",40],["BTS06BBT","LIPSHAPE MARSALA",40],["BTS10BBT","LIPSHAPE DEEP BROWN",40],["BTS09B-BT","LIPSHAPE RED CARPET",40],["BJO01BBT","JUICYOIL ÓLEO LABIAL GUAVA",40]]

class InventoryMovement(BaseModel):
    request_id: str = Field(min_length=8, max_length=80)
    product_id: str = ''
    name: str = Field(default='', max_length=180)
    supplier_sku: str = Field(default='', max_length=80)
    kind: str
    quantity: int = Field(gt=0, le=1000000)
    unit_price: Decimal | None = Field(default=None, ge=0)
    movement_date: date
    notes: str = Field(default='', max_length=500)

def setup_inventory(con):
    con.execute("""CREATE TABLE IF NOT EXISTS inventory_locations (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL,
        declared_total NUMERIC(14,2), opening_date DATE NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS inventory_products (
        id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES inventory_locations(id),
        name TEXT NOT NULL, supplier_sku TEXT NOT NULL DEFAULT '',
        balance BIGINT NOT NULL CHECK(balance>=0), unit_price NUMERIC(14,2) NOT NULL CHECK(unit_price>=0))""")
    con.execute("""CREATE TABLE IF NOT EXISTS inventory_movements (
        id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, product_id TEXT NOT NULL REFERENCES inventory_products(id),
        kind TEXT NOT NULL CHECK(kind IN ('Abertura','Entrada','Saída')), quantity BIGINT NOT NULL CHECK(quantity>0),
        unit_price NUMERIC(14,2) NOT NULL, movement_date DATE NOT NULL, notes TEXT NOT NULL DEFAULT '',
        username TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    inserted = con.execute("""INSERT INTO inventory_locations(id,name,address,declared_total,opening_date)
        VALUES(%s,'EULER ZAPPA','TRAVESSA PORTUL 174 CRP, BELÉM-PA',11200,'2026-10-14')
        ON CONFLICT DO NOTHING RETURNING id""", (LOCATION,)).fetchone()
    if inserted:
        for index,(sku,name,quantity) in enumerate(INITIAL):
            pid = LOCATION+'-opening-'+str(index+1)
            con.execute("INSERT INTO inventory_products(id,location_id,name,supplier_sku,balance,unit_price) VALUES(%s,%s,%s,%s,%s,20)", (pid,LOCATION,name,sku,quantity))
            con.execute("""INSERT INTO inventory_movements(id,request_id,product_id,kind,quantity,unit_price,movement_date,notes,username)
                VALUES(%s,%s,%s,'Abertura',%s,20,'2026-10-14','Saldo inicial informado. Total declarado 11.200; itens calculados 12.400.','Ana Paula')""", (pid,pid,pid,quantity))

def snapshot(con):
    location = con.execute("SELECT id,name,address,declared_total,opening_date FROM inventory_locations WHERE id=%s", (LOCATION,)).fetchone()
    products = [{'id':r[0],'name':r[1],'supplierSku':r[2],'balance':r[3],'unitPrice':str(r[4])} for r in con.execute("SELECT id,name,supplier_sku,balance,unit_price FROM inventory_products WHERE location_id=%s ORDER BY name", (LOCATION,))]
    movements = [{'id':r[0],'product':r[1],'kind':r[2],'quantity':r[3],'unitPrice':str(r[4]),'date':str(r[5]),'notes':r[6],'username':r[7]} for r in con.execute("""SELECT m.id,p.name,m.kind,m.quantity,m.unit_price,m.movement_date,m.notes,m.username
        FROM inventory_movements m JOIN inventory_products p ON p.id=m.product_id WHERE p.location_id=%s
        ORDER BY m.created_at DESC,m.id DESC LIMIT 200""", (LOCATION,))]
    return {'location':{'id':location[0],'name':location[1],'address':location[2],'declaredTotal':str(location[3]),'openingDate':str(location[4])},
            'products':products,'movements':movements,'openingCalculatedTotal':'12400.00','openingUnits':620,'openingDifference':'1200.00'}

def apply_movement(con, data, username):
    if data.kind not in ('Entrada','Saída'): raise HTTPException(400,'Selecione Entrada ou Saída')
    if data.unit_price is not None and (not data.unit_price.is_finite() or data.unit_price.as_tuple().exponent < -2):
        raise HTTPException(400,'Valor unitário inválido. Use até duas casas decimais.')
    con.execute('SELECT pg_advisory_xact_lock(%s)', (8239140,))
    previous = con.execute("SELECT id FROM inventory_movements WHERE request_id=%s", (data.request_id,)).fetchone()
    if previous: return {'id':previous[0],'duplicate':True}
    pid = data.product_id.strip()
    if not pid:
        if data.kind != 'Entrada' or not data.name.strip() or data.unit_price is None:
            raise HTTPException(400,'Novo item exige nome, valor unitário e uma entrada.')
        same = con.execute("SELECT id FROM inventory_products WHERE location_id=%s AND (lower(name)=lower(%s) OR (supplier_sku<>'' AND lower(supplier_sku)=lower(%s)))", (LOCATION,data.name.strip(),data.supplier_sku.strip())).fetchone()
        if same: raise HTTPException(409,'Item já cadastrado. Selecione-o para registrar a entrada.')
        pid = secrets.token_hex(16)
        con.execute("INSERT INTO inventory_products(id,location_id,name,supplier_sku,balance,unit_price) VALUES(%s,%s,%s,%s,0,%s)", (pid,LOCATION,data.name.strip(),data.supplier_sku.strip(),data.unit_price))
    row = con.execute("SELECT balance,unit_price FROM inventory_products WHERE id=%s AND location_id=%s FOR UPDATE", (pid,LOCATION)).fetchone()
    if not row: raise HTTPException(404,'Item não encontrado')
    delta = data.quantity if data.kind == 'Entrada' else -data.quantity
    if row[0]+delta < 0: raise HTTPException(400,'Quantidade de saída maior que o saldo disponível')
    price = data.unit_price if data.unit_price is not None else row[1]
    con.execute("UPDATE inventory_products SET balance=balance+%s,unit_price=%s WHERE id=%s", (delta,price if data.kind=='Entrada' else row[1],pid))
    mid = secrets.token_hex(16)
    con.execute("INSERT INTO inventory_movements(id,request_id,product_id,kind,quantity,unit_price,movement_date,notes,username) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", (mid,data.request_id,pid,data.kind,data.quantity,price,data.movement_date,data.notes,username))
    return {'id':mid,'productId':pid,'balance':row[0]+delta,'duplicate':False}
