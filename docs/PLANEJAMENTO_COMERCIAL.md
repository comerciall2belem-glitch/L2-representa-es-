# L2 One — arquitetura de inteligência comercial

Especificação v1 • 02/11/2026 • fuso America/Belem.

## Estado da entrega e compatibilidade

Este documento especifica dados, cálculos, interfaces, integração e aceite. A entrega
agora inclui API e esquema aditivo em `commercial_planning.py`, UI integrada em
`commercial_planning.js`, exportação PDF/Excel, agendas de relatórios, recebíveis,
OFX, contrato de webhook assinado e MFA nas operações financeiras. O núcleo puro
continua em `commercial_indicators.py`. A flag de ativação é desabilitada por padrão.
Nenhuma migração ou publicação em produção foi executada. O e-book anexado foi
lido; sua definição de produtividade em 12 meses foi incorporada à API. O desenho
da página 9 contém números ilustrativos que não fecham aritmeticamente (unidades,
UPA e base), por isso os testes usam identidades calculadas, sem copiar a divergência.

A aplicação atual usa FastAPI e registros JSONB em `entities`, com inteligência
comercial em `strategic_crm.py` e interface em `strategic_crm.js`. A implantação deve
estender esses componentes e reutilizar autenticação, autorização de carteira e
notificações existentes. Não substituir pedidos, preços, SKUs ou fluxos atuais.

## 1. Cadeia e reconhecimento

Papéis: indústria GB; intermediário/franqueado L2; varejo (salão/perfumaria);
consumidor final. Papéis pertencem à relação comercial, não a uma classificação
única e irreversível da empresa. O perfil jurídico da L2 deve refletir o contrato
real de cada indústria: representação sem estoque não constitui automaticamente
revenda ou franquia. GB é um identificador configurável da indústria.

| Etapa | Origem → destino | Documento/base |
|---|---|---|
| Sell In | Indústria → L2 ou varejo direto | NF faturada da indústria |
| Sell Through | L2 → salão/perfumaria | NF de revenda confirmada |
| Sell Out | Varejo → consumidor | PDV/ERP do parceiro, com cobertura informada |

Venda direta da indústria ao varejo não gera Sell Through fictício da L2.
Pedido aberto, proposta, orçamento e NF cancelada não compõem realizado.
Venda é reconhecida pela data comercial do faturamento confirmado; devolução é
um evento negativo próprio, vinculado à nota original. Estorno e cancelamento
preservam trilha e recomputam projeções; não se apagam fatos faturados.

Valores: NUMERIC(18,2); quantidades NUMERIC(18,4); moeda BRL. Receita líquida
comercial = mercadoria bruta − descontos − devoluções; frete e tributos destacados
ficam em campos separados. Não confundir com receita líquida contábil. Dashboard
informa a base e permite conciliar com bruto e total fiscal.

GMV não é Sell In + Sell Through + Sell Out: essa soma repete a cadeia. Configurar
por marca a etapa de reconhecimento e os canais mutuamente exclusivos. Padrão
proposto: GMV ao consumidor = vendas brutas de Sell Out válidas, descontos
abatidos e frete/tributos destacados excluídos; devoluções separadas para obter
GMV líquido. Só consolidar eventos únicos da etapa escolhida. Mostrar cobertura
(percentual de PDVs que reportam, período e última carga); sem dados, exibir
“Não informado”. GMV B2B pode usar Sell Through em painel separado e identificado,
sem adicionar Sell In de abastecimento. Nunca estimar Sell Out a partir de Sell In.

## 2. Modelo de dados proposto

Todas as tabelas incluem organization_id, id UUID, created_at, updated_at e
version para concorrência otimista. FK inclui organization_id para impedir
referências cruzadas. Auditoria guarda autor, origem e revisão, sem tokens.

| Entidade | Campos essenciais e regras |
|---|---|
| commercial_party | tipo pessoa, CPF/CNPJ normalizado e validado, nome, município, UF; UNIQUE organização/documento |
| commercial_unit | party_id, endereço, geolocalização, rede_id; lojas com CNPJs distintos permanecem distintas |
| commercial_relationship | marca, fornecedor, comprador, papel de cada parte, início/fim, contrato; permite múltiplas rotas |
| account_profile | party_id, canal, perfil, gestão, foco, início da operação, motivadores, desafios, expectativas; campos desconhecidos aceitam nulo |
| portfolio_assignment | cliente, vendedor, vigência início/fim; mantém histórico da transferência de carteira |
| commercial_document | fonte, external_id, etapa, tipo venda/devolução, vendedor e comprador, marca, data, status, moeda, valores, original_id |
| commercial_document_item | documento, linha, SKU da marca, unidades, preço, desconto, valor líquido, devolução referenciada |
| portfolio_classification | marca/SKU, canal/perfil opcionais, classe, vigência, regra/versão, evidência e responsável |
| commercial_goal | período, marca, segmento, vendedor, métrica, alvo quantitativo ou objetivo qualitativo, evidência e responsável |
| campaign_baseline | campanha, janela, filtros, métrica, valor, versão da fórmula, data de captura, autor; snapshot imutável |
| consultative_interaction | cliente, usuário, data, dores, observação factual, perguntas, paráfrase, validação, objeções, compromisso, prazo e status |
| source_coverage | fonte/marca/etapa, cobertura desde, última carga, histórico completo, contagem e rejeições |
| metric_snapshot | organização, data de corte, dimensões, métricas, cobertura, versão da regra e watermark |
| audit_event / integration_outbox | evento, agregado, versão, autor, payload mínimo e processamento |

