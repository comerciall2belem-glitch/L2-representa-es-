# BI estratégico em produção

Acesso: Acompanhamento de vendas (Crescimento) > BI estratégico L2. Port isolado da homologação, baseado na main 8e6dfe48. Nenhuma dependência do planejamento comercial, migração ou seed de homologação.

Duas bases explícitas e separadas: pedidos marcados Faturado (data billedAt ou data do pedido) e NFs registradas em entities/settlement (data fiscal cadastrada). Não se somam as bases. Na primeira não se inferem comissões nem comprovantes fiscais. Pedidos em aberto ficam excluídos. Vendas são atribuídas ao vendedor responsável do pedido. Regras de acesso são aplicadas no servidor, inclusive para filtros e campos financeiros. Atualização manual e automática a cada 60 s sem recarregar a página.

Fontes ainda pendentes: lucro líquido, YoY/MoM, churn, ativação, mapa, giro e ABC SKU exigem conciliação e dados adicionais; a interface informa indisponibilidade. Metas só aparecem para mês completo e escopo compatível. Comissão aberta/vencida é posição atual da L2, não dívida do cliente perante a indústria.

Rollback: reverter o PR de BI ou restaurar o deploy anterior 8e6dfe48. Não há alteração de dados ou esquema a desfazer.

Sincronização mantém a aba selecionada quando há registros pendentes bloqueados, preservando o BI aberto. O aviso de pendências e o acesso a Configurações continuam disponíveis. Três fixtures DOM aguardam a sincronização inicial antes de simular navegação.
