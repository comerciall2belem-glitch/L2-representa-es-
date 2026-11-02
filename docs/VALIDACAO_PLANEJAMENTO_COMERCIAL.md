# Planejamento comercial — entrega e validação

02/11/2026 • branch feat/commercial-indicators-design.

## Implementado no código

- API FastAPI em `/api/strategy/commercial`, reaproveitando sessões revogáveis
  existentes, permissões efetivas e isolamento de carteira no SQL.
- Migração aditiva e índices próprios; flag `L2_COMMERCIAL_PLANNING_ENABLED=false`
  por padrão. Startup cria as tabelas somente quando a flag está habilitada.
- Clientes e Crescimento: fichas por canal/perfil/gestão/foco/portfólio, escuta
  ativa, pesquisas no servidor e paginação. Conflitos entre aparelhos retornam 409.
- Documento normalizado com etapa explícita, importação idempotente, vínculo opcional
  ao pedido faturado e devolução com controle de saldo por SKU/marca.
- Reconhecimento manual de pedidos L2 faturados, com conciliação dos totais e
  proibição de classificar o mesmo pedido em duas etapas. Pedidos legados não são
  automaticamente tratados como Sell Through: representação e revenda são distintas.
- Produtividade e UPA anuais, resultado mensal separado, atividade, ticket por pedido,
  preço unitário, inícios/reinícios/cessados com controle de cobertura histórica.
- Retenção nos 60 dias anteriores à cessação, por calendário de 12 meses; relatórios
  por região/canal, GMV líquido exclusivo de Sell Out e curva ABC por SKU/marca.
- Baseline imutável por usuário/filtros, protegido quando sua carteira mudar.
- PDF e XLSX no servidor; texto externo em Excel não vira fórmula. Agendas diárias,
  semanais (sábado) e mensais (dia 1), em Belém, com arquivos em banco e retenção
  de 90 dias; download revalida autoria e escopo. Nenhuma agenda foi criada remotamente.
- Recebíveis/parcelas vinculados à venda reconhecida, sem ultrapassar o total.
- OFX 1.x/2.x com conta extraída do extrato, BRL, idempotência por conta/FITID;
  baixa automática apenas por referência e saldo exatos. Pagamento nunca gera receita.
- Alocação manual de créditos/recebimentos parciais com verificação de conta/saldo.
- MFA TOTP de seis dígitos nas mutações financeiras, proteção contra replay e
  bloqueio por tentativas; segredo cifrado com chave Fernet fornecida no ambiente.
- Contrato L2 de webhook HMAC-SHA256, timestamp até 5 minutos e idempotência.
  Esse contrato depende de um adaptador bancário; não é uma API específica de banco.
- CPF/CNPJ com dígitos verificadores e proteção de duplicidade também no sync atual.

## Validação local executada

`python -m pytest -q`: **304 passed, 70 skipped** após instalar dependências
previstas pelo projeto. Os skips incluem testes existentes que exigem banco e os
sete novos testes PostgreSQL. Eles NÃO representam homologação aprovada.

`commercial_planning_dom.cjs`: teste de tela aprovado (12 asserções), cobrindo
apresentação, RBAC visual, autorização em chamadas, escape de HTML, pesquisa,
salvamento com versão e preservação de formulário ao atualizar indicadores.

Sintaxe Python/JavaScript e `git diff --check` aprovados. Também aprovados:
`test_commercial_workflow.cjs`, `commercial_tables_ui.cjs`, `admin_seller_ui.cjs`.

Falhas existentes reproduzidas sem alterações em origin/main (8e6dfe4):
- `strategic_crm_dom.cjs`: consulta botão ausente na rotina de descoberta;
- `sales_workspace_dom.cjs`: espera texto “Vender e acompanhar”;
- `live_records_dom.cjs`: espera preservar formulário que é substituído.

Essas falhas permanecem pendências para a liberação global; não foram mascaradas.
A revisão do novo módulo não deve ser confundida com aprovação de toda a aplicação.

## PostgreSQL e duas sessões