Restrições: UNIQUE(organization_id, source, stage, external_id) nos documentos e
UNIQUE(document_id, line_number) nos itens. Etapa por documento; importação mista
é desmembrada antes de gravar. Um documento multimarcas tem marca em cada item;
valores são somados por itens, nunca repetidos pelo join de cabeçalho. Uma
transação da marca é elegível se contiver ao menos um item daquela marca.

Índices: (organization_id, stage, status, business_date),
(organization_id, buyer_id, stage, business_date),
(organization_id, brand_id, sku_id), carteira por usuário/vigência,
interações por cliente/data e snapshot por dimensões/data. As dimensões de marca
podem exigir projeção indexada de itens/documentos. Paginação e filtros no servidor.
Nunca carregar toda a base para pesquisar ou calcular no navegador.

## 3. Contrato temporal e árvore de indicadores

Janela de relatório [início, fim), com fim exclusivo. Para mês fechado, início é o
primeiro dia do mês e fim o primeiro dia seguinte. Em mês corrente, corte vai até
a data corrente incluída, explicitado como parcial. Base móvel 12m =
[fim − 12 meses-calendário, fim). Não usar 365 dias. Denominadores usam exatamente
os mesmos filtros de marca, canal, território e carteira autorizada do numerador.

Positivação decorre de venda válida com unidades positivas; devolução sozinha
não positiva nem reinicia cliente. Histórico inclui compras anteriores à janela.
Cliente com compra e posterior devolução permanece com histórico de compra;
valor e unidades líquidos podem cair a zero ou ficar negativos, com sinalização.

| Indicador | Fórmula/critério |
|---|---|
| Portas | CPFs/CNPJs distintos cadastrados dentro do escopo, incluindo nunca compradores |
| Base ativa 12m | Documentos fiscais de clientes distintos com ≥1 compra válida na janela móvel |
| Sell Through período | Soma líquida dos itens elegíveis da etapa no período |
| Produtividade | Sell Through período ÷ base ativa 12m no corte |
| Positivados período | Clientes distintos com ≥1 compra válida no período |
| Atividade % | 100 × positivados que pertencem à base ativa no corte ÷ base ativa 12m |
| Inícios 12m | Clientes cuja primeira compra histórica ocorreu dentro da janela móvel |
| Reinícios mês/período | Clientes com compra no período cuja compra anterior foi anterior a data da nova compra −12 meses |
| Reinícios 12m | Mesmo critério de evento, contando clientes distintos na janela móvel; não somar contagens mensais |
| Cessados 12m | Clientes que já compraram antes da janela, sem compras nela; nunca compradores separados |
| Novos cessados mês | Clientes que cruzaram o limite de inatividade no mês, separados do estoque de cessados |
| Unidades | Soma de unidades físicas normalizadas, com devoluções negativas |
| UPA | Unidades líquidas período ÷ base ativa 12m |
| Ticket médio | Sell Through líquido período ÷ pedidos faturados distintos do período |
| Preço médio unitário | Sell Through líquido período ÷ unidades líquidas positivas do período |
| Baseline | Snapshot de métrica, janela e escopo anteriores à intervenção |

Pedido faturado parcialmente conta uma vez no ticket; várias NFs do mesmo
pedido não multiplicam o denominador. Para fontes sem pedido, usar transação
única e rotular “ticket por transação”. O núcleo inicial calcula por documento
normalizado; a integração precisa consolidar por pedido antes de usá-lo para ticket.
Para reinícios 12m, chamar a projeção com início = fim −12 meses; o campo
`restarts_period` devolve a janela solicitada. Dashboard mensal solicita também a
projeção mensal. Consulta de histórico nunca limitada ao período do relatório.

