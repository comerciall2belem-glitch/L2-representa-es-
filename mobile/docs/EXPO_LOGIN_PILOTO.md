# Vinculação e APK piloto

Owner l2representacoes e slug l2-one estão configurados. A verificação EAS CLI 24.13.0 retornou Not logged in neste ambiente; não houve criação/vinculação remota nem solicitação de build pela conta Expo.

No diretório do projeto, autenticar com a conta autorizada usando `npx eas-cli@24.13.0 login`, conferir com `npx eas-cli@24.13.0 whoami`, vincular com `npx eas-cli@24.13.0 project:init` e gerar com `npx eas-cli@24.13.0 build --profile preview-pilot --platform android`.

Como app.config.js é dinâmico, se o EAS solicitar inserção manual do projectId, usar o UUID retornado em EAS_PROJECT_ID ou extra.eas.projectId no app.json. Não usar um UUID inventado nem um texto de exemplo. O aplicativo mantém SQLCipher: a opção correta do plugin nesta implementação é useSQLCipher: true. Os demais plugins nativos existentes também permanecem necessários.

preview-pilot herda preview: distribuição interna, credenciais remotas e APK ARM64. A URL desse perfil é https://l2-one-homologacao.onrender.com. A assinatura remota Android depende do acesso autorizado às credenciais da conta. O APK piloto anterior foi compilado pelo GitHub com certificado de desenvolvimento e não comprova um build EAS assinado.

Para automatizar o build neste ambiente, configurar EXPO_TOKEN pelo canal seguro e informar o Project ID real. Não enviar senha ou token no chat. O Apple Team ID não é necessário para este build Android; continua pendente para o IPA.
