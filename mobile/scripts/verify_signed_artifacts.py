"""Download own EAS build results and verify signatures before exposing artifacts."""
import json,os,subprocess,urllib.request,hashlib,zipfile,tempfile
from pathlib import Path

def run(*args):
    result=subprocess.run(args,capture_output=True,text=True,check=True)
    return result.stdout+result.stderr

builds=json.loads(Path('signed-builds.json').read_text())
if isinstance(builds,dict): builds=[builds]
out=Path('release-artifacts');out.mkdir(exist_ok=True)
receipts=[]
for build in builds:
    if str(build.get('status','')).upper()!='FINISHED': raise SystemExit('Build não finalizado')
    platform=str(build['platform']).lower()
    url=build.get('artifacts',{}).get('applicationArchiveUrl')
    if not url or not url.startswith('https://'): raise SystemExit('Artefato HTTPS ausente')
    path=out/('L2_Produtividade.'+('apk' if platform=='android' else 'ipa'))
    urllib.request.urlretrieve(url,path)
    if platform=='android':
        tools=Path(os.environ['ANDROID_HOME'])/'build-tools'/'36.0.0'
        cert=run(str(tools/'apksigner'),'verify','--verbose','--print-certs',str(path))
        if 'Android Debug' in cert: raise SystemExit('Certificado de debug recusado para entrega assinada')
        metadata=run(str(tools/'aapt'),'dump','badging',str(path))
        if "sdkVersion:'24'" not in metadata or "native-code: 'arm64-v8a'" not in metadata:
            raise SystemExit('APK exige Android 7/API24 e ARM64')
        (out/'android-signature.txt').write_text(cert)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(path) as archive:
                if any(n.startswith('/') or '..' in Path(n).parts for n in archive.namelist()): raise SystemExit('IPA inválido')
                archive.extractall(tmp)
            apps=list((Path(tmp)/'Payload').glob('*.app'))
            if len(apps)!=1: raise SystemExit('Payload iOS inválido')
            run('codesign','--verify','--deep','--strict',str(apps[0]))
            signature=run('codesign','-dv','--verbose=4',str(apps[0]))
            if 'TeamIdentifier='+os.environ['APPLE_TEAM_ID'] not in signature:
                raise SystemExit('IPA não pertence à equipe Apple esperada')
            (out/'ios-signature.txt').write_text(signature)
    receipts.append({'file':path.name,'buildId':build['id'],'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'signatureVerified':True})
(out/'checksums.json').write_text(json.dumps(receipts,indent=2)+'\n')
print('Artefatos assinados verificados:',len(receipts))
