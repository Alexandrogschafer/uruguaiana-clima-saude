/* Gráficos SVG dos painéis ClimaPampa.
   Extraído sem alteração do painel de clima (commit d0e3abc): escalas,
   formatação pt-BR, linha, barras, sparkline, dica por cursor e por toque
   e eixo espaçado pela largura real do rótulo (rodadas 104 a 107).
   Único acréscimo: exigirFonte() no início de renderLineChart e
   renderBarChart, que acusa erro no console quando o texto da dica não foi
   montado com dica(rotulo, fonte) de componentes.js. */

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

function renderLineChart(containerId, tooltipId, opts){
  exigirFonte(opts.series.map(s => s.name), containerId);
  const container = document.getElementById(containerId);
  const tooltip = document.getElementById(tooltipId);
  const width = container.clientWidth || 560;
  const height = opts.height || 200;
  const padL = opts.padL || 46, padR = opts.padR || 14, padT = 14, padB = 24;
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

  opts.series.forEach(s => {
    // Com ano final parcial, o último trecho é tracejado e o marcador é vazado.
    const lastI = s.data.length - 1;
    const fimCheio = s.ultimoParcial ? lastI - 1 : lastI;
    const d = s.data.slice(0, fimCheio+1).map((v,i) => (i===0?'M':'L') + xAt(i).toFixed(1) + ' ' + yAt(v).toFixed(1)).join(' ');
    el('path', {d, fill:'none', stroke:s.color, 'stroke-width':2, 'stroke-linecap':'round', 'stroke-linejoin':'round', opacity: s.emphasis===false?0.7:1}, svg);
    if(s.ultimoParcial){
      el('path', {d:`M${xAt(lastI-1).toFixed(1)} ${yAt(s.data[lastI-1]).toFixed(1)} L${xAt(lastI).toFixed(1)} ${yAt(s.data[lastI]).toFixed(1)}`, fill:'none', stroke:s.color, 'stroke-width':2, 'stroke-dasharray':'4 4'}, svg);
      el('circle', {cx:xAt(lastI), cy:yAt(s.data[lastI]), r:4, fill:cssVar('--surface-card'), stroke:s.color, 'stroke-width':2}, svg);
    } else {
      el('circle', {cx:xAt(lastI), cy:yAt(s.data[lastI]), r:4, fill:s.color, stroke:cssVar('--surface-card'), 'stroke-width':2}, svg);
    }
    if(s.emphasis !== false){
      const t = opts.rotuloFinalAoLado
        ? el('text', {x:xAt(lastI)+8, y:yAt(s.data[lastI])+4, class:'end-label', fill:cssVar('--ink-secondary'), 'text-anchor':'start'}, svg)
        : el('text', {x:xAt(lastI)-2, y:yAt(s.data[lastI]) - 9, class:'end-label', fill:s.color, 'text-anchor':'end'}, svg);
      t.textContent = opts.yFormat ? opts.yFormat(s.data[lastI]) : fmtInt(s.data[lastI]);
    }
  });

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

  if(opts.legendSlot){
    const slot = document.getElementById(opts.legendSlot);
    slot.innerHTML = '';
    opts.series.forEach(s => {
      const item = document.createElement('span'); item.className = 'legend-item';
      const sw = document.createElement('span'); sw.className = 'legend-swatch'; sw.style.background = s.color;
      const lab = document.createElement('span'); lab.textContent = s.name;
      item.appendChild(sw); item.appendChild(lab); slot.appendChild(item);
    });
  }
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

