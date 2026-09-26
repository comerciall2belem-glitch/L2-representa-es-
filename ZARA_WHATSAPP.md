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

Esta versão atende **mensagens recebidas**. Campanhas iniciadas pela empresa exigem modelos de mensagem aprovados pela Meta e uma implementação própria. O painel precisa estar aberto ou ser acompanhado pela equipe para observar encaminhamentos em tempo real. Se a Meta falhar no envio de uma resposta, a entrada continua registrada para análise.
