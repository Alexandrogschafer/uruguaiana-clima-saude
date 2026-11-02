// Teste headless (Playwright) de uma tela dos painéis.
// Uso: node scripts/paineis/test_tela.js <tela> [--saida <pasta fora do repositório>]
//      npm run test:paineis            (roda todas as telas configuradas)
//
// Para cada tela, em 1280 e 390 px, claro e escuro (preferência do sistema e
// atributo data-theme="dark"), confere:
//   - 0 erros de console, de página e de requisição;
//   - nenhum NaN/undefined em atributos SVG nem no texto visível;
//   - sem rolagem lateral; menor folga entre rótulos do eixo x >= 8 px;
//   - indicadores, tabelas e rótulos finais iguais aos valores do JSON;
//   - dicas ponto a ponto (cursor a 1280 px, toque a 390 px): valor, rótulo e
//     fonte iguais ao JSON, dica dentro do cartão; toque fora fecha;
//   - cores escuras aplicadas no modo escuro; rodapé com as fontes dos produtos;
//   - produto que falha ao carregar vira erro no bloco, sem derrubar a página;
//   - o endereço antigo redireciona mantendo os parâmetros.
// Registra a altura do documento a 390 px. Screenshots vão para uma pasta
// temporária fora do repositório (ou --saida, que não pode ficar dentro dele).

const path = require("path");
const http = require("http");
const fs = require("fs");
const os = require("os");
const { chromium } = require("playwright");

const RAIZ = path.resolve(__dirname, "..", "..");
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".png": "image/png" };
const FUNDO_ESCURO = "rgb(11, 20, 20)";

// Formatação igual à do painel (pt-BR), recalculada aqui a partir do JSON.
const fmtInt = (n) => Math.round(n).toLocaleString("pt-BR");
const fmt1 = (n) => n.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
const MARCA = { parcial: "*", lacuna: "†" };

