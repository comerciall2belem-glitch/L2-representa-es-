# Roteiro de homologação em campo

Execute somente em homologação com usuários e clientes de teste. Cada resultado exige evidência: aparelho/OS, versão, horário, operationId, número ERP e responsável. Não marque este roteiro como executado sem testar em aparelho.

| Cenário | Execução | Resultado esperado |
|---|---|---|
| Primeiro acesso | Login online, abrir carteira e catálogo | Somente carteira autorizada; dados e versão dos preços disponíveis |
| Pedido offline | Modo avião; montar pedido multimarcas; fechar e reabrir app | Carrinho/fila persistem; pedido indicado como pendente, sem número ERP inventado |
| Reconexão | Recuperar rede com pedido pendente | Um recibo por operação; pedido aparece no ERP e no app |
| Resposta perdida | Interromper retorno após aceite no ERP | Reenvio recupera recibo; nenhum pedido duplicado |
| Preço alterado | Atualizar tabela no ERP antes de reconectar | Rejeição explicada; revisão com preço atual exige novo envio |
| Visita concorrente | Editar mesma visita no ERP e no app offline | Ambas as versões preservadas; escolha explícita do representante |
| Acesso revogado | Retirar cliente da carteira antes do reenvio | Operação negada; nenhum vazamento de dados de outra carteira |
| Sessão expirada | Revogar token e reconectar | Nova autenticação solicitada; fila preservada |
| Fechamento | Registrar objeções, resumo e solicitação offline | Relatório fica pendente e sincroniza uma vez; consultar registro no servidor |
| WhatsApp | Preencher snippet e abrir conversa | Texto e telefone corretos; atividade indica abertura, sem afirmar entrega |
| Catálogo PDF | Baixar online, abrir offline e compartilhar | Conteúdo cifrado em repouso; temporário compartilhado é removido |
| Segundo usuário | Sair e entrar com outro representante | Nenhuma leitura de banco/carrinho da sessão anterior |
| Background | Bloquear tela, reconectar e voltar ao app | Fila progride conforme restrições do sistema; retorno ao foreground sincroniza |
| Celular/tablet | Testar teclado, orientação, fontes ampliadas, claro/escuro | Sem corte de ações; cliente, histórico e pedido acessíveis rapidamente |
| QR/utilitários | Gerar QR público offline e limpar texto com valores/SKUs | Link legível; valores e códigos preservados |

Critério de aceite: todos os cenários críticos aprovados no Android e iOS, SQLCipher confirmado no binário, fluxo real de ida/volta no L2 One com PostgreSQL e regras por marca verificado, assinatura definitiva e plano de suporte aprovados. Estoque, logística e WhatsApp Business API só podem ser liberados após integração e teste das fontes oficiais correspondentes.
