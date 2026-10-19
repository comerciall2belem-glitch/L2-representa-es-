"""Optional Google Drive backups. OAuth credentials never reach the browser."""
import asyncio
import base64
import gzip
import hashlib
import json
import logging
import os
import secrets
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

SCOPE = 'https://www.googleapis.com/auth/drive.file'
PREFIX = '/api/integrations/google-drive'
COOKIE = 'l2_drive_oauth'
db = authenticate = export_snapshot = None


def settings():
    names = ('GOOGLE_DRIVE_CLIENT_ID', 'GOOGLE_DRIVE_CLIENT_SECRET', 'GOOGLE_DRIVE_REDIRECT_URI')
    values = {name: os.environ.get(name, '').strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    redirect = values[names[2]]
    if redirect and (urlsplit(redirect).scheme != 'https' or urlsplit(redirect).path != PREFIX + '/callback' or urlsplit(redirect).query or urlsplit(redirect).fragment):
        missing.append('GOOGLE_DRIVE_REDIRECT_URI válida em HTTPS')
    return values, missing


def cipher():
    values, missing = settings()
    if missing:
        raise HTTPException(503, 'A configuração Google do L2 One ainda não foi concluída no servidor')
    material = values['GOOGLE_DRIVE_CLIENT_SECRET'] + '\0' + values['GOOGLE_DRIVE_CLIENT_ID']
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest()))


