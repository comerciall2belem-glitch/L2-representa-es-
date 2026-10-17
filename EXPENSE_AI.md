# L2 Expense AI

Implementação para revisão, ainda não homologada/publicada.

## Uso

- Caixa diário e Financeiro Pessoal: área L2 Expense AI, escolha Empresa ou Pessoal e envie PDF/JPG/PNG (5 MB, até 3 páginas).
- Empresa: exige acesso financeiro e caixa aberto na data extraída. Não abre caixa nem inventa saldo inicial.
- Pessoal: exclusivo de Ana Paula/Euler; grava no schema pessoal, sem entities, fila ou cache corporativo. Conta padrão Dinheiro pessoal. Despesa paga, compartilhada pelo casal.
- Na falta de data, usa a data do processamento em America/Belem e informa dateSource=envio.
- Fornecedor e total precisam ser identificados; múltiplos totais ou OCR de baixa confiança impedem o lançamento. Resumo exibido após o envio; edição nas telas já existentes.
- Categoria inicial por palavras-chave, com preferência pelo histórico do mesmo fornecedor no destino escolhido. Não cruza históricos entre empresa e pessoal. Vision API externa não está implementada nesta versão. OCR local usa RapidOCR/PyMuPDF já presentes nas dependências.
- Original preservado no PostgreSQL em nuvem, acessível por API autenticada; pessoal também usa attachments do módulo pessoal. Não usa bucket público.
- Hash global do original impede reenvio e lançamento do mesmo documento nos dois destinos. Arquivos reexportados/fotos diferentes do mesmo recibo ainda precisam de conferência.
- Empresa: Estornar cria entrada compensatória no caixa aberto de hoje e preserva original. Exclusão de despesas OCR bloqueada; estornos imutáveis. Pessoal: usa lixeira e auditoria já existentes.

## WhatsApp

Integrado ao webhook assinado existente da Zara. Só processa imagem/documento com DESPESA EMPRESA ou DESPESA PESSOAL na legenda. Destino ausente/ambíguo não cria despesa. Operador precisa ter telefone vinculado e ativo em field_operators e permissão para o destino. Representantes sem financeiro não recebem acesso por esta funcionalidade.

Downloads Meta têm URL permitida, limite de bytes e timeout; processamento OCR ocorre em thread. Resposta financeira é enviada ao operador autorizado; histórico compartilhado armazena só confirmação genérica, sem resumo pessoal. A entrega real depende das credenciais Meta, webhook e número/operador configurados; não foi validada nesta alteração. Credenciais e produção não foram alteradas.

## Validação

25 testes do módulo: seleção e acesso a destinos, valores brasileiros, data, total ambíguo, PDF selecionável real, limites de arquivos, não duplicação e roteamento exclusivo das gravações. As gravações SQL são testadas com conexão simulada. Mais 9 testes de regressão do financeiro pessoal passaram; 63 testes que exigem banco foram ignorados por falta de banco de testes. Sintaxe Python e JS conferida.

Antes de publicar: validar transações/rollback/RLS/concorrência em PostgreSQL de homologação; OCR de fotos reais JPG/PNG/PDF escaneado; categorias e revisão; upload e edição em celular/tablet; saldos após estorno e caixa fechado; permissões e não exposição pessoal; entrega/reenvio do webhook Meta. Nenhuma despesa real foi lançada pelos testes.

## Restrição: preservar tabelas e catálogo

Esta versão não cria nem altera tabelas, colunas ou índices. Não requer migração de OCR. Usa entities existente para o anexo empresarial (registro privado fora do snapshot de sincronização) e para marcador de não duplicação. Originais pessoais ficam exclusivamente em personal_finance.attachments existente, sob RLS. Nenhum preço, SKU ou indústria é editado pelo módulo. As estruturas financeiras existentes devem estar disponíveis; ausência gera erro sem tentar criá-las. A checagem interna não exige uma tabela expense_receipts. Não altera o comando de início.

Os testes de integração usam somente banco descartável, com schema de teste. Incluem comparação do catálogo PostgreSQL e sentinelas de preços/SKUs antes e depois dos lançamentos. Não executar fixtures de teste contra homologação ou produção.
