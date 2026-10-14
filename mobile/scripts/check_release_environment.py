"""Validate identifiers without printing credentials."""
import os,sys,re
required=['EXPO_TOKEN','EAS_PROJECT_ID','EXPO_OWNER','EXPO_PUBLIC_L2_API_URL']
if os.getenv('L2_BUILD_PLATFORM','all') != 'android': required.append('APPLE_TEAM_ID')
missing=[key for key in required if not os.getenv(key)]
if missing:
    print('Assinatura bloqueada: configure '+', '.join(missing),file=sys.stderr)
    sys.exit(1)
if os.getenv('APPLE_TEAM_ID') and not re.fullmatch(r'[A-Z0-9]{10}',os.environ['APPLE_TEAM_ID']):
    raise SystemExit('APPLE_TEAM_ID inválido')
print('Identificadores presentes. Credenciais remotas e dispositivos precisam estar cadastrados no EAS.')
if not os.environ['EXPO_PUBLIC_L2_API_URL'].startswith('https://'):
    raise SystemExit('A URL do L2 One deve usar HTTPS')
