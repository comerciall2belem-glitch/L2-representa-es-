"""Busca de clientes e pedidos no PostgreSQL, com escopo de carteira."""
import re
import unicodedata

ACCENTED = 'áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ'
PLAIN =    'aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC'


def document(alias, fields):
    pieces = [f"coalesce({alias}.payload->>'{field}', '')" for field in fields]
    pieces.append(f"coalesce({alias}.id, '')")
    if alias == 'c':
        pieces.append("regexp_replace(coalesce(c.payload->>'taxId', c.payload->>'cnpj', ''), '[^0-9]', '', 'g')")
    return "to_tsvector('simple'::regconfig, lower(translate(" + " || ' ' || ".join(pieces) + f", '{ACCENTED}', '{PLAIN}')))"


CLIENT_DOCUMENT = document('c', ('name', 'tradeName', 'taxId', 'cnpj'))
ORDER_DOCUMENT = document('o', ('orderNumber',))


def create_indexes(con):
    """Execute somente em janela de migração: CREATE INDEX pode bloquear escritas."""
    con.execute(f"CREATE INDEX IF NOT EXISTS l2_search_client_v1 ON entities USING gin ({CLIENT_DOCUMENT.replace('c.', '')}) WHERE kind='client'")
    con.execute(f"CREATE INDEX IF NOT EXISTS l2_search_order_v1 ON entities USING gin ({ORDER_DOCUMENT.replace('o.', '')}) WHERE kind='order'")


def query_tokens(text):
    if not isinstance(text, str) or len(text) > 80:
        raise ValueError('Pesquisa deve ter no máximo 80 caracteres')
    clean = unicodedata.normalize('NFKD', text)
    clean = ''.join(c for c in clean if not unicodedata.combining(c)).lower()
    words = re.findall(r'[a-z0-9]+', clean)
    digits = re.sub(r'[^0-9]', '', clean)
    if digits and re.fullmatch(r'[\d\s./-]+', clean):
        words = [digits]
    words = words[:8]
    if not words or sum(map(len, words)) < 2:
        return None
    return ' & '.join(word + ':*' for word in words)


def search(con, text, username, seller, limit=8):
    tsquery = query_tokens(text)
    if not tsquery:
        return {'clients': [], 'orders': []}
    limit = max(1, min(int(limit), 20))
    scope = "(%s = false OR c.payload->>'owner' = %s OR EXISTS (SELECT 1 FROM client_seller_authorizations a WHERE a.client_id=c.id AND a.seller_username=%s AND a.active))"
    clients = con.execute(f"""SELECT c.payload FROM entities c
        WHERE c.kind='client' AND {scope}
        AND {CLIENT_DOCUMENT} @@ to_tsquery('simple'::regconfig, %s)
        ORDER BY lower(coalesce(c.payload->>'name','')),c.id LIMIT %s""",
        (seller, username, username, tsquery, limit)).fetchall()
    orders = con.execute(f"""SELECT o.payload, c.payload->>'name' FROM entities o
        JOIN entities c ON c.kind='client' AND c.id=o.payload->>'clientId'
        WHERE o.kind='order' AND {scope}
        AND ({ORDER_DOCUMENT} @@ to_tsquery('simple'::regconfig, %s)
             OR {CLIENT_DOCUMENT} @@ to_tsquery('simple'::regconfig, %s))
        ORDER BY o.updated_at DESC,o.id LIMIT %s""",
        (seller, username, username, tsquery, tsquery, limit)).fetchall()
    return {'clients': [row[0] for row in clients],
            'orders': [{'id': row[0].get('id'), 'orderNumber': row[0].get('orderNumber'),
                        'status': row[0].get('status'), 'date': row[0].get('date'),
                        'amount': row[0].get('amount'), 'brand': row[0].get('brand'),
                        'clientName': row[1]} for row in orders]}
