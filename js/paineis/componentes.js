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
