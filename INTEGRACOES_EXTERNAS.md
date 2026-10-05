# Integrações externas · L2 ONE

## Speedio
A tela **Visão 360º > Leads** permite consultar um CNPJ diretamente na API documentada da Speedio, conferir o resultado e adicionar o lead. A busca só aceita empresas PA/AP, preserva a carteira existente e não grava automaticamente os dados obtidos.

Endpoint consultado: `GET https://api-get-leads.speedio.com.br/search_enriched_leads/cnpj?cnpjs=["<cnpj>"]`. Autenticação Basic, conforme https://docs.speedio.com.br/endpoints/search-enriched-leads-cnpj e https://docs.speedio.com.br/introducao.

Para ativar na instância Render, configurar as variáveis secretas `SPEEDIO_USERNAME` e `SPEEDIO_PASSWORD` fornecidas pela Speedio. Sem as duas variáveis, a consulta informa que está indisponível; a importação CSV continua funcionando. Não incluir os valores em Git ou no navegador. Validar uma consulta real com CNPJ autorizado em PA/AP depois da configuração.

## L2 One Chat
O aplicativo atual abre conversas no WhatsApp e permite registrar um resumo no histórico do cliente. Captura automática permanece desativada até receber o contrato técnico do L2 One Chat e uma credencial própria de integração. Antes da ativação, confirmar: endereço e método de entrega (webhook ou API de consulta), formato de eventos de mensagem, identificador imutável da mensagem, telefone e vínculo com cliente, autenticação/assinatura, política de reenvio e limites, anexos, consentimento, e como distinguir mensagens recebidas e enviadas.

A implementação deve autenticar cada evento, rejeitar mensagens duplicadas, evitar associação ambígua de telefone, não criar cliente automaticamente, registrar erros sem dados sensíveis e oferecer replay seguro para eventos perdidos. Não configurar um endpoint aberto ou presumir o formato do fornecedor.

## Homologação
A suíte local cobre 24 testes Python e o teste comercial JavaScript. Fluxos autenticados de tarefa, oportunidade, previsão e sincronização foram exercitados na interface com registros temporários removidos. Testes de integração real com Speedio e L2 One Chat exigem as credenciais e o contrato acima.
