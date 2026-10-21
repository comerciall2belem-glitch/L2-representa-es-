/* Reports are computed server-side. No report payload is persisted on the device. */
window.L2Reports=(()=>{
 const labels={commercial:'1. Comerciais e vendas',financial:'2. Financeiros e fiscais',operations:'3. Logísticos e operações',productivity:'4. Prospecção e produtividade'};
 let controller;
 function shell(){return `<div class="page-heading"><div><h2>Relatórios estratégicos</h2><p>Consulte os dados sincronizados e exporte para PDF ou Excel (CSV).</p></div></div><form id="strategicReportForm" class="box"><div class="row"><label>Relatório<select name="report" required><option value="">Carregando permissões…</option></select></label><label>De<input name="start" type="date" value="${today().slice(0,7)}-01" required></label><label>Até<input name="end" type="date" value="${today()}" required></label><label>Referência<input name="as_of" type="date" value="${today()}" max="${today()}" required></label><label>Inatividade (dias)<input name="inactive_days" type="number" min="1" max="3650" value="60" required></label></div><button type="submit">Consultar / Atualizar</button> <button type="button" data-export="csv">Excel / CSV</button> <button type="button" data-export="pdf">PDF</button><p class="muted">Valores em reais. Campos sem informação aparecem como “Sem base”. Atualização manual, sem recarregar a página.</p></form><section id="strategicReportResult" class="box" aria-live="polite">Selecione um relatório e consulte.</section>`}
 async function bind(){
  const form=$('strategicReportForm'),panel=$('strategicReportResult'),token=s.token;
  if(!form)return;
  controller?.abort();controller=new AbortController();const signal=controller.signal;
  form.onsubmit=e=>{e.preventDefault();run('json')};
  form.querySelectorAll('[data-export]').forEach(b=>b.onclick=()=>run(b.dataset.export));
  try{
   const r=await fetch(s.server+'/api/reports/strategic/catalog',{headers:{Authorization:'Bearer '+token},cache:'no-store',signal});const catalog=await r.json();if(!r.ok)throw Error(catalog.detail||'Não foi possível carregar os relatórios');
   if(!form.isConnected||s.token!==token)return;
   form.elements.report.innerHTML=Object.entries(labels).map(([key,title])=>`<optgroup label="${esc(title)}">${(catalog[key]||[]).map(x=>`<option value="${esc(x.id)}">${esc(x.title)}</option>`).join('')}</optgroup>`).join('');
  }catch(e){if(e.name!=='AbortError')panel.textContent=e.message}
  async function run(format){
   if(!form.reportValidity())return;
   const params=new URLSearchParams(new FormData(form));const report=params.get('report');params.delete('report');params.set('format',format);
   form.querySelectorAll('button').forEach(b=>b.disabled=true);panel.textContent='Gerando relatório…';
   try{
    const r=await fetch(s.server+'/api/reports/strategic/'+encodeURIComponent(report)+'?'+params,{headers:{Authorization:'Bearer '+token},cache:'no-store',signal});
    if(!r.ok){const err=await r.json();throw Error(typeof err.detail==='string'?err.detail:'Confira o período e os filtros')}
    if(!form.isConnected||s.token!==token)return;
    if(format!=='json'){downloadBlob(await r.blob(),`L2_ONE_${report}.${format}`);panel.textContent='Arquivo exportado.';return}
    const out=await r.json();if(!form.isConnected||s.token!==token)return;
    const cell=(v,i)=>v===null?'Sem base':typeof v==='number'?(out.columns[i].includes('R$')?money(v):v.toLocaleString('pt-BR',{maximumFractionDigits:2})):String(v);
    panel.innerHTML=`<p>${esc(out.scope)} · ${out.count} registros</p>${out.notes.map(n=>`<p class="muted">${esc(n)}</p>`).join('')}<div class="scroll"><table><thead><tr>${out.columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${out.rows.slice(0,500).map(row=>`<tr>${row.map((v,i)=>`<td>${esc(cell(v,i))}</td>`).join('')}</tr>`).join('')||`<tr><td colspan="${out.columns.length}">Sem registros no período e na carteira autorizada.</td></tr>`}</tbody></table></div>${out.count>500?'<p>Visualização limitada a 500 linhas. A exportação contém todos os registros.</p>':''}`;
   }catch(e){if(e.name!=='AbortError'&&panel.isConnected)panel.textContent=e.message}finally{form.querySelectorAll('button').forEach(b=>b.disabled=false)}
  }
 }
 return {shell,bind};
})();