// ------------------------------------------------------------------ telas
const TELAS = {
  clima: {
    pagina: "dashboards/clima.html",
    enderecoAntigo: "dashboards/painel-clima-ambiental.html",
    produtos: {
      anual: "clima_inmet-a809_2007-2025_anual",
      precip: "clima_precipitacao-merge_2010-2025_anual",
      mensal: "clima_precipitacao-merge_2010-2025_mensal",
      queim: "queimadas_inpe_2010-2025_anual",
      rio: "rio_ana-77150000_2026-08-06-a-2026-08-07_15min",
      tabela: "clima_tabela-anual_2007-2025",
    },
    // dica esperada em cada ponto: [rótulo do eixo, [[valor, nome da série], ...]]
    graficos: (p) => {
      const fc = (k) => p[k].meta.fonte_curta;
      const meses = p.mensal.dados.slice(-60);
      return {
        "precip-anual": p.precip.dados.map((r) => [r.ano, [[fmtInt(r.merge_mm), `mm · ${fc("precip")}`]]]),
        calor: p.anual.dados.map((r) => [r.ano, [[fmtInt(r.dias_calor_extremo), `dias · ${fc("anual")}`]]]),
        "temp-anual": p.anual.dados.map((r) => [r.ano, [[fmt1(r.temp_media_anual) + "°C", `Temperatura média · ${fc("anual")}`]]]),
        "precip-mensal": meses.map((r) => [r.periodo.slice(2), [[fmtInt(r.precipitacao_mm) + "mm", `Precipitação · ${fc("mensal")}`]]]),
        comparacao: p.precip.dados.map((r) => [r.ano, [[fmtInt(r.merge_mm), fc("precip")], [fmtInt(r.a809_mm), p.precip.meta.fonte_curta_comparacao]]]),
        nivel: p.rio.dados.map((r) => [r.data_hora_utc.slice(5, 16), [[fmtInt(r.nivel_cm) + " cm", `Nível · ${fc("rio")}`]]]),
        vazao: p.rio.dados.map((r) => [r.data_hora_utc.slice(5, 16), [[fmtInt(r.vazao_m3s), `Vazão (m³/s) · ${fc("rio")}`]]]),
        queimadas: p.queim.dados.map((r) => [r.ano, [[fmtInt(r.focos), `focos · ${fc("queim")}`]]]),
      };
    },
    kpis: (p) => {
      const u = (k) => p[k].dados[p[k].dados.length - 1];
      return [fmtInt(u("precip").merge_mm) + "mm", fmtInt(u("anual").dias_calor_extremo) + "dias", fmtInt(u("queim").focos) + "focos", fmtInt(Math.round(u("rio").nivel_cm)) + "cm"];
    },
    tabelas: (p) => ({
      tabelaAnual: p.tabela.dados.map((r) => {
        const m = MARCA[r.nota] || "";
        const v = (x, f) => (x === null || x === undefined ? "—" : f ? f(x) : String(x));
        return [String(r.ano), v(r.precipitacao_merge_mm, fmtInt), v(r.temp_media_anual, fmt1) + m, v(r.temp_max_anual, fmt1) + m, v(r.temp_min_anual, fmt1) + m, v(r.dias_calor_extremo) + m, v(r.dias_geada) + m, v(r.focos, fmtInt)];
      }),
      tabelaNivel: p.rio.dados.slice().reverse().map((r) => [r.data_hora_utc, fmtInt(r.nivel_cm), fmt1(r.vazao_m3s)]),
    }),
    // rótulo final dos gráficos de linha (série em destaque) e rótulos de barra (máximo e último)
    rotulosFinais: (p) => {
      const ult = (a) => a[a.length - 1];
      const barras = (vals, f) => { const i = vals.indexOf(Math.max(...vals)); return [...new Set([i, vals.length - 1])].sort((a, b) => a - b).map((j) => f(vals[j])); };
      return {
        "temp-anual": [fmt1(ult(p.anual.dados).temp_media_anual) + "°C"],
        "precip-mensal": [fmtInt(ult(p.mensal.dados).precipitacao_mm) + "mm"],
        comparacao: [fmtInt(ult(p.precip.dados).merge_mm), fmtInt(ult(p.precip.dados).a809_mm)],
        nivel: [fmtInt(ult(p.rio.dados).nivel_cm) + " cm"],
        vazao: [fmtInt(ult(p.rio.dados).vazao_m3s)],
        "precip-anual": barras(p.precip.dados.map((r) => r.merge_mm), fmtInt),
        calor: barras(p.anual.dados.map((r) => r.dias_calor_extremo), fmtInt),
        queimadas: barras(p.queim.dados.map((r) => r.focos), fmtInt),
      };
    },
  },
  saude: {
    pagina: "dashboards/saude.html",
    enderecoAntigo: "dashboards/painel-saude.html",
    produtos: {
      sim: "saude_sim_2010-2024_anual",
      afog: "saude_sim-afogamento-enchente_2010-2024_quinquenal",
      sih: "saude_sih_2010-2025_anual",
      sinan: "saude_sinan_2010-2026_anual",
      unidades: "saude_unidades-esf-ubs_2026",
    },
    graficos: (p) => {
      const fc = (k) => p[k].meta.fonte_curta;
      const sup = (k, campo) => new Set((p[k].meta.suprimidos || []).filter((s) => s.campo === campo).map((s) => s.linha));
      const nulo = (k, campo, i) => (sup(k, campo).has(i) ? "<5" : "—");
      const v = (k, campo) => (r, i) => (r[campo] === null ? nulo(k, campo, i) : fmtInt(r[campo]));
      const rot = (r) => (r.preliminar ? `${r.ano}*` : String(r.ano));
      const arbo = (ag) => p.sinan.dados.map((r, i) => [rot(r), [[v("sinan", ag)(r, i), `notificações · ${fc("sinan")}`]]]);
      return {
        obitos: p.sim.dados.map((r) => [r.ano, [[fmtInt(r.obitos_total), `Óbitos totais · ${fc("sim")}`], [fmtInt(r.obitos_respiratorios), `Óbitos por causas respiratórias · ${fc("sim")}`]]]),
        afogamento: p.afog.dados.map((r, i) => [r.periodo, [[v("afog", "obitos")(r, i), `óbitos · ${fc("afog")}`]]]),
        sih: p.sih.dados.map((r) => [r.ano, [[fmtInt(r.internacoes_total), `Internações totais · ${fc("sih")}`], [fmtInt(r.internacoes_respiratorias), `Internações respiratórias · ${fc("sih")}`]]]),
        dengue: arbo("dengue"), chik: arbo("chikungunya"), zika: arbo("zika"),
      };
    },
    kpis: (p) => {
      const us = p.sim.dados[p.sim.dados.length - 1], uh = p.sih.dados[p.sih.dados.length - 1];
      const f = p.sinan.dados.filter((r) => !r.preliminar);
      return [fmtInt(us.obitos_total), fmtInt(us.obitos_respiratorios), fmtInt(f[f.length - 1].dengue), fmtInt(uh.internacoes_respiratorias)];
    },
    tabelas: (p) => {
      const por = (d) => Object.fromEntries(d.map((r) => [r.ano, r]));
      const s = por(p.sim.dados), sn = por(p.sinan.dados), h = por(p.sih.dados);
      const anos = [...new Set([...Object.keys(s), ...Object.keys(sn), ...Object.keys(h)].map(Number))].sort((a, b) => a - b);
      const c = (x) => (x === null || x === undefined ? "—" : fmtInt(x));
      const anual = anos.map((a) => {
        const A = s[a] || {}, B = sn[a] || {}, C = h[a] || {};
        return [`${a}${B.preliminar ? "*" : ""}`, c(A.obitos_total), c(A.obitos_respiratorios), c(B.dengue), c(B.chikungunya), c(B.zika), c(C.internacoes_total), c(C.internacoes_respiratorias)];
      });
      const m = p.unidades.meta;
      const supU = (i, campo) => (m.suprimidos || []).some((x) => x.linha === i && x.campo === campo);
      const unid = p.unidades.dados.map((u, i) => [
        u.nome, u.fora_das_comparacoes ? `${u.classe} · fora das comparações` : u.classe, u.zona || "—", u.bairro || "—",
        ...["2017", "2023"].map((a) => { const k = `populacao_informada_${a}`; return supU(i, k) ? "<5" : c(u[k]); }),
        "ver no mapa",
      ]);
      return { tabelaAnual: anual, tabelaUnidades: unid };
    },
    rotulosFinais: (p) => {
      const ult = (a) => a[a.length - 1];
      const barras = (vals) => { const num = vals.map((x) => (x === null ? 0 : x)); const i = num.indexOf(Math.max(...num)); return [...new Set([i, vals.length - 1])].sort((a, b) => a - b).map((j) => (vals[j] === null ? "—" : fmtInt(vals[j]))); };
      return {
        obitos: [fmtInt(ult(p.sim.dados).obitos_respiratorios)],
        sih: [fmtInt(ult(p.sih.dados).internacoes_respiratorias)],
        afogamento: barras(p.afog.dados.map((r) => r.obitos)),
        dengue: barras(p.sinan.dados.map((r) => r.dengue)),
        chik: barras(p.sinan.dados.map((r) => r.chikungunya)),
        zika: barras(p.sinan.dados.map((r) => r.zika)),
      };
    },
    extras: async (browser, base, prod) => testesExtrasSaude(browser, base, prod),
  },
};

