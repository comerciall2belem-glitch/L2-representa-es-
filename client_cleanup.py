"""Regras conservadoras para manter a carteira comercial em PA/AP."""
import re
import unicodedata
from collections import defaultdict


def normalized(value):
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+', ' ', value).strip()


def uf(value):
    return {'pa': 'PA', 'para': 'PA', 'ap': 'AP', 'amapa': 'AP'}.get(normalized(value))


def fingerprint(record):
    state = uf(record.get('state'))
    name = normalized(record.get('name'))
    city = normalized(record.get('city'))
    if not state or not name or not city:
        return None
    cnpj = re.sub(r'\D', '', str(record.get('taxId') or ''))
    if len(cnpj) == 14:
        return ('cnpj', state, cnpj)
    address = normalized(record.get('address'))
    district = normalized(record.get('district'))
    if address and district:
        return ('address', state, name, city, district, address)
    if not address:
        phone = re.sub(r'\D', '', str(record.get('phone') or ''))
        if len(phone) >= 10:
            return ('phone-no-address', state, name, city, phone)
    return None


def plan(rows):
    """Retorna arquivo de fora da região e duplicatas inequívocas com destino."""
    outside, groups = [], defaultdict(list)
    for row in rows:
        record = row['payload']
        if not uf(record.get('state')):
            outside.append(row)
            continue
        key = fingerprint(record)
        if key:
            groups[key].append(row)
    duplicates = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda x: (
            -sum(bool(x['payload'].get(k)) for k in ('taxId','stateRegistration','address','district','phone','contact','email','brands','last_purchase')),
            str(x['id'])
        ))
        survivor = members[0]
        for duplicate in members[1:]:
            duplicates.append({'old': duplicate, 'keep': survivor, 'reason': key[0]})
    return {'outside': outside, 'duplicates': duplicates}
