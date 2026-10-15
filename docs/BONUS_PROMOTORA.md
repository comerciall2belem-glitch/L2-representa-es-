# Bônus e perfil Promotora - configuração de homologação

## Bônus
Administração com permissão Financeiro: Administração > Bônus por meta do escritório.
Informe o mês de vigência e marque os vendedores elegíveis. A escolha vale desse mês em diante até nova alteração, com auditoria.
Nenhum vendedor é incluído automaticamente. Não altera os percentuais individuais nem pedidos já faturados.
Cálculo: meta geral explícita (responsável Equipe e sem indústria) comparada com vendas confirmadas/faturadas e progressão logística do mês. Se meta positiva atingida, cada beneficiário ativo recebe prévia de 1% do total faturado do escritório.
A base faturada segue pedidos com status/histórico Faturado, Em transporte, Entregue ou Pós-venda concluído, pela data do pedido, como a visão de faturamento por pedidos existente. Não é conciliação de notas por data de emissão.
Orçamentos, cancelados, datas futuras e outros meses não contam. Não presume meta pela soma de indústrias; sem meta geral o bônus fica bloqueado.
GET /api/office-policy/bonus?month=AAAA-MM recalcula com dados atuais da nuvem. Prévia não gera pagamento, lançamento financeiro nem obrigação de quitação; não duplica bônus a cada pedido/sincronização.
PUT /api/office-policy/bonus/eligibility: {"month":"2026-10","user":"Erika","eligible":true}. Exige administração e financeiro. Booleano estrito.

## Promotora
Administração > Novo acesso > Função Promotora.
Permissões dedicadas: promotion e routes; não usar Administrativo para contornar limites.
Após cadastrar: equipe > Carteira autorizada > selecionar PDVs. A autorização não troca o vendedor responsável.
A Promotora consulta somente a carteira autorizada e o catálogo e registra seus atendimentos/interações/tarefas/rotas. Pode trabalhar com a fila offline existente.
Pedidos, financeiro, comissões, bônus, alterações de cadastro de clientes, movimentos de estoque e disparos WhatsApp via API são bloqueados no backend. Sem transferência de carteira por iniciativa da própria promotora.
Snapshot não inclui pedidos, dados financeiros, indicadores globais da equipe nem custos privados. API não autorizada retorna 403, inclusive anexos de pedidos conhecidos.
No piloto desta função não há upload de fotos para Promotora; o campo é omitido. Atribuição de carteira e elegibilidade requerem conexão e administração.

## Integrações
Estoque interno persistente existe em inventory.py e /api/inventory. Não equivale a saldo de fornecedor externo.
Logística/faturamento possuem registros e conciliação internos; as rotas de parceiros externos ainda exigem documentação e credenciais.
WhatsApp possui cliente Cloud API em whatsapp_media.py. Logs do ambiente em 14/10/2026 23:14 UTC indicavam token, ID do número, segredo e versão Graph não configurados. Não houve envio real nesta alteração.
Não instalar chaves no app, nem marcar entrega após apenas abrir um link do WhatsApp.

## Gate de publicação
Testes Chromium/WebKit não são ensaio físico nem Safari instalado.
Mantêm-se pendentes o aceite em aparelhos/Safari e a restauração do backup real do Render em instância isolada.
O ensaio automatizado de recuperação usa dados fictícios, sem validar PITR ou retenção do banco real.
Esta implementação está em PR, sem merge/deploy de produção.
