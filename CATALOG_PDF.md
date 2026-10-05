# Tabelas comerciais em PDF

Em Marcas e preços, selecione a tabela comercial e use Baixar PDF ou Compartilhar no WhatsApp. O PDF usa todos os produtos persistidos da tabela selecionada, sem o limite de 100 linhas da tela e sem misturar tabelas PA/AP ou canais comerciais.

Texto vetorial selecionável, Helvetica, fundo branco, azul petróleo e dourado, cabeçalho repetido, preço unitário alinhado à direita e condições comerciais estruturadas. Notas internas e percentuais de comissão não são exportados. Dados de embalagem ausentes aparecem como Não informada.

A imagem original logo-light.jpg.b64 é reutilizada sem redesenho. Conferir com a identidade aprovada antes de publicar. Para cada indústria, adicionar o arquivo original em industry-logos/ e configurar logoFile no cadastro persistido (PNG/JPG, nome simples). Ausência de arquivo não produz marca inventada. Não há logotipos originais de indústrias nesta alteração.

A rota GET /api/catalog/table.pdf?table_id=... exige autenticação e setor comercial ou catálogo, tabela e indústria ativas. PDF usa compressão e imagem reutilizada entre páginas. Teste de 160 produtos: aproximadamente 81 KB.

Compartilhamento usa a folha nativa do celular; em navegadores sem suporte baixa o arquivo para anexar ao WhatsApp. Esta alteração não envia documentos pela Cloud API; o conector de mídia atual suporta imagens e vídeo. O PDF produzido possui MIME application/pdf e pode ser consumido por uma integração de documentos futura.
