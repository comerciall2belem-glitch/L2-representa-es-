"""Public link chat, isolated from WhatsApp and protected by browser sessions."""
import hashlib
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter()
BASE = Path(__file__).resolve().parent
COOKIE = 'l2_chat_session'
WELCOME = 'Olá! Sou a Zara, assistente virtual da L2. Você pode solicitar tabelas, catálogos, orçamento ou atendimento. Informe seu nome, cidade e a marca de interesse. Não envie senhas ou dados bancários.'


def db():
    from server import db as connect
    return connect()


def staff(authorization):
    from server import auth
    user = auth(authorization)
    if user not in ('Ana Paula', 'Euler', 'Laís', 'Marlene'):
        raise HTTPException(403, 'Atendimento restrito à equipe responsável')
    return user


def setup(con):
    con.execute('''CREATE TABLE IF NOT EXISTS webchat_sessions (
        id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, ip_hash TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT 'bot',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '7 days',
        CHECK(mode IN ('bot','human')))''')
    con.execute('''CREATE TABLE IF NOT EXISTS webchat_messages (
        id TEXT PRIMARY KEY, seq BIGSERIAL UNIQUE NOT NULL, session_id TEXT NOT NULL REFERENCES webchat_sessions(id),
        sender TEXT NOT NULL CHECK(sender IN ('client','bot','staff')), body TEXT NOT NULL,
        nonce TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), seen_at TIMESTAMPTZ,
        UNIQUE(session_id,nonce,sender))''')
    con.execute('CREATE INDEX IF NOT EXISTS webchat_thread_idx ON webchat_messages(session_id,created_at)')
    con.execute('CREATE INDEX IF NOT EXISTS webchat_ip_idx ON webchat_sessions(ip_hash,created_at)')


def same_origin(request):
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.headers.get('host'):
        raise HTTPException(403, 'Origem não permitida')


