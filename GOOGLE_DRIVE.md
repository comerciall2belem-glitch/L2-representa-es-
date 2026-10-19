# Google Drive · L2 One

O servidor grava uma cópia dos registros e rascunhos na pasta privada criada pelo aplicativo. Não usa downloads no aparelho. PostgreSQL continua sendo a origem operacional, sem alteração de pedidos ou tabelas de preços. Comprovantes binários permanecem no L2 One; a cópia contém seus metadados.

## Configuração do projeto Google

1. No projeto Google Cloud da L2, habilitar a Google Drive API.
2. Configurar o consentimento OAuth e um cliente do tipo Aplicativo da Web, com escopo `https://www.googleapis.com/auth/drive.file`.
3. Cadastrar exatamente o redirecionamento de produção: `https://l2-one.onrender.com/api/integrations/google-drive/callback`.
4. No serviço Render **l2-one**, configurar `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET` e `GOOGLE_DRIVE_REDIRECT_URI`. Inserir segredos diretamente no Render, nunca no repositório ou chat. Preservar as demais variáveis. Salvar e implantar.
5. Ana Paula acessa Configurações → Google Drive → Conectar Google Drive e escolhe a conta destinatária na tela do Google.

A configuração pública `/api/integrations/google-drive/configuration` informa apenas presença/ausência das chaves. O status privado, último envio e botões exigem sessão da gestora. Credenciais de atualização ficam criptografadas no banco; a chave deriva do segredo OAuth do servidor. Trocar o segredo requer reconectar o Drive.

## Operação e validação

- O servidor verifica alterações a cada minuto enquanto está executando. Serviços Render suspensos não executam o envio; o envio retoma após a inicialização.
- A pasta `L2 One · Registros` recebe `L2_One_Registros.json.gz`, atualizado sem acumular arquivos novos a cada lançamento. Reconectar cria um novo destino para evitar misturar contas Google.
- Configurações mostra o último envio confirmado. **Enviar agora ao Drive** força uma cópia e só confirma após sucesso da API. Falhas preservam os registros e a cópia anterior.
- **Desconectar Drive** interrompe os envios e remove as credenciais armazenadas pelo L2 One. Preserva arquivos; a permissão do Google também pode ser removida na própria conta Google.
- A cópia contém os registros visíveis à gestora, rascunhos dela e registros financeiros pessoais do casal, sem senhas, sessões ou tokens. Não é restauração automática de banco ou cópia integral de anexos.
- Limite de 40 MB para o JSON antes da compactação, com erro explícito caso ultrapassado.

Testes automatizados: `python -m unittest discover -s tests -p test_drive_backup.py`. A validação com conta Google real depende das credenciais e do consentimento acima. O conector Google Drive de uma conversa não fornece essas credenciais ao aplicativo.