// ------------------------------------------------------------------ apoio
function servir() {
  return new Promise((resolve) => {
    const s = http.createServer((req, res) => {
      const p = path.join(RAIZ, decodeURIComponent(req.url.split("?")[0]));
      if (!p.startsWith(RAIZ)) { res.writeHead(403); res.end(); return; }
      fs.readFile(p, (err, data) => {
        if (err) { res.writeHead(404); res.end("404"); return; }
        res.writeHead(200, { "Content-Type": MIME[path.extname(p)] || "application/octet-stream" });
        res.end(data);
      });
    });
    s.listen(0, "127.0.0.1", () => resolve(s));
  });
}

function lerProdutos(def) {
  const out = {};
  for (const [k, id] of Object.entries(def.produtos)) {
    const j = JSON.parse(fs.readFileSync(path.join(RAIZ, "data", "paineis", `${id}.json`), "utf8"));
    const { dados, ...meta } = j;
    out[k] = { meta, dados };
  }
  return out;
}

const limpa = (s) => s.replace(/\s+/g, " ").trim();

async function lerDica(page, id) {
  return page.evaluate((id) => {
    const t = document.getElementById(`tt-${id}`);
    const card = t.closest(".card").getBoundingClientRect();
    const b = t.getBoundingClientRect();
    return { texto: t.innerText.replace(/\s+/g, " ").trim(), visivel: t.style.opacity === "1", dentro: b.left >= card.left - 0.5 && b.right <= card.right + 0.5 };
  }, id);
}

