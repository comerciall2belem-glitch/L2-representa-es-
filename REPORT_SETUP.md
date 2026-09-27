# Relatório diário L2 ONE

O painel Financeiro permite baixar `/api/reports/daily.pdf` com sessão autenticada e permissão `finance`.

## Envio automático

O relatório usa os registros sincronizados do PostgreSQL. O serviço `report-cron.yaml` executa às 01:00 UTC (22:00 de Belém) e envia por SMTP com TLS a `comerciall2belem@gmail.com`. O processo evita reenvio no mesmo dia pela tabela `report_delivery`.

No painel Render, configure `L2_SMTP_HOST`, `L2_SMTP_PORT`, `L2_SMTP_USER`, `L2_SMTP_PASSWORD` e opcionalmente `L2_SMTP_FROM` como variáveis secretas no cron job; o `DATABASE_URL` deve usar a conexão interna do banco. Para Gmail use `smtp.gmail.com`, porta `465` e uma senha de app da conta remetente, configurada diretamente no Render. Não coloque a senha no repositório nem em mensagens. O remetente precisa permitir SMTP.

Após configurar, crie o serviço de cron com a definição em `report-cron.yaml` e execute uma vez pelo Render para validar entrega, dados e anexo. Não confunda vendas de pedidos com receita própria; a DRE considera somente registros financeiros classificados pela competência. O caixa diário é controle separado.
