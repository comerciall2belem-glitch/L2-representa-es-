# BI integrado ao L2 One

Acesso: Crescimento > BI estratégico L2. Implementação inicial de acompanhamento baseada em NFs registradas em `entities` com `kind=settlement`; não transforma pedidos em aberto em realizado. Não altera esquema nem lançamentos.

## Disponível

Filtros por período, vendedor responsável, marca, UF, cidade e segmento cadastrado; quatro visões; faturamento, metas mensais em escopo compatível, tickets, positivação, composição por documento, marcas, equipe, ABC de clientes, faixas desde a última NF e cidades. Funil como snapshot atual. Atualização manual e a cada 60 s sem recarregar a página, pausada durante edição de filtros.

`GET /api/bi/dashboard` exige sessão, aplica carteira no servidor e limita vendedores aos próprios pedidos. Comissão só é retornada com setor financeiro. A origem de vendedor é `sellerResponsible`, `commissionSeller`, depois `user`; autoria administrativa de Laís não recebe crédito.

## Limites explícitos

Somente NFs cadastradas no financeiro. Cobertura fiscal e devoluções não foram atestadas. Lucro líquido, YoY/MoM, churn, ativação, ABC SKU, mapa e giro continuam indisponíveis até conciliar fontes e histórico. Metas só para mês completo; filtros de cidade/UF/segmento não recebem rateio arbitrário. Meta geral prevalece sobre marcas e equipe sobre indivíduos no consolidado.

Comissão prevista e recebida são dos documentos selecionados. Saldo aberto/vencido é posição atual das NFs autorizadas e dimensões filtradas, independente da janela. `due` representa vencimento da comissão da L2, não inadimplência do cliente perante a indústria. Não calcula lucro a partir de custos incompletos.

## Homologação e avanço

Base do trabalho: `codex/l2-next-staging-isolado` (fdbd470), preservando os módulos já integrados. Branch de BI não altera produção. Rodar testes Python e DOM; deploy em homologação isolada; aceitar login por perfil, saldo real e documentos. Somente então planejar liberação na main. Reversão: restaurar commit anterior da homologação, sem rollback de dados, pois o BI é somente leitura.

## Próximas fontes necessárias

Conciliação por item/NF para DW; eventos fiscais de retorno/cancelamento; histórico de carteira; despesas, tributos e repasses por competência; estoque por proprietário; geografia validada; transições históricas de oportunidade. Speedio permanece enriquecimento cadastral, sem substituir vendas realizadas.
