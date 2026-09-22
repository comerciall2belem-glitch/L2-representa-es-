# L2 ONE — relatório técnico da revisão

## Estado da versão

- Backend FastAPI e banco PostgreSQL gerenciado.
- Importação protegida validada: 432 clientes e 432 identificadores únicos, enviados diretamente ao banco após a publicação e nunca armazenados no repositório público.
- Quatro usuários previstos: Ana Paula, Euler, Laís e Marlene.
- Sessões aleatórias e revogáveis, com senhas armazenadas por hash PBKDF2.
- Dados de clientes, visitas, pedidos, tarefas, rotas e metas persistidos no PostgreSQL.
- Fila local de alterações para trabalho offline e sincronização automática ao reconectar.
- Proteções HTTP: CSP, bloqueio de iframe, `nosniff`, política de referência e cache desativado para API.
- Trilha de auditoria no banco para inclusões, alterações e exclusões de rota.

## Testes concluídos antes da implantação

- Validação sintática de Python e JavaScript.
- Validação da base de clientes, nomes obrigatórios e unicidade de IDs.
- Validação do hash de senhas e rejeição de senha incorreta.
- Validação das dependências e do manifesto do Render.

## Testes obrigatórios após a publicação

1. Login individual dos quatro usuários.
2. Confirmação de 432 clientes no primeiro sincronismo.
3. Registro de visita offline e envio automático após reconexão.
4. Registro de pedido e alteração de status em outro dispositivo.
5. Cadastro de meta mensal e conferência do percentual realizado.
6. Reinício do serviço e conferência da persistência no PostgreSQL.
7. Rejeição de senha inválida, sessão expirada e rota restrita ao administrativo.
8. Verificação do certificado HTTPS e dos cabeçalhos de segurança.

## Observação de custo

O `render.yaml` está configurado com serviço web e PostgreSQL gratuitos. Esses planos podem hibernar, expirar ou sofrer limitações definidas pelo Render e não oferecem a mesma estabilidade dos planos pagos.
