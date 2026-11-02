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

/* ======================================================================
   Gráficos dos painéis de território e demografia, trazidos dos painéis
   antigos com as adaptações da base (rodadas 104–111): dica com fonte
   (exigirFonte), dica por toque (pointerdown) e contida no cartão
   (posicionarDica), área de toque inteira por linha/coluna (a marca em si
   não captura o toque) e, no eixo de categorias, rótulos espaçados pela
   largura real. Os atributos data-* nas áreas de toque servem ao teste.
   ====================================================================== */

function roundedRightBarPath(x, y, w, h, r){
  r = Math.max(0, Math.min(r, h/2, Math.max(0, w)));
  return `M ${x} ${y} L ${x+w-r} ${y} Q ${x+w} ${y} ${x+w} ${y+r} L ${x+w} ${y+h-r} Q ${x+w} ${y+h} ${x+w-r} ${y+h} L ${x} ${y+h} Z`;
}

function _ocultarDica(tooltip){ return e => { if(e.pointerType === 'mouse') tooltip.style.opacity = 0; }; }

function renderHBarChart(containerId, tooltipId, opts){
  exigirFonte([opts.seriesName], containerId);
  const container = document.getElementById(containerId);
  const tooltip = document.getElementById(tooltipId);
  const width = container.clientWidth || 300;
  const n = opts.labels.length;
  const barH = opts.barH || 22;
  const gap = opts.gap || 11;
  const padL = opts.padL || 200, padR = opts.padR || 56, padT = 6, padB = 24;
  const innerH = n * (barH + gap) - gap;
  const height = padT + innerH + padB;
  const innerW = width - padL - padR;

  const ticks = niceTicks(Math.max(...opts.data, 0), opts.tickCount || 4);
  const maxX = ticks[ticks.length - 1] || 1;

  container.innerHTML = '';
  const svg = el('svg', {viewBox:`0 0 ${width} ${height}`, width:'100%', height, class:'chart-svg'}, container);
  const xAt = v => padL + innerW * v / maxX;

  // rótulos do eixo x espaçados pela largura real (no celular, os do meio podem sair)
  const medidor = el('text', {x:0, y:-100, class:'axis-label'}, svg);
  medidor.textContent = opts.xFormat ? opts.xFormat(maxX) : fmtInt(maxX);
  const larguraRotulo = medidor.getComputedTextLength() || 40;
  medidor.remove();
  const passo = ticks.length > 1 ? innerW / (ticks.length - 1) : innerW;
  const cada = Math.max(1, Math.ceil((larguraRotulo + 10) / passo));
  ticks.forEach((v, j) => {
    const x = xAt(v);
    el('line', {x1:x, x2:x, y1:padT, y2:padT+innerH, class:'gridline'}, svg);
    const ultimo = j === ticks.length - 1;
    // o último rótulo é ancorado à direita: o vizinho precisa de 1,5 rótulo de distância
    if((j % cada === 0 && (ultimo || (ticks.length - 1 - j) * passo >= larguraRotulo * 1.5 + 10)) || ultimo){
      const t = el('text', {x, y:padT+innerH+16, class:'axis-label', 'text-anchor': ultimo && j > 0 ? 'end' : (j === 0 ? 'start' : 'middle')}, svg);
      t.textContent = opts.xFormat ? opts.xFormat(v) : fmtInt(v);
    }
  });
  el('line', {x1:padL, x2:padL, y1:padT, y2:padT+innerH, class:'baseline'}, svg);

  const ocultar = _ocultarDica(tooltip);
  opts.data.forEach((v, i) => {
    const y = padT + i*(barH+gap);
    const xEnd = xAt(v);
    const color = opts.colors ? opts.colors[i] : opts.color;
    // área de toque: a linha inteira, do rótulo ao fim do gráfico
    const linha = el('rect', {x:0, y:y-gap/2, width, height:barH+gap, fill:'transparent'}, svg);
    el('path', {d:roundedRightBarPath(padL, y, xEnd - padL, barH, 3), fill:color, class:'bar-mark'}, svg);
    linha.addEventListener('pointermove', e => showTip(e, i));
    linha.addEventListener('pointerdown', e => showTip(e, i));
    linha.addEventListener('pointerleave', ocultar);

    const lab = el('text', {x:padL-8, y:y+barH/2+4, class:'cat-label', 'text-anchor':'end'}, svg);
    lab.textContent = opts.labels[i];
    const val = el('text', {x:xEnd+6, y:y+barH/2+4, class:'bar-label', 'text-anchor':'start'}, svg);
    val.textContent = opts.xFormat ? opts.xFormat(v) : fmtInt(v);
  });

  function showTip(e, i){
    const rect = svg.getBoundingClientRect();
    const color = opts.colors ? opts.colors[i] : opts.color;
    tooltip.innerHTML = `<div class="tt-x">${opts.labels[i]}</div><div class="tt-row"><span class="tt-key" style="border-color:${color}"></span><span class="tt-val">${opts.xFormat ? opts.xFormat(opts.data[i]) : fmtInt(opts.data[i])}</span><span class="tt-name">${opts.seriesName || ''}</span></div>`;
    tooltip.style.opacity = 1;
    posicionarDica(tooltip, ((e.clientX - rect.left) / rect.width) * 100);
  }
}

