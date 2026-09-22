# L2 ONE — pacote de implantação Render (revisão técnica)

## Situação
Este pacote **não foi publicado** e **não foi testado em um Render/PostgreSQL real**. Não há URL pública nem contas criadas. O `render.yaml` declara apenas recursos gratuitos; interrompa a implantação se o painel apresentar qualquer cobrança.

## Correções realizadas
- API migrada de SQLite para PostgreSQL via `DATABASE_URL`, com tabelas persistentes. Os 432 clientes são importados diretamente pela API autenticada após a publicação e não ficam no repositório.
- `clients.json`, código Python, configurações e variáveis de ambiente não são servidos como arquivos públicos.
- Fila de alterações com IDs idempotentes: evita duplicação após resposta de rede perdida; alterações feitas durante sincronização permanecem pendentes.
- Alterações pendentes impedem a troca acidental de usuário/servidor; cache de serviço não armazena respostas de API ou base de clientes.
- Histórico básico de alterações no servidor (`audit_log`), validações iniciais e verificação de saúde (`/health`).

## Implantação (requer sua autorização no Render)
1. Publique somente os arquivos de código deste pacote. O arquivo `clients.json` não deve ser enviado ao repositório público porque contém dados comerciais da carteira; a importação é feita diretamente pela API autenticada após a publicação.
2. No Render, selecione New > Blueprint e conecte o repositório. Revise custos de Web Service e PostgreSQL antes de aprovar. O Render pode pedir ajustes no plano disponível.
3. Configure `L2_PASSWORD_1` (Ana Paula), `_2` (Euler), `_3` (Laís), `_4` (Marlene) com **senhas diferentes de 12+ caracteres** em variáveis secretas no painel. Não registre senhas em arquivos nem compartilhe no chat.
4. Confirme `DATABASE_URL` apontando para o banco persistente. Aguarde `/health` responder `{"status":"ok"}` e verifique nos logs a inicialização sem erros.
5. Abra o endereço HTTPS atribuído pelo Render, entre em Nuvem com a URL do **mesmo domínio**, usuário e senha. Repita nos quatro aparelhos. Valide os 432 clientes e realize testes de visitas, pedidos, tarefas, rotas e sincronização offline/online em ambiente de teste antes de uso real.
6. Ative backups gerenciados do PostgreSQL e teste uma restauração. Defina responsáveis por gestão de senhas e acesso a dados.

## Limitações e bloqueios antes de produção definitiva
- O frontend usa `localStorage` para dados comerciais e token de sessão, **sem criptografia local**. Evite aparelhos compartilhados; para dados sensíveis, é necessária uma solução de armazenamento seguro e política de sessão.
- A sincronização ainda usa **última gravação** para edições concorrentes do mesmo registro; não há interface de resolução de conflitos.
- O servidor recebe e retorna a carteira completa para usuários autenticados; ainda não há segregação granular por vendedor. A autorização de edição ainda é básica.
- Há atraso defensivo para credenciais inválidas, mas ainda não há bloqueio distribuído de tentativas, redefinição de senha ou testes de carga. O acompanhamento dos logs e a rotação periódica das senhas continuam necessários.
- `goals` (metas) são persistidas e sincronizadas pela API, usando a mesma política de última gravação aplicada aos demais registros.
- Offline depende do cache do navegador e da permanência dos dados locais; modo privado, limpeza de dados ou desinstalação podem apagar registros não sincronizados.
- Rota é agrupamento manual por bairro/cidade, não otimização geográfica; sem integração com ERP/WhatsApp.
- O pacote contém dados de clientes: avaliar LGPD, controle de acesso, retenção e política de backup.

## Verificações locais executadas
Compilação Python (`py_compile`), sintaxe JavaScript (`node --check`) e conferência de contagem/unicidade dos IDs de clientes. **Não foram realizados testes de integração com PostgreSQL/Render nem testes multiusuário em dispositivos reais.**
