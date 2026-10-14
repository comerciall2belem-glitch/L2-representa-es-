# L2 Produtividade - Maleta Digital | 0.3.0

Código React Native/Expo com integração candidata para o backend existente L2 One.
Estado: compilação TypeScript e bundles Hermes aprovados; NÃO homologado em produção e sem APK/AAB/IPA assinado nesta entrega.

## Executar

Requer Node 24, npm, ambiente Android SDK ou macOS/Xcode para builds locais; alternativamente EAS Build com conta autorizada. Development build necessário para SQLCipher, não Expo Go.

```bash
npm ci
npm run typecheck
npm test
python3 -m pip install -r backend/requirements-test.txt
python3 -m unittest discover -s backend -v
npx expo install --check
npx expo prebuild --platform all --no-install
```

Usar `npm run android` em máquina com Android SDK e emulador/aparelho. iOS: instalar CocoaPods no macOS, executar `npx pod-install`, depois `npm run ios`.

## Arquivos

- App.tsx: interface celular/tablet, tema claro/escuro, login, Hoje, carteira, checklist, carrinho, snippets, tarefas, PDF offline e diário.
- src/store.ts: SQLite, fila persistente, rascunhos, recibos, resolução de conflitos e lease por worker.
- src/native.ts: SQLCipher, chave e token no SecureStore, banco separado por usuário/servidor, tarefa de background.
- src/api.ts: cliente HTTP para login existente e rotas protegidas mobile; timeout e erros estruturados.
- src/sync.ts: fila e confirmação; foreground, reconexão, SSE e background via native.ts/App.tsx.
- src/catalog.ts: download autenticado e armazenamento de PDF no SQLite cifrado; cópia temporária removida após compartilhar.
- backend/mobile_gateway.py: rotas aditivas /api/mobile/v1/snapshot e /sync, reutilizando autenticação e validações existentes em transação compartilhada.
- tests/: 17 testes TypeScript, incluindo ida/volta HTTP com ERP simulado e SQLite real do Node.
- backend/test_mobile_gateway.py: 8 testes ASGI com ERP legado simulado e adaptador SQLite. Locks PostgreSQL não são testados nesse adaptador.
- docs/: homologação e instalação, com limites e passos para piloto real.
- maestro/: roteiro para piloto de UI; não executado nesta entrega.
- android/ e ios/: projetos nativos gerados, sem compilação/assinatura do sistema operacional.
- dist/: bundles Hermes Android/iOS; são código compilado JavaScript, não instaladores.

## Integração obrigatória antes de login

Copiar `backend/mobile_gateway.py` para o repositório L2 One e adicionar no fim do server.py, depois das definições sync/auth:

```python
import sys
from mobile_gateway import install
install(sys.modules[__name__])
```

Publicar primeiro no ambiente de homologação. O gateway reutiliza `db`, `auth`, `sync`, `Sync`, `Change`, `require_sector`, `sectors_for`, `is_seller`, `check_client_scope`, `normalize_uf`, `price_table_matches_client`, `validate_table` e `price_id`. Conferir compatibilidade com o commit implantado. Código de referência consultado: server.py SHA 0f39bc85c8350380071a73a9f35baf8d361210ca. Não altera o login PWA; usa token de sessão opaco atual. JWT/refresh/delta incremental do contrato alvo v0.2 ainda não foram implementados. Não ativar rotas antigas inseguras como fallback.

O gateway mantém a mesma conexão/lock na verificação de preço, validação ERP, gravação e recibo. Essa adaptação utiliza ContextVar em torno de `module.db`; requer revisão e teste com o PostgreSQL real antes de produção. Não passa por credenciais de indústria nem envia mensagens de WhatsApp automaticamente. Snapshot retorna apenas coleções usadas no mobile, respeitando filtragem do legado e sem recursos financeiros brutos.

## Comportamento entregue

Login online inicial com conta L2 One. Senha provisória exige nova senha de 12 caracteres ou mais. Depois, banco local cifrado e acesso offline configurado para 72h; servidor revalida sessão e carteira ao sincronizar. A janela precisa ser aprovada na política de produção. Toda gravação mantém UUID estável e conteúdo imutável durante tentativas. Saldo, estoque, faturamento industrial e rastreamento só são conhecidos se a origem do L2 One fornecer dados; não há estoque presumido.

Pedidos novos são revisados no aparelho e enviados automaticamente ao reconectar; preço alterado exige revisão do vendedor. Sem motor de descontos por indústria homologado, não permitir desconto genérico. O ERP atual usa uma UF de tabela comum no pedido: PA para Pará; AP usa AP automaticamente nesta UI. Não exibir seletor de tabela manual. Suporte a preço PA excepcional para cliente AP exige política/seleção administrativa posterior.

Metas/indicadores desta versão são de pedidos registrados e visitas. Não tratar pedido recebido pelo L2 One como aceite/faturamento da indústria. Fechamentos ficam em mobile_closings e na consulta gerencial mobile; painel web central ainda precisa de componente que consuma esses registros. Campos metrics no fechamento são snapshot local, não prova de faturamento. O Kit v2.1 original não foi disponibilizado como arquivo; ferramentas foram implementadas a partir dos requisitos da conversa.

## Segurança e limitações conhecidas

Banco SQLCipher precisa de binário nativo próprio. Token e chave no Keychain/Keystore; nenhum segredo incluído no pacote. PDFs só são decifrados temporariamente após ação do usuário. Reiniciar/reinstalar sem a chave pode inviabilizar recuperar dados locais; exigir sincronização antes de desinstalar. Usuários sem autorização não recebem dados financeiros por esta interface. Logout mantém dados cifrados e fila para a conta original.

Notificações SSE atuais invalidam dados; polling 30s é fallback com app aberto. Background depende de agendamento iOS/Android: não garante execução imediata com app encerrado. Sync faz snapshots completos nesta integração de compatibilidade. Monitorar volume/performance e migrar para delta antes de carteiras muito grandes. Não houve teste visual em iPhone/iPad/Android, teste de criptografia no binário ou piloto PA/AP. Esta entrega é candidata de desenvolvimento, não versão liberada à equipe.