function renderStackedBarChart(containerId, tooltipId, opts){
  exigirFonte(opts.series.map(s => s.name), containerId);
  const container = document.getElementById(containerId);
  const tooltip = document.getElementById(tooltipId);
  const width = container.clientWidth || 300;
  const height = opts.height || 190;
  const padL = opts.padL || 34, padR = 10, padT = 20, padB = 26;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  const n = opts.labels.length;

  const totais = opts.labels.map((_, i) => opts.series.reduce((s, ser) => s + (ser.data[i] || 0), 0));
  const ticks = niceTicks(Math.max(...totais, 0), opts.tickCount || 4);
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
  const barW = Math.max(3, Math.min(opts.barMax || 26, slot - Math.max(2, slot*0.25)));
  // rótulos do eixo pela largura real (mesma regra de renderBarChart)
  const medidor = el('text', {x:0, y:-100, class:'cat-label'}, svg);
  const maisLongo = opts.labels.reduce((a, b) => String(b).length > String(a).length ? b : a, '');
  medidor.textContent = maisLongo;
  const larguraRotulo = medidor.getComputedTextLength() || String(maisLongo).length * 7;
  medidor.remove();
  const distMin = larguraRotulo + 10;
  const cada = Math.max(1, Math.ceil(distMin / slot));
  const ocultar = _ocultarDica(tooltip);

  opts.labels.forEach((lab, i) => {
    const cx = padL + slot*i + slot/2;
    const x = cx - barW/2;
    const coluna = el('rect', {x:padL+slot*i, y:padT, width:slot, height:innerH, fill:'transparent'}, svg);
    coluna.addEventListener('pointermove', e => showTip(e, i));
    coluna.addEventListener('pointerdown', e => showTip(e, i));
    coluna.addEventListener('pointerleave', ocultar);
    let cum = 0;
    opts.series.forEach(ser => {
      const v = ser.data[i] || 0;
      if(v > 0){
        const yTop = yAt(cum + v), yBottom = yAt(cum);
        el('rect', {x, y:yTop, width:barW, height:Math.max(0, yBottom-yTop), fill:ser.color, class:'bar-mark'}, svg);
      }
      cum += v;
    });
    if(totais[i] > 0){
      const t = el('text', {x:cx, y:yAt(totais[i])-7, class:'bar-label', 'text-anchor':'middle'}, svg);
      t.textContent = fmtInt(totais[i]);
    }
    if((i % cada === 0 && slot*(n-1-i) >= distMin) || i === n-1){
      const labEl = el('text', {x:cx, y:height-6, class:'cat-label axis-x', 'text-anchor':'middle'}, svg);
      labEl.textContent = lab;
    }
  });

  function showTip(e, i){
    const rect = svg.getBoundingClientRect();
    let html = `<div class="tt-x">${opts.labels[i]}</div>`;
    const comValor = opts.series.filter(ser => (ser.data[i] || 0) > 0);
    comValor.forEach(ser => {
      html += `<div class="tt-row"><span class="tt-key" style="border-color:${ser.color}"></span><span class="tt-val">${fmtInt(ser.data[i])}</span><span class="tt-name">${ser.name}</span></div>`;
    });
    if(!comValor.length) html += `<div class="tt-row"><span class="tt-val">0</span><span class="tt-name">${opts.nomeVazio || ''}</span></div>`;
    tooltip.innerHTML = html;
    tooltip.style.opacity = 1;
    posicionarDica(tooltip, ((e.clientX - rect.left) / rect.width) * 100);
  }
}