def setup(con):
    con.execute('''CREATE TABLE IF NOT EXISTS google_drive_connections (
        username TEXT PRIMARY KEY, refresh_cipher TEXT NOT NULL, folder_id TEXT,
        file_id TEXT, last_hash TEXT, last_backup TIMESTAMPTZ, last_error TEXT,
        connected_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    con.execute('''CREATE TABLE IF NOT EXISTS google_drive_oauth_states (
        state_hash TEXT PRIMARY KEY, username TEXT NOT NULL, session_hash TEXT NOT NULL,
        cookie_hash TEXT NOT NULL, verifier_cipher TEXT NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '10 minutes')''')


def owner(header):
    user = authenticate(header)
    if user != 'Ana Paula':
        raise HTTPException(403, 'A conexão do Drive é administrada pela gestora Ana Paula')
    return user


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def json_bytes(snapshot):
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode()


def checked(response):
    if response.status_code >= 400:
        # Do not expose Google's token responses or third-party error contents.
        raise RuntimeError('Não foi possível enviar ao Google Drive. Confira a conexão e o espaço disponível na conta Google.')
    return response.json()


def backup(user, force=False):
    if user != 'Ana Paula':
        raise HTTPException(403, 'Acesso restrito à gestora')
    cipher()  # Fail before touching a connection when configuration is incomplete.
    with db() as con:
        # Serialize across workers and manual requests; disconnect waits for this operation.
        con.execute('SELECT pg_advisory_xact_lock(%s)', (8239151,))
        row = con.execute('SELECT refresh_cipher,folder_id,file_id,last_hash FROM google_drive_connections WHERE username=%s FOR UPDATE', (user,)).fetchone()
        if not row:
            raise HTTPException(409, 'Conecte a conta Google antes de enviar')
        try:
            content = json_bytes(export_snapshot(user))
            fingerprint = hashlib.sha256(content).hexdigest()
            if not force and fingerprint == row[3]:
                return {'sent': False, 'unchanged': True}
            if len(content) > 40 * 1024 * 1024:
                raise RuntimeError('A cópia dos registros excedeu o limite de 40 MB; o envio requer revisão técnica')
            values, _ = settings()
            refresh = cipher().decrypt(row[0].encode()).decode()
            with httpx.Client(timeout=45) as client:
                token = checked(client.post('https://oauth2.googleapis.com/token', data={
                    'client_id': values['GOOGLE_DRIVE_CLIENT_ID'], 'client_secret': values['GOOGLE_DRIVE_CLIENT_SECRET'],
                    'grant_type': 'refresh_token', 'refresh_token': refresh}))
                headers = {'Authorization': 'Bearer ' + token['access_token']}
                folder, file_id = row[1], row[2]
                if not folder:
                    folder = checked(client.post('https://www.googleapis.com/drive/v3/files', headers=headers,
                        params={'fields': 'id'}, json={'name': 'L2 One · Registros', 'mimeType': 'application/vnd.google-apps.folder'}))['id']
                metadata = {'name': 'L2_One_Registros.json.gz', 'mimeType': 'application/gzip'}
                if not file_id:
                    metadata['parents'] = [folder]
                # Memory only; no device or server disk copy is created.
                boundary = 'l2-' + secrets.token_hex(16)
                body = (('--' + boundary + '\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n').encode()
                    + json_bytes(metadata) + ('\r\n--' + boundary + '\r\nContent-Type: application/gzip\r\n\r\n').encode()
                    + gzip.compress(content, mtime=0) + ('\r\n--' + boundary + '--\r\n').encode())
                url = 'https://www.googleapis.com/upload/drive/v3/files' + ('/' + file_id if file_id else '')
                result = checked(client.request('PATCH' if file_id else 'POST', url,
                    params={'uploadType': 'multipart', 'fields': 'id'},
                    headers={**headers, 'Content-Type': 'multipart/related; boundary=' + boundary}, content=body))
                file_id = result['id']
            con.execute('UPDATE google_drive_connections SET folder_id=%s,file_id=%s,last_hash=%s,last_backup=now(),last_error=NULL WHERE username=%s', (folder, file_id, fingerprint, user))
            return {'sent': True}
        except (InvalidToken, httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
            # Keep old cloud copy and credentials; never claim a successful upload.
            message = 'Reconecte o Google Drive' if isinstance(exc, InvalidToken) else 'Falha no envio ao Drive; os registros continuam preservados no L2 One'
            con.execute('UPDATE google_drive_connections SET last_error=%s WHERE username=%s', (message, user))
            return {'sent': False, 'error': message}


async def worker():
    while True:
        try:
            _, missing = settings()
            if not missing:
                with db() as con:
                    connected = con.execute("SELECT 1 FROM google_drive_connections c JOIN app_users u ON u.username=c.username WHERE c.username='Ana Paula' AND u.active AND NOT u.must_change_password").fetchone()
                if connected:
                    await asyncio.to_thread(backup, 'Ana Paula')
        except Exception:
            logging.getLogger('uvicorn.error').warning('google_drive_backup_unavailable')
        await asyncio.sleep(60)


class RedactOAuthQuery(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) >= 3 and str(record.args[2]).startswith(PREFIX + '/callback'):
            args = list(record.args)
            args[2] = PREFIX + '/callback'
            record.args = tuple(args)
        return True


def configure(app, database, auth, snapshot):
    global db, authenticate, export_snapshot
    db, authenticate, export_snapshot = database, auth, snapshot
    logging.getLogger('uvicorn.access').addFilter(RedactOAuthQuery())

    @app.get(PREFIX + '/configuration')
    def configuration():
        _, missing = settings()
        return {'configured': not missing, 'missing': missing}

    @app.get(PREFIX + '/status')
    def status(authorization: str | None = Header(default=None)):
        user = owner(authorization)
        _, missing = settings()
        with db() as con:
            row = con.execute('SELECT last_backup,last_error FROM google_drive_connections WHERE username=%s', (user,)).fetchone()
        return {'configured': not missing, 'connected': bool(row), 'missing': missing,
            'lastBackup': row[0].isoformat() if row and row[0] else None, 'error': row[1] if row else None}

    @app.post(PREFIX + '/connect')
    def connect(authorization: str | None = Header(default=None)):
        user = owner(authorization)
        values, _ = settings()
        encryption = cipher()
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        with db() as con:
            con.execute('DELETE FROM google_drive_oauth_states WHERE expires_at<now()')
            con.execute('INSERT INTO google_drive_oauth_states(state_hash,username,session_hash,cookie_hash,verifier_cipher) VALUES(%s,%s,%s,%s,%s)',
                (digest(state), user, digest(authorization[7:]), digest(nonce), encryption.encrypt(verifier.encode()).decode()))
        query = {'client_id': values['GOOGLE_DRIVE_CLIENT_ID'], 'redirect_uri': values['GOOGLE_DRIVE_REDIRECT_URI'],
            'response_type': 'code', 'scope': SCOPE, 'access_type': 'offline', 'prompt': 'consent', 'state': state,
            'code_challenge_method': 'S256', 'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
        response = JSONResponse({'url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(query)}, headers={'Cache-Control': 'no-store'})
        response.set_cookie(COOKIE, nonce, max_age=600, secure=True, httponly=True, samesite='lax', path=PREFIX)
        return response

    @app.get(PREFIX + '/callback')
    def callback(request: Request, state: str = '', code: str = '', error: str = ''):
        encryption = cipher()
        with db() as con:
            row = con.execute('SELECT username,session_hash,cookie_hash,verifier_cipher FROM google_drive_oauth_states WHERE state_hash=%s AND expires_at>now() FOR UPDATE', (digest(state),)).fetchone()
            if not row or not secrets.compare_digest(row[2], digest(request.cookies.get(COOKIE, ''))):
                raise HTTPException(400, 'Conexão Google inválida ou expirada. Inicie novamente pelo L2 One')
            active = con.execute('SELECT 1 FROM sessions s JOIN app_users u ON u.username=s.username WHERE s.token_hash=%s AND s.username=%s AND s.expires_at>now() AND u.active AND NOT u.must_change_password', (row[1], row[0])).fetchone()
            if not active:
                raise HTTPException(401, 'Entre novamente no L2 One antes de conectar o Google')
            con.execute('DELETE FROM google_drive_oauth_states WHERE state_hash=%s', (digest(state),))
        if error or not code:
            message = 'A conexão não foi autorizada. Seus registros continuam no L2 One.'
        else:
            values, _ = settings()
            try:
                with httpx.Client(timeout=30) as client:
                    token = checked(client.post('https://oauth2.googleapis.com/token', data={
                        'client_id': values['GOOGLE_DRIVE_CLIENT_ID'], 'client_secret': values['GOOGLE_DRIVE_CLIENT_SECRET'],
                        'code': code, 'code_verifier': encryption.decrypt(row[3].encode()).decode(),
                        'redirect_uri': values['GOOGLE_DRIVE_REDIRECT_URI'], 'grant_type': 'authorization_code'}))
                if not token.get('refresh_token') or SCOPE not in token.get('scope', '').split():
                    raise RuntimeError('Permissão incompleta')
                with db() as con:
                    con.execute('SELECT pg_advisory_xact_lock(%s)', (8239151,))
                    # A different Google account must never reuse folder/file IDs of the prior account.
                    con.execute('INSERT INTO google_drive_connections(username,refresh_cipher) VALUES(%s,%s) ON CONFLICT(username) DO UPDATE SET refresh_cipher=excluded.refresh_cipher,folder_id=NULL,file_id=NULL,last_hash=NULL,last_backup=NULL,last_error=NULL,connected_at=now()', (row[0], encryption.encrypt(token['refresh_token'].encode()).decode()))
                message = 'Google Drive conectado. A cópia dos registros será enviada automaticamente em até um minuto.'
            except (httpx.HTTPError, RuntimeError, InvalidToken, KeyError, ValueError):
                message = 'Não foi possível concluir a conexão Google. Tente novamente em Configurações.'
        response = HTMLResponse('<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><title>L2 One · Google Drive</title><main style="max-width:600px;margin:60px auto;font:20px system-ui;padding:24px;color:#243f46"><h1>L2 One</h1><p>' + message + '</p><a href="/">Voltar ao L2 One</a></main>', headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"})
        response.delete_cookie(COOKIE, path=PREFIX)
        return response

    @app.post(PREFIX + '/backup')
    def send_backup(authorization: str | None = Header(default=None)):
        result = backup(owner(authorization), force=True)
        if result.get('error'):
            raise HTTPException(502, result['error'])
        return result

    @app.delete(PREFIX + '/connection')
    def disconnect(authorization: str | None = Header(default=None)):
        user = owner(authorization)
        with db() as con:
            con.execute('SELECT pg_advisory_xact_lock(%s)', (8239151,))
            con.execute('DELETE FROM google_drive_connections WHERE username=%s', (user,))
            con.execute('DELETE FROM google_drive_oauth_states WHERE username=%s', (user,))
        return {'disconnected': True, 'filesPreserved': True}