def session(con, token, lock=False):
    if not token or len(token) > 200:
        raise HTTPException(401, 'Inicie uma conversa')
    row = con.execute('SELECT id,name,mode FROM webchat_sessions WHERE token_hash=%s AND expires_at>now()' + (' FOR UPDATE' if lock else ''), (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row:
        raise HTTPException(401, 'Conversa expirada. Inicie novamente.')
    return row


def history(con, sid):
    rows = con.execute('''SELECT id,sender,body,created_at,seen_at FROM (
        SELECT id,sender,body,created_at,seen_at,seq FROM webchat_messages WHERE session_id=%s
        ORDER BY seq DESC LIMIT 200) recent ORDER BY seq''', (sid,)).fetchall()
    return [{'id':mid,'sender':sender,'text':body,'at':at.isoformat(),
             'status':'seen' if seen else 'saved'} for mid,sender,body,at,seen in rows]


@router.get('/atendimento')
@router.get('/atendimento/equipe')
def page():
    return FileResponse(BASE/'webchat.html', headers={'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer', 'X-Content-Type-Options':'nosniff', 'X-Frame-Options':'DENY'})


@router.get('/api/chat/logo')
def logo():
    return FileResponse(BASE/'logo-l2.jpeg', media_type='image/jpeg')


class Start(BaseModel):
    name: str = Field(default='', max_length=100)


@router.post('/api/chat/session')
def start(data: Start, request: Request, response: Response, l2_chat_session: str | None = Cookie(None)):
    same_origin(request)
    with db() as con:
        if l2_chat_session:
            try:
                row = session(con, l2_chat_session)
                response.headers['Cache-Control'] = 'no-store'
                return {'id':row[0], 'mode':row[2], 'messages':history(con,row[0])}
            except HTTPException:
                pass
        # Rate limit shared between processes; only a digest is stored.
        ip = hashlib.sha256((request.client.host if request.client else 'unknown').encode()).hexdigest()
        con.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (ip,))
        count = con.execute("SELECT count(*) FROM webchat_sessions WHERE ip_hash=%s AND created_at>now()-interval '1 hour'", (ip,)).fetchone()[0]
        if count >= 20:
            raise HTTPException(429, 'Aguarde para iniciar outra conversa')
        sid, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        con.execute('INSERT INTO webchat_sessions(id,token_hash,ip_hash,name) VALUES(%s,%s,%s,%s)', (sid,hashlib.sha256(token.encode()).hexdigest(),ip,data.name.strip()))
        con.execute("INSERT INTO webchat_messages(id,session_id,sender,body,nonce) VALUES(%s,%s,'bot',%s,'welcome')", (uuid.uuid4().hex,sid,WELCOME))
        items = history(con,sid)
    response.set_cookie(COOKIE, token, secure=True, httponly=True, samesite='strict', max_age=604800, path='/api/chat')
    response.headers['Cache-Control'] = 'no-store'
    return {'id':sid,'mode':'bot','messages':items}


@router.get('/api/chat/messages')
def messages(response: Response, l2_chat_session: str | None = Cookie(None)):
    with db() as con:
        sid,name,mode = session(con,l2_chat_session)
        con.execute("UPDATE webchat_messages SET seen_at=coalesce(seen_at,now()) WHERE session_id=%s AND sender IN ('bot','staff')", (sid,))
        response.headers['Cache-Control'] = 'no-store'
        return {'mode':mode,'messages':history(con,sid)}


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    nonce: str = Field(pattern=r'^[a-zA-Z0-9-]{16,64}$')


def save(con, sid, sender, text, nonce):
    con.execute('INSERT INTO webchat_messages(id,session_id,sender,body,nonce) VALUES(%s,%s,%s,%s,%s)', (uuid.uuid4().hex,sid,sender,text,nonce))
    con.execute('UPDATE webchat_sessions SET updated_at=now() WHERE id=%s', (sid,))


@router.post('/api/chat/messages')
def send(data: Message, request: Request, response: Response, l2_chat_session: str | None = Cookie(None)):
    same_origin(request)
    text = data.text.strip()
    if not text:
        raise HTTPException(422, 'Escreva uma mensagem')
    with db() as con:
        sid,name,mode = session(con,l2_chat_session,True)
        if not con.execute("SELECT 1 FROM webchat_messages WHERE session_id=%s AND nonce=%s AND sender='client'", (sid,data.nonce)).fetchone():
            count = con.execute("SELECT count(*) FROM webchat_messages WHERE session_id=%s AND sender='client' AND created_at>now()-interval '1 minute'", (sid,)).fetchone()[0]
            if count >= 12:
                raise HTTPException(429, 'Aguarde um instante antes de enviar novamente')
            save(con,sid,'client',text,data.nonce)
            if mode == 'bot':
                # Deterministic assistance: no provider credential or external call required.
                answer = 'Solicitação registrada para a equipe L2. Você pode continuar descrevendo o que precisa. A resposta aparecerá neste chat quando um atendente estiver disponível.'
                mode = 'human'
                con.execute("UPDATE webchat_sessions SET mode='human' WHERE id=%s", (sid,))
                save(con,sid,'bot',answer,data.nonce)
        response.headers['Cache-Control'] = 'no-store'
        return {'mode':mode,'messages':history(con,sid)}


@router.get('/api/chat/staff/conversations')
def conversations(response: Response, authorization: str | None = Header(None)):
    staff(authorization)
    with db() as con:
        rows = con.execute('''SELECT s.id,s.name,s.mode,s.updated_at,
            (SELECT body FROM webchat_messages WHERE session_id=s.id ORDER BY seq DESC LIMIT 1),
            (SELECT count(*) FROM webchat_messages WHERE session_id=s.id AND sender='client' AND seen_at IS NULL)
            FROM webchat_sessions s ORDER BY s.updated_at DESC LIMIT 100''').fetchall()
    response.headers['Cache-Control'] = 'no-store'
    return [{'id':sid,'name':name,'mode':mode,'at':at.isoformat(),'last':last,'unread':unread} for sid,name,mode,at,last,unread in rows]


@router.get('/api/chat/staff/conversations/{sid}')
def staff_thread(sid: str, response: Response, authorization: str | None = Header(None)):
    staff(authorization)
    with db() as con:
        if not con.execute('SELECT 1 FROM webchat_sessions WHERE id=%s', (sid,)).fetchone():
            raise HTTPException(404, 'Conversa não encontrada')
        con.execute("UPDATE webchat_messages SET seen_at=coalesce(seen_at,now()) WHERE session_id=%s AND sender='client'", (sid,))
        response.headers['Cache-Control'] = 'no-store'
        return history(con,sid)


@router.post('/api/chat/staff/conversations/{sid}/reply')
def staff_reply(sid: str, data: Message, request: Request, authorization: str | None = Header(None)):
    same_origin(request)
    user = staff(authorization)
    text = data.text.strip()
    if not text:
        raise HTTPException(422, 'Escreva uma mensagem')
    with db() as con:
        if not con.execute('SELECT id FROM webchat_sessions WHERE id=%s FOR UPDATE', (sid,)).fetchone():
            raise HTTPException(404, 'Conversa não encontrada')
        if not con.execute("SELECT 1 FROM webchat_messages WHERE session_id=%s AND nonce=%s AND sender='staff'", (sid,data.nonce)).fetchone():
            save(con,sid,'staff',text,data.nonce)
            con.execute("UPDATE webchat_sessions SET mode='human' WHERE id=%s", (sid,))
            con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES(%s,'webchat',%s,'reply')", (user,sid))
    return {'ok':True}