function renderTimeLineChart(containerId, tooltipId, opts){
  exigirFonte(opts.series.map(s => s.name), containerId);
  const container = document.getElementById(containerId);
  const tooltip = document.getElementById(tooltipId);
  const width = container.clientWidth || 300;
  const height = opts.height || 190;
  const padL = opts.padL || 44, padR = 14, padT = 14, padB = 24;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  const allYears = opts.series.flatMap(s => s.points.map(p => p.x));
  const minX = Math.min(...allYears), maxX = Math.max(...allYears);
  const allY = opts.series.flatMap(s => s.points.map(p => p.y));
  const ticks = niceTicks(Math.max(...allY, 0), opts.tickCount || 4);
  const maxY = ticks[ticks.length - 1];
  const minY = opts.zeroBase === false ? Math.min(...allY) * 0.95 : 0;

  container.innerHTML = '';
  const svg = el('svg', {viewBox:`0 0 ${width} ${height}`, width:'100%', height, class:'chart-svg'}, container);
  const xAt = year => padL + innerW * (year - minX) / (maxX - minX);
  const yAt = v => padT + innerH - (innerH * (v - minY) / (maxY - minY));

  ticks.forEach(v => {
    const y = yAt(v);
    el('line', {x1:padL, x2:padL+innerW, y1:y, y2:y, class:'gridline'}, svg);
    const t = el('text', {x:padL-7, y:y+3.5, class:'axis-label', 'text-anchor':'end'}, svg);
    t.textContent = opts.yFormat ? opts.yFormat(v) : fmtInt(v);
  });
  el('line', {x1:padL, x2:padL+innerW, y1:padT+innerH, y2:padT+innerH, class:'baseline'}, svg);

  const xTicks = opts.xTicks || allYears.filter((v,i,a) => a.indexOf(v)===i).sort((a,b)=>a-b);
  xTicks.forEach(year => {
    const t = el('text', {x:xAt(year), y:height-6, class:'axis-label', 'text-anchor':'middle'}, svg);
    t.textContent = year;
  });

  const hoverLine = el('line', {x1:padL, x2:padL, y1:padT, y2:padT+innerH, class:'crosshair'}, svg);
  hoverLine.style.opacity = 0;

  opts.series.forEach(s => {
    const pts = s.points;
    const d = pts.map((p,i) => (i===0?'M':'L') + xAt(p.x).toFixed(1) + ' ' + yAt(p.y).toFixed(1)).join(' ');
    el('path', {d, fill:'none', stroke:s.color, 'stroke-width':2, 'stroke-linecap':'round', 'stroke-linejoin':'round', 'stroke-dasharray': s.dashed ? '4 3' : 'none'}, svg);
    pts.forEach(p => {
      if(p.marker) el('circle', {cx:xAt(p.x), cy:yAt(p.y), r:5, fill:s.color, stroke:cssVar('--surface-card'), 'stroke-width':2, class:'marker-censo'}, svg);
    });
    const last = pts[pts.length-1];
    const t = el('text', {x:xAt(last.x)-2, y:yAt(last.y) - 11, class:'end-label', fill:s.color, 'text-anchor':'end'}, svg);
    t.textContent = opts.yFormat ? opts.yFormat(last.y) : fmtInt(last.y);
  });

  // área de toque única; o ano mais próximo do ponteiro abre a dica
  const anos = Array.from(new Set(allYears)).sort((a,b)=>a-b);
  const hit = el('rect', {x:padL, y:0, width:innerW, height, fill:'transparent', 'data-anos': anos.join(','), 'data-min': minX, 'data-max': maxX}, svg);
  function showAt(clientX){
    const rect = svg.getBoundingClientRect();
    const mx = (clientX - rect.left) * (width / rect.width);
    const alvo = minX + (mx - padL) / innerW * (maxX - minX);
    const year = anos.reduce((a, b) => Math.abs(b - alvo) < Math.abs(a - alvo) ? b : a);
    hoverLine.setAttribute('x1', xAt(year)); hoverLine.setAttribute('x2', xAt(year));
    hoverLine.style.opacity = 1;
    let html = `<div class="tt-x">${year}</div>`;
    opts.series.forEach(s => {
      const p = s.points.find(pp => pp.x === year) || s.points.reduce((a,b) => Math.abs(b.x-year)<Math.abs(a.x-year)?b:a);
      html += `<div class="tt-row"><span class="tt-key" style="border-color:${s.color}"></span><span class="tt-val">${opts.yFormat ? opts.yFormat(p.y) : fmtInt(p.y)}</span><span class="tt-name">${s.name}</span></div>`;
    });
    tooltip.innerHTML = html;
    tooltip.style.opacity = 1;
    posicionarDica(tooltip, (xAt(year) / width) * 100);
  }
  hit.addEventListener('pointermove', e => showAt(e.clientX));
  hit.addEventListener('pointerdown', e => showAt(e.clientX));
  hit.addEventListener('pointerleave', e => { if(e.pointerType === 'mouse'){ hoverLine.style.opacity = 0; tooltip.style.opacity = 0; } });
}

