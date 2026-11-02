/* Componentes comuns dos painéis ClimaPampa: dica com fonte, aviso, selo,
   valor (suprimido ou ausente) e caixa de erro de bloco.
   Tudo fica em window.Paineis; nada aqui calcula supressão: o JSON já vem
   suprimido pelo gerador (scripts/paineis/comum.py). */
(function(){
  const P = window.Paineis = window.Paineis || {};
  const comFonte = new Set();

  // Texto de dica de gráfico: "rótulo · fonte". Sem fonte, acusa erro no console.
  P.dica = function(rotulo, fonte){
    if(fonte === undefined || fonte === null || !String(fonte).trim()){
      console.error(`dica sem fonte: "${rotulo || ''}"`);
      return rotulo ? String(rotulo) : '';
    }
    const texto = rotulo ? `${rotulo} · ${fonte}` : String(fonte);
    comFonte.add(texto);
    return texto;
  };
  P.dicaTemFonte = nome => comFonte.has(nome);

  // Valor para exibição: suprimido → "<5"; ausente → "—"; nunca 0 no lugar de vazio.
  P.valor = function(v, formatar, suprimido){
    if(suprimido) return '<5';
    if(v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v))) return '—';
    return formatar ? formatar(v) : String(v);
  };
  P.estaSuprimido = (meta, linha, campo) =>
    (meta.suprimidos || []).some(s => s.linha === linha && s.campo === campo);

  const SELOS = {
    conferido:      {simbolo: '●', texto: 'conferido'},
    pendente:       {simbolo: '○', texto: 'pendente de conferência'},
    em_confirmacao: {simbolo: '◐', texto: 'em confirmação'},
  };
  P.selo = function(selo){
    const s = SELOS[selo];
    const span = document.createElement('span');
    if(!s){ console.error(`selo desconhecido: ${selo}`); return span; }
    span.className = `selo selo-${selo}`;
    span.textContent = `${s.simbolo} ${s.texto}`;
    return span;
  };

  const TIPOS_AVISO = ['lacuna', 'parcial', 'preliminar', 'em_confirmacao', 'metodo', 'evento_em_curso'];
  // Textos dos avisos do produto, na ordem do JSON; filtra por tipo se pedido.
  P.textosAvisos = (meta, tipos) =>
    (meta.avisos || []).filter(a => !tipos || tipos.includes(a.tipo)).map(a => a.texto);
  // Acrescenta os avisos como parágrafos .aviso no contêiner.
  P.aviso = function(container, avisos){
    (avisos || []).forEach(a => {
      if(!TIPOS_AVISO.includes(a.tipo)) console.error(`tipo de aviso desconhecido: ${a.tipo}`);
      const p = document.createElement('p');
      p.className = `aviso aviso-${a.tipo}`;
      p.textContent = a.texto;
      container.appendChild(p);
    });
  };

  // Texto de nota vindo do JSON: escapa o HTML e só aceita **negrito**, `código`
  // e [link](pagina.html) para páginas do próprio site.
  P.notaHtml = function(texto){
    const esc = String(texto).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    return esc.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
              .replace(/`(.+?)`/g, '<code>$1</code>')
              .replace(/\[(.+?)\]\(((?:\.\.\/)?[a-z0-9_\-\/]+\.html)\)/g, '<a href="$2">$1</a>');
  };
  // Preenche todo elemento [data-nota="chave"] com a nota de mesmo nome dos produtos carregados.
  P.preencherNotas = function(raiz){
    const notas = Object.assign({}, ...(P.produtosCarregados || []).map(m => m.notas || {}));
    (raiz || document).querySelectorAll('[data-nota]').forEach(el => {
      const k = el.dataset.nota;
      if(!(k in notas)){ console.error(`nota ausente nos produtos: ${k}`); return; }
      el.innerHTML = P.notaHtml(notas[k]);
    });
    return notas;
  };
  // Indicadores prontos no JSON: [{rotulo, prefixo, valor, unidade, sub}] (valor já formatado pelo gerador).
  P.montarKpis = function(container, kpis){
    const host = typeof container === 'string' ? document.getElementById(container) : container;
    if(!host) return;
    host.innerHTML = '';
    (kpis || []).forEach(k => {
      const tile = document.createElement('div'); tile.className = 'kpi-tile';
      tile.innerHTML = `<div class="kpi-label">${P.notaHtml(k.rotulo)}</div><div class="kpi-value">${P.notaHtml((k.prefixo || '') + k.valor)}<span class="kpi-unit">${P.notaHtml(k.unidade || '')}</span></div><div class="kpi-sub">${P.notaHtml(k.sub || '')}</div>`;
      host.appendChild(tile);
    });
  };

  // Chips dos filtros ativos (só os nomes passados), com botão para remover.
  P.montarChips = function(hostId, rotulos, textoValor){
    const host = document.getElementById(hostId);
    if(!host || !P.filtros) return;
    host.innerHTML = '';
    Object.entries(P.filtros.estado).filter(([k]) => rotulos[k]).forEach(([k, v]) => {
      const chip = document.createElement('span');
      chip.className = 'chip-filtro';
      chip.textContent = `${rotulos[k]}: ${textoValor ? textoValor(k, v) : v} `;
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = '✕'; b.setAttribute('aria-label', `remover filtro ${rotulos[k]}`);
      b.addEventListener('click', () => P.definirFiltro(k, undefined));
      chip.appendChild(b);
      host.appendChild(chip);
    });
  };

  // Bloco longo recolhido só no celular (ponto de quebra da base, 720 px); aberto acima dele.
  // abrirSe(): true quando um filtro ativo afeta o bloco (ex.: ?periodo=), que então abre sozinho.
  // aoAbrir(): redesenho dos gráficos de dentro, que no bloco fechado não têm largura.
  P.recolherNoCelular = function(id, abrirSe, aoAbrir){
    const det = document.getElementById(id);
    if(!det) return () => {};
    const celular = window.matchMedia('(max-width: 720px)');
    const ajustar = () => { det.open = !celular.matches || !!(abrirSe && abrirSe()); };
    ajustar();
    celular.addEventListener('change', ajustar);
    if(aoAbrir) det.addEventListener('toggle', () => { if(det.open) aoAbrir(); });
    return ajustar;
  };

  // Filtro de período sem nenhum ponto no gráfico: mensagem no lugar do gráfico. Devolve true se vazio.
  P.semDadosNoPeriodo = function(containerId, n){
    if(n > 0) return false;
    const el = document.getElementById(containerId);
    if(el) el.innerHTML = '<div class="empty-state">Sem dados no período escolhido.</div>';
    return true;
  };

  // Mostra o erro dentro do bloco, sem derrubar o resto da página.
  P.erroBloco = function(el, mensagem){
    if(!el) return;
    el.innerHTML = '';
    const div = document.createElement('div');
    div.className = 'erro-bloco';
    div.textContent = `Não foi possível carregar este bloco: ${mensagem}`;
    el.appendChild(div);
  };
})();
