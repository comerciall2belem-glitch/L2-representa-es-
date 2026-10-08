"""Bounded transcription for signed field-operator audio; never confirms mutations by voice."""
import asyncio
import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse
import httpx
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

MAX_BYTES=6_000_000
FALLBACK='Áudio recebido, mas a transcrição está indisponível. Envie o comando por texto.'
MIMES={'audio/ogg':'ogg','audio/mpeg':'mp3','audio/mp4':'mp4','audio/aac':'aac','audio/wav':'wav','audio/x-wav':'wav','audio/webm':'webm'}

def ready():
    return os.getenv('L2_FIELD_AUDIO_ENABLED','').lower() in ('1','true') and bool(os.getenv('OPENAI_API_KEY','').strip())

def trusted_url(url):
    p=urlparse(url)
    return p.scheme=='https' and p.port in (None,443) and not p.username and not p.password and bool(p.hostname) and (p.hostname=='lookaside.fbsbx.com' or p.hostname.endswith('.fbcdn.net'))

def command_text(text):
    import field_assistant as field
    text=text.strip().strip('.').strip()
    text=re.sub(r'^(?:por favor[, ]+)?(?:registre|registrar) uma? visita (?:no cliente |na cliente |cliente )?','visita ',text,flags=re.I)
    text=re.sub(r'^(visita .+?)\s*,?\s+(pedido realizado|sem pedido|cliente ausente|cliente quente)$',r'\1: \2',text,flags=re.I)
    command=field.intent(text)
    # Explicit text confirmation/selection is required; speech cannot consume an existing session.
    if not command or command.get('kind') in ('confirmar','cancelar','choice','help') or re.fullmatch(r'\d+',text):return None
    return text

def reserve(db,phone,mid):
    key='audio-'+hashlib.sha256((phone+'|'+mid).encode()).hexdigest()[:40]
    with db() as con:
        con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
        row=con.execute("SELECT f.username FROM field_operators f JOIN app_users u ON u.username=f.username WHERE f.phone=%s AND f.enabled AND u.active AND u.sectors ? 'commercial'",(phone,)).fetchone()
        if not row:return None
        if con.execute('SELECT 1 FROM zara_messages WHERE message_id=%s',(mid,)).fetchone():return {'skip':True}
        old=con.execute("SELECT payload FROM entities WHERE kind='field_audio' AND id=%s",(key,)).fetchone()
        if old:return {**old[0],'key':key,'cached':True}
        count=con.execute("SELECT count(*) FROM entities WHERE kind='field_audio' AND payload->>'user'=%s AND updated_at::date=CURRENT_DATE",(row[0],)).fetchone()[0]
        value={'id':key,'user':row[0],'status':'Processing' if ready() and count<30 else 'Unavailable'}
        con.execute("INSERT INTO entities(kind,id,payload) VALUES('field_audio',%s,%s)",(key,Jsonb(value)))
        return {**value,'key':key}

def finish(db,key,value):
    with db() as con:
        con.execute("UPDATE entities SET payload=payload||%s,updated_at=now() WHERE kind='field_audio' AND id=%s",(Jsonb(value),key))

def convert_audio(data,ext):
    import imageio_ffmpeg
    with tempfile.TemporaryDirectory(prefix='l2-audio-') as root:
        source=Path(root)/('input.'+ext);output=Path(root)/'speech.wav';source.write_bytes(data)
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-i',str(source),'-t','91','-vn','-ac','1','-ar','16000',str(output)],check=True,timeout=15,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if output.stat().st_size>2_880_100:raise ValueError('Audio exceeds 90 seconds')
        return output.read_bytes()

async def transcribe(media,setting):
    mid=str(media.get('id',''))
    if not re.fullmatch(r'\d{1,40}',mid):raise ValueError('Invalid media id')
    token=setting('WA_ACCESS_TOKEN');version=setting('WA_GRAPH_VERSION')
    if not re.fullmatch(r'v\d+\.\d+',version):raise ValueError('Invalid Graph version')
    async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:
        metadata=await client.get(f'https://graph.facebook.com/{version}/{mid}',headers={'Authorization':'Bearer '+token})
        metadata.raise_for_status();meta=metadata.json();url=meta.get('url','');mime=str(meta.get('mime_type','')).split(';')[0]
        if not trusted_url(url) or mime not in MIMES or int(meta.get('file_size',MAX_BYTES+1))>MAX_BYTES:raise ValueError('Unsupported media')
        chunks=bytearray()
        async with client.stream('GET',url,headers={'Authorization':'Bearer '+token}) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks)>MAX_BYTES:raise ValueError('Media too large')
        wav=await run_in_threadpool(convert_audio,bytes(chunks),MIMES[mime])
        model=os.getenv('L2_FIELD_AUDIO_MODEL','gpt-4o-mini-transcribe')
        if model not in ('gpt-4o-mini-transcribe','gpt-4o-transcribe','whisper-1'):raise ValueError('Invalid transcription model')
        response=await client.post('https://api.openai.com/v1/audio/transcriptions',headers={'Authorization':'Bearer '+os.environ['OPENAI_API_KEY']},data={'model':model,'language':'pt','response_format':'json'},files={'file':('speech.wav',wav,'audio/wav')},timeout=40)
        response.raise_for_status();text=response.json().get('text','')
        if not isinstance(text,str) or not 1<=len(text)<=4000:raise ValueError('Invalid transcript')
        return text

async def prepare(db,phone,mid,media,setting):
    reserved=await run_in_threadpool(reserve,db,phone,mid)
    if reserved is None:return None
    if reserved.get('skip'):return {'skip':True}
    if reserved.get('cached') and reserved['status']=='Processing':return {'skip':True}
    if reserved.get('cached') or reserved['status']!='Processing':return reserved
    try:
        async with asyncio.timeout(75):text=await transcribe(media,setting)
        value={'status':'Transcribed','transcript':text}
    except Exception:
        value={'status':'Failed'}
    await run_in_threadpool(finish,db,reserved['key'],value)
    return value
