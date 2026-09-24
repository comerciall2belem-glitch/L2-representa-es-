# Homologação — preços por UF do cliente

Configuração preparada em `render.homologacao.yaml`. A criação desse arquivo não provisiona o ambiente: a ativação e os testes no Render ainda estão pendentes.

## Ativação

1. Acessar https://dashboard.render.com e criar um NOVO Blueprint, sem editar ou substituir o Blueprint de produção.
2. Selecionar o repositório `comerciall2belem-glitch/L2-representa-es-`, a branch `feature/precos-uf-cliente` e o caminho `render.homologacao.yaml`.
3. Conferir os novos recursos `l2-one-homologacao` e `l2-one-homologacao-db`. Se já existirem, verificar sua finalidade antes de aplicar qualquer alteração. Os recursos de produção não devem aparecer na proposta de alteração.
4. O arquivo solicita planos gratuitos. Se não estiverem disponíveis na conta ou o painel indicar cobrança, obter aprovação do responsável antes de contratar um plano pago.
5. Informar no painel senhas exclusivas de testes, diferentes entre si, de pelo menos 12 caracteres: `L2_PASSWORD_1` (Ana Paula), `L2_PASSWORD_2` (Euler), `L2_PASSWORD_3` (Laís), `L2_PASSWORD_4` (Marlene). Não registrar senhas no GitHub.
6. Conferir que `DATABASE_URL` referencia o NOVO banco `l2-one-homologacao-db`. Não copiar variáveis, grupos de segredos ou `L2_INITIAL_CLIENTS_B64` da produção. O banco inicia vazio e a aplicação cria as tabelas na inicialização.
7. Aplicar o Blueprint e aguardar a publicação. Registrar a URL real do serviço na issue #1.

## Validação

- Confirmar no painel que a branch implantada é `feature/precos-uf-cliente` e que serviço e banco são recursos distintos dos de produção.
- Abrir `/health` na URL de homologação e conferir `status: ok`, quatro usuários e zero clientes antes de inserir dados de teste.
- Usar um perfil de navegador exclusivo para testes, acessar a URL de homologação e configurar a seção Nuvem para o mesmo domínio de homologação. Não reutilizar dados locais ou filas de sincronização de produção.
- Entrar com as credenciais de homologação, cadastrar clientes fictícios PA/AP, importar preços de teste por UF e validar pedidos para os dois estados.
- Executar o autoteste autenticado `/api/self-test`, se utilizado pelo operador, e conferir `status: ok` e `transactionRolledBack: true`.
- Confirmar a persistência dos dados de teste após recarregar e verificar o destino da conexão no painel, sem divulgar sua senha. Não declarar isolamento validado apenas porque `/health` respondeu.

## Atualização

O serviço aponta explicitamente para `feature/precos-uf-cliente`. Para atualizar, utilizar Manual Deploy > Deploy latest commit no serviço de homologação e conferir o commit implantado. Alterações na configuração devem ser sincronizadas somente no Blueprint de homologação.

## Referência

https://render.com/docs/blueprint-spec

A issue #1 deve permanecer aberta até a URL, o banco separado e os testes serem confirmados.
