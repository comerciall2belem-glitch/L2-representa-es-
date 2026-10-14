# Relatório de homologação técnica

L2 Produtividade - Maleta Digital | 0.3.0 | 14/10/2026

## Resultado atual

Candidato de desenvolvimento. Código TypeScript compila; bundles Hermes Android/iOS gerados; projetos nativos Android/iOS gerados; 27 testes automatizados aprovados. Esses resultados NÃO comprovam uso em produção, criptografia nativa em aparelho ou integração autenticada com a indústria/L2 One real.

## Evidências executadas

Ambiente: Linux, Node 24.19.0, JDK 17, Expo SDK 54, React Native 0.81.5, Python 3.12. Versões efetivas fixadas em package-lock.json. Banco do motor de testes: SQLite real pelo Node; ERP simulado via HTTP local. Gateway: FastAPI/ASGI, simulador de regras legadas e adaptador SQLite para testes. SQL PostgreSQL/advisory lock/row locks não são exercitados por esse adaptador.

`npm run typecheck`: sem erros. `npx expo install --check`: dependências compatíveis. `npx expo prebuild --platform all --no-install`: projetos gerados. `npx expo export --platform all`: bundles Hermes para Android e iOS. Avisos de depreciação de dependências são registrados, mas não invalidaram esses checks.

## Testes do app: 17 aprovados

1. Pedido salvo offline percorre HTTP, recebe confirmação do ERP simulado e aparece no SQLite.
2. Resposta perdida depois do aceite: retry mantém um pedido e uma numeração.
3. Reinício mantém fila e carrinho.
4. Edição concorrente preserva registro local e versão remota.
5. Sessão HTTP 401 não apaga fila nem reconhece operação.
6. Falha de gravação SQLite não cria fila parcial.
7. Banco de outro usuário é recusado.
8. Recibo ausente não limpa operação.
9. Nova edição não altera payload de operação pendente.
10. Lease evita dois workers simultâneos.
11. Cálculo em centavos e quantidade inteira.
12. Data operacional em America/Belem.
13. Carrinho multimarcas mantém vendedor e autoria administrativa.
14. Snippet rejeita variável sem preenchimento e normaliza telefone.
15. Limpeza preserva valores e códigos SKU.
16. Extrator identifica CNPJ, e-mail e SKU sem alterar cadastros.
17. QR Code é gerado offline e rejeita links com credenciais.

## Testes do gateway: 10 aprovados

1. Rotas exigem autenticação.
2. Reenvio idempotente mantém recibo e registro únicos.
3. Mesma chave com conteúdo alterado é recusada.
4. Preço vencido é rejeitado antes de gravar pedido.
5. Conflito remoto não sobrescreve entidade.
6. Cliente fora da carteira é negado.
7. Falha de validação reverte a escrita da operação legada.
8. Fechamento mantém unicidade por dia/revisão.
9. Recibo não expõe campos privados de comissão.
10. Reenvio respeita revogação de carteira posterior ao primeiro aceite.

## Verificação do ambiente conectado

O workspace do Render foi confirmado por Ana Paula. Foram localizados `l2-one` e `l2-one-homologacao`, ambos ativos no cadastro do Render. Homologação usa branch feature/precos-uf-cliente, produção usa main. Essa descoberta de configuração não é prova de login ou saúde da API. Duas sondagens HTTPS locais sem autenticação em /health e /api/mobile/v1/snapshot expiraram; o resultado é inconclusivo e pode envolver rede do ambiente ou inicialização do serviço. Nenhum pedido/cliente real foi enviado nessa verificação.

O código server.py da main consultada tem SHA 0f39bc85c8350380071a73a9f35baf8d361210ca. Usa token opaco, sincronização changeId e sinal SSE; não JWT/refresh/delta incremental. Esta implementação usa integração de compatibilidade protegida, com snapshot completo, recibos, comparação otimista e bloqueio de preço no servidor. O contrato alvo v0.2 é uma referência de evolução e não se confunde com endpoints já implementados.

## Cobertura funcional e limites

Implementado em fonte: login/troca de senha provisória, Hoje, metas individuais por pedidos, rota existente, carteira/histórico, visita/checklist/GPS opcional, carrinho multimarcas, snippets/copiar/WhatsApp por link, tarefas, fechamento, PDFs locais cifrados, sincronização automática/reconexão/background e conflitos. Navegação celular/tablet, tema claro/escuro, ícones e filtros compactos codificados.

Ainda exige homologação ou integração adicional: teste visual em aparelhos; background/GPS/compartilhamento nativos; SQLCipher no binário; regras completas de descontos/prazos por todas as marcas; metas coletivas agregadas; estoque/logística de fonte externa; registro de entrega WhatsApp oficial; painel web central de fechamentos; OCR de imagens do Kit v2.1 original; JWT/refresh e delta incremental. Snippets são funcionais por compartilhamento; não existe envio automático WhatsApp Business API nesta versão. Financeiro e faturamento não são inferidos a partir de pedido. A fonte do logotipo é o ativo existente no repositório L2 One, preservado sem redesenhar tipografia.

## Condições para liberação

Instalar o gateway em ambiente de homologação isolado, validar usuário/escopos, executar roteiro nativo em Android/iPhone/iPad e comprovar ida/volta no L2 One real. Conferir alterações de preço sob transação real PostgreSQL e direitos de carteira. Completar assinatura definitiva Android/iOS e limites/performance, testar migração e recuperabilidade e revisar itens não integrados antes de declarar todas as ferramentas liberadas. Resultado dos builds nativos e pacote instalável, quando disponíveis, consta em BUILD_STATUS.json.
