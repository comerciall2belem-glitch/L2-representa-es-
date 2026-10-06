"""One-time admin recovery, restricted to the named homologation service."""
import os
import re

SERVICE = 'srv-daqk8ifavr4c738m78f0'


def recover(connect, hash_password):
    recovery_id = os.getenv('L2_HOMOLOGATION_RECOVERY_ID', '')
    password = os.getenv('L2_HOMOLOGATION_RECOVERY_PASSWORD', '')
    if os.getenv('RENDER_SERVICE_ID') != SERVICE or not recovery_id or not password:
        return False
    if not re.fullmatch(r'[a-f0-9]{32}', recovery_id) or len(password) < 20:
        raise RuntimeError('Invalid homologation recovery configuration')
    action = 'homologation_recovery_' + recovery_id
    with connect() as con:
        con.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (action,))
        if con.execute("SELECT 1 FROM audit_log WHERE username='Ana Paula' AND kind='app_user' AND action=%s", (action,)).fetchone():
            return False
        row = con.execute("SELECT username FROM app_users WHERE username='Ana Paula' FOR UPDATE").fetchone()
        if not row:
            raise RuntimeError('Homologation administrator missing')
        con.execute("UPDATE app_users SET password_hash=%s,must_change_password=true,active=true,updated_at=now() WHERE username='Ana Paula'", (hash_password(password),))
        con.execute("DELETE FROM sessions WHERE username='Ana Paula'")
        con.execute("INSERT INTO audit_log(username,kind,entity_id,action) VALUES('Ana Paula','app_user','Ana Paula',%s)", (action,))
    return True
