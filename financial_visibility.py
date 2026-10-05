import re
import unicodedata


def hide_industry_commissions(value):
    if isinstance(value, list):
        return [hide_industry_commissions(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = unicodedata.normalize('NFKD', key).encode('ascii','ignore').decode().lower()
            if normalized.startswith('commission') or normalized.startswith('comissao') or normalized.startswith('comissoes'):
                continue
            if key == 'notes' and isinstance(item, str):
                sentences = re.split(r'(?<=\.)\s+|\n', item)
                item = ' '.join(sentence for sentence in sentences if not re.search(r'comiss(?:ão|ões|ao|oes|ion)', sentence, re.I))
            result[key] = hide_industry_commissions(item)
        return result
    return value


def preserve_industry_commissions(previous, updated):
    result = hide_industry_commissions(updated)
    for key, item in (previous or {}).items():
        normalized = unicodedata.normalize('NFKD', key).encode('ascii','ignore').decode().lower()
        if normalized.startswith(('commission','comissao','comissoes')):
            result[key] = item
        elif key == 'notes' and isinstance(item, str):
            private = [sentence for sentence in re.split(r'(?<=\.)\s+|\n', item) if re.search(r'comiss(?:ão|ões|ao|oes|ion)', sentence, re.I)]
            result[key] = ' '.join([result.get(key,'') or '', *private]).strip()
    return result
