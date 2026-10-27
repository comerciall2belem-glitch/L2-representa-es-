"""Fail closed when an environment declares its expected database."""
import os
from urllib.parse import urlsplit, unquote

def validate_database_target(database_url, expected=None):
    expected = os.getenv('L2_EXPECTED_DATABASE', '').strip() if expected is None else expected.strip()
    if not expected:
        return
    try:
        parsed = urlsplit(database_url)
        name = unquote(parsed.path.lstrip('/'))
    except ValueError as exc:
        raise RuntimeError('Configuração de banco inválida') from exc
    if parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname or name != expected:
        raise RuntimeError('Banco configurado não corresponde ao ambiente esperado')
