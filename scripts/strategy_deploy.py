"""L2 strategic deployment utilities. Never prints credentials or sends in check mode."""
import argparse
import json
import os
import sys
from urllib.parse import urlparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['check','migrate','tick'])
    args=parser.parse_args()
    from whatsapp_media import provider_config
    if args.action=='check':
        from field_audio import ready
        print(json.dumps({'audioTranscriptionConfigured':ready(),'databaseConfigured':bool(os.getenv('DATABASE_URL')),'whatsappOutboundConfigured':bool(provider_config()),'whatsappSignatureConfigured':bool(os.getenv('WA_APP_SECRET') or os.getenv('WHATSAPP_APP_SECRET')),'routingProviderHostname':urlparse(os.getenv('L2_ROUTING_URL','https://router.project-osrm.org')).hostname,'automaticSending':'Requires enabled settings and bound operators or customer consent'},ensure_ascii=False))
        return
    if not os.getenv('DATABASE_URL'):raise SystemExit('DATABASE_URL ausente')
    import strategic_crm as crm
    import field_assistant as field
    if args.action=='migrate':
        import psycopg
        with psycopg.connect(os.environ['DATABASE_URL']) as con:
            con.execute('SELECT pg_advisory_xact_lock(%s)',(8239020,))
            crm.setup(con);field.setup(con)
        print('Migração estratégica concluída; cadências e vínculos não foram ativados.')
        return
    import server
    crm.configure(**{key:getattr(server,key) for key in ('db','auth','require_sector','valid_cnpj','check_client_scope','scoped_rows','project_attendance','is_seller')})
    crm.tick();field.notification_tick()
    print('Ciclo executado; consulte os históricos do MCR e Preposto para aceitação e entrega.')

if __name__=='__main__':main()