async function conferirDicas(page, esperadas, toque, falhas) {
  const res = {};
  for (const [id, pontos] of Object.entries(esperadas)) {
    let ok = 0;
    const tipo = await page.evaluate((id) => document.querySelectorAll(`#chart-${id} svg rect[fill="transparent"]`).length, id);
    for (let i = 0; i < pontos.length; i++) {
      const c = await page.evaluate(([id, i, n, tipo]) => {
        const rects = document.querySelectorAll(`#chart-${id} svg rect[fill="transparent"]`);
        const alvo = tipo > 1 ? rects[i] : rects[0];
        if (i === 0 || tipo > 1) alvo.scrollIntoView({ block: "center" });
        const b = alvo.getBoundingClientRect();
        if (tipo > 1) return { x: b.left + b.width / 2, y: b.top + b.height * 0.9 };
        return { x: b.left + (n === 1 ? b.width / 2 : (b.width * i) / (n - 1)), y: b.top + b.height / 2 };
      }, [id, i, pontos.length, tipo]);
      if (toque) await page.touchscreen.tap(c.x, c.y); else await page.mouse.move(c.x, c.y);
      const d = await lerDica(page, id);
      const [rotulo, series] = pontos[i];
      const esperado = limpa(`${rotulo} ${series.map(([v, nome]) => `${v} ${nome}`).join(" ")}`);
      if (d.texto === esperado && d.visivel && d.dentro) ok++;
      else if (falhas.length < 40) falhas.push(`dica ${id}[${i}] ${toque ? "toque" : "cursor"}: "${d.texto}" ≠ "${esperado}"${d.visivel ? "" : " (invisível)"}${d.dentro ? "" : " (fora do cartão)"}`);
    }
    res[id] = `${ok}/${pontos.length}`;
  }
  return res;
}