Divisão por zero retorna nulo, exibido “—”, com explicação. Para preço unitário,
unidades líquidas ≤0 retornam nulo. Crescimento = (atual/baseline −1) ×100 apenas
com baseline positivo; baseline zero mostra diferença absoluta.

Na janela móvel que inclua todo o período de relatório, identidade verificável:
Sell Through = base ativa × produtividade; produtividade = UPA × preço médio
quando unidades >0. Arredondar só na apresentação.

Inícios e reinícios exigem histórico completo; importação inicial não é primeira
compra comprovada. Sem cobertura, valores ficam “Histórico insuficiente”. Manter
marco operacional de 28/09/2026 como filtro da operação, separado do histórico
necessário para indicadores de 12 meses.

Exemplo: R$100.000, 200 clientes ativos, 80 positivados, 5.000 unidades e 100
pedidos: produtividade R$500; atividade 40%; UPA 25; ticket R$1.000; preço unitário
R$20. O período e o corte devem acompanhar esses números.

## 4. Regras de segmentação

Canal: Salão ou Perfumaria; preservar outros canais já existentes. Perfil:
Premium, Especializado ou Bairro. Gestão: Familiar, Rede ou Outra. Rede e perfil
são dimensões independentes; não classificar automaticamente pelo bairro.
Foco: Coloração, Tratamento ou Ambos. Tempo de operação calculado da data inicial,
sem inventar data quando desconhecida. Registrar fonte e data da qualificação.

Classificação de portfólio é por SKU/marca com contexto canal/perfil e vigência,
independente da curva ABC do cliente. Regras propostas, editáveis pela gestão:

| Classe | Regra de negócio | Ação e meta |
|---|---|---|
| Estratégico | Prioridade formal da marca e adequação confirmada ao segmento | Distribuição, recorrência, receita e treinamento |
| Potencial | Adequação confirmada e oportunidade de expansão do mix | Clientes que incorporam SKU e recompra |
| Combate | Concorrência por preço documentada, margem mínima preservada | Conversão e volume dentro do piso aprovado |
| Tático | Campanha/oportunidade com prazo e objetivo definidos | Giro, receita incremental e encerramento na validade |

Prioridade em conflito: exceção manual aprovada e vigente; depois regra específica
de marca/canal/perfil; depois padrão da marca. Conflitos de mesma prioridade
bloqueiam classificação e pedem revisão. Ausência de evidência = “Não classificado”.
Ao vencer campanha tática, recalcular sem apagar histórico. Nenhuma regra altera
preço automaticamente. Combate exige margem/custo disponível ao usuário autorizado;
sem esses dados, depende de aprovação da gestão, sem expor custos ao vendedor.

Metas quantitativas: receita, unidades, portas novas, reativações, positivação e
mix. Qualitativas: treinamento, exposição, diagnóstico confirmado e plano conjunto,
com responsável, prazo, evidência e aceite. Objetivos qualitativos não geram
receita fictícia. Meta zero ou ausente não permite percentual de atingimento.

## 5. Atendimento e rotas

Fluxo: selecionar cliente autorizado → revisar Visão 360 → escuta e diagnóstico →
confirmar entendimento → propor mix por segmento → registrar compromisso e prazo
→ pedido → faturamento → indicadores → próxima ação. Rotas geográficas mantêm
agenda e proximidade; a cadeia indireta representa relações, não deslocamentos.

Roteiro consultivo na ficha: “O que mais dificulta a venda hoje?”, “Qual resultado
você espera?”, “Entendi que sua prioridade é… Está correto?”, “Que alternativa
faz sentido?”, “Qual próximo passo combinamos?”. Guardar resposta e validação.
Observações não verbais são opcionais, factuais e atribuídas ao observador,
sem inferir emoção, personalidade ou condição pessoal. Ex.: “pediu para retomar
após atendimento”, sem diagnóstico subjetivo. Anotações protegidas pela carteira,
com retenção configurada e trilha de acesso/alteração.

## 6. Especificações de interface e API

Incorporar os módulos nas áreas existentes para preservar navegação:

| Local | Conteúdo e comportamento |
|---|---|
| Gestão → Inteligência comercial | Árvore com Base ativa e Produtividade; apoio de atividade, UPA, preço, ticket, inícios, reinícios e cessados |
| Clientes → Visão 360 | Perfil, canal, rede/unidades, mix, dores, expectativas, histórico e próxima ação |
| Registrar atendimento | Roteiro curto expansível, validação do cliente, compromisso e prazo |
| Marcas e preços | Pastas por marca; classes de portfólio com vigência e edição autorizada |
| Gestão → Metas e campanhas | Alvos por segmento e snapshot de baseline |
| Operações → Integrações | Importação, conciliação, cobertura, pendências e reprocessamento |

