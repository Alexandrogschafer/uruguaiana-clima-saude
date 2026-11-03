/* Gráficos SVG dos painéis ClimaPampa.
   Extraído sem alteração do painel de clima (commit d0e3abc): escalas,
   formatação pt-BR, linha, barras, sparkline, dica por cursor e por toque
   e eixo espaçado pela largura real do rótulo (rodadas 104 a 107).
   Único acréscimo: exigirFonte() no início de renderLineChart e
   renderBarChart, que acusa erro no console quando o texto da dica não foi
   montado com dica(rotulo, fonte) de componentes.js.
   Rodada A6: legenda comum (montarLegenda) em todo gráfico com 2 ou mais
   séries e rótulos finais sem sobreposição (desenharRotulosFinais). */

// O texto da dica vem do nome da série; ele precisa ter sido montado com dica().
function exigirFonte(nomes, grafico){
  const ok = window.Paineis && window.Paineis.dicaTemFonte;
  nomes.forEach(nome => {
    if(!ok || !ok(nome)) console.error(`dica sem fonte no gráfico ${grafico}: "${nome}"`);
  });
}

const NS = 'http://www.w3.org/2000/svg';
function el(tag, attrs, parent){
  const e = document.createElementNS(NS, tag);
  for(const k in attrs) e.setAttribute(k, attrs[k]);
  if(parent) parent.appendChild(e);
  return e;
}
function fmtInt(n){ return Math.round(n).toLocaleString('pt-BR'); }
function fmt1(n){ return n.toLocaleString('pt-BR', {maximumFractionDigits:1}); }

