# Tabelas por cliente e WhatsApp

Na tela Clientes, escolha o cliente em Tabelas para o cliente. Marque até dez tabelas comerciais e ajuste a mensagem. Baixar PDF funciona sem credenciais Meta; o envio exige internet e WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID e WHATSAPP_GRAPH_VERSION válidos no servidor. Nenhum token fica no navegador.

Cada tabela ativa é consultada no PostgreSQL no momento da geração. PA permite PA e tabelas ALL; AP permite AP, PA e ALL. Tabelas de canais diferentes são identificadas pelo título e selecionadas explicitamente. O PDF contém SKU, descrição, EAN e preço; comissões nunca são incluídas. Não recria a logomarca oficial. Este recurso gera tabelas de preços, não catálogos fotográficos.

O servidor valida a carteira do vendedor, consulta o telefone do cadastro e envia documentos anexados à Cloud API. Mensagem livre exige a janela de atendimento da Meta; envio fora dela por template aprovado ainda não foi implementado. A aceitação pela API não comprova entrega: confirmação de entrega/leitura por webhook é uma próxima etapa.

Cada documento registra cliente, indústria, tabela, responsável, data/hora UTC, hash do PDF, status e ID da mensagem em entities/material_send e entities/interaction. A linha do tempo recebe o resultado imediatamente no navegador que enviou e via sincronização nos demais. O identificador por ação impede repetição do mesmo documento por duplo clique ou repetição de requisição. Falhas incertas não são reenviadas automaticamente. Confira a conversa antes de iniciar nova ação. O registro de tentativa é persistido antes da chamada externa; se o processo terminar no meio, permanece em processamento para conferência.

Validação: testes Python, validação de sintaxe JS e fluxos existentes de tabelas comerciais e WhatsApp. Homologação pendente: PostgreSQL real, API Meta real e interface em desktop/iPhone/iPad. Nenhuma mensagem real foi disparada durante os testes.