function renderStackedAreaChart(containerId, tooltipId, opts){
  exigirFonte([opts.nomeUrbano, opts.nomeRural], containerId);
  const container = document.getElementById(containerId);
  const tooltip = document.getElementById(tooltipId);
  const width = container.clientWidth || 300;
  const height = opts.height || 200;
  const padL = 44, padR = 14, padT = 14, padB = 24;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;

  const years = opts.years;
  const minX = Math.min(...years), maxX = Math.max(...years);
  const maxY = 100;

  container.innerHTML = '';
  const svg = el('svg', {viewBox:`0 0 ${width} ${height}`, width:'100%', height, class:'chart-svg'}, container);
  const xAt = year => padL + innerW * (year - minX) / (maxX - minX);
  const yAt = v => padT + innerH - (innerH * v / maxY);

  [0,25,50,75,100].forEach(v => {
    const y = yAt(v);
    el('line', {x1:padL, x2:padL+innerW, y1:y, y2:y, class:'gridline'}, svg);
    const t = el('text', {x:padL-7, y:y+3.5, class:'axis-label', 'text-anchor':'end'}, svg);
    t.textContent = v + '%';
  });
  el('line', {x1:padL, x2:padL+innerW, y1:padT+innerH, y2:padT+innerH, class:'baseline'}, svg);
  years.forEach(year => {
    const t = el('text', {x:xAt(year), y:height-6, class:'axis-label', 'text-anchor':'middle'}, svg);
    t.textContent = year;
  });

  const urbanTop = years.map((y,i) => opts.urbano[i]);
  const dUrban = 'M ' + xAt(years[0]) + ' ' + yAt(0) + ' ' + years.map((y,i)=>'L '+xAt(y)+' '+yAt(urbanTop[i])).join(' ') + ' L ' + xAt(years[years.length-1]) + ' ' + yAt(0) + ' Z';
  el('path', {d: dUrban, fill: opts.colorUrbano, 'fill-opacity': 0.85}, svg);
  const dRural = 'M ' + xAt(years[0]) + ' ' + yAt(100) + ' ' + years.map((y,i)=>'L '+xAt(y)+' '+yAt(urbanTop[i])).join(' ') + ' L ' + xAt(years[years.length-1]) + ' ' + yAt(100) + ' Z';
  el('path', {d: dRural, fill: opts.colorRural, 'fill-opacity': 0.85}, svg);
  years.forEach((year,i) => {
    el('circle', {cx:xAt(year), cy:yAt(urbanTop[i]), r:3.5, fill:opts.colorUrbano, stroke:cssVar('--surface-card'), 'stroke-width':1.5, class:'marker-censo'}, svg);
  });

  const hit = el('rect', {x:padL, y:0, width:innerW, height, fill:'transparent', 'data-anos': years.join(','), 'data-min': minX, 'data-max': maxX}, svg);
  function showAt(clientX){
    const rect = svg.getBoundingClientRect();
    const mx = (clientX - rect.left) * (width / rect.width);
    const year = minX + (mx - padL) / innerW * (maxX - minX);
    const idx = years.reduce((best,y,i) => Math.abs(y-year) < Math.abs(years[best]-year) ? i : best, 0);
    tooltip.innerHTML = `<div class="tt-x">${years[idx]}</div>` +
      `<div class="tt-row"><span class="tt-key" style="border-color:${opts.colorUrbano}"></span><span class="tt-val">${fmt1(opts.urbano[idx])}%</span><span class="tt-name">${opts.nomeUrbano}</span></div>` +
      `<div class="tt-row"><span class="tt-key" style="border-color:${opts.colorRural}"></span><span class="tt-val">${fmt1(opts.rural[idx])}%</span><span class="tt-name">${opts.nomeRural}</span></div>`;
    tooltip.style.opacity = 1;
    posicionarDica(tooltip, (xAt(years[idx]) / width) * 100);
  }
  hit.addEventListener('pointermove', e => showAt(e.clientX));
  hit.addEventListener('pointerdown', e => showAt(e.clientX));
  hit.addEventListener('pointerleave', _ocultarDica(tooltip));
}