function niceTicks(maxVal, count){
  count = count || 4;
  if(maxVal <= 0) maxVal = 1;
  const roughStep = maxVal / count;
  const mag = Math.pow(10, Math.floor(Math.log10(roughStep)));
  const norm = roughStep / mag;
  let step;
  if(norm < 1.5) step = 1 * mag;
  else if(norm < 3) step = 2 * mag;
  else if(norm < 7) step = 5 * mag;
  else step = 10 * mag;
  const niceMax = Math.ceil(maxVal / step) * step;
  const ticks = [];
  for(let v = 0; v <= niceMax + 1e-9; v += step) ticks.push(Math.round(v * 1000) / 1000);
  return ticks;
}
function cssVar(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

// Centraliza a dica no ponto sem deixar que ela passe da borda do cartão.
function posicionarDica(tooltip, pct){
  const larguraPai = tooltip.parentElement.clientWidth, meia = tooltip.offsetWidth/2;
  tooltip.style.left = Math.max(meia, Math.min(larguraPai - meia, larguraPai*pct/100)) + 'px';
}
// No toque não há "sair do ponto": a dica fica até o próximo toque fora dela.
document.addEventListener('pointerdown', () => {
  document.querySelectorAll('.chart-tooltip').forEach(t => { t.style.opacity = 0; });
  document.querySelectorAll('.crosshair').forEach(l => { l.style.opacity = 0; });
}, true);

// ------------------------------------------------------------------ legenda comum (rodada A6)
// Acima do gráfico, em linha (quebra no celular), um item por série na ordem das séries: traço da cor
// da série e o nome por extenso (s.rotulo, vindo do produto: P.rotulo). Com mais de 4 séries, cada uma
// ganha também um traço próprio (sólido, tracejado...), igual na linha e no marcador. Uma série só: sem legenda.
const TRACOS = ['', '6 3', '2 3', '8 3 2 3', '1 3', '10 4'];
function tracoDaSerie(i, n){ return n > 4 ? TRACOS[i % TRACOS.length] : ''; }
function montarLegenda(container, itens){
  if(itens.length < 2) return null;
  if(itens.some(it => !it.rotulo)) console.error(`legenda sem nome de série no gráfico ${container.id}`);
  const div = document.createElement('div');
  div.className = 'chart-legenda';
  div.setAttribute('role', 'list');
  div.setAttribute('aria-label', 'Legenda');
  itens.forEach(it => {
    const item = document.createElement('span');
    item.className = 'legend-item';
    item.setAttribute('role', 'listitem');
    const sw = el('svg', {viewBox: '0 0 18 8', width: 18, height: 8, class: 'legend-traco', 'aria-hidden': 'true'});
    el('line', {x1: 1, x2: 17, y1: 4, y2: 4, stroke: it.cor, 'stroke-width': 3, 'stroke-linecap': 'round', ...(it.traco ? {'stroke-dasharray': it.traco} : {})}, sw);
    const nome = document.createElement('span');
    nome.className = 'legend-nome';
    nome.textContent = it.rotulo || '';
    item.appendChild(sw); item.appendChild(nome); div.appendChild(item);
  });
  container.insertBefore(div, container.firstChild);
  return div;
}

// ------------------------------------------------------------------ rótulos finais sem sobreposição (rodada A6)
// rotulos: [{xPonto, yPonto, texto, cor, aoLado}]. Cada rótulo fica no lugar de sempre (acima e à esquerda do
// último ponto, ou à direita com aoLado); dois rótulos a menos de 14 px na vertical são afastados, com uma
// linha-guia curta até o ponto; nenhum sai da área do gráfico (topo..base). O fundo é da cor do cartão,
// para o rótulo não cruzar a linha.
const DIST_ROTULOS = 14;
function desenharRotulosFinais(svg, rotulos, topo, base){
  if(!rotulos.length) return;
  const largura = +svg.getAttribute('viewBox').split(' ')[2];
  // altura real da caixa (texto e fundo): a distância mínima é a maior entre 14 px e essa altura
  const med = el('text', {x: 0, y: -100, class: 'end-label'}, svg);
  med.textContent = rotulos[0].texto;
  const dist = Math.max(DIST_ROTULOS, Math.ceil(med.getBBox().height) + 3);
  med.remove();
  const desejado = r => r.aoLado ? r.yPonto + 4 : r.yPonto - 9;
  const lista = rotulos.map((r, i) => ({...r, i, y: desejado(r)})).sort((a, b) => a.y - b.y || a.i - b.i);
  const ymin = topo + 9, ymax = base - 3;
  lista.forEach((r, i) => { r.y = Math.max(ymin, r.y); if(i > 0) r.y = Math.max(r.y, lista[i-1].y + dist); });
  // passou da base: empurra de baixo para cima
  for(let i = lista.length - 1; i >= 0; i--){
    const lim = i === lista.length - 1 ? ymax : lista[i+1].y - dist;
    if(lista[i].y > lim) lista[i].y = lim;
  }
  const fundo = cssVar('--surface-card');
  // desenho na ordem das séries (a posição já foi resolvida pela altura)
  lista.slice().sort((a, b) => a.i - b.i).forEach(r => {
    let x = r.aoLado ? r.xPonto + 8 : r.xPonto - 2;
    const g = el('g', {class: 'rotulo-final'}, svg);
    const caixa = el('rect', {fill: fundo, opacity: 0.92, rx: 2}, g);
    const t = el('text', {x, y: r.y, class: 'end-label', fill: r.cor, 'text-anchor': r.aoLado ? 'start' : 'end'}, g);
    t.textContent = r.texto;
    let b = t.getBBox();
    // nunca fora do gráfico: rótulo que passaria da borda volta para dentro
    const dx = b.x + b.width + 2 > largura ? largura - 2 - (b.x + b.width) : (b.x - 2 < 0 ? 2 - b.x : 0);
    if(dx){ x += dx; t.setAttribute('x', x); b = t.getBBox(); }
    caixa.setAttribute('x', b.x - 2); caixa.setAttribute('y', b.y - 1);
    caixa.setAttribute('width', b.width + 4); caixa.setAttribute('height', b.height + 2);
    if(Math.abs(r.y - desejado(r)) > 2){
      // linha-guia curta do ponto até a borda do rótulo (por baixo do rótulo)
      const xg = r.aoLado ? b.x - 1 : b.x + b.width + 1;
      svg.insertBefore(el('line', {x1: r.xPonto, y1: r.yPonto, x2: xg, y2: b.y + b.height / 2, stroke: r.cor, 'stroke-width': 1, opacity: 0.7, class: 'linha-guia'}), g);
    }
  });
}

// Com 2 ou mais rótulos finais, eles vão para a margem direita (à direita do último ponto, na cor da série):
// ali nenhuma linha passa e o fundo do rótulo não cobre marcador nem fim de linha. A margem é a do rótulo
// mais largo; devolve a margem direita a usar.
function margemRotulosFinais(container, textos, padR){
  if(textos.length < 2) return padR;
  const tmp = el('svg', {width: 10, height: 10, style: 'position:absolute;visibility:hidden'}, container);
  const t = el('text', {x: 0, y: 10, class: 'end-label'}, tmp);
  const larg = Math.max(...textos.map(s => { t.textContent = s; return t.getComputedTextLength() || s.length * 7; }));
  tmp.remove();
  return Math.max(padR, Math.ceil(larg) + 14);
}

function renderLineChart(containerId, tooltipId, opts){
  exigirFonte(opts.series.map(s => s.name), containerId);
  const container = document.getElementById(containerId);
  const tooltip = document.getElementById(tooltipId);
  const width = container.clientWidth || 560;
  const height = opts.height || 200;
  const fmtY = v => opts.yFormat ? opts.yFormat(v) : fmtInt(v);
  const comRotulo = opts.series.filter(s => s.emphasis !== false);
  const ladoMulti = !opts.rotuloFinalAoLado && comRotulo.length >= 2;
  const padL = opts.padL || 46, padT = 14, padB = 24;
  const padR = ladoMulti ? margemRotulosFinais(container, comRotulo.map(s => fmtY(s.data[s.data.length - 1])), opts.padR || 14) : (opts.padR || 14);
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  const n = opts.labels.length;

  const allY = opts.series.flatMap(s => s.data);
  const ticks = niceTicks(Math.max(...allY, 0), 4);
  const maxY = ticks[ticks.length - 1];

  container.innerHTML = '';
  const svg = el('svg', {viewBox:`0 0 ${width} ${height}`, width:'100%', height, class:'chart-svg'}, container);

  const xAt = i => padL + (n === 1 ? innerW/2 : innerW * i/(n-1));
  const yAt = v => padT + innerH - (innerH * v/maxY);

  ticks.forEach(v => {
    const y = yAt(v);
    el('line', {x1:padL, x2:padL+innerW, y1:y, y2:y, class:'gridline'}, svg);
    const t = el('text', {x:padL-8, y:y+3.5, class:'axis-label', 'text-anchor':'end'}, svg);
    t.textContent = opts.yFormat ? opts.yFormat(v) : fmtInt(v);
  });
  el('line', {x1:padL, x2:padL+innerW, y1:padT+innerH, y2:padT+innerH, class:'baseline'}, svg);

  // Espaça os rótulos pela largura real do texto. O primeiro e o último ficam
  // ancorados para dentro, então a distância mínima é de 1,5 rótulo mais a folga.
  const medidor = el('text', {x:0, y:-100, class:'axis-label'}, svg);
  const maisLongo = opts.labels.reduce((a, b) => String(b).length > String(a).length ? b : a, '');
  medidor.textContent = maisLongo;
  const larguraRotulo = medidor.getComputedTextLength() || String(maisLongo).length * 7;
  medidor.remove();
  const distMin = larguraRotulo * 1.5 + 10;
  const xTickEvery = n > 1 ? Math.max(1, Math.ceil(distMin / (innerW/(n-1)))) : 1;
  opts.labels.forEach((lab, i) => {
    const longeDoUltimo = xAt(n-1) - xAt(i) >= distMin;
    if((i % xTickEvery === 0 && longeDoUltimo) || i === n-1){
      const t = el('text', {x:xAt(i), y:height-6, class:'axis-label', 'text-anchor': i===n-1 ? 'end' : (i===0?'start':'middle')}, svg);
      t.textContent = lab;
    }
  });

  // Faixa e ano escrito marcam os anos sinalizados, mesmo sem rótulo no eixo.
  (opts.faixas || []).forEach(i => {
    const meia = innerW/(n-1)/2;
    const x0 = Math.max(padL, xAt(i)-meia), x1 = Math.min(padL+innerW, xAt(i)+meia);
    el('rect', {x:x0, y:padT, width:x1-x0, height:innerH, fill:cssVar('--series-context'), opacity:0.25}, svg);
    const t = el('text', {x:(i===n-1 ? x1 : xAt(i)), y:padT-3, class:'axis-label', 'text-anchor': i===n-1 ? 'end' : 'middle'}, svg);
    t.textContent = opts.labels[i];
  });

  const hoverLine = el('line', {x1:padL, x2:padL, y1:padT, y2:padT+innerH, class:'crosshair'}, svg);
  hoverLine.style.opacity = 0;

  const nS = opts.series.length, rotulos = [];
  opts.series.forEach((s, iS) => {
    // Com ano final parcial, o último trecho é tracejado e o marcador é vazado.
    const lastI = s.data.length - 1;
    const fimCheio = s.ultimoParcial ? lastI - 1 : lastI;
    const d = s.data.slice(0, fimCheio+1).map((v,i) => (i===0?'M':'L') + xAt(i).toFixed(1) + ' ' + yAt(v).toFixed(1)).join(' ');
    const traco = tracoDaSerie(iS, nS);
    el('path', {d, fill:'none', stroke:s.color, 'stroke-width':2, 'stroke-linecap':'round', 'stroke-linejoin':'round', opacity: s.emphasis===false?0.7:1, ...(traco ? {'stroke-dasharray': traco} : {})}, svg);
    if(s.ultimoParcial){
      el('path', {d:`M${xAt(lastI-1).toFixed(1)} ${yAt(s.data[lastI-1]).toFixed(1)} L${xAt(lastI).toFixed(1)} ${yAt(s.data[lastI]).toFixed(1)}`, fill:'none', stroke:s.color, 'stroke-width':2, 'stroke-dasharray':'4 4'}, svg);
      el('circle', {cx:xAt(lastI), cy:yAt(s.data[lastI]), r:4, fill:cssVar('--surface-card'), stroke:s.color, 'stroke-width':2}, svg);
    } else {
      el('circle', {cx:xAt(lastI), cy:yAt(s.data[lastI]), r:4, fill:s.color, stroke:cssVar('--surface-card'), 'stroke-width':2}, svg);
    }
    if(s.emphasis !== false){
      rotulos.push({xPonto: xAt(lastI), yPonto: yAt(s.data[lastI]), aoLado: !!opts.rotuloFinalAoLado || ladoMulti,
                    cor: opts.rotuloFinalAoLado ? cssVar('--ink-secondary') : s.color,
                    texto: opts.yFormat ? opts.yFormat(s.data[lastI]) : fmtInt(s.data[lastI])});
    }
  });
  desenharRotulosFinais(svg, rotulos, padT, padT + innerH);

  const hit = el('rect', {x:padL, y:0, width:innerW, height, fill:'transparent'}, svg);
  function showAt(clientX){
    const rect = svg.getBoundingClientRect();
    const scaleX = width / rect.width;
    const mx = (clientX - rect.left) * scaleX;
    let idx = Math.round((mx - padL) / innerW * (n-1));
    idx = Math.max(0, Math.min(n-1, idx));
    hoverLine.setAttribute('x1', xAt(idx)); hoverLine.setAttribute('x2', xAt(idx));
    hoverLine.style.opacity = 1;
    let html = `<div class="tt-x">${opts.labels[idx]}</div>`;
    opts.series.forEach(s => {
      html += `<div class="tt-row"><span class="tt-key" style="border-color:${s.color}"></span><span class="tt-val">${opts.yFormat ? opts.yFormat(s.data[idx]) : fmtInt(s.data[idx])}</span><span class="tt-name">${s.name}</span></div>`;
    });
    tooltip.innerHTML = html;
    tooltip.style.opacity = 1;
    posicionarDica(tooltip, (xAt(idx) / width) * 100);
  }
  hit.addEventListener('pointermove', e => showAt(e.clientX));
  hit.addEventListener('pointerdown', e => showAt(e.clientX));
  hit.addEventListener('pointerleave', e => { if(e.pointerType === 'mouse'){ hoverLine.style.opacity = 0; tooltip.style.opacity = 0; } });

  montarLegenda(container, opts.series.map((s, i) => ({rotulo: s.rotulo, cor: s.color, traco: tracoDaSerie(i, nS)})));
}

function roundedTopBarPath(x, yTop, w, yBottom, r){
  r = Math.max(0, Math.min(r, w/2, Math.max(0, yBottom - yTop)));
  return `M ${x} ${yBottom} L ${x} ${yTop+r} Q ${x} ${yTop} ${x+r} ${yTop} L ${x+w-r} ${yTop} Q ${x+w} ${yTop} ${x+w} ${yTop+r} L ${x+w} ${yBottom} Z`;
}
function renderBarChart(containerId, tooltipId, opts){
  exigirFonte([opts.seriesName], containerId);
  const container = document.getElementById(containerId);
  const tooltip = document.getElementById(tooltipId);
  const width = container.clientWidth || 300;
  const height = opts.height || 190;
  const padL = opts.padL || 40, padR = 10, padT = 14, padB = 24;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  const n = opts.labels.length;

  const ticks = niceTicks(Math.max(...opts.data, 0), opts.tickCount || 4);
  const maxY = ticks[ticks.length - 1] || 1;

  container.innerHTML = '';
  const svg = el('svg', {viewBox:`0 0 ${width} ${height}`, width:'100%', height, class:'chart-svg'}, container);

  const yAt = v => padT + innerH - (innerH * v / maxY);
  ticks.forEach(v => {
    const y = yAt(v);
    el('line', {x1:padL, x2:padL+innerW, y1:y, y2:y, class:'gridline'}, svg);
    const t = el('text', {x:padL-7, y:y+3.5, class:'axis-label', 'text-anchor':'end'}, svg);
    t.textContent = opts.yFormat ? opts.yFormat(v) : fmtInt(v);
  });
  el('line', {x1:padL, x2:padL+innerW, y1:padT+innerH, y2:padT+innerH, class:'baseline'}, svg);

  const slot = innerW / n;
  const barW = Math.max(3, Math.min(24, slot - Math.max(2, slot*0.25)));
  // Mesma regra dos gráficos de linha: espaça pela largura real do rótulo.
  const medidor = el('text', {x:0, y:-100, class:'axis-label'}, svg);
  const maisLongo = opts.labels.reduce((a, b) => String(b).length > String(a).length ? b : a, '');
  medidor.textContent = maisLongo;
  const larguraRotulo = medidor.getComputedTextLength() || String(maisLongo).length * 7;
  medidor.remove();
  const distMin = larguraRotulo + 10;
  const xTickEvery = Math.max(1, Math.ceil(distMin / slot));
  const maxIdx = opts.data.indexOf(Math.max(...opts.data));

  opts.data.forEach((v, i) => {
    const cx = padL + slot*i + slot/2;
    const x = cx - barW/2;
    const yTop = yAt(v);
    const path = roundedTopBarPath(x, yTop, barW, padT+innerH, 3);
    const color = opts.colors ? opts.colors[i] : opts.color;
    // Área de toque na coluna inteira, para que barras baixas também respondam.
    const coluna = el('rect', {x:padL+slot*i, y:padT, width:slot, height:innerH, fill:'transparent'}, svg);
    const bar = el('path', {d:path, fill:color, class:'bar-mark'}, svg);
    [coluna, bar].forEach(alvo => {
      alvo.addEventListener('pointermove', e => showTip(e, i));
      alvo.addEventListener('pointerdown', e => showTip(e, i));
      alvo.addEventListener('pointerleave', hideTip);
    });

    if(i === maxIdx || i === n-1){
      const t = el('text', {x:cx, y:yTop-6, class:'bar-label', 'text-anchor':'middle'}, svg);
      t.textContent = opts.yFormat ? opts.yFormat(v) : fmtInt(v);
    }
    if((i % xTickEvery === 0 && slot*(n-1-i) >= distMin) || i === n-1){
      const t = el('text', {x:cx, y:height-6, class:'axis-label', 'text-anchor':'middle'}, svg);
      t.textContent = opts.labels[i];
    }
  });

  function showTip(e, i){
    const rect = svg.getBoundingClientRect();
    const color = opts.colors ? opts.colors[i] : opts.color;
    tooltip.innerHTML = `<div class="tt-x">${opts.labels[i]}</div><div class="tt-row"><span class="tt-key" style="border-color:${color}"></span><span class="tt-val">${opts.yFormat ? opts.yFormat(opts.data[i]) : fmtInt(opts.data[i])}</span><span class="tt-name">${opts.seriesName || ''}</span></div>`;
    tooltip.style.opacity = 1;
    posicionarDica(tooltip, ((e.clientX - rect.left) / rect.width) * 100);
  }
  function hideTip(e){ if(e.pointerType === 'mouse') tooltip.style.opacity = 0; }
}

function sparkline(svgHost, values, color){
  svgHost.innerHTML = '';
  const w = 100, h = 28;
  const svg = el('svg', {viewBox:`0 0 ${w} ${h}`, preserveAspectRatio:'none'}, svgHost);
  const max = Math.max(...values, 1), min = Math.min(...values, 0);
  const range = (max - min) || 1;
  const n = values.length;
  const x = i => (w-4) * i/(n-1) + 2;
  const y = v => h - 3 - ( (v-min)/range * (h-6) );
  const d = values.map((v,i) => (i===0?'M':'L') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ');
  el('path', {d, fill:'none', stroke:color, 'stroke-width':1.6, 'stroke-linecap':'round', 'stroke-linejoin':'round'}, svg);
  el('circle', {cx:x(n-1), cy:y(values[n-1]), r:2.4, fill:color}, svg);
}

