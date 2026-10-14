# Guia de instalação e deploy

L2 Produtividade - Maleta Digital | candidato 0.3.0 | 14/10/2026

## Preparar o backend L2 One

1. Usar primeiro `l2-one-homologacao`, serviço `srv-daqk8ifavr4c738m78f0`, workspace escolhido por Ana Paula. Confirmar que seu banco está isolado da produção. A configuração observada usa a branch `feature/precos-uf-cliente`; o código mobile foi preparado contra a main atual, portanto conferir compatibilidade antes do deploy.
2. Copiar mobile_gateway.py para a raiz do backend e acrescentar o hook `install(sys.modules[__name__])` ao final de server.py. A proposta de código é aditiva; não remove operações do CRM/PWA. Os dois endpoints são /api/mobile/v1/snapshot e /api/mobile/v1/sync. O gateway cria mobile_receipts/mobile_closings e reutiliza as transações/validações existentes.
3. Executar os testes do ERP e da extensão. Publicar o commit em homologação. A conta e os escopos devem ser os mesmos de L2 One, sem compartilhamento de senha no código.
4. Validar login, carteira, preço, ordem, tarefa, visita e fechamento com fixtures identificadas. Reenviar o mesmo operation/changeId: deve manter um só pedido/número. Remover ou arquivar fixtures no ambiente de teste somente após verificar evidências.
5. Implantar em produção somente após piloto em aparelhos e aprovação dos resultados. Não alterar bancos ou disparar pedidos para as indústrias durante homologação. O fechamento mobile é uma tabela própria e aparece na consulta gerencial do app; a interface web L2 One precisa consumir essa tabela para mostrá-lo no painel central.

## Preparar o aplicativo

Instalar Node 24 e npm. Na pasta do código:

```bash
npm ci
npm run typecheck
npm test
python3 -m pip install -r backend/requirements-test.txt
python3 -m unittest discover -s backend -v
npx expo install --check
npx expo prebuild --platform all --no-install
```

Configurar EXPO_PUBLIC_L2_API_URL com URL HTTPS de homologação para o piloto; essa variável não é segredo. API URL também pode ser informada na tela de login. Não incluir token, senha, chave da indústria ou do WhatsApp em EXPO_PUBLIC_*. Para usar SQLCipher, instalar o binário nativo próprio; Expo Go é recusado pelo código quando cipher_version não existe.

## Android: APK para equipe e AAB para loja

Opção EAS Build: autenticar uma conta Expo autorizada, registrar projeto com `eas init` e configurar assinatura com `eas build:configure`. Usar o profile preview de eas.json para APK interno. O profile production gera AAB para Google Play.

```bash
npx eas-cli login
npx eas-cli init
npx eas-cli build:configure
npx eas-cli build --platform android --profile preview
npx eas-cli build --platform android --profile production
```

Os comandos acima dependem de conta, condições de serviço e eventuais limites de plano. Não foram executados na conta Expo de Ana Paula nesta entrega. APK é instalável; AAB exige distribuição pela Play e não é instalado diretamente.

Build local: JDK 17, Android SDK/NDK e ferramentas compatíveis com Expo SDK 54. Projetos nativos foram gerados. Configurar ANDROID_HOME/ANDROID_SDK_ROOT; em android, executar gradlew assembleRelease. O template gerado usa chave de desenvolvimento por padrão; para produção, configurar keystore da empresa e guardar credenciais fora do repositório. Se um APK de piloto estiver incluído, ele deve ser usado apenas para homologação, e não enviado à Google Play como versão definitiva. A chave/assinatura de produção precisa permanecer estável nas atualizações.

Distribuir o APK interno por canal autorizado. No Android, abrir o arquivo e autorizar instalação por aquela origem, quando o sistema solicitar. Confirmar nome, versão e hash do arquivo antes de distribuir. O build local de piloto considera arm64-v8a; outros aparelhos exigem build com arquitetura correspondente. Não desinstalar ou limpar dados com fila pendente.

## iOS e iPad

EAS: conta Expo autorizada, Apple Developer, Bundle ID `br.com.l2representacoes.produtividade`, certificados e provisioning profile. Production build gera IPA assinado; distribuir pelo TestFlight ou método interno autorizado da empresa. A instalação de IPA não equivale à instalação de APK: o aparelho precisa atender ao perfil de distribuição.

```bash
npx eas-cli build --platform ios --profile production
npx eas-cli submit --platform ios --profile production
```

Alternativa local: macOS, Xcode, CocoaPods e conta Apple. Executar `npx pod-install`, abrir workspace iOS, configurar equipe de assinatura, escolher aparelho/archive e exportar IPA conforme método permitido. Revisar declaração de criptografia da App Store para os recursos SQLCipher/HTTPS/SecureStore. Não há IPA assinado nesta entrega.

## Primeiro uso e rotina de campo

1. Com internet, entrar com usuário L2 One, trocar senha provisória quando solicitada e baixar carteira.
2. Conferir rota, preços e cliente. Em Maleta, baixar PDFs de cada tabela antes de sair.
3. Iniciar visita, preencher checklist/observações e registrar data de retorno. GPS é opcional; indisponibilidade não bloqueia atendimento.
4. Criar carrinho, indicar vendedor, revisar totais e salvar. Offline: status pendente. Online: gateway verifica preços e condições; preço alterado exige revisão no carrinho.
5. Em Pendências, conferir recibos/conflitos. O botão Sincronizar e a retomada/reconexão tentam envio; sistema operacional pode adiar background com app fechado.
6. Registrar fechamento, sincronizar e conferir o relatório no app gerencial. Abrir WhatsApp registra abertura, não prova de envio ou entrega.

## Piloto e aceite final

Executar em Android de campo, iPhone e iPad: 24h offline, reinício durante pedido, perda de resposta, concorrência entre dois aparelhos, mudança de preço, troca de usuário, revogação de carteira e falta de espaço. Validar SQLite cifrado e Keychain/Keystore, PDF compartilhado offline, legibilidade externa e tamanhos de toque. Conferir um único pedido no ERP por UUID e atribuição correta de Laís/vendedor. Só liberar após evidência real de ida e volta no L2 One e emissão/assinatura dos instaladores.
