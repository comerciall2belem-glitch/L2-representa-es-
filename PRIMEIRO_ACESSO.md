# Primeiro acesso em homologação

Defina L2_INITIAL_PASSWORD no painel do serviço de homologação com uma senha provisória comum de pelo menos 12 caracteres. Não coloque o valor no repositório. Ela substitui L2_PASSWORD_1..4 no cadastro inicial.

Novos usuários devem alterar a senha antes de acessar dados. O login devolve mustChangePassword, a interface mostra a troca obrigatória e a API bloqueia as demais operações. Após a troca, todas as sessões do usuário são revogadas; é necessário entrar com a senha pessoal. Reimplantações não redefinem senhas de usuários já cadastrados. Contas preexistentes são preservadas.

A migração adiciona must_change_password à tabela app_users. A senha é armazenada somente como hash com salt. A nova senha precisa ter pelo menos 12 caracteres e ser diferente da provisória.

Validação: seis testes locais das funções de autenticação/troca com banco simulado e verificação de sintaxe Python/JavaScript. Teste completo com PostgreSQL e navegador ainda depende do segredo configurado no Render.
