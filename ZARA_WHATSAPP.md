# Zara — atendimento WhatsApp da L2 Representações

## Ativação

1. Configure a conta WhatsApp Business Platform (Cloud API) no painel Meta e associe o número comercial. O acesso atual via WhatsApp pessoal/WhatsApp Web não ativa este webhook.
2. Defina **no Render, apenas como variáveis secretas** `WA_VERIFY_TOKEN` (valor aleatório), `WA_APP_SECRET` (segredo do aplicativo Meta), `WA_ACCESS_TOKEN` (token do sistema) e `WA_PHONE_NUMBER_ID` (ID do número). Não inclua essas credenciais no GitHub.
3. Configure no painel Meta a URL `https://l2-one.onrender.com/api/zara/webhook`, informe o mesmo token de verificação e assine o evento `messages`.
4. Opcional: configure `OPENAI_API_KEY` para respostas generativas dentro das regras da Zara. Sem essa chave, a Zara responde boas-vindas e casos conhecidos, encaminhando as demais dúvidas à equipe. `ZARA_MODEL` permite escolher o modelo. `WA_GRAPH_VERSION` fixa a versão da Graph API compatível com a conta.
5. Faça uma conversa de teste com um número autorizado: boas-vindas, preço sem valor cadastrado, pedido com problema, resposta humana e retomada do robô.

## Operação

No L2 ONE, entre como Ana Paula e abra **WhatsApp → Abrir painel de atendimento da Zara**. As conversas com marcador de atendimento humano aguardam a equipe. Selecione uma, assuma, responda e, ao terminar, escolha **Devolver à Zara**. As respostas humanas são enviadas pelo número conectado à Cloud API.

O agente não consulta pedidos nem libera preços, descontos ou prazos. Essas solicitações recebem aviso de verificação ou encaminhamento. Mensagens de mídia são encaminhadas para atendimento humano sem transcrição automática. O webhook valida assinatura HMAC e ignora mensagens duplicadas. O histórico fica no PostgreSQL do L2 ONE. Configure política de retenção e informe aos clientes sobre o tratamento das mensagens antes de operar em produção.

## Limite operacional

Esta versão atende **mensagens recebidas**. Campanhas iniciadas pela empresa exigem modelos de mensagem aprovados pela Meta e uma implementação própria. O painel aberto consulta atualizações a cada 5 segundos; a equipe deve acompanhá-lo para atender encaminhamentos. Se a Meta falhar no envio de uma resposta, a entrada continua registrada para análise.

## Revisão de segurança e diagnóstico

A rota canônica é `GET/POST /api/zara/webhook`; `/webhook` é um alias com as mesmas proteções.
O GET exige apenas `WA_VERIFY_TOKEN`: modo `subscribe`, token não vazio comparado em
 tempo constante (bytes UTF-8) e challenge não vazio, de até 1024 caracteres. Retorna
 o challenge como texto puro. Token inválido retorna 403, challenge inválido 400 e
 configuração ausente 503. Tokens e query strings não devem aparecer em logs do proxy.

O POST exige `WA_APP_SECRET` e `WA_PHONE_NUMBER_ID`, lê o corpo em streaming até
256.000 bytes e verifica HMAC-SHA256 nos **bytes originais**, antes de interpretar o
JSON. O header deve ser `X-Hub-Signature-256: sha256=<64 caracteres hexadecimais>`.
Assinatura ausente, adulterada ou malformada retorna 403; JSON/estrutura inválida
retorna 400; corpo excessivo retorna 413. Eventos de outro número e eventos de status
sem mensagens não geram respostas. O ID recebido deduplica entradas no PostgreSQL.

Envios usam `WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID` numérico e `WA_GRAPH_VERSION`
no formato `vN.N`. Há timeout de conexão de 4s e timeout HTTP de 12s por operação.
Falhas HTTP (incluindo 401, 429 e 5xx), rede, JSON ou configuração viram erros
sanitizados. Logs JSON têm componente, evento, severidade, ID de requisição, duração
e, quando presentes, códigos numéricos da Meta. Não incluem token, telefone, corpo de
mensagem ou texto bruto do erro da Meta. O logger é `uvicorn.error.zara`.

Uma falha de envio mantém a entrada já gravada e tenta marcar a conversa para
atendimento humano; o webhook responde 200 para não repetir envios ambíguos.
Falha ao gravar uma entrada responde 503, permitindo nova entrega pela Meta.
Transações do webhook executam em threadpool, fora do event loop da API.
Não há retry automático de envio: após timeout, a Meta pode ter aceitado a mensagem.
O ID retornado pela Graph significa **aceitação**, não comprovação de entrega ao
cliente. O estado de entrega exibido é calculado dos eventos assinados, independentemente
do campo legado `delivered` (que não serve como comprovação de leitura).

Limites: ainda não há fila durável/outbox ou circuit breaker compartilhado; lotes
com várias mensagens são processados sequencialmente e o ACK aguarda esse trabalho.
Uma interrupção do processo após gravar a entrada pode impedir a resposta automática;
reentregas são deduplicadas. Falhas depois de a Graph aceitar, mas antes de registrar
a saída são logadas, sem reenviar. Antes de operação em escala, implementar inbox/
outbox durável e worker, com política explícita de reconciliação de envios ambíguos.
O teste mock não substitui validação de PostgreSQL real, credenciais e entrega no Render.

