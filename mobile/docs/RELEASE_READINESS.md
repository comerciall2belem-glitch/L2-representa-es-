# L2 Produtividade — implantação e prontidão

Atualização: 14/10/2026. Produção ainda não liberada.

## Executado

Gateway instalado em https://l2-one-homologacao.onrender.com, deploy dep-db7gg9m7bikc73d0b5q0, commit 70f02492ef38241db6bc0737fb3db7a827940c85. Serviço live e /health retornando HTTP 200 nos logs. A conexão do gateway recusa qualquer banco diferente de l2one_homologacao antes de executar escritas. Usuário, cliente, tabela e produto de QA exclusivos foram criados no banco isolado. Nenhum pedido foi enviado a uma indústria.

O serviço implantado executou 8 verificações via TestClient/ASGI e PostgreSQL real: acesso sem token recusado; login do usuário dedicado; snapshot com carteira autorizada; pedido aceito pelas regras reais; reenvio com recibo idêntico e um único pedido; pedido retornando no snapshot; preço vencido recusado sem gravação; logout revogando sessão. Evidência: log mobile_qa_result passed=8 em 14/10/2026 04:43:15 UTC. Isso não é teste do aplicativo em aparelho nem teste HTTP pela rede a partir do dispositivo. Uma sondagem HTTPS a partir do ambiente de desenvolvimento expirou; não inferimos sucesso da conexão móvel a partir dela.

11 testes automatizados do gateway passaram, incluindo compatibilidade com o contrato antigo de preços da homologação. Os 17 testes anteriores do motor mobile continuam documentados. APK piloto 0.3.0 já compilado, ARM64, minSdk 24/Android 7 e assinatura v2 conferida; usa certificado Android Debug e não é artefato final de produção.

## Assinatura preparada

Workflow manual .github/workflows/mobile-signed-release.yml preparado para Expo/EAS CLI 24.13.0. Perfis preview (distribuição interna) e production; Android gera APK ARM64, e iOS usa credenciais remotas da conta Apple. O perfil play-store separado gera AAB. Projeto Expo vinculado à equipe comerciall2belems-team, slug l2-one, Project ID 2ff296d6-739f-43b7-a075-a2856f6ae57a. A conta autenticada é comerciall2belem; a organização inicialmente informada l2representacoes recusou a criação por falta de permissão.

Configure EXPO_TOKEN em GitHub Actions Secrets. Configure nas Variables: EAS_PROJECT_ID, EXPO_OWNER, EXPO_PROJECT_SLUG, APPLE_TEAM_ID e L2_API_URL. L2_API_URL deve apontar para homologação no piloto. O token não deve ser incluído em arquivos ou mensagens públicas. Para iOS interno, os dispositivos precisam estar cadastrados no perfil de distribuição; para produção, certificados e provisioning profile da equipe Apple devem existir no EAS.

O workflow bloqueia variáveis ausentes. Após um build finalizado, verifica assinatura APK, recusa certificado Android Debug, confirma API24 e ARM64, e no macOS verifica codesign/TeamIdentifier do IPA. Gera hashes e disponibiliza somente os artefatos verificados. O workflow GitHub assinado ainda não foi executado. O build Android preview-pilot foi solicitado diretamente pelo EAS CLI autenticado e usa keystore remoto gerado na equipe. Resultado e assinatura final do artefato ficam em EAS_PILOT_BUILD.json. Nenhum IPA foi emitido.

## Dependências ainda abertas

| Etapa | Situação | Dependência concreta |
|---|---|---|
| Estoque externo | Não conectado | Nome do ERP/WMS, documentação, ambiente sandbox, identificação por SKU/marca/CD, autenticação |
| Logística externa | Não conectada | Nome da transportadora/plataforma, API sandbox, mapeamento de pedido/nota/rastreio, autenticação |
| WhatsApp oficial | Não configurado na homologação | Credenciais Meta no Render, número/phone ID, templates/janela permitida e destinatário de teste autorizado |
| Assinatura iOS | Contas confirmadas pela usuária; acesso não configurado | Apple Team ID real, credenciais/perfis Apple e token seguro para CI; owner/slug/Project ID Expo já vinculados |
| Android definitivo | Pipeline preparado | Keystore/certificado empresarial no EAS e aceite da homologação física |
| QA físico | Não executado | Aparelho Android ARM64 Android7+, iPhone/iPad e evidências de layout/performance/bateria |
| Release produção | Bloqueado | Aprovação dos testes reais, integrações oficiais e artefatos definitivos assinados |

Relatório de bugs: a incompatibilidade allowed_states do gateway com a versão antiga do ERP foi corrigida e coberta por teste. A falha web live_records_dom.cjs:41 já existia na main base e permanece registrada. Não foram coletados bugs físicos porque não houve acesso a aparelhos.

## Próxima execução

Com os identificadores e acessos seguros configurados, executar o workflow preview na branch mobile e instalar os artefatos assinados nos aparelhos. Seguir ROTEIRO_CAMPO.md, anexando modelo/OS, versão/hash, horário, evidência, pedido/recibo e responsável por cenário. Testar offline prolongado, reconexão, preço alterado, troca de usuário, revogação e PDFs. Medir performance e consumo de bateria no aparelho; não preencher medições por estimativa. Só então emitir e liberar os artefatos de produção.
