# Relatórios estratégicos

Implementação adaptada à branch de homologação, base 127a0cc. Acesso pelo menu **Relatórios**, com quatro grupos e 13 relatórios. Não cria tabelas nem altera dados existentes. Requer publicação do código e validação em homologação; não foi publicada nesta sessão.

## Operação

Selecionar relatório, início, fim e referência; consultar ou exportar. Inatividade parametrizada de 1 a 3650 dias. Período máximo de 732 dias. CSV UTF-8 com BOM, separado por ponto e vírgula, abre no Excel; não há exportador XLSX. PDF horizontal com cabeçalhos repetidos e quebra de textos. A tabela mostra até 500 linhas; as exportações incluem todas. Consulta manual sem recarregar a página ou persistir resultados no aparelho.

API autenticada: `GET /api/reports/strategic/catalog` e `GET /api/reports/strategic/{report}?start=2026-10-01&end=2026-10-21&format=json`. Formatos `json`, `csv`, `pdf`. A referência `as_of` não pode estar no futuro. Todas as respostas usam no-store.

## Fontes e limites de apuração

- Funil: oportunidades pela criação, etapas existentes preservadas. Conversão de encerrados = ganho / (ganho + perdido), por responsável e geral. Não reconstrói eventos históricos de etapa.
- Marca/categoria e região: pedidos confirmados, faturados ou entregues, pela data do pedido; itens por quantidade vezes preço. Bairro e categoria precisam estar cadastrados. Linhas ausentes não são distribuídas por estimativa.
- Faturamento: `settlement` com `invoiceNumber`, `billedDate` e `billed`; não considera status de pedido como prova de NF. Não consolida anexos de NF ainda sem conciliação no settlement. Meta Equipe sem marca, sem somar metas individuais ou de indústria; metas integrais em meses parciais.
- Comissão: percentual histórico do pedido aplicado ao faturamento conciliado. Bônus separado por mês, somente com mês integral encerrado, meta Equipe única e contrato de vendedor vigente desde o começo do mês, referência contratual e base Faturado. Base Recebido, mudança contratual durante o mês ou regras ausentes ficam Sem base; não gera pagamentos.
- Financeiro: `office_finance`; nenhuma venda de representada é transformada em receita própria. Aging é posição na referência e histórico de atraso com liquidação registrada. Caixa é variação acumulada dos títulos abertos, sem saldo bancário inicial. Não representa liquidez completa sem esse saldo e todos os títulos.
- Logística: fulfillment vinculado a pedido; última etapa registrada e previsão. Alertas usam etapa registrada de retenção/fiscal e previsão vencida. Integração com transportadoras/fiscalizações não é criada pelo módulo.
- ABC: pedidos faturados/entregues; ordenação decrescente, item que cruza 80% permanece A e o que cruza 95% permanece B. Não substitui conciliação contábil das notas.
- Atividades: visitas e interações persistidas. Não estima chamadas ou WhatsApp sem eventos. Prospecção exige vínculo lead.clientId ou client.sourceLeadId; origem não cadastrada fica Não informado. Conector Speedio e eventos WhatsApp dependem da integração existente; módulo não ativa credenciais.
- Inatividade: último pedido elegível ou last_purchase, prevalecendo a data mais recente até a referência. Sem histórico é separado de Inativo.

## Acesso

Sessão validada no servidor em toda consulta/exportação. Financeiro exige finance; comissões exige management. Comercial exige commercial/office/management. Sem management: somente clientes próprios ou autorizados, inclusive em exportações; não utiliza o painel compartilhado crmTeam. O catálogo oculta relatórios proibidos. A autorização da URL é independente da interface. Não retorna financeiro pessoal.

## Validação realizada

14 testes em `tests/test_strategic_reports.py`: catálogo, valores multimarca, exclusão de orçamento, faturamento documentado, comissão histórica e bônus, ABC, inatividade, caixa, aging, proteção CSV, bloqueios diretos de acesso e período. Verificados sintaxe Python/JavaScript e testes existentes `goals_separation.cjs` e `commercial_dashboard.cjs`. PDF sintético renderizado e inspecionado. Validação remota em homologação é executada quando L2_REPORTS_STAGING_CHECK=1. Interface em aparelho e integrações externas exigem verificações próprias. A suíte completa local tem 1 falha e 8 erros preexistentes de Zara/WhatsApp, reproduzidos na base sem alterações (124 testes); o módulo adiciona 14 testes aprovados (138 no total).

Antes de publicar: executar testes de integração em homologação com contas gestão/campo/financeiro, confirmar mapa de campos contra registros reais, validar faturamento conciliado e contratos, testar navegação e exportações em celular, conferir dados de origens e atividades integradas.

## Validação interna em homologação

`reports_staging_validation.validate` roda somente se `L2_REPORTS_STAGING_CHECK=1`, exige banco `l2one_homologacao`, usa transação REPEATABLE READ / READ ONLY, testa os 13 relatórios nos três formatos com perfis reais e registra somente contagens/tempos. Não cria fixtures, usuários, sessões ou títulos. Não chama provedores externos. Identidade interna substitui exclusivamente a autenticação dos handlers isolados; a rejeição de acesso anônimo usa a autenticação real. Não altera funções globais da aplicação. Verifica bloqueios financeiros e compara os resultados de campo com a carteira autorizada. Realiza smoke concorrente limitado a duas conexões e seis consultas, sem certificar capacidade máxima. Dados não disponíveis são contabilizados no resumo de mapeamento e não inventados. Logs: `strategic_reports_validated`.

O flag é variável direta do serviço existente no Render, mantida fora do Blueprint e da produção. Credenciais e variáveis de banco existentes permanecem preservadas. Não abrir allowlist externa do banco para realizar esta validação.
