# Reestruturação comercial L2 One — PR 65

## Comportamento implementado

- Cinco etapas no seletor e no quadro. Rótulos históricos são normalizados quando editados; perda fica fora das cinco colunas.
- Cadastro base, contexto de contato, próxima ação e prazo obrigatório para novos cards. Avanços posteriores exigem proposta CPQ aprovada, PDF emitido, envio registrado, aceite e pedido convertido conforme a etapa.
- CPQ calcula preços oficiais por tabela PA/AP e canal, quantidade/embalagem, desconto em centavos, mínimo líquido por indústria, frete CIF/FOB e prazos configurados. Política ausente bloqueia a emissão, sem condições inventadas.
- Até 5%: automático. Acima de 5% até 10%: gestor comercial. Acima de 10%: diretoria. Ana Paula e Euler representam a gestão/diretoria da empresa; não há autoaprovação do solicitante.
- Snapshots imutáveis de cotação. Mudanças de preço, tabela ou política invalidam a aprovação antes da conversão. Proposta emitida após aprovação; envio e aceite são registrados pelo operador, sem simular entrega pelo provedor.
- Conversão em pedido passa pelo endpoint existente do ERP, revalida a aprovação e registra vínculo de origem na mesma transação. Identificador e chave de alteração determinísticos impedem conversão duplicada. O vínculo não pode ser removido para contornar a aprovação.
- Confirmação de novos pedidos pelo sync legado exige CPQ; rascunhos permanecem disponíveis. Desconto informado fora de uma proposta CPQ não é aceito.
- Radar calcula média entre datas distintas de compras, mostra amostra e cria tarefa prioritária ao ultrapassar 120% do ciclo. Sem histórico suficiente, não inventa ciclo histórico.
- Sugestão de mix usa vendas faturadas do segmento no período de 365 dias disponível, excluindo produtos comprados e tabelas incompatíveis. A sugestão não comprova disponibilidade em estoque.
- Forecast da carteira tem fallback para taxas globais agregadas; quando ambas as amostras são insuficientes, informa ausência de base. Não publica dados de outras carteiras para o vendedor.
- Leads qualificados recebidos pelo fluxo existente entram de forma idempotente na primeira coluna, com tarefa e prazo. A consulta Speedio continua dependente de credenciais próprias no CRM.

## Acesso

- Vendedor: carteira autorizada, suas propostas, suas oportunidades e tarefas. Snapshot comercial compartilhado não expõe outras carteiras ao vendedor.
- Gestão/diretoria: aprovações conforme a matriz e sem autoaprovação.
- Marlene e Érika continuam sem financeiro e sem comissões industriais. Laís e Euler mantêm as permissões financeiras existentes. Financeiro pessoal continua segregado e exclusivo dos membros existentes.

## Interface

Pedidos → montar carrinho → CPQ: informar canal e desconto, vincular oportunidade, gerar proposta.
Gestão comercial / Pedidos → CPQ: aprovar alçada quando necessário, emitir PDF, registrar envio, registrar aceite, converter em pedido.
O PDF precisa ser efetivamente enviado pelo operador; o registro de envio não dispara mensagem automaticamente.

## Pré-requisitos operacionais ainda necessários

- Cadastrar políticas reais de mínimo/frete/prazo nas marcas sem configuração. Não ativar esta mudança em produção antes dessa revisão, pois novos pedidos confirmados passam a exigir CPQ.
- OAuth Gmail/Outlook do CRM ainda não implementado/ativado; a conexão de e-mail do ChatGPT não conecta o ERP. Não considerar sincronização de mensagens entregue.
- Importação segura da carteira e histórico no staging depende de conexões de origem e destino. A base atual de produção tinha 388 clientes e 78 pedidos, com datas de setembro a novembro de 2026 na consulta de 08/11; não corresponde a 12 meses de histórico.
- O banco de staging bloqueia conexões externas; nenhum acesso de rede foi aberto. Painel do Render exige login para funções não disponíveis no conector.
- Projeção mensal de faturamento por tempo de conversão continua pendente. O forecast entregue estima conversão em pedido sem horizonte mensal.
- Validar pedidos CPQ já convertidos após troca/desativação de tabelas e transição de faturamento via NF-e antes de produção.
- Operações legadas e filas offline devem ser homologadas: cards antigos precisarão preencher novos campos ao serem editados.

## Testes

A suíte CPQ/PostgreSQL usa apenas uma base descartável explicitamente identificada, sem dados reais nem credenciais de produção. A integração CI executa também os 63 testes do financeiro pessoal antes ignorados por falta de PERSONAL_TEST_DSN.
Consulte os resultados do workflow do commit do PR; criação de workflow ou início de job não comprovam aprovação.