async function rodarModo(browser, base, def, prod, largura, tema, saida) {
  const falhas = [];
  const ctx = await browser.newContext({ viewport: { width: largura, height: 900 }, colorScheme: tema === "claro" ? "light" : "dark", hasTouch: largura === 390 });
  const page = await ctx.newPage();
  const erros = [];
  page.on("console", (m) => { if (m.type() === "error") erros.push(m.text()); });
  page.on("pageerror", (e) => erros.push(String(e)));
  page.on("requestfailed", (r) => erros.push(`falha de requisição ${r.url()}`));
  page.on("response", (r) => { if (r.status() >= 400) erros.push(`HTTP ${r.status()} ${r.url()}`); });
  if (tema === "atributo") {
    // modo escuro pelo atributo: grava data-theme="dark" no HTML servido (como na rodada 106)
    await page.route(`**/${def.pagina}`, async (route) => {
      const resp = await route.fetch();
      const corpo = (await resp.text()).replace("<html ", '<html data-theme="dark" ');
      await route.fulfill({ response: resp, body: corpo });
    });
  }
  await page.goto(base + def.pagina, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.documentElement.dataset.pronto === "1", null, { timeout: 10000 });
  await page.waitForTimeout(300);

  const r = await page.evaluate(() => {
    const svgNaN = Array.from(document.querySelectorAll("svg *")).filter((e) => Array.from(e.attributes).some((a) => /NaN|undefined/.test(a.value))).length;
    const texto = document.body.innerText;
    const folgas = {};
    document.querySelectorAll('[id^="chart-"] svg').forEach((svg) => {
      const h = +svg.getAttribute("height");
      const cx = Array.from(svg.querySelectorAll("text.axis-label")).filter((t) => Math.abs(+t.getAttribute("y") - (h - 6)) < 0.5)
        .map((t) => { const b = t.getBBox(); return [b.x, b.x + b.width]; }).sort((a, b) => a[0] - b[0]);
      let f = Infinity;
      for (let i = 1; i < cx.length; i++) f = Math.min(f, cx[i][0] - cx[i - 1][1]);
      folgas[svg.parentElement.id.replace("chart-", "")] = cx.length > 1 ? +f.toFixed(1) : null;
    });
    const finais = {};
    document.querySelectorAll('[id^="chart-"] svg').forEach((svg) => {
      finais[svg.parentElement.id.replace("chart-", "")] = Array.from(svg.querySelectorAll("text.end-label, text.bar-label")).map((t) => t.textContent);
    });
    const tabelas = {};
    document.querySelectorAll("table.data-table").forEach((t) => {
      tabelas[t.id] = Array.from(t.querySelectorAll("tbody tr")).map((tr) => Array.from(tr.cells).map((c) => c.textContent));
    });
    return {
      svgNaN, textoNaN: /\bNaN\b|undefined/.test(texto),
      rolagem: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      altura: document.documentElement.scrollHeight,
      fundo: getComputedStyle(document.body).backgroundColor,
      kpis: Array.from(document.querySelectorAll(".kpi-value")).map((e) => e.textContent),
      folgas, finais, tabelas,
      rodape: document.getElementById("rodape-comum") ? document.getElementById("rodape-comum").innerText : "",
      cabecalho: !!document.querySelector("#cabecalho-comum nav"),
      errosBloco: document.querySelectorAll(".erro-bloco").length,
    };
  });
  const modo = `${largura}px ${tema}`;
  if (r.svgNaN) falhas.push(`${r.svgNaN} atributos SVG com NaN/undefined`);
  if (r.textoNaN) falhas.push("NaN ou undefined no texto visível");
  if (r.rolagem) falhas.push("rolagem lateral");
  if (r.errosBloco) falhas.push(`${r.errosBloco} blocos com erro de carga`);
  if (!r.cabecalho) falhas.push("cabeçalho comum ausente");
  const escuro = tema !== "claro";
  if (escuro !== (r.fundo === FUNDO_ESCURO)) falhas.push(`fundo ${r.fundo} no modo ${tema}`);
  for (const [g, f] of Object.entries(r.folgas)) if (f !== null && f < 8) falhas.push(`folga de eixo ${g}: ${f} px`);
  const kEsp = def.kpis(prod);
  if (JSON.stringify(r.kpis) !== JSON.stringify(kEsp)) falhas.push(`indicadores ${JSON.stringify(r.kpis)} ≠ ${JSON.stringify(kEsp)}`);
  for (const [t, linhas] of Object.entries(def.tabelas(prod))) {
    if (JSON.stringify(r.tabelas[t]) !== JSON.stringify(linhas)) falhas.push(`tabela ${t} difere do JSON`);
  }
  for (const [g, rot] of Object.entries(def.rotulosFinais(prod))) {
    if (JSON.stringify(r.finais[g]) !== JSON.stringify(rot)) falhas.push(`rótulo final ${g}: ${JSON.stringify(r.finais[g])} ≠ ${JSON.stringify(rot)}`);
  }
  for (const m of Object.values(prod)) {
    const f = "fonte_rodape" in m.meta ? m.meta.fonte_rodape : m.meta.fonte;
    if (f && !r.rodape.includes(f)) falhas.push(`rodapé sem a fonte "${f}"`);
  }
  let dicas = null, toqueFecha = null;
  if (tema === "claro") {
    dicas = await conferirDicas(page, def.graficos(prod), largura === 390, falhas);
    if (largura === 390) {
      await page.touchscreen.tap(3, 3);
      toqueFecha = await page.evaluate(() => Array.from(document.querySelectorAll(".chart-tooltip")).every((t) => t.style.opacity !== "1"));
      if (!toqueFecha) falhas.push("toque fora não fechou a dica");
    }
  }
  await page.screenshot({ path: path.join(saida, `${def.pagina.split("/").pop().replace(".html", "")}_${largura}_${tema}.png`), fullPage: true });
  falhas.unshift(...erros.map((e) => `erro: ${e}`));
  await ctx.close();
  return { modo, falhas, altura: r.altura, dicas, toqueFecha, folgas: r.folgas };
}