Filtros compartilhados: período, corte, marca, canal, perfil, UF/cidade, vendedor e
carteira. Cards abrem lista paginada dos documentos/clientes que sustentam o total.
Distinguir zero de indisponível; mostrar última atualização, cobertura e fórmula.
Layout móvel em coluna, campos rotulados, estados de carregamento/erro/vazio,
reenvio idempotente e feedback de salvamento. Dados atualizam após lançamento
confirmado sem recarregar a página inteira nem perder filtros/posição do cursor.

Contratos propostos sob `/api/strategy/commercial`:
- GET `/indicators`: filtros, corte; retorna valores, numeradores/denominadores,
  cobertura, versão, watermark e horário.
- GET `/indicators/{metric}/details`: mesma regra e escopo, cursor e limite ≤100.
- PATCH `/accounts/{id}/profile`: campos validados e expected_version.
- POST `/interactions`: cliente autorizado, texto estruturado e idempotency_key.
- POST `/imports/preview` e `/imports/{id}/confirm`: prévia de mapeamento,
  rejeições e confirmação idempotente; só funções autorizadas.
- POST `/baselines`: captura pelo servidor, sem valor arbitrário do navegador.
- GET `/reports` e POST `/report-schedules`: geração e agenda persistida no servidor.

Autorização aplicada antes das agregações e na exportação, usando carteira e
permissões vigentes. Vendedor vê carteira atual; visão histórica por responsável
à época é opção exclusiva da gestão. Marlene e Érika não recebem custos, margem,
caixa ou dados financeiros restritos. Indicadores comerciais não concedem acesso
aos módulos financeiros. Totais gerais não podem revelar carteiras fora do escopo.

## 7. Integração, consistência e relatórios automáticos

Criar adaptadores para pedidos/NF atuais, ERP indústria e arquivos PDV do varejo.
Importar para staging; validar CPF/CNPJ, SKU, unidade, marca, moeda, etapa, data,
status e vínculo de devolução; pendências não entram no realizado. Unidades de
caixa convertem conforme cadastro vigente. Associar pedido às NFs; devoluções
parciais não podem exceder o saldo sem ocorrência explícita de conciliação.

Na mesma transação de confirmação: persistir fato, auditoria e outbox. Worker
idempotente atualiza projeções dos clientes/marcas e snapshots afetados; evento
notifica interface com watermark. Reexecução não duplica; falha permite retry e
reconciliação diária entre documentos e somatórios. Data retroativa invalida
snapshots afetados. Histórico mantém datas e atribuições originais.

Relatórios previstos: diário operacional às 08h, semanal sábado às 09h e mensal
no primeiro dia às 08h com mês anterior, todos America/Belem. Agendas propostas,
não criadas nesta entrega. PDF compartilhável e exportação tabular com filtros,
fonte, corte, versão, cobertura, fórmulas e responsável. Geração não envia mensagens
externas automaticamente; agendamento por usuário respeita sua autorização no
momento de gerar e de baixar.

## 8. Implantação e aceite

1. Branch isolada, feature flag desabilitada, migrações aditivas e backup verificado.
2. Banco de homologação isolado, nomes e conexão validados; nenhuma cópia de dados
   reais sem tratamento/autorização adequada. Seed equivalente pode cobrir fluxos.
3. Adaptadores e reconciliação com pedidos reais/equivalentes autorizados.
4. Testar permissão no servidor, listas e PDF, inclusive tentativa de outro usuário.
5. Testar duas sessões autenticadas: faturamento, devolução, atualização e reabertura.
6. Ativar flag em homologação após aceite; publicação de produção é etapa separada.

Critérios: exemplo numérico acima confere; cliente com duas compras conta uma vez;
compras em duas marcas contam em cada marca e uma vez no consolidado; nunca
comprador não é cessado; compra no limite inferior é ativa; fim exclusivo fica
fora; histórico incompleto não fabrica início; devolução não positiva; pedido
fracionado não duplica ticket; importação repetida não duplica valores; ausência
de Sell Out fica indisponível; baseline congelado não muda com filtros posteriores;
zero denominador não quebra tela; acesso fora da carteira é negado antes da soma;
faturamento aparece em duas sessões com watermark atualizado; PDF bate com painel.

Rollback desabilita flag e worker, preserva fatos e auditoria, e volta à versão
anterior da aplicação; não remove tabelas nem documentos de produção. Validar
restauração em homologação. Desempenho: medir com volume representativo e registrar
p95, plano de consulta e índices antes de definir o SLA final.

A implementação e o estado de validação atuais estão descritos em
`VALIDACAO_PLANEJAMENTO_COMERCIAL.md`. A validação em PostgreSQL e a homologação
autenticada no Render continuam obrigatórias antes da ativação em produção.
