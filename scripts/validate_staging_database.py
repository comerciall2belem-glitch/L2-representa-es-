"""Read-only validation from the staging service's private network.
Run: L2_EXPECTED_DATABASE=l2one_homologacao python scripts/validate_staging_database.py
Never run destructive integration fixtures against this database.
"""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database_connection import validate_staging_database, validate_staging_configuration

def main():
    expected=os.environ.get('L2_EXPECTED_DATABASE','')
    if not expected:
        print(json.dumps({'ok':False,'error':'L2_EXPECTED_DATABASE obrigatório'}));return 2
    dsn=os.environ.get('DATABASE_URL','')
    try:
        validate_staging_configuration()
        result=validate_staging_database(dsn,expected)
        print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 1
    except Exception as exc:
        # Exception messages can contain credentials or document data. Log class only.
        print(json.dumps({'ok':False,'errorType':type(exc).__name__,'error':'Falha de conexão ou validação; confira rede interna e configuração do serviço'}));return 1

if __name__=='__main__': raise SystemExit(main())
