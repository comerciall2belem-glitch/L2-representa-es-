# Auditoria de fluxos e interface — L2 One

## Escopo
Varredura automatizada das áreas permitidas para Ana Paula, Euler, Laís, Marlene e Erika, incluindo subáreas de Escritório, Financeiro, Metas e fichas/modais. Verifica renderização, identificadores únicos e existência das funções vinculadas às ações. Não equivale a executar cada ação destrutiva em produção.

## Correções
- Gestão deixou de receber o dashboard comercial inteiro novamente: um painel de metas, uma fonte visual por seção e sem IDs repetidos.
- Catálogo: busca abreviada atualiza cards e tabela detalhada juntos, sem substituir formulários em edição.
- Navegação da ficha da indústria limpa filtros de outra marca ao abrir produtos.
- Central e Equipe deixam de repetir o bloco geral de ações do dia a dia; os atalhos permanecem em Hoje, Escritório e Rotina.
- Cadastros de apoio da Equipe ficam recolhidos; editar tabela abre a seção correta.
- Central oferece compartilhamento real de mídia pelo aparelho, independente da API; histórico de testes fica recolhido e não conta como contato comercial.
- Ajuste anterior de SKUs agrupados e abertura de tabelas publicado como parte da conclusão do fluxo de catálogo.

## Validação
Testes DOM de cinco perfis; catálogo/seleção PA-AP; carrinho multimarcas; tabelas e permissões; administração de vendedores; edição de indústria; PDF/mídia; atualização de registros preservando formulário/foco. A varredura de telas e ações é mantida na integração contínua.

## Limites operacionais
Envios pela API oficial exigem credenciais e configuração do provedor. Compartilhar pelo aparelho abre o seletor ou baixa o arquivo; entrega externa deve ser conferida no aplicativo. Simulações são identificadas e guardadas separadamente. Mudanças locais seguem a fila de sincronização, sem apagar histórico, pedidos ou cadastros. Testes usam dados de teste e não disparam campanhas externas.