function renderPyramidChart(containerId, tooltipId, opts){
  exigirFonte([opts.nomeHomens, opts.nomeMulheres], containerId);
  const container = document.getElementById(containerId);
  const tooltip = document.getElementById(tooltipId);
  const width = container.clientWidth || 300;
  const n = opts.labels.length;
  const barH = 8, gap = 2.2;
  const padT = 6, padB = 20, padMid = 2;
  const innerH = n * (barH + gap) - gap;
  const height = padT + innerH + padB;
  const halfW = (width - padMid) / 2;

  container.innerHTML = '';
  const svg = el('svg', {viewBox:`0 0 ${width} ${height}`, width:'100%', height, class:'chart-svg'}, container);
  const cx = width / 2;
  const maxV = opts.maxValue;
  const xLeft = v => cx - padMid/2 - (halfW * v / maxV);
  const xRight = v => cx + padMid/2 + (halfW * v / maxV);

  el('line', {x1:cx, x2:cx, y1:padT, y2:padT+innerH, class:'baseline'}, svg);
  const ocultar = _ocultarDica(tooltip);
  opts.labels.forEach((lab, i) => {
    const y = padT + i*(barH+gap);
    const h = opts.homens[i], m = opts.mulheres[i];
    const linha = el('rect', {x:0, y:y-gap/2, width, height:barH+gap, fill:'transparent'}, svg);
    linha.addEventListener('pointermove', e => showTip(e, i));
    linha.addEventListener('pointerdown', e => showTip(e, i));
    linha.addEventListener('pointerleave', ocultar);
    el('rect', {x:xLeft(h), y, width:(cx-padMid/2-xLeft(h)), height:barH, fill:opts.colorHomens, rx:1.5, class:'bar-mark'}, svg);
    el('rect', {x:cx+padMid/2, y, width:(xRight(m)-cx-padMid/2), height:barH, fill:opts.colorMulheres, rx:1.5, class:'bar-mark'}, svg);
    if(i % 4 === 0 || i === n-1){
      const t = el('text', {x:cx, y:y+barH-1.5, class:'axis-label', 'text-anchor':'middle', style:'font-size:9px'}, svg);
      t.textContent = lab.replace(' anos','').replace(' a ','-').replace('ou mais','+');
    }
  });

  function showTip(e, i){
    const rect = svg.getBoundingClientRect();
    tooltip.innerHTML = `<div class="tt-x">${opts.labels[i]}</div>` +
      `<div class="tt-row"><span class="tt-key" style="border-color:${opts.colorHomens}"></span><span class="tt-val">${fmtInt(opts.homens[i])}</span><span class="tt-name">${opts.nomeHomens}</span></div>` +
      `<div class="tt-row"><span class="tt-key" style="border-color:${opts.colorMulheres}"></span><span class="tt-val">${fmtInt(opts.mulheres[i])}</span><span class="tt-name">${opts.nomeMulheres}</span></div>`;
    tooltip.style.opacity = 1;
    posicionarDica(tooltip, ((e.clientX - rect.left) / rect.width) * 100);
  }
}