## Simulação local sem envio real

Na raiz do repositório:

```bash
python -m pip install -r requirements-dev.txt
python scripts/simulate_zara.py
python -m unittest discover -s tests -p 'test_zara*.py' -v
```

O primeiro comando de simulação usa credenciais fictícias, banco em memória e
`httpx.MockTransport`: valida GET, assina um payload de entrada, percorre o webhook,
registra entrada/saída, gera resposta da Zara e confere deduplicação. Nenhuma
requisição externa à Meta ou à OpenAI é feita. O banco em memória é apenas um stub
para fluxo; não verifica SQL, transações nem concorrência do PostgreSQL.

Para testar o servidor **local** com PostgreSQL de teste e Graph previamente
substituída por mock, forneça `WA_APP_SECRET` e `WA_PHONE_NUMBER_ID` locais:

```bash
python scripts/simulate_zara.py --url http://127.0.0.1:8000/api/zara/webhook
```

Esse modo assina os bytes enviados e só aceita HTTP de loopback, sem seguir
redirecionamentos. Um servidor local com credenciais reais pode enviar uma resposta
real; use credenciais e destinatários de teste. O modo padrão sem `--url` é totalmente
isolado e recomendado para validação inicial.

## Antes de publicar no Render

Confira se as alterações do PR 5 foram incorporadas à versão implantada e resolva
os conflitos com `main`. Configure os quatro segredos `WA_*` no serviço correto;
eles são diferentes das variáveis `WHATSAPP_*` usadas pelo outro recurso de envio.
Fixe `WA_GRAPH_VERSION` na versão homologada da conta (o código mantém o default
existente). Valide GET, assinatura inválida, mensagem recebida real e atendimento
humano. Confirme também que uma falha da Meta não interrompe as rotas do CRM.
Não considerar testes locais como evidência de deploy ou entrega real.


## Entrega e homologação E2E por mensagem

`graph_accepted` permanece pendente de entrega e de homologação. O POST autenticado
agora grava `statuses[]` (`sent`, `delivered`, `read`, `failed`) em
`zara_delivery_events`, vinculados por ID da mensagem e destinatário. Callbacks que
chegam antes da gravação da saída são conservados. Repetições são idempotentes;
`read`/`delivered` não regridem com `sent` ou `failed` atrasados. Eventos de outro
`WA_PHONE_NUMBER_ID` são ignorados. Falha de persistência retorna 503 para reentrega.

`setup()` acrescenta `reply_to` e `panel_confirmed_at` às mensagens existentes e cria
a tabela de eventos. Não presume entrega de mensagens antigas marcadas pelo fluxo
legado: sem evento autenticado, permanecem `graph_accepted`. Valide essa migração em
PostgreSQL de homologação antes de produção.

Fluxo real de teste:
1. Use um número de teste autorizado e inicie uma conversa com a Zara (abre a janela
   para resposta de texto). Aguarde a mensagem da Zara chegar ao aparelho.
2. No WhatsApp do aparelho, use **Responder** especificamente sobre a mensagem da
   Zara. Isso fornece `messages[].context.id`; mensagens independentes não comprovam
   o retorno dessa saída e deixam a homologação pendente.
3. No painel, confira a mensagem enviada, o status Entregue/Lida e o retorno recebido.
   Clique **Confirmar recebimento e retorno no painel** na mensagem original. Só Ana
   Paula pode confirmar; o backend exige entrega/leitura e retorno vinculado do mesmo
   telefone. Consultar o histórico não confirma E2E automaticamente.

A API do histórico retorna `deliveryStatus`, `replyReceived`, `e2eReady`, `e2eStatus`
e `panelConfirmedAt`. `e2eStatus=confirmed` registra a conferência humana por mensagem;
não é um indicador global de disponibilidade da integração. A lista indica quando
há uma confirmação no histórico recente (últimas 100 mensagens). O painel faz polling
visível a cada 5 segundos, mantém o rascunho e atualiza o modo de atendimento.

Logs: `graph_accepted` (aceitação), `delivery_status_received` (status autenticado) e
`e2e_panel_confirmed` (conferência explícita). Somente o último, acompanhado do teste
no aparelho e do histórico, fecha a homologação. Não registrar telefones ou mensagens
nos logs operacionais.

Testes locais:

```bash
python -m unittest discover -s tests -p 'test_zara*.py' -v
node tests/test_zara_panel.cjs
python scripts/zara_e2e_mock.py
```

O mock E2E percorre Graph simulada → webhook assinado de entrega → resposta vinculada
→ API de listagem/histórico → confirmação. Ele usa SQLite com adaptador de SQL para
exercitar as consultas e não prova migração/concorrência em PostgreSQL, renderização
em navegador real nem recebimento em aparelho físico. O teste do painel usa DOM e
fetch simulados. A homologação real continua pendente até executar o roteiro acima
no Render e no WhatsApp de teste.
