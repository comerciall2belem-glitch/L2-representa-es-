"""Meta settings shared by webhook, receipt download and outbound messages."""
import os
import re
from urllib.parse import urlsplit

ALIASES={
 'WA_ACCESS_TOKEN':('WA_ACCESS_TOKEN','WHATSAPP_ACCESS_TOKEN'),
 'WA_PHONE_NUMBER_ID':('WA_PHONE_NUMBER_ID','WHATSAPP_PHONE_NUMBER_ID'),
 'WA_VERIFY_TOKEN':('WA_VERIFY_TOKEN','WHATSAPP_VERIFY_TOKEN','WHATSAPP_WEBHOOK_VERIFY_TOKEN'),
 'WA_APP_SECRET':('WA_APP_SECRET','WHATSAPP_APP_SECRET'),
 'WA_GRAPH_VERSION':('WA_GRAPH_VERSION','WHATSAPP_GRAPH_VERSION'),
}

def value(key,environ=None):
    env=os.environ if environ is None else environ
    for name in ALIASES.get(key,(key,)):
        setting=str(env.get(name,'')).strip()
        if setting:
            if key=='WA_GRAPH_VERSION' and not re.fullmatch(r'v\d+\.\d+',setting):
                raise ValueError('Versão Graph inválida')
            return setting
    if key=='WA_GRAPH_VERSION' and env.get('WHATSAPP_API_URL','').strip():
        parsed=urlsplit(env['WHATSAPP_API_URL'].strip())
        if parsed.scheme!='https' or parsed.netloc!='graph.facebook.com' or parsed.query or parsed.fragment or not re.fullmatch(r'/v\d+\.\d+/?',parsed.path):
            raise ValueError('WHATSAPP_API_URL deve apontar para https://graph.facebook.com/vN.N')
        return parsed.path.strip('/')
    return ''

def status(environ=None):
    fields={'token':'WA_ACCESS_TOKEN','phoneNumber':'WA_PHONE_NUMBER_ID','verifyToken':'WA_VERIFY_TOKEN','appSecret':'WA_APP_SECRET','graphVersion':'WA_GRAPH_VERSION'}
    state={}
    for field,key in fields.items():
        try:
            setting=value(key,environ)
            state[field]=bool(setting) and (bool(re.fullmatch(r'\d+',setting)) if field=='phoneNumber' else True)
        except ValueError: state[field]=False
    return dict(state,ready=all(state.values()))
