from fastapi import HTTPException

COMMERCIAL_SECTORS = {'commercial', 'clients_edit', 'routes'}


def effective_sectors(username, role, sectors):
    allowed = set(sectors or [])
    if role == 'Vendedor' and username != 'Euler':
        allowed &= COMMERCIAL_SECTORS
    if username in ('Marlene','Erika'):
        allowed.discard('finance')
    return allowed


def attribute_order(con, actor, order, previous=None):
    previous = previous or {}
    member = con.execute('SELECT role FROM app_users WHERE username=%s AND active', (actor,)).fetchone()
    role = member[0] if member else ''
    administrative = actor == 'Laís' or role in ('Administrativo', 'Administradora', 'Gestão')
    seller = str(order.get('sellerResponsible') or previous.get('sellerResponsible') or '').strip()
    if not administrative and actor != 'Euler':
        if seller and seller != actor:
            raise HTTPException(403, 'Vendedor só pode registrar pedidos em seu próprio nome')
        seller = actor
    if not seller:
        raise HTTPException(400, 'Informe o vendedor responsável pelo pedido')
    responsible = con.execute('SELECT role FROM app_users WHERE username=%s AND active', (seller,)).fetchone()
    if seller == 'Laís' or not responsible or responsible[0] != 'Vendedor':
        raise HTTPException(400, 'Responsável deve ser um vendedor ativo; Laís atua como Adm')
    # A autoria original não muda quando outra pessoa altera o pedido.
    entered = previous.get('enteredBy') or previous.get('user') or actor
    order.update(user=entered, enteredBy=entered,
                 enteredAs=previous.get('enteredAs') or ('Adm' if entered == 'Laís' or (entered == actor and administrative) else 'Comercial'),
                 updatedAs='Adm' if administrative else 'Sócio' if actor == 'Euler' else 'Comercial',
                 sellerResponsible=seller, commissionSeller=seller)