async function testeFalhaDeCarga(browser, base, def) {
  // um produto indisponível: o bloco mostra erro e o resto da página continua
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const erros = [];
  page.on("pageerror", (e) => erros.push(String(e)));
  const alvo = Object.values(def.produtos)[0];
  await page.route(`**/data/paineis/${alvo}.json`, (route) => route.fulfill({ status: 404, body: "" }));
  await page.goto(base + def.pagina, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.documentElement.dataset.pronto === "1", null, { timeout: 10000 });
  const r = await page.evaluate(() => ({ erroBloco: document.querySelectorAll(".erro-bloco").length, svgs: document.querySelectorAll('[id^="chart-"] svg').length }));
  await ctx.close();
  return { ok: r.erroBloco > 0 && r.svgs > 0 && erros.length === 0, ...r, errosDePagina: erros.length };
}

async function testeRedirecionamento(browser, base, def) {
  if (!def.enderecoAntigo) return null;
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto(base + def.enderecoAntigo + "?periodo=2021-2025", { waitUntil: "networkidle" });
  const url = new URL(page.url());
  await ctx.close();
  return { ok: url.pathname.endsWith(def.pagina.split("/").pop()) && url.search === "?periodo=2021-2025", destino: url.pathname + url.search };
}


// ------------------------------------------------------------------ extras da tela de saúde
async function abrir(browser, url, opcoes) {
  const ctx = await browser.newContext({ viewport: { width: opcoes.largura || 1280, height: 900 }, hasTouch: (opcoes.largura || 1280) === 390 });
  const page = await ctx.newPage();
  const erros = [], avisosConsole = [];
  page.on("console", (m) => { if (m.type() === "error") erros.push(m.text()); if (m.type() === "warning") avisosConsole.push(m.text()); });
  page.on("pageerror", (e) => erros.push(String(e)));
  for (const [padrao, fn] of opcoes.rotas || []) await page.route(padrao, fn);
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.documentElement.dataset.pronto === "1", null, { timeout: 10000 });
  await page.waitForTimeout(200);
  return { ctx, page, erros, avisosConsole };
}

