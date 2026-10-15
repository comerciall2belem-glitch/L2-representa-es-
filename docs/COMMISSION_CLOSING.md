# Fechamento mensal e preservação de comissões

- Novos vendedores recebem sugestão de 2,50% na UI e, se o campo for omitido na criação administrativa, no backend. Nenhuma migração de percentuais existentes.
- Pedidos faturados/concluídos (incluindo histórico de faturamento) rejeitam alteração e arquivamento com HTTP 409 na sincronização. Replay idêntico preserva inclusive ausência de campos legados; não consulta preços atuais nem recalcula comissão.
- O bônus de 1% fica em entidades separadas bonus_accrual, vinculadas a pedido e beneficiário. Nunca modifica amount, itens, responsável, percentual ou comissão original.
- Mantém a regra já homologada da prévia: cada vendedor ativo explicitamente elegível recebe 1% da base faturada geral do escritório, por data do pedido; não somente sua carteira. Não representa pagamento.
- POST /api/office-policy/bonus/close recebe apenas {"month":"2026-09"}; exige administração + financeiro, mês encerrado, meta geral explícita e atingida, e elegibilidade configurada. Flags de meta fornecidas pelo cliente são rejeitadas.
- O fechamento usa o mesmo advisory lock da sincronização, transação única, recibo mensal imutável e lançamentos determinísticos. Repetições retornam o mesmo recibo. Centavos são distribuídos pelo maior resto para coincidir com 1% arredondado da base consolidada.
- GET /api/office-policy/bonus/closing?month=2026-09 consulta o recibo auditado, separado da prévia atual.
- A auditoria registra close_office_bonus_v1; recibo e lançamentos preservam autor, horário UTC, meta, base e versão da regra.

## Rotina em nuvem

O workflow office-bonus-monthly.yml chama o fechamento do mês anterior às 06:00 UTC dos dias 1 e 2 (03:00 em Belém); o segundo dia permite nova tentativa sem duplicar lançamentos. Depende de merge na branch padrão e configuração segura; não está ativado por este código na branch da PR.

Configurar no backend L2_BONUS_JOB_TOKEN (segredo dedicado com pelo menos 32 caracteres) e L2_BONUS_JOB_ADMIN (administrador ativo com acesso financeiro). Configurar no GitHub Environment production o mesmo segredo L2_BONUS_JOB_TOKEN e a variável L2_BONUS_JOB_URL=https://l2-one.onrender.com/api/office-policy/bonus/close-due. Nunca colocar valores secretos no repositório ou chat. Ausência de configuração bloqueia a execução. A conta usada é revalidada no backend, e o endpoint não aceita mês, elegibilidade ou meta do chamador.

## Limites de homologação

Os testes PostgreSQL usam apenas l2_policy_qa descartável, sem dados reais. Chromium/WebKit automatizados não substituem ensaio em Safari/dispositivos físicos. A publicação continua condicionada à validação física/Safari e restauração de backup real; este código não autoriza nem executa merge/deploy.
