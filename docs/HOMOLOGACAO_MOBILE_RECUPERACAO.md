# Homologação mobile e recuperação em nuvem
## Verificação visual
Workflow mobile-visual.yml: Chromium com dados fictícios e todas as requisições externas bloqueadas. Telas Hoje, Clientes, Pedidos e Marcas em 360x800, 390x844, 768x1024 e 1024x768. Anexa 16 PNGs e métricas de fonte, altura dos controles e largura da página. Falha se houver overflow da página ou controles medidos abaixo de 48 px (tolerância 0,5 px). Não representa testes físicos, bateria ou Safari.

## Recuperação do PostgreSQL
Produção usa l2one (dpg-dap2hrbm8hqs73e3trtg-a), plano pago 0.1c-256mb. A aplicação web continua free. Não confundir recuperação do banco com suspensão da aplicação.

Antes do teste: abrir Backups no painel Render, confirmar janela disponível e último ponto recuperável. Não registrar credenciais no repositório. Documentar horário UTC, identificador do backup e operador.
Restaurar para NOVA instância de QA, sem sobrescrever produção. A criação dessa instância pode gerar cobrança; alinhar custo antes de executar.
Manter a instância de QA sem tráfego de representantes, automações ou mensagens habilitadas. Validar conexão e comparar totais de clientes, pedidos, itens e financeiro com uma referência consistente do mesmo instante. Conferir pedidos representativos e permissões de vendedor; uma restauração não deve liberar acessos adicionais.
Medir tempo de restauração (RTO observado) e diferença para o ponto recuperado (RPO observado). Só registrar aprovado com evidências. Definir metas RPO/RTO antes do aceite.

## Chaveamento
Sem chaveamento durante o teste. Em incidente real: preservar registros posteriores ao ponto restaurado, reconciliar filas offline e recibos idempotentes, aprovar a janela, guardar referência da conexão anterior no painel seguro, atualizar DATABASE_URL para a instância validada e reiniciar/deployar. Validar /health, login, escopo por vendedor e reenviar um pedido fictício duas vezes sem duplicidade.
Se houver erro de consistência, suspender escritas e avaliar rollback. Voltar simplesmente para a conexão anterior após novas escritas pode perder registros; reconciliar antes.
Backups e instâncias temporárias devem permanecer protegidos. Retenção e exportação durável precisam ser confirmadas no painel. Nenhum backup foi restaurado por esta PR.
