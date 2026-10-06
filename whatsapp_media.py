"""Single WhatsApp Cloud API media message with its caption."""
import json
import os
import re
import secrets
import urllib.error
import urllib.request


class MediaError(ValueError):
    pass


LIMITS = {'image/jpeg': 5 * 1024 * 1024, 'image/png': 5 * 1024 * 1024,
          'video/mp4': 16 * 1024 * 1024, 'application/pdf': 10 * 1024 * 1024}


def provider_config():
    token = os.getenv('WHATSAPP_ACCESS_TOKEN', '')
    phone_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID', '')
    version = os.getenv('WHATSAPP_GRAPH_VERSION', '')
    if not token or token != token.strip() or any(x in token.lower() for x in ('seu_token','your_token','placeholder','<')) or not re.fullmatch(r'\d+', phone_id) or not re.fullmatch(r'v\d+\.\d+', version):
        return None
    return token, phone_id, version


def validate_media(filename, declared_type, content, caption):
    name = (filename or '').lower()
    types = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.mp4': 'video/mp4', '.pdf': 'application/pdf'}
    extension = next((ext for ext in types if name.endswith(ext)), None)
    mime = types.get(extension)
    if not mime or declared_type != mime or not content or len(content) > LIMITS[mime]:
        raise MediaError('Envie JPG/PNG até 5 MB, MP4 até 16 MB ou PDF até 10 MB.')
    if mime == 'application/pdf' and (not content.startswith(b'%PDF-') or b'%%EOF' not in content[-1024:]):
        raise MediaError('O documento precisa ser um PDF válido.')
    if mime == 'image/jpeg' and not content.startswith(b'\xff\xd8\xff'):
        raise MediaError('A imagem JPG não é válida.')
    if mime == 'image/png' and not content.startswith(b'\x89PNG\r\n\x1a\n'):
        raise MediaError('A imagem PNG não é válida.')
    if mime == 'video/mp4' and (len(content) < 12 or content[4:8] != b'ftyp'):
        raise MediaError('O vídeo precisa ser um MP4 válido.')
    if not isinstance(caption, str) or len(caption) > 1024:
        raise MediaError('A legenda deve ter no máximo 1024 caracteres.')
    return mime, 'document' if mime == 'application/pdf' else ('image' if mime.startswith('image/') else 'video')


def media_message_payload(to, kind, media_id, caption, filename=None):
    if kind not in ('image', 'video', 'document') or not re.fullmatch(r'55\d{10,11}', to):
        raise MediaError('Destinatário ou tipo de mídia inválido.')
    media = {'id': media_id, 'caption': caption}
    if kind == 'document':
        media['filename'] = re.sub(r'[^a-zA-Z0-9_.-]', '_', str(filename or 'tabela.pdf'))[:150]
    return {'messaging_product': 'whatsapp', 'recipient_type': 'individual',
            'to': to, 'type': kind, kind: media}


def _post(url, token, data, content_type):
    req = urllib.request.Request(url, data=data, method='POST', headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': content_type})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return json.loads(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Provider responses can contain recipient and account details; do not expose them.
        raise MediaError('O WhatsApp recusou o envio. Confira a conexão e a janela de atendimento.') from exc


def send_media(to, filename, mime, content, caption):
    config = provider_config()
    if not config:
        raise MediaError('Envio pela API indisponível: configure o acesso do WhatsApp no servidor.')
    token, phone_id, version = config
    mime, kind = validate_media(filename, mime, content, caption)
    base = f'https://graph.facebook.com/{version}/{phone_id}'
    boundary = secrets.token_hex(16)
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\n{mime}\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="media"\r\nContent-Type: {mime}\r\n\r\n'.encode() + content + b'\r\n',
        f'--{boundary}--\r\n'.encode(),
    ]
    uploaded = _post(base + '/media', token, b''.join(parts), 'multipart/form-data; boundary=' + boundary)
    media_id = uploaded.get('id')
    if not media_id:
        raise MediaError('O WhatsApp não confirmou o recebimento do arquivo.')
    payload = media_message_payload(to, kind, media_id, caption, filename)
    result = _post(base + '/messages', token, json.dumps(payload).encode(), 'application/json')
    messages = result.get('messages') or []
    if not messages or not messages[0].get('id'):
        raise MediaError('O WhatsApp não confirmou a mensagem.')
    return messages[0]['id']


def send_text(to, body):
    config=provider_config()
    if not config:
        raise MediaError('Envio pela API indisponível: configure o acesso do WhatsApp no servidor.')
    if not re.fullmatch(r'55\d{10,11}',to) or not isinstance(body,str) or not body.strip() or len(body)>4096:
        raise MediaError('Destinatário ou mensagem inválida (até 4.096 caracteres).')
    token,phone_id,version=config
    result=_post(f'https://graph.facebook.com/{version}/{phone_id}/messages',token,
        json.dumps({'messaging_product':'whatsapp','to':to,'type':'text','text':{'body':body}}).encode(),'application/json')
    try:
        mid=result['messages'][0]['id']
        if not isinstance(mid,str) or not mid or len(mid)>256:raise ValueError()
        return mid
    except (KeyError,IndexError,TypeError,ValueError) as exc:
        raise MediaError('Resposta de envio inválida. Confira o histórico antes de repetir.') from exc
