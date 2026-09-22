L2 ONE — PROTÓTIPO FUNCIONAL DE CAMPO (v1)
==========================================
INCLUSO: interface responsiva, 432 clientes importados, cadastro, visitas,
pedidos por valor total, tarefas, rotas manuais agrupáveis por bairro/cidade,
painel diário, oportunidades por última compra informada, backup JSON,
armazenamento local offline e API de sincronização com quatro contas.

INICIAR NO COMPUTADOR PARA TESTAR (sem nuvem):
1. Extraia o ZIP.
2. Abra o terminal na pasta extraída.
3. Execute: python -m http.server 8080
4. Abra http://localhost:8080 no navegador.
5. Dados permanecem APENAS no navegador/dispositivo até conectar servidor.

ATIVAR EQUIPE / NUVEM:
1. Instale: pip install -r requirements.txt
2. Configure variáveis de ambiente L2_PASSWORD_1 (Ana Paula), _2 (Euler),
   _3 (Laís) e _4 (Marlene), todas como variáveis secretas.
3. Execute: uvicorn server:app --host 0.0.0.0 --port 8000
4. Publique em hospedagem que suporte Python + armazenamento persistente e HTTPS.
   Não use banco SQLite em hospedagem com disco efêmero.
5. Em Nuvem, informe a URL HTTPS do servidor, selecione o usuário, informe a
   senha e clique Conectar. Faça isso em cada dispositivo.

LIMITAÇÕES IMPORTANTES:
- Este pacote NÃO está hospedado nem configurado em sua conta; link público
  e sincronização entre aparelhos dependem da publicação do servidor.
- Offline: alterações locais ficam na fila até o retorno da internet; a
  sincronização exige autenticação válida. Se a sessão expirar, reconecte.
- Dados offline no navegador não são criptografados; utilize dispositivo
  protegido, não compartilhado. Faça backups JSON periódicos.
- Edição concorrente do mesmo cadastro usa último envio; falta resolução
  avançada de conflitos, trilha de auditoria e backup automático do servidor.
- Rota é agrupamento por cidade/bairro, NÃO otimização por distância rodoviária.
- Não integra WhatsApp, ERP, mapas com trânsito, nem emite nota fiscal.
- A última compra pode estar ausente; nenhuma data foi inventada.
- Teste permissões, segurança, restauração de backup e operação offline antes
  de colocar dados sensíveis reais em produção.
