# Carteira, vendedores, pedidos e confronto fiscal

## Divisão da carteira

`entities(kind='client')` guarda o responsável no campo JSON `owner`. O painel da administradora permite prévia e aplicação em lote por UF, cidade e bairro, preservando por padrão atribuições existentes. Clientes legados sem responsável aparecem apenas para perfis administrativos até serem atribuídos.

O servidor filtra clientes, pedidos, visitas, tarefas, rotas e histórico comercial por responsável quando a conta tem função `Vendedor`. Leituras de documentos, fotos, comprovantes, notas e preços por cliente verificam a mesma carteira. A transferência de um cliente existente exige Ana Paula. A sincronização de um vendedor rejeita alterações relativas a cliente de outra carteira.

## Dados do vendedor

`seller_profiles` guarda cadastro e dados bancários vinculados por chave estrangeira a `app_users.username`: nome completo, documento, e-mail, telefone, banco, tipo de conta, agência, conta e PIX. O cadastro de novo vendedor exige todos esses campos. Perfis legados podem ser completados pela administradora em **Vendedores e acessos > Dados bancários**; a conta não é retornada na sincronização geral. Somente o token da administradora acessa os endpoints do perfil. Considere criptografia de coluna com chave externa ao banco antes de armazenar dados bancários em ambientes que a exijam.

## Pedidos multimarcas

O pedido tem uma UF e pode conter itens de várias indústrias. Cada item registra `brand`, SKU, quantidade, preço e subtotal; o servidor consulta a tabela de preço daquela indústria e UF, recalcula todos os valores e marca o cabeçalho como `Multimarcas` quando necessário. O PDF identifica a indústria de cada linha. O faturamento pode ser registrado separadamente por indústria.

## Confronto de NF-e

Anexar um **XML da NF-e** ao pedido dispara automaticamente o confronto de código (`cProd`), quantidade (`qCom`) e valor do item (`vProd`) por indústria. O resultado é `Conferido` ou `Divergente`, com diferenças detalhadas. O destinatário é conferido com o CNPJ cadastrado do cliente quando o XML o informa. Cada nota e o relatório ficam persistidos em `order_invoices` com restrição de duplicação por pedido, indústria e número da nota. O PDF da nota pode ser mantido em Comprovantes, mas não é usado para calcular diferenças.

O campo `brand` é escolhido por quem envia o XML; enquanto o cadastro de indústria não tiver CNPJ fiscal validado, a correspondência do emitente com a indústria exige conferência humana. Descontos e frete podem gerar diferença entre valor de produtos e valor líquido da nota; o relatório não presume esses ajustes. Nenhum valor de faturamento é criado a partir de dados ausentes.

## Verificação

Executar `python -m unittest discover -s tests -p 'test_*.py' -q`, `node --check app.js` e `node tests/test_commercial_workflow.cjs`. Os testes de NF-e usam XML sintético, sem dados de clientes nem chamada externa. Homologar também com sessão da administradora e sessão de vendedor antes de dividir toda a carteira.
