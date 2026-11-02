/* Complementos dos gráficos de graficos.js (sem copiar nem reescrever as
   funções de lá): só ajustes aplicados depois do desenho. */

// Barra vazada (contorno tracejado, sem preenchimento) para valores parciais,
// como o ano em curso preliminar. Chamar depois de cada renderBarChart.
function marcarBarrasVazadas(containerId, indices){
  const barras = document.querySelectorAll(`#${containerId} svg path.bar-mark`);
  indices.forEach(i => {
    const b = barras[i];
    if(!b) return;
    const cor = b.getAttribute('fill');
    b.setAttribute('fill', cssVar('--surface-card'));
    b.setAttribute('stroke', cor);
    b.setAttribute('stroke-width', 1.5);
    b.setAttribute('stroke-dasharray', '3 2');
    b.classList.add('barra-vazada');
  });
}

// Formato de valor para gráficos com null: suprimido → "<5"; ausente → "—".
// O gráfico só passa o valor, então o índice dos nulos suprimidos é guardado aqui.
function formatoComNulos(meta, campo, linhasOriginais, formatar){
  const sup = new Set((meta.suprimidos || []).filter(s => s.campo === campo).map(s => s.linha));
  const nulosSuprimidos = linhasOriginais.map((_, i) => i).filter(i => linhasOriginais[i][campo] === null && sup.has(i));
  const nulosAusentes = linhasOriginais.map((_, i) => i).filter(i => linhasOriginais[i][campo] === null && !sup.has(i));
  const textoNulo = nulosSuprimidos.length && !nulosAusentes.length ? '<5' : (nulosSuprimidos.length ? '<5 ou —' : '—');
  // marcas de eixo fracionárias (ex.: 0,5 numa série que vai até 1) não viram inteiro repetido
  const padrao = v => Number.isInteger(v) ? fmtInt(v) : fmt1(v);
  return v => (v === null || v === undefined) ? textoNulo : (formatar ? formatar(v) : padrao(v));
}
