# Busca e massa de homologação do L2 One Next

A branch é destinada ao ambiente Next isolado. O sistema legado e a base de produção não devem ser alterados por esta etapa.

A busca autenticada consulta PostgreSQL por nome, nome fantasia, CNPJ, identificador ou número de pedido. Vendedores veem apenas carteiras próprias ou explicitamente autorizadas. Os índices GIN são instalados automaticamente exclusivamente quando L2_EXPECTED_DATABASE=l2_one_next_staging_db. Para outro ambiente, programe uma migração e aplique global_search_server.create_indexes numa janela apropriada; CREATE INDEX pode bloquear escritas e não deve ser executado sem avaliação de volume.

Massa sintética: next_staging_seed.py inclui três clientes, três atendimentos e dois pedidos fictícios. O seed só aceita o banco l2_one_next_staging_db com L2_EXPECTED_DATABASE correspondente. É idempotente e não sobrescreve registros. Para execução manual no ambiente correto: defina L2_ALLOW_TEST_SEED=yes e execute python next_staging_seed.py. L2_NEXT_SEED=yes também ativa o seed durante a inicialização do serviço isolado. Desative essa variável após a carga se os registros excluídos não devem ser recriados em reinicializações.

Contas iniciais Ana Paula, Euler, Laís e Marlene usam credenciais geradas por Render, separadas de produção e não armazenadas no repositório. Segredos são acessados pelo mecanismo seguro de gestão de credenciais do ambiente, com troca obrigatória conforme a política de autenticação. Não publicar senhas em documentação, código, logs ou issues. O seed não cria conta de vendedora nem altera perfis existentes.

Verificação: o workflow Busca indexada PostgreSQL cria banco descartável PostgreSQL 16 e testa o índice, nomes, CNPJ, pedidos, segregação e idempotência. Os testes automatizados não provam o upload em dispositivo físico, a sincronização entre sessões autenticadas ou as permissões reais de cada pessoa.
