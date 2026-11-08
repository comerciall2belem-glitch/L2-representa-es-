# Reestruturação comercial L2 One — entrega inicial

## Implementado nesta alteração

- Funil visual com cinco grupos: qualificado/prospecção ativa, apresentação/CPQ, negociação/aprovação, pedido/acompanhamento, recompra/próximo ciclo.
- Etapas existentes permanecem nos registros para preservar histórico e relatórios. As cinco colunas agrupam essas etapas; não apagam dados.
- Oportunidades abertas exigem próxima ação, data a partir de hoje e contexto do contato. Perda exige motivo, conforme validação existente.
- Proposta enviada e negociação exigem vínculo com proposta do mesmo cliente cujo envio esteja registrado.
- Aceite exige evidência registrada. Pedido, faturamento e entrega exigem pedido real compatível com a etapa.
- Arrastar não altera mais a etapa imediatamente: a edição permite preencher os requisitos antes de salvar.
- Previsão ilustrativa com pesos fixos substituída por conversão observada por etapa. Amostra mínima de cinco resultados; sem amostra, sem valor ponderado. Negócios sem histórico explícito da etapa não entram na taxa. Pedidos compartilhados contam uma única vitória.
- Valor ponderado representa conversão sem horizonte temporal. Não é uma previsão mensal de faturamento.

## Migração e validação operacional

Nenhum cadastro é excluído ou reescrito nesta entrega. Oportunidades antigas sem ação aparecem com indicação de pendência; ao editá-las, será necessário preencher os novos requisitos. Filas antigas podem ser rejeitadas pelo servidor até correção do registro. Validar essa recuperação na homologação antes de publicar.

O envio de proposta já existente é uma declaração registrada pelo operador; não comprova entrega ao destinatário. O aceite também é declarado pelo operador. A integração com provedores de comunicação não foi criada nesta alteração.

## Requisitos do projeto ainda pendentes

1. Revisar e consolidar menus em todo o sistema sem perder operações necessárias.
2. Tornar o CPQ obrigatório no servidor para todas as rotas de criação de pedidos, incluindo política versionada por marca, frete, pedido mínimo, tier e exceções.
3. Implementar alçadas de aprovação com solicitante, aprovador, justificativa, validade, trilha e invalidação por mudança de condições. A coluna de aprovação não implementa esse fluxo.
4. Conversão atômica e idempotente de proposta aceita em pedido sem redigitação, preservando condições aprovadas.
5. Painel comparável por vendedor: conversão da carteira e penetração de marcas/SKUs elegíveis, com denominadores e atribuição histórica.
6. Revisar alertas de queda de compras por cliente/marca e ciclo de recompra; consolidar as regras atuais do MCR e da visão 360.
7. Recomendações explicáveis de cross-sell/upsell com elegibilidade comercial e evidência de histórico; sem inferir estoque disponível.
8. Previsão mensal baseada no tempo histórico até faturamento, com amostra e qualidade dos dados expostas.
9. OAuth Gmail/Outlook, sincronização incremental e associação de interações à carteira autorizada. Integração do ChatGPT com e-mail não conecta o CRM automaticamente.
10. Cobrança centralizada com permissão financeira e revisão das fontes de recebíveis.
11. Teste autenticado em PostgreSQL e dois dispositivos; revisão da carteira existente; publicação e verificação no Render.

## Validação técnica

Consultar a descrição do PR para resultados efetivamente executados. Testes com dados simulados não substituem aceite com dados reais.
