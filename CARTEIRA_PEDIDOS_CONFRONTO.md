# Carteira, vendedores, pedidos e confronto fiscal

## Divisão da carteira

`entities(kind='client')` guarda o responsável no campo JSON `owner`. O painel da administradora permite prévia e aplicação em lote por UF, cidade e bairro, preservando por padrão atribuições existentes. Clientes legados sem responsável aparecem apenas para perfis administrativos até serem atribuídos.

O servidor filtra clientes, pedidos, visitas, tarefas, rotas e histórico comercial por responsável quando a conta tem função `Vendedor`. Leituras de documentos, fotos, comprovantes, notas e preços por cliente verificam a mesma carteira. A transferência de um cliente existente exige Ana Paula. A sincronização de um vendedor rejeita alterações relativas a cliente de outra carteira.

## Dados do vendedor

`seller_profiles` guarda cadastro e dados bancários vinculados por chave estrangeira a `app_users.username`: nome completo, documento, e-mail, telefone, banco, tipo de conta, agência, conta e PIX. O cadastro de novo vendedor exige todos esses campos. Perfis legados podem ser completados pela administradora em **Vendedores e acessos > Dados bancários**; a conta não é retornada na sincronização geral. Somente o token da administradora acessa os endpoints do perfil. Considere criptografia de coluna com chave externa ao banco antes de armazenar dados bancários em ambientes que a exijam.

## Pedidos multimarcas

O pedido tem uma UF e pode conter itens de várias indústrias. Cada item registra `brand`, SKU, quantidade, preço e subtotal; o servidor consulta a tabela de preço daquela indústria e UF, recalcula todos os valores e marca o cabeçalho como `Multimarcas` quando necessário. O PDF identifica a indústria de cada linha. O faturamento pode ser registrado separadamente por indústria.

## Confronto de NF-e

O confronto aceita **PDF ou XML** em um único formulário; o texto colado é uma alternativa opcional. A extração salva a conferência sem faturar. O botão **Confirmar faturamento** grava a nota, recalcula o faturamento acumulado e atualiza a projeção de vendas e o CRM na mesma transação PostgreSQL. A operação compartilha o bloqueio transacional da sincronização offline.

O confronto compara SKU e quantidade por indústria e classifica atendimento completo, corte parcial e corte total. SKUs extras e quantidades cumulativas acima do pedido bloqueiam a confirmação para revisão; diferenças de preço permanecem visíveis. Não se presume equivalência entre códigos diferentes. CNPJ destinatário ausente ou divergente também bloqueia a gravação.

`order_invoices.reconciliation.confirmed` diferencia documentos pendentes de confirmados. Repetir a confirmação do mesmo registro retorna o resultado sem somar novamente. Chave da NF-e, ou emitente/série/número/indústria na ausência de chave, impedem confirmação duplicada em outro registro. O esquema legado mantém a unicidade por pedido/indústria/número; documentos de séries diferentes com o mesmo número neste pedido exigem tratamento posterior, sem sobrescrever nota confirmada.

O pedido preserva os itens e guarda seu total original em `originalAmount`. `amount` e `billedAmount` passam a refletir somente o total das notas confirmadas; `billedByBrand` alimenta as metas e os relatórios por indústria. O PDF comercial do pedido continua usando o total original. A sincronização comum não pode forjar os campos de faturamento nem reescrever pedidos com notas confirmadas. Os pedidos legados sem confirmação permanecem intactos.

O total fiscal `vNF` é usado quando presente no XML; no texto/PDF, aceita-se um total explicitamente rotulado como total da nota. Na ausência desse total, usa-se a soma dos itens extraídos (no XML, descontando `vDesc`). A base é exibida para revisão. Não se infere valor a partir de dado ausente. PDF com estrutura de itens não reconhecida é recusado, sem atualizar o faturamento; deve-se fornecer XML ou texto estruturado revisado. OCR exige conferência humana antes da confirmação. A correspondência do emitente com a indústria selecionada ainda depende de conferência humana.

**Baixar PDF de cortes** gera relatório consolidado com código, indústria, descrição, quantidade pedida, faturada, cortada e valor do corte. Quantidades somam todas as notas confirmadas; indústrias ainda não faturadas também aparecem como saldo pendente. Valor de corte usa o preço médio do SKU no pedido original, sem confundir corte com diferença de preço da nota. Notas complementares recalculam esse saldo. Nenhuma confirmação presume recebimento de dinheiro ou de comissão.

## Homologação pendente

Validar com pedido original e NF real, sessões de gestão/escritório/vendedor e banco de homologação. Conferir commit e rollback das transações PostgreSQL e duas confirmações concorrentes. Não publicar em produção antes desta validação. Os testes automatizados de persistência usam conexão simulada, sem documentos ou banco de clientes.

## Verificação

Executar `python -m unittest discover -s tests -p 'test_*.py' -q`, `node --check app.js` e `node tests/test_commercial_workflow.cjs`. Os testes de NF-e usam XML sintético, sem dados de clientes nem chamada externa. Homologar também com sessão da administradora e sessão de vendedor antes de dividir toda a carteira.