async function testesExtrasSaude(browser, base, prod) {
  const res = [];
  const pag = base + "dashboards/saude.html";
  const valida = prod.unidades.dados.find((u) => !u.fora_das_comparacoes).cnes;
  for (const largura of [1280, 390]) {
    const { ctx, page, erros } = await abrir(browser, `${pag}?unidade=${valida}`, { largura });
    const r = await page.evaluate((c) => {
      const tr = document.querySelector(`#tabelaUnidades tr[data-cnes="${c}"]`);
      return { destacado: !!tr && tr.classList.contains("destacado") && tr.getAttribute("aria-current") === "true", outros: document.querySelectorAll("#tabelaUnidades tr.destacado").length, chip: document.getElementById("chips-filtro").innerText };
    }, valida);
    res.push({ nome: `?unidade=${valida} destaca a linha (${largura} px)`, ok: r.destacado && r.outros === 1 && erros.length === 0, detalhe: JSON.stringify(r) });
    await ctx.close();
  }
  {
    const { ctx, page, erros, avisosConsole } = await abrir(browser, `${pag}?unidade=1234567&periodo=1990-1995`, {});
    const r = await page.evaluate(() => ({ avisos: [...document.querySelectorAll(".cabecalho-avisos .aviso")].map((e) => e.textContent), destacados: document.querySelectorAll("#tabelaUnidades tr.destacado").length, url: location.search }));
    const ok = r.avisos.includes("Parâmetro ignorado: unidade=1234567") && r.avisos.includes("Parâmetro ignorado: periodo=1990-1995") && r.destacados === 0 && r.url === "" && erros.length === 0;
    res.push({ nome: "unidade e período inválidos geram aviso e saem da URL", ok, detalhe: JSON.stringify({ ...r, avisosConsole }) });
    await ctx.close();
  }
  {
    const { ctx, page, erros } = await abrir(browser, `${pag}?periodo=2015-2020`, {});
    const r = await page.evaluate(() => ({ linhas: document.querySelectorAll("#tabelaAnual tbody tr").length, pontosObitos: document.querySelector("#chart-obitos svg path[stroke]").getAttribute("d").match(/[ML]/g).length, chip: document.getElementById("chips-filtro").innerText }));
    res.push({ nome: "?periodo=2015-2020 recorta séries e tabela anual", ok: r.linhas === 6 && r.pontosObitos === 6 && /2015-2020/.test(r.chip) && erros.length === 0, detalhe: JSON.stringify(r) });
    await ctx.close();
  }
  {
    // JSON simulado: um valor suprimido (null + suprimidos) e um ausente (null), nas unidades e no afogamento
    const idU = prod.unidades.meta.id, idA = prod.afog.meta.id;
    const rotas = [
      [`**/data/paineis/${idU}.json`, async (route) => {
        const j = JSON.parse(JSON.stringify({ ...prod.unidades.meta, dados: prod.unidades.dados }));
        j.dados[0].populacao_informada_2017 = null; j.dados[1].populacao_informada_2023 = null;
        j.suprimidos = [{ linha: 0, campo: "populacao_informada_2017", motivo: "primaria" }];
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(j) });
      }],
      [`**/data/paineis/${idA}.json`, async (route) => {
        const j = JSON.parse(JSON.stringify({ ...prod.afog.meta, dados: prod.afog.dados }));
        const k = j.dados.length - 1; j.dados[k].obitos = null; j.suprimidos = [{ linha: k, campo: "obitos", motivo: "primaria" }];
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(j) });
      }],
    ];
    const { ctx, page, erros } = await abrir(browser, pag, { rotas });
    const r = await page.evaluate(() => {
      const linhas = document.querySelectorAll("#tabelaUnidades tbody tr");
      const cel = (i, j) => linhas[i].cells[j].textContent;
      const rot = [...document.querySelectorAll("#chart-afogamento svg text.bar-label")].map((t) => t.textContent);
      return { suprimido: cel(0, 4), ausente: cel(1, 5), rotulosAfog: rot, nan: /NaN|undefined/.test(document.body.innerText) };
    });
    res.push({ nome: "JSON simulado: suprimido aparece como \"<5\" e ausente como \"—\"", ok: r.suprimido === "<5" && r.ausente === "—" && r.rotulosAfog.includes("<5") && !r.nan && erros.length === 0, detalhe: JSON.stringify(r) });
    await ctx.close();
  }
  {
    const { ctx, page } = await abrir(browser, pag, {});
    const r = await page.evaluate(() => {
      const linhas = [...document.querySelectorAll("#tabelaUnidades tbody tr")];
      const ult = linhas[linhas.length - 1];
      return { ultimaCnes: ult.dataset.cnes, classe: ult.cells[1].textContent, numeros: [ult.cells[4].textContent, ult.cells[5].textContent], n: linhas.length,
        palavraCobertura: /cobertura/i.test(document.body.innerText), mapa: ult.querySelector("a").getAttribute("href") };
    });
    const ok = r.ultimaCnes === "4126947" && /fora das comparações/.test(r.classe) && r.numeros.every((x) => !/\d/.test(x)) && r.n === 22 && !r.palavraCobertura && r.mapa === "../index.html?camada=unidades&unidade=4126947";
    res.push({ nome: "Prisional na última linha, sem números; 22 linhas; sem a palavra \"cobertura\"; link do mapa", ok, detalhe: JSON.stringify(r) });
    // o link do mapa abre o portal mesmo sem o portal ler o parâmetro
    const resp = await page.request.get(new URL(r.mapa, page.url()).toString());
    res.push({ nome: "\"ver no mapa\" abre o portal", ok: resp.status() === 200, detalhe: `HTTP ${resp.status()}` });
    await ctx.close();
  }
  return res;
}