`tests/test_commercial_planning_postgres.py` tem sete cenários em esquema descartável:
RBAC com sessão em banco, importação concorrente, saldo de devolução, duas sessões
com conflito de versão, MFA/replay, baixa idempotente, exportações e agenda sem
duplicação. A fixture exige nome de banco terminado em `_test` e elimina só o
esquema de UUID que criou. Workflow próprio provisiona PostgreSQL 16 no CI.

A execução local de PostgreSQL foi impedida pelo ambiente ao abrir socket Unix.
A validação foi então executada no GitHub Actions com PostgreSQL 16 real:
- Commit validado: `8e0f77f89feeb48c2fa27dcfab28c671566ad751`.
- Workflow do módulo: https://github.com/comerciall2belem-glitch/L2-representa-es-/actions/runs/41478366768
- Resultado terminal: **success**, **45 testes aprovados**, sem skips nessa suíte,
  incluindo os sete cenários de PostgreSQL. DOM: 12 asserções aprovadas.
- Foram corrigidos dois erros na fixture de dados antes da rodada aprovada.
- PDF renderizado e conferido visualmente nas páginas de indicadores, retenção
  e portfólio; fontes incorporadas e valores monetários em Real.

Validação geral: https://github.com/comerciall2belem-glitch/L2-representa-es-/actions/runs/41478366897
O workflow geral teve 251 testes unittest aprovados, mas falhou no teste
`order_multibrand_ui.cjs:26` (0 produtos encontrados, 2 esperados). A mesma falha
foi reproduzida em origin/main sem esta implementação. Esse bloqueio global
permanece aberto. Navegador autenticado no Render e celular real seguem pendentes.

## Integrações e segurança ainda não ativadas

A emissão real de boletos NÃO foi implementada: o endpoint responde 503 com motivo,
sem criar boleto fictício. Banco/provedor, contrato de cobrança, ambiente sandbox,
credenciais e modelo de autenticação/certificados não foram informados. O contrato
HMAC entregue não substitui a validação do webhook oficial de um banco.

A aplicação mantém as sessões opacas existentes, armazenadas por hash e revogáveis;
não houve migração global para JWT/OAuth2. MFA foi acrescentado como segundo fator
nas operações bancárias deste módulo, sem alterar o login da equipe.

O token JWT/OAuth2, se obrigatório na identidade global, requer uma migração
compatível com o aplicativo atual e homologação própria. Não está concluído aqui.

Ainda não há cancelamento/reversão de documentos reconhecidos nem estorno de baixa
bancária por esta API. Eventos incorretos devem permanecer bloqueados para correção
supervisionada; não usar exclusão ou edição de fato financeiro para contornar.

## Gate de produção e configuração

1. Confirmar workspace Render: “Ana Paula's workspace” (comerciall2belem@gmail.com).
   O conector não tinha workspace selecionado e a seleção ainda não foi confirmada.
2. Identificar serviços e banco de homologação e produção pelo ID e URL; validar
   isolamento antes de qualquer conexão com dados reais.
3. Manter o workflow PostgreSQL aprovado e resolver as falhas gerais; testar navegador autenticado,
   carteira/financeiro, duas sessões, PDF/Excel e fluxos bancários no sandbox.
4. Configurar `L2_COMMERCIAL_MFA_KEY` (Fernet) e `L2_BANK_WEBHOOK_SECRET` (≥32
   caracteres) em armazenamento seguro. Não gravar valores em código ou logs.
5. Definir e implementar adaptador oficial e emissão de boletos do banco escolhido.
6. Backup e restauração verificados; ativar a flag em homologação e executar aceite.
7. Publicar somente o commit validado. Na produção, ativação exige restart para o
   setup aditivo. Verificar health, API, permissões e downloads após o deploy.
8. Rollback: desabilitar flag e retornar à versão anterior, preservando fatos e
   auditoria. Não apagar tabelas ou registros para simular reversão.

Nenhuma autorização genérica pode substituir banco/provedor ou credenciais ausentes.
A entrega de código não significa que houve merge, deploy ou operação bancária real.