async function main() {
  const args = process.argv.slice(2);
  const iSaida = args.indexOf("--saida");
  let saida = iSaida >= 0 ? path.resolve(args[iSaida + 1]) : fs.mkdtempSync(path.join(os.tmpdir(), "teste-paineis-"));
  if (saida.startsWith(RAIZ)) { console.error("a pasta de saída precisa ficar fora do repositório"); process.exit(2); }
  fs.mkdirSync(saida, { recursive: true });
  const nomes = args.filter((a, i) => !a.startsWith("--") && args[i - 1] !== "--saida");
  const telas = nomes.length ? nomes : Object.keys(TELAS);
  const server = await servir();
  const base = `http://127.0.0.1:${server.address().port}/`;
  const browser = await chromium.launch({ headless: true });
  let total = 0;
  for (const nome of telas) {
    const def = TELAS[nome];
    if (!def) { console.error(`tela desconhecida: ${nome}`); process.exit(2); }
    const prod = lerProdutos(def);
    console.log(`\n== tela ${nome} (${def.pagina})`);
    for (const largura of [1280, 390]) {
      for (const tema of ["claro", "escuro", "atributo"]) {
        const r = await rodarModo(browser, base, def, prod, largura, tema, saida);
        total += r.falhas.length;
        console.log(`[${r.falhas.length ? "FALHA" : "ok"}] ${r.modo}: altura ${r.altura} px; menor folga ${Math.min(...Object.values(r.folgas).filter((v) => v !== null))} px` +
          (r.dicas ? `; dicas ${Object.entries(r.dicas).map(([g, v]) => `${g} ${v}`).join(", ")}` : "") + (r.toqueFecha !== null ? `; toque fora fecha: ${r.toqueFecha}` : ""));
        r.falhas.forEach((f) => console.log(`    - ${f}`));
      }
    }
    const fc = await testeFalhaDeCarga(browser, base, def);
    console.log(`[${fc.ok ? "ok" : "FALHA"}] produto indisponível vira erro no bloco (${fc.erroBloco} bloco(s) com erro, ${fc.svgs} gráficos desenhados)`);
    if (!fc.ok) total++;
    if (def.extras) {
      for (const x of await def.extras(browser, base, prod)) {
        console.log(`[${x.ok ? "ok" : "FALHA"}] ${x.nome}`);
        if (!x.ok) { total++; console.log(`    - ${x.detalhe}`); }
      }
    }
    const rd = await testeRedirecionamento(browser, base, def);
    if (rd) { console.log(`[${rd.ok ? "ok" : "FALHA"}] endereço antigo redireciona para ${rd.destino}`); if (!rd.ok) total++; }
  }
  await browser.close();
  server.close();
  console.log(`\nscreenshots: ${saida}`);
  console.log(total ? `\n${total} falha(s)` : "\ntodas as verificações passaram");
  process.exit(total ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
