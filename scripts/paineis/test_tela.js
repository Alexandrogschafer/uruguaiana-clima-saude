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
//   - o endereço antigo redireciona mantendo os parâmetros;
//   - identidade (rodada A1): Plus Jakarta Sans local carregada, nenhuma requisição a
//     serviço externo de fontes, marca em texto levando ao mapa, cabeçalho numa linha a
//     1280 px, "Atualizado em" na linha da tela e no rodapé, realização em texto e
//     nenhum texto herdado no masthead;
//   - nome do sistema (rodada A4): <title> no padrão de config/identidade.json
//     (nome_sistema.titulos) e nenhum "geoportal" no <title>, no texto visível, nas
//     descrições (meta, og:) e nos aria-label/title — caminhos e URLs não contam.
// Registra a altura do documento a 390 px. Screenshots vão para uma pasta
// temporária fora do repositório (ou --saida, que não pode ficar dentro dele).

const path = require("path");
const http = require("http");
const fs = require("fs");
const os = require("os");
const { chromium } = require("playwright");

const RAIZ = path.resolve(__dirname, "..", "..");
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".png": "image/png", ".woff2": "font/woff2" };
const FUNDO_ESCURO = "rgb(11, 20, 20)";

// Formatação igual à do painel (pt-BR), recalculada aqui a partir do JSON.
const fmtInt = (n) => Math.round(n).toLocaleString("pt-BR");
const fmt1 = (n) => n.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
const MARCA = { parcial: "*", lacuna: "†" };
const dec1 = (n) => n.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const DIA = 864e5;
const diaNum = (iso) => Math.round(Date.parse(iso + "T00:00:00Z") / DIA);
const diaTxt = (n) => { const d = new Date(n * DIA); return `${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}/${String(d.getUTCFullYear()).slice(2)}`; };
// regra das frases do tempo de retorno (B.1 da rodada 115): camada e documento diferem menos que o limite do JSON → "cerca de N anos"
const trNaFrase = (r, lim) => {
  const c = r.tr_anos_camada, d = r.tr_anos_documento;
  return d != null && c !== d && Math.abs(c - d) < lim ? `cerca de ${fmtInt(Math.round((c + d) / 2))} anos` : `${dec1(c)} anos`;
};
// telemetria do rio na tela de clima: últimos 365 dias corridos até o último dia; provisório = depois de fim_serie_ana
const rioJanela = (pr) => { const fim = diaNum(pr.meta.dados_ate); return pr.dados.filter((r) => diaNum(r.dia) > fim - 365); };
const rioProvisorio = (pr, r) => pr.meta.provisorio === true && (!pr.meta.fim_serie_ana || r.dia > pr.meta.fim_serie_ana);
// nome do sistema (rodada A4): <title> esperado de cada tela, montado de config/identidade.json; <Tela> é o
// nome da dimensão ou do cruzamento em config/textos_inicio.json
const NOME_SISTEMA = JSON.parse(fs.readFileSync(path.join(RAIZ, "config", "identidade.json"), "utf8")).nome_sistema;
const ID_TEXTOS = { "cheia-saude": "cheia" };
function tituloEsperado(pagina) {
  const tpl = NOME_SISTEMA.titulos, nome = NOME_SISTEMA.nome;
  if (pagina === "index.html") return tpl.inicio.replace("{nome}", nome);
  if (pagina === "dashboards/catalogo.html") return tpl.catalogo.replace("{nome}", nome);
  const tx = JSON.parse(fs.readFileSync(path.join(RAIZ, "config", "textos_inicio.json"), "utf8"));
  const id = path.basename(pagina, ".html"), d = [...tx.dimensoes, ...tx.cruzamentos].find((x) => x.id === (ID_TEXTOS[id] || id));
  return d ? tpl.tela.replace("{tela}", d.nome).replace("{nome}", nome) : null;
}
// "geoportal" fora de caminho, arquivo e URL (tokens com / ou terminados em extensão de arquivo não contam)
const semCaminhos = (s) => String(s || "").split(/\s+/).filter((w) => !/[/\\]|\.(geo)?json\b|\.html?\b|\.png\b|\.csv\b|\.js\b/i.test(w)).join(" ");
const temGeoportal = (s) => /geoportal/i.test(semCaminhos(s));
// nome da série = rótulo da coluna no produto (campo "rotulo" de colunas, rodada A6)
const rotC = (pr, campo) => ((pr.meta.colunas || []).find((c) => c.nome === campo) || {}).rotulo;
const dataBR = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || ""); return m ? `${m[3]}/${m[2]}/${m[1]}` : iso || ""; };

// colunas das séries com legenda na tela de economia, na ordem do gráfico
const VAB = ["vab_servicos_pct", "vab_administracao_publica_pct", "vab_agropecuaria_pct", "vab_industria_pct"];
const PAM = ["arroz_area_colhida_ha", "soja_area_colhida_ha"], PPM = ["bovino_cabecas", "ovino_cabecas"], CAGED = ["admissoes", "desligamentos"];
const IDHM = ["idhm", "idhm_longevidade", "idhm_renda", "idhm_educacao"];

// ------------------------------------------------------------------ telas
const TELAS = {
  clima: {
    pagina: "dashboards/clima.html",
    enderecoAntigo: "dashboards/painel-clima-ambiental.html",
    produtos: {
      anual: "clima_inmet-a809_serie_anual",
      precip: "clima_precipitacao-merge_serie_anual",
      mensal: "clima_precipitacao-merge_serie_mensal",
      queim: "queimadas_inpe_serie_anual",
      rio: "rio_ana-77150000_telemetria-recente_diario",
      tabela: "clima_tabela_serie_anual",
    },
    // dica esperada em cada ponto: [rótulo do eixo, [[valor, nome da série], ...]]
    graficos: (p) => {
      const fc = (k) => p[k].meta.fonte_curta;
      const meses = p.mensal.dados.slice(-60);
      // rio: telemetria, últimos 365 dias até a última leitura: um ponto a cada 7 dias e o último (x em dias corridos);
      // a dica de dia provisório termina com "provisório"
      const rio = rioJanela(p.rio);
      const amostraRio = rio.filter((r, i, a) => i % 7 === 0 || i === a.length - 1);
      const prov = (r) => (rioProvisorio(p.rio, r) ? " provisório" : "");
      return {
        "precip-anual": p.precip.dados.map((r) => [r.ano, [[fmtInt(r.merge_mm), `mm · ${fc("precip")}`]]]),
        calor: p.anual.dados.map((r) => [r.ano, [[fmtInt(r.dias_calor_extremo), `dias · ${fc("anual")}`]]]),
        "temp-anual": p.anual.dados.map((r) => [r.ano, [[fmt1(r.temp_media_anual) + "°C", `Temperatura média · ${fc("anual")}`]]]),
        "precip-mensal": meses.map((r) => [r.periodo.slice(2), [[fmtInt(r.precipitacao_mm) + "mm", `Precipitação · ${fc("mensal")}`]]]),
        comparacao: p.precip.dados.map((r) => [r.ano, [[fmtInt(r.merge_mm), fc("precip")], [fmtInt(r.a809_mm), p.precip.meta.fonte_curta_comparacao]]]),
        nivel: amostraRio.map((r) => [diaTxt(diaNum(r.dia)), [[fmtInt(r.nivel_max_cm), `Nível máximo do dia (cm) · ${fc("rio")}${prov(r)}`]], diaNum(r.dia)]),
        vazao: amostraRio.map((r) => [diaTxt(diaNum(r.dia)), [[fmtInt(r.vazao_max_m3s), `Vazão máxima do dia (m³/s) · ${fc("rio")}${prov(r)}`]], diaNum(r.dia)]),
        queimadas: p.queim.dados.map((r) => [r.ano, [[fmtInt(r.focos), `focos · ${fc("queim")}`]]]),
      };
    },
    kpis: (p) => {
      const u = (k) => p[k].dados[p[k].dados.length - 1];
      return [fmtInt(u("precip").merge_mm) + "mm", fmtInt(u("anual").dias_calor_extremo) + "dias", fmtInt(u("queim").focos) + "focos", fmtInt(Math.round(u("rio").nivel_ultimo_cm)) + "cm"];
    },
    tabelas: (p) => ({
      tabelaAnual: p.tabela.dados.map((r) => {
        const m = MARCA[r.nota] || "";
        const v = (x, f) => (x === null || x === undefined ? "—" : f ? f(x) : String(x));
        return [String(r.ano), v(r.precipitacao_merge_mm, fmtInt), v(r.temp_media_anual, fmt1) + m, v(r.temp_max_anual, fmt1) + m, v(r.temp_min_anual, fmt1) + m, v(r.dias_calor_extremo) + m, v(r.dias_geada) + m, v(r.focos, fmtInt)];
      }),
      tabelaNivel: rioJanela(p.rio).slice().reverse().map((r) => [dataBR(r.dia), fmtInt(r.nivel_max_cm), fmtInt(r.nivel_ultimo_cm), fmt1(r.vazao_max_m3s), fmt1(r.vazao_ultima_m3s), r.hora_ultima, String(r.n_leituras), rioProvisorio(p.rio, r) ? "provisório" : "consolidado"]),
    }),
    // rótulo final dos gráficos de linha (série em destaque) e rótulos de barra (máximo e último)
    rotulosFinais: (p) => {
      const ult = (a) => a[a.length - 1];
      const barras = (vals, f) => { const i = vals.indexOf(Math.max(...vals)); return [...new Set([i, vals.length - 1])].sort((a, b) => a - b).map((j) => f(vals[j])); };
      return {
        "temp-anual": [fmt1(ult(p.anual.dados).temp_media_anual) + "°C"],
        "precip-mensal": [fmtInt(ult(p.mensal.dados).precipitacao_mm) + "mm"],
        comparacao: [fmtInt(ult(p.precip.dados).merge_mm), fmtInt(ult(p.precip.dados).a809_mm)],
        nivel: [fmtInt(ult(p.rio.dados).nivel_max_cm)],
        vazao: [fmtInt(ult(p.rio.dados).vazao_max_m3s)],
        "precip-anual": barras(p.precip.dados.map((r) => r.merge_mm), fmtInt),
        calor: barras(p.anual.dados.map((r) => r.dias_calor_extremo), fmtInt),
        queimadas: barras(p.queim.dados.map((r) => r.focos), fmtInt),
      };
    },
    extras: async (browser, base, prod) => testesExtrasClima(browser, base, prod),
    legendas: (p) => ({ comparacao: [p.precip.meta.fonte_curta, p.precip.meta.fonte_curta_comparacao] }),
  },
  saude: {
    pagina: "dashboards/saude.html",
    enderecoAntigo: "dashboards/painel-saude.html",
    produtos: {
      sim: "saude_sim_serie_anual",
      afog: "saude_sim-afogamento-enchente_serie_quinquenal",
      sih: "saude_sih_serie_anual",
      sinan: "saude_sinan_serie_anual",
      unidades: "saude_unidades-esf-ubs_atual",
    },
    graficos: (p) => {
      const fc = (k) => p[k].meta.fonte_curta;
      const sup = (k, campo) => new Set((p[k].meta.suprimidos || []).filter((s) => s.campo === campo).map((s) => s.linha));
      const nulo = (k, campo, i) => (sup(k, campo).has(i) ? "<5" : "—");
      const v = (k, campo) => (r, i) => (r[campo] === null ? nulo(k, campo, i) : fmtInt(r[campo]));
      const rot = (r) => (r.preliminar ? `${r.ano}*` : String(r.ano));
      const arbo = (ag) => p.sinan.dados.map((r, i) => [rot(r), [[v("sinan", ag)(r, i), `notificações · ${fc("sinan")}`]]]);
      return {
        // rodada 121: ano preliminar (SIM) e parcial (SIH) com * no rótulo
        obitos: p.sim.dados.map((r) => [r.preliminar ? `${r.ano}*` : r.ano, [[fmtInt(r.obitos_total), `${rotC(p.sim, "obitos_total")} · ${fc("sim")}`], [fmtInt(r.obitos_respiratorios), `${rotC(p.sim, "obitos_respiratorios")} · ${fc("sim")}`]]]),
        afogamento: p.afog.dados.map((r, i) => [r.periodo, [[v("afog", "obitos")(r, i), `óbitos · ${fc("afog")}`]]]),
        sih: p.sih.dados.map((r) => [r.parcial ? `${r.ano}*` : r.ano, [[fmtInt(r.internacoes_total), `${rotC(p.sih, "internacoes_total")} · ${fc("sih")}`], [fmtInt(r.internacoes_respiratorias), `${rotC(p.sih, "internacoes_respiratorias")} · ${fc("sih")}`]]]),
        dengue: arbo("dengue"), chik: arbo("chikungunya"), zika: arbo("zika"),
      };
    },
    kpis: (p) => {
      // indicadores do topo: último ano consolidado (sem o preliminar do SIM nem o parcial do SIH)
      const fs = p.sim.dados.filter((r) => !r.preliminar), fh = p.sih.dados.filter((r) => !r.parcial);
      const us = fs[fs.length - 1], uh = fh[fh.length - 1];
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
        return [`${a}${B.preliminar || A.preliminar || C.parcial ? "*" : ""}`, c(A.obitos_total), c(A.obitos_respiratorios), c(B.dengue), c(B.chikungunya), c(B.zika), c(C.internacoes_total), c(C.internacoes_respiratorias)];
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
    legendas: (p) => ({ obitos: ["obitos_total", "obitos_respiratorios"].map((c) => rotC(p.sim, c)), sih: ["internacoes_total", "internacoes_respiratorias"].map((c) => rotC(p.sih, c)) }),
  },
  populacao: {
    pagina: "dashboards/populacao.html", enderecoAntigo: "dashboards/painel-demografia.html",
    produtos: { serie: "populacao_ibge_serie_anual", ind: "demografia_ibge_serie_indicadores", pir: "demografia_ibge_censos_piramides" },
    recolhiveis: ["det-piramides"], extras: async (browser, base, prod) => testePeriodoRecolhivel(browser, base, "dashboards/populacao.html", { "det-piramides": false }),
    legendas: (p) => Object.fromEntries([["urbano-rural", ["pct_urbano", "pct_rural"].map((c) => rotC(p.ind, c))],
      ...[...new Set(p.pir.dados.map((r) => r.ano))].map((a) => [`piramide-${a}`, ["homens", "mulheres"].map((c) => rotC(p.pir, c))])]),
    kpisCompletos: true,
    kpis: (p) => kpisTexto(p.serie.meta.kpis),
    graficos: (p) => {
      const fi = p.ind.meta.fonte_curta, fp = p.pir.meta.fonte_curta, I = p.ind.dados;
      const tl = (campo, nome, fmt) => I.map((r) => [r.periodo, [[fmt(r[campo]), `${nome} · ${fi}`]]]);
      const g = {
        populacao: p.serie.dados.map((r) => [r.ano, [[fmtInt(r.populacao), `População total · ${p.serie.meta.fonte_curta}`]]]),
        envelhecimento: tl("indice_envelhecimento", "Índice de envelhecimento", fmt1),
        dependencia: tl("razao_dependencia", "Razão de dependência", fmt1),
        "urbano-rural": I.map((r) => [r.periodo, [[fmt1(r.pct_urbano) + "%", `${rotC(p.ind, "pct_urbano")} · ${fi}`], [fmt1(r.pct_rural) + "%", `${rotC(p.ind, "pct_rural")} · ${fi}`]]]),
        "criancas-abs": tl("pop_0_4", "Crianças 0-4", fmtInt), "criancas-pct": tl("pct_pop_0_4", "Crianças 0-4 (%)", (v) => fmt1(v) + "%"),
        "idosos-abs": tl("pop_60_mais", "Idosos 60+", fmtInt), "idosos-pct": tl("pct_pop_60_mais", "Idosos 60+ (%)", (v) => fmt1(v) + "%"),
      };
      for (const ano of [...new Set(p.pir.dados.map((r) => r.ano))]) {
        g[`piramide-${ano}`] = p.pir.dados.filter((r) => r.ano === ano).reverse().map((r) => [r.grupo_idade, [[fmtInt(r.homens), `${rotC(p.pir, "homens")} · ${fp}`], [fmtInt(r.mulheres), `${rotC(p.pir, "mulheres")} · ${fp}`]]]);
      }
      return g;
    },
    tabelas: (p) => ({
      tabelaIndicadores: p.ind.dados.map((d) => [String(d.periodo), fmtInt(d.populacao_total), d.taxa_crescimento === null ? "—" : (d.taxa_crescimento > 0 ? "+" : "") + fmt1(d.taxa_crescimento) + "%",
        fmt1(d.indice_envelhecimento), fmt1(d.razao_dependencia), fmt1(d.pct_urbano) + "%", fmt1(d.pct_rural) + "%", `${fmtInt(d.pop_0_4)} (${fmt1(d.pct_pop_0_4)}%)`, `${fmtInt(d.pop_60_mais)} (${fmt1(d.pct_pop_60_mais)}%)`]),
    }),
  },
  territorio: {
    pagina: "dashboards/territorio.html", enderecoAntigo: "dashboards/painel-territorio.html",
    // meio físico do BDiA fora da tela desde a rodada A5 (testesExtrasTerritorio confere que não volta)
    produtos: { serie: "uso-solo_mapbiomas_serie_classes", classes: "uso-solo_mapbiomas_atual_todas-classes",
                pocos: "pocos_siagas_atual_aquifero", app: "app-hidrica_ana-bho-mapbiomas_atual_curso", urb: "areas-urbanizadas_ibge_2019_classe" },
    recolhiveis: ["det-uso"],
    extras: async (browser, base, prod) => [...await testePeriodoRecolhivel(browser, base, "dashboards/territorio.html", { "det-uso": true }),
      ...await testesExtrasTerritorio(browser, base)],
    kpisCompletos: true,
    kpis: (p) => kpisTexto([...p.serie.meta.kpis_uso, ...p.serie.meta.kpis_meio]),
    graficos: (p) => {
      const S = p.serie.dados, fs = p.serie.meta.fonte_curta;
      const uso = (classe, nome, fmt) => S.map((r) => [r.ano, [[fmt(r[classe]), `${nome} · ${fs}`]]]);
      return {
        campestre: uso("Formação Campestre", "Formação Campestre", fmtInt), arroz: uso("Arroz", "Arroz", fmtInt), urbano: uso("Área Urbanizada", "Área urbanizada", fmt1),
        mosaico: uso("Mosaico de Usos", "Mosaico de Usos", fmtInt), "outras-lavouras": uso("Outras Lavouras Temporárias", "Outras Lavouras Temporárias", fmtInt),
        florestal: uso("Formação Florestal", "Formação Florestal", fmtInt),
        "uso-solo-2024": p.classes.dados.map((r) => [r.classe, [[fmtInt(r.area_km2) + " km²", `km² · ${p.classes.meta.fonte_curta}`]]]),
        "pocos-aquifero": p.pocos.dados.map((r) => [r.aquifero, [[fmtInt(r.pocos), `poços · ${p.pocos.meta.fonte_curta}`]]]),
        "app-curso": p.app.dados.map((r) => [r.rotulo, [[fmtInt(r.area_km2) + " km²", `km² · ${p.app.meta.fonte_curta}`]]]),
        urbanizadas: p.urb.dados.map((r) => [r.tipo === r.densidade ? r.tipo : `${r.tipo} (${r.densidade.toLowerCase()})`, [[fmt1(r.area_km2) + " km²", `km² · ${p.urb.meta.fonte_curta}`]]]),
      };
    },
    tabelas: (p) => ({
      tabelaUso: p.serie.dados.map((r) => [String(r.ano), fmt1(r["Formação Campestre"]), fmt1(r["Arroz"]), fmt1(r["Área Urbanizada"])]),
    }),
  },
  economia: {
    pagina: "dashboards/economia.html",
    produtos: { censo: "censo_ibge_2022_situacao-setor", pib: "pib_ibge_serie_anual", pam: "agropecuaria_ibge_serie_anual", caged: "emprego_caged_serie_mensal",
                rais: "emprego_rais_atual_secao", idhm: "idhm_atlas_censos", cadunico: "cadunico_mds_serie_anual", siconfi: "despesas_siconfi_serie_anual" },
    recolhiveis: ["det-emprego", "det-vulnerabilidade", "det-despesas"],
    extras: async (browser, base, prod) => testePeriodoRecolhivel(browser, base, "dashboards/economia.html", { "det-emprego": true, "det-vulnerabilidade": true, "det-despesas": true }),
    kpisCompletos: true,
    kpis: (p) => kpisTexto([...p.censo.meta.kpis_censo, ...p.pib.meta.kpis_economia, ...p.idhm.meta.kpis_vulnerabilidade, ...p.siconfi.meta.kpis_siconfi]),
    graficos: (p) => {
      const f = (k) => p[k].meta.fonte_curta;
      const fmt3 = (v) => v.toLocaleString("pt-BR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
      const pc = p.pib.dados.filter((r) => r.pib_per_capita !== null), cp = p.pib.dados.filter((r) => r.vab_agropecuaria_pct !== null);
      const sf = p.siconfi.dados.filter((r) => !r.parcial), sp = sf.filter((r) => r.saude_pct_pib !== null);
      const ser = (linhas, rot, defs) => linhas.map((r) => [rot(r), defs.map(([campo, nome, fmt, k]) => [fmt(r[campo]), `${nome} · ${f(k)}`])]);
      return {
        "pib-per-capita": ser(pc, (r) => r.ano, [["pib_per_capita", "PIB per capita", (v) => "R$ " + fmtInt(v), "pib"]]),
        "pib-composicao": ser(cp, (r) => r.ano, VAB.map((c) => [c, rotC(p.pib, c), (v) => fmt1(v) + "%", "pib"])),
        "pam-area": ser(p.pam.dados, (r) => r.ano, PAM.map((c) => [c, rotC(p.pam, c), (v) => fmtInt(v) + " ha", "pam"])),
        "ppm-rebanho": ser(p.pam.dados, (r) => r.ano, PPM.map((c) => [c, rotC(p.pam, c), fmtInt, "pam"])),
        "caged-fluxo": ser(p.caged.dados, (r) => r.rotulo, CAGED.map((c) => [c, rotC(p.caged, c), fmtInt, "caged"])),
        "caged-saldo": ser(p.caged.dados, (r) => r.rotulo, [["saldo_acumulado", "Saldo acumulado", fmtInt, "caged"]]),
        "rais-setor": p.rais.meta.grafico.map((r) => [r.rotulo, [[fmtInt(r.vinculos), `vínculos · ${f("rais")}`]]]),
        "idhm-componentes": ser(p.idhm.dados, (r) => r.ano, IDHM.map((c) => [c, rotC(p.idhm, c), fmt3, "idhm"])),
        "idhm-brasil": ser(p.idhm.dados, (r) => r.ano, [["idhm", p.idhm.meta.nome_municipio, fmt3, "idhm"], ["idhm_brasil", rotC(p.idhm, "idhm_brasil"), fmt3, "idhm"]]),
        "cadunico-familias": ser(p.cadunico.dados, (r) => r.rotulo, [["familias_cadastradas", "Famílias cadastradas", fmtInt, "cadunico"]]),
        "cadunico-taxa": ser(p.cadunico.dados, (r) => r.rotulo, [["taxa_atualizacao_pct", "Taxa de atualização", (v) => fmt1(v) + "%", "cadunico"]]),
        "siconfi-saude-pc": ser(sf, (r) => r.ano, [["saude_per_capita_reais", "Saúde per capita", (v) => "R$ " + fmtInt(v), "siconfi"]]),
        "siconfi-assist-pc": ser(sf, (r) => r.ano, [["assistencia_social_per_capita_reais", "Assistência Social per capita", (v) => "R$ " + fmt1(v), "siconfi"]]),
        "siconfi-pct-pib": ser(sp, (r) => r.ano, [["saude_pct_pib", "Saúde — % do PIB", (v) => fmt1(v) + "%", "siconfi"]]),
      };
    },
    tabelas: (p) => ({ tabelaRais: p.rais.dados.map((r) => [r.rotulo, fmtInt(r.vinculos), fmt1((100 * r.vinculos) / p.rais.meta.total) + "%"]) }),
    legendas: (p) => ({ "pib-composicao": VAB.map((c) => rotC(p.pib, c)), "pam-area": PAM.map((c) => rotC(p.pam, c)), "ppm-rebanho": PPM.map((c) => rotC(p.pam, c)),
      "caged-fluxo": CAGED.map((c) => rotC(p.caged, c)), "idhm-componentes": IDHM.map((c) => rotC(p.idhm, c)),
      "idhm-brasil": [p.idhm.meta.nome_municipio, rotC(p.idhm, "idhm_brasil")] }),
  },
  riscos: {
    pagina: "dashboards/riscos.html",
    produtos: { s2id: "desastres_s2id_serie_anual", cprm: "risco-geologico_cprm_2021_classe", aneel: "energia_aneel_serie_anual",
                setor: "setorizacao-risco_sgb_2014_setor", bater: "areas-de-risco_ibge_2018_bater", indisp: "infraestrutura_indisponivel_atual" },
    recolhiveis: ["det-setorizacao", "det-indisponivel"], extras: async (browser, base, prod) => testeSuprimidoSetor(browser, base, prod),
    legendas: (p) => ({ "s2id-anual": p.s2id.meta.categorias }),
    kpisCompletos: true,
    kpis: (p) => kpisTexto([...p.s2id.meta.kpis_s2id, ...p.aneel.meta.kpis_aneel]),
    graficos: (p) => {
      const fc = p.cprm.meta.fonte_curta, fs = p.s2id.meta.fonte_curta, fa = p.aneel.meta.fonte_curta;
      const cl = (cam) => p.cprm.dados.filter((r) => (cam ? r.camada === "Inundação" : r.camada !== "Inundação")).map((r) => [r.classe, [[fmtInt(r.area_km2) + " km²", `km² · ${fc}`]]]);
      const fech = p.aneel.dados.filter((r) => !r.parcial);
      return {
        "risco-inund": cl(true), "risco-mov": cl(false),
        "s2id-anual": p.s2id.dados.map((r) => {
          const linhas = p.s2id.meta.categorias.filter((c) => r[c] > 0).map((c) => [fmtInt(r[c]), `${c} · ${fs}`]);
          return [r.ano, linhas.length ? linhas : [["0", `reconhecimentos · ${fs}`]]];
        }),
        "aneel-dec": fech.map((r) => [r.ano, [[fmt1(r.dec_horas) + "h", `DEC · ${fa}`]]]),
        "aneel-fec": fech.map((r) => [r.ano, [[fmt1(r.fec_interrupcoes), `FEC · ${fa}`]]]),
      };
    },
    tabelas: (p) => {
      const sup = (pr, i, c) => (pr.meta.suprimidos || []).some((s) => s.linha === i && s.campo === c);
      const n = (pr, r, i, c) => (sup(pr, i, c) ? "<5" : r[c] === null ? "—" : fmtInt(r[c]));
      const tx = (v) => (v === null || v === undefined ? "—" : String(v));
      return {
        tabelaRisco: p.cprm.dados.map((r) => [r.camada, r.classe, fmt1(r.area_km2), fmtInt(r.n_poligonos)]),
        tabelaSetores: p.setor.dados.map((r, i) => [r.setor, r.local, r.tipologia, r.grau_risco, r.grau_vulnerabilidade, n(p.setor, r, i, "edificacoes"), n(p.setor, r, i, "domicilios"), n(p.setor, r, i, "pessoas")]),
        tabelaBater: p.bater.dados.map((r) => [r.area_bater, tx(r.origem), tx(r.observacao)]),
        tabelaIndisp: p.indisp.dados.map((r) => [r.tema, r.fonte, r.motivo]),
      };
    },
  },
  "cheia-saude": {
    pagina: "dashboards/cheia-saude.html",
    produtos: { maximas: "rio_ana-77150000_maximas-anuais", diario: "rio_ana-77150000_ultimos-365-dias_diario", cotas: "inundacao_sgb_cotas",
                unidades: "inundacao_sgb_unidades-cotas", setor: "setorizacao-risco_sgb_2014_setor", bater: "areas-de-risco_ibge_2018_bater",
                rio: "rio_ana-77150000_telemetria-recente_diario" },
    recolhiveis: ["det-filtros", "det-unidades", "det-delimitacoes", "det-espera"],
    kpis: () => [],
    graficos: (p) => {
      const fm = p.maximas.meta.fonte_curta, fd = p.diario.meta.fonte_curta;
      // série diária: um ponto a cada 7 dias e o último (x explícito em dias corridos)
      const amostra = p.diario.dados.filter((r, i, a) => i % 7 === 0 || i === a.length - 1);
      return {
        maximas: p.maximas.dados.map((r) => [r.ano, [[r.maximo_cm === null ? "—" : fmtInt(r.maximo_cm), `Máxima anual (cm) · ${fm}`]]]),
        nivel: amostra.map((r) => [diaTxt(diaNum(r.dia)), [[fmtInt(r.nivel_medio_cm), `Nível médio do dia (cm) · ${fd}`]], diaNum(r.dia)]),
        vazao: amostra.map((r) => [diaTxt(diaNum(r.dia)), [[fmtInt(r.vazao_media_m3s), `Vazão média do dia (m³/s) · ${fd}`]], diaNum(r.dia)]),
      };
    },
    rotulosFinais: (p) => {
      const u = p.diario.dados[p.diario.dados.length - 1];
      return { nivel: [fmtInt(u.nivel_medio_cm)], vazao: [fmtInt(u.vazao_media_m3s)], maximas: [] };
    },
    tabelas: (p) => {
      const sup = (pr, i, c) => (pr.meta.suprimidos || []).some((s) => s.linha === i && s.campo === c);
      const n = (pr, r, i, c) => (sup(pr, i, c) ? "<5" : r[c] === null ? "—" : fmtInt(r[c]));
      const tx = (v) => (v === null || v === undefined ? "—" : String(v));
      const cotas = p.cotas.dados.map((r) => r.cota_cm);
      return {
        tabelaCotas: p.cotas.dados.map((r) => [`${fmtInt(r.cota_cm)} cm`, `${dec1(r.tr_anos_camada)} anos`, `${dec1(r.tr_anos_documento)} anos`,
          `${fmt1(r.area_no_municipio_km2)} km²`, `${fmt1(r.area_km2)} km²`, r.periodo_referencia, fmtInt(r.esf_ubs_dentro)]),
        tabelaUnidades: p.unidades.dados.filter((u) => !u.fora_das_comparacoes).map((u) => [u.nome, u.classe, ...cotas.map((k) => (u[`dentro_${k}`] ? "dentro" : "fora")), "ver na saúde →"]),
        tabelaSetores: p.setor.dados.map((r, i) => [r.setor, r.local, r.tipologia, r.grau_risco, n(p.setor, r, i, "edificacoes"), n(p.setor, r, i, "domicilios"), n(p.setor, r, i, "pessoas")]),
        tabelaBater: p.bater.dados.map((r) => [r.area_bater, tx(r.origem), tx(r.observacao)]),
      };
    },
    extras: async (browser, base, prod) => testesExtrasCheia(browser, base, prod),
  },
  inicio: {
    pagina: "index.html",
    semGraficos: true,
    produtos: { resumo: "inicio_resumo", rio: "rio_ana-77150000_telemetria-recente_diario" },
    recolhiveis: [],
    // a etiqueta do início vem de config/textos_inicio.json (rodada A2), não do padrão das telas
    eyebrow: () => TEXTOS_INICIO().etiqueta,
    // indicadores na ordem dos cartões: dimensões (ordem de textos_inicio.json) e depois os cruzamentos
    kpis: (p) => {
      const tx = TEXTOS_INICIO(), ordem = [...tx.dimensoes, ...tx.cruzamentos].map((d) => d.id);
      return ordem.flatMap((id) => p.resumo.dados.filter((r) => r.dimensao === id).sort((x, y) => x.ordem - y.ordem)
        .map((r) => `${r.prefixo || ""}${r.valor}${r.unidade || ""}`));
    },
    graficos: () => ({}), tabelas: () => ({}),
    extras: async (browser, base, prod) => testesExtrasInicio(browser, base, prod),
  },
  catalogo: {
    pagina: "dashboards/catalogo.html",
    semGraficos: true,
    produtos: { catalogo: "catalogo_dados" },
    recolhiveis: [],
    kpis: () => [], graficos: () => ({}), tabelas: () => ({}),
    extras: async (browser, base, prod) => testesExtrasCatalogo(browser, base, prod),
  }
};

// Texto completo esperado de cada indicador vindo do JSON (rótulo, valor, unidade e subtítulo).
function kpisTexto(kpis) {
  return kpis.map((k) => `${k.rotulo} ${(k.prefixo || "") + k.valor}${k.unidade || ""} ${k.sub || ""}`.replace(/\s+/g, " ").trim());
}

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
      // terceiro elemento opcional do ponto: valor de x no eixo (amostra de uma série longa)
      const c = await page.evaluate(([id, i, n, tipo, xs]) => {
        const rects = document.querySelectorAll(`#chart-${id} svg rect[fill="transparent"]`);
        const alvo = tipo > 1 ? rects[i] : rects[0];
        if (i === 0 || tipo > 1) alvo.scrollIntoView({ block: "center" });
        const b = alvo.getBoundingClientRect();
        if (tipo > 1) return { x: b.left + b.width / 2, y: b.top + b.height * 0.9 };
        // eixo em escala real de tempo: posição pelo ano do ponto
        if (alvo.dataset.anos) {
          const anos = alvo.dataset.anos.split(",").map(Number), mn = +alvo.dataset.min, mx = +alvo.dataset.max;
          const xv = xs[i] !== undefined ? xs[i] : anos[i];
          return { x: b.left + (b.width * (xv - mn)) / (mx - mn), y: b.top + b.height / 2 };
        }
        return { x: b.left + (n === 1 ? b.width / 2 : (b.width * i) / (n - 1)), y: b.top + b.height / 2 };
      }, [id, i, pontos.length, tipo, pontos.map((p) => p[2])]);
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
  page.on("request", (q) => { if (/fonts\.(googleapis|gstatic)\.com/.test(q.url())) erros.push(`requisição a serviço externo de fontes ${q.url()}`); });
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
  // blocos recolhíveis: fechados a 390 px e abertos a 1280 px; depois abre tudo para medir e percorrer as dicas
  const alturaRecolhida = await page.evaluate(() => document.documentElement.scrollHeight);
  const estado = await page.evaluate(() => Object.fromEntries(Array.from(document.querySelectorAll("details.recolhivel")).map((d) => [d.id, d.open])));
  for (const id of def.recolhiveis || []) {
    const esperado = largura !== 390;
    if (estado[id] !== esperado) falhas.push(`bloco ${id} ${estado[id] ? "aberto" : "fechado"} a ${largura} px (esperado ${esperado ? "aberto" : "fechado"})`);
  }
  if ((def.recolhiveis || []).length !== Object.keys(estado).length) falhas.push(`recolhíveis na página: ${Object.keys(estado).join(", ")}`);
  await page.evaluate(() => document.querySelectorAll("details.recolhivel").forEach((d) => { d.open = true; }));
  await page.waitForTimeout(400);

  const r = await page.evaluate(() => {
    const svgNaN = Array.from(document.querySelectorAll("svg *")).filter((e) => Array.from(e.attributes).some((a) => /NaN|undefined/.test(a.value))).length;
    const texto = document.body.innerText;
    const folgas = {};
    document.querySelectorAll('[id^="chart-"] svg.chart-svg').forEach((svg) => {
      const h = svg.dataset.alturaEixo ? +svg.dataset.alturaEixo : +svg.getAttribute("height");
      const cx = Array.from(svg.querySelectorAll("text.axis-label, text.axis-x")).filter((t) => Math.abs(+t.getAttribute("y") - (h - 6)) < 3)
        .map((t) => { const b = t.getBBox(); return [b.x, b.x + b.width]; }).sort((a, b) => a[0] - b[0]);
      let f = Infinity;
      for (let i = 1; i < cx.length; i++) f = Math.min(f, cx[i][0] - cx[i - 1][1]);
      folgas[svg.parentElement.id.replace("chart-", "")] = cx.length > 1 ? +f.toFixed(1) : null;
    });
    const finais = {};
    document.querySelectorAll('[id^="chart-"] svg.chart-svg').forEach((svg) => {
      finais[svg.parentElement.id.replace("chart-", "")] = Array.from(svg.querySelectorAll("text.end-label, text.bar-label")).map((t) => t.textContent);
    });
    // legenda comum (rodada A6): itens (nome e cor do traço) por gráfico; caixas dos rótulos finais por gráfico
    const legendas = {};
    document.querySelectorAll('[id^="chart-"] > .chart-legenda').forEach((l) => {
      legendas[l.parentElement.id.replace("chart-", "")] = [...l.querySelectorAll(".legend-item")].map((it) => ({
        nome: it.querySelector(".legend-nome").textContent, tracos: it.querySelectorAll("svg.legend-traco line").length }));
    });
    const sobreposicoes = [];
    document.querySelectorAll('[id^="chart-"] svg.chart-svg').forEach((svg) => {
      const g = svg.parentElement.id.replace("chart-", ""), area = svg.getBoundingClientRect();
      const cx = [...svg.querySelectorAll("g.rotulo-final")].map((e) => ({ b: e.getBoundingClientRect(), t: e.textContent }));
      for (let i = 0; i < cx.length; i++) {
        const a = cx[i].b;
        if (a.left < area.left - 0.5 || a.right > area.right + 0.5 || a.top < area.top - 0.5 || a.bottom > area.bottom + 0.5) sobreposicoes.push(`${g}: "${cx[i].t}" fora do gráfico`);
        for (let j = i + 1; j < cx.length; j++) {
          const b = cx[j].b;
          if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) sobreposicoes.push(`${g}: "${cx[i].t}" sobre "${cx[j].t}"`);
        }
      }
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
      kpisCompletos: Array.from(document.querySelectorAll(".kpi-tile")).map((e) => e.innerText.replace(/\s+/g, " ").trim()),
      folgas, finais, tabelas, legendas, sobreposicoes,
      rodape: document.getElementById("rodape-comum") ? document.getElementById("rodape-comum").innerText : "",
      textoTela: document.body.innerText,
      titulo: document.title,
      textosNome: [document.title, document.body.innerText,
        ...[...document.querySelectorAll('meta[name="description"], meta[property^="og:"], meta[name^="twitter:"]')].map((m) => m.getAttribute("content")),
        ...[...document.querySelectorAll("[aria-label], [title]")].flatMap((e) => [e.getAttribute("aria-label"), e.getAttribute("title")])].filter(Boolean),
      cabecalho: !!document.querySelector("#cabecalho-comum nav"),
      errosBloco: document.querySelectorAll(".erro-bloco").length,
      identidade: (() => {
        const marca = document.querySelector("#cabecalho-comum a.cabecalho-marca"), nav = document.querySelector("#cabecalho-comum nav");
        const bm = marca && marca.getBoundingClientRect(), bn = nav && nav.getBoundingClientRect();
        const masthead = document.querySelector(".masthead");
        return {
          fonteCorpo: getComputedStyle(document.body).fontFamily.split(",")[0].replace(/["']/g, "").trim(),
          fonteCarregada: [...document.fonts].some((f) => f.family.replace(/["']/g, "") === "Plus Jakarta Sans" && f.status === "loaded"),
          marcaHref: marca ? marca.getAttribute("href") : null, marcaTexto: marca ? marca.querySelector(".marca-texto").textContent : null,
          marcaImg: marca ? marca.querySelectorAll("img, svg").length : null,
          umaLinha: !!(bm && bn && bn.top < bm.bottom && bm.top < bn.bottom),
          linhaTela: (document.querySelector(".masthead .linha-tela .atualizado-tela") || {}).textContent || "",
          eyebrow: (document.querySelector(".masthead .eyebrow") || {}).textContent || "",
          menu: [...document.querySelectorAll("#cabecalho-nav a.nav-item, #cabecalho-nav summary")].map((a) => a.textContent),
          menuLinks: [...document.querySelectorAll("#cabecalho-comum a")].map((a) => a.getAttribute("href")),
          herdado: masthead ? (/Painel \d de \d|\bColeta\b|Ver também/.exec(masthead.innerText) || [""])[0] : "",
        };
      })(),
    };
  });
  {
    const id = r.identidade, data = /^Atualizado em \d{2}\/\d{2}\/\d{4}$/;
    if (id.fonteCorpo !== "Plus Jakarta Sans" || !id.fonteCarregada) falhas.push(`fonte do corpo ${id.fonteCorpo}, carregada: ${id.fonteCarregada}`);
    const marcaEsperada = def.pagina.startsWith("dashboards/") ? "../index.html" : "./index.html";
    if (id.marcaHref !== marcaEsperada || id.marcaTexto !== "ClimaPampa" || id.marcaImg !== 0) falhas.push(`marca: ${JSON.stringify(id)}`);
    if (largura === 1280 && !id.umaLinha) falhas.push("cabeçalho não cabe numa linha a 1280 px");
    if (JSON.stringify(id.menu) !== JSON.stringify(MENU)) falhas.push(`menu: ${JSON.stringify(id.menu)}`);
    if (!id.menuLinks.every((h) => !/sintese\.html|\/index\.html\?|^\.\.\/index\.html\?/.test(h))) falhas.push(`link do menu para endereço antigo: ${JSON.stringify(id.menuLinks)}`);
    if (!data.test(id.linhaTela)) falhas.push(`"Atualizado em" fora da linha da tela: "${id.linhaTela}"`);
    if (id.herdado) falhas.push(`texto herdado no masthead: "${id.herdado}"`);
    // nome do sistema (rodada A4)
    const tEsp = tituloEsperado(def.pagina);
    if (!tEsp || r.titulo !== tEsp) falhas.push(`<title> "${r.titulo}" ≠ "${tEsp}"`);
    const comGeo = r.textosNome.filter(temGeoportal);
    if (comGeo.length) falhas.push(`"geoportal" em texto visível ou descrição: ${JSON.stringify(comGeo.map((s) => s.slice(Math.max(0, s.search(/geoportal/i) - 40), s.search(/geoportal/i) + 40)))}`);
    // grafia única do programa (Parte 0 da rodada A2) e eyebrow terminado no nome da tela
    const eyebrowOk = def.eyebrow ? id.eyebrow === def.eyebrow() : /^ClimaPampa · PET-Saúde: Clima · Uruguaiana \/ RS · \S/.test(id.eyebrow);
    if (!eyebrowOk) falhas.push(`eyebrow fora do padrão: "${id.eyebrow}"`);
    const rod = r.rodape.split("\n").map((l) => l.trim()).filter(Boolean);
    if (rod[0] !== "Realização: ClimaPampa — PET-Saúde: Clima · Unipampa e Secretaria Municipal de Saúde de Uruguaiana" || !rod[1].startsWith("Fontes:") || !data.test(rod[rod.length - 1]) || /Outros painéis/.test(r.rodape))
      falhas.push(`rodapé: ${JSON.stringify(rod)}`);
  }
  // legenda comum (rodada A6): todo gráfico multissérie com legenda (tantos itens quanto séries, nomes do produto, traço em
  // cada item) e nenhum gráfico de série única com legenda; rótulos finais sem sobreposição e dentro do gráfico
  {
    const esp = def.legendas ? def.legendas(prod) : {};
    for (const [g, nomes] of Object.entries(esp)) {
      const obt = r.legendas[g];
      if (!obt) { falhas.push(`legenda ausente em ${g}`); continue; }
      if (JSON.stringify(obt.map((x) => x.nome)) !== JSON.stringify(nomes)) falhas.push(`legenda ${g}: ${JSON.stringify(obt.map((x) => x.nome))} ≠ ${JSON.stringify(nomes)}`);
      if (obt.some((x) => x.tracos !== 1)) falhas.push(`legenda ${g}: item sem traço`);
    }
    for (const g of Object.keys(r.legendas)) if (!(g in esp)) falhas.push(`legenda em gráfico sem legenda prevista: ${g}`);
    r.sobreposicoes.forEach((s) => falhas.push(`rótulo final: ${s}`));
  }
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
  const kObtido = def.kpisCompletos ? r.kpisCompletos : r.kpis;
  if (JSON.stringify(kObtido) !== JSON.stringify(kEsp)) falhas.push(`indicadores ${JSON.stringify(kObtido)} ≠ ${JSON.stringify(kEsp)}`);
  for (const [t, linhas] of Object.entries(def.tabelas(prod))) {
    if (JSON.stringify(r.tabelas[t]) !== JSON.stringify(linhas)) falhas.push(`tabela ${t} difere do JSON`);
  }
  for (const [g, rot] of Object.entries(def.rotulosFinais ? def.rotulosFinais(prod) : {})) {
    if (JSON.stringify(r.finais[g]) !== JSON.stringify(rot)) falhas.push(`rótulo final ${g}: ${JSON.stringify(r.finais[g])} ≠ ${JSON.stringify(rot)}`);
  }
  // decisão C1 (rodada 121): produto com dado de outubro de 2026 em diante precisa do rótulo na tela
  for (const m of Object.values(prod)) {
    const fim = String(m.meta.dados_ate || "").slice(0, 7);
    const marcado = m.meta.provisorio === true || m.meta.provisorio_desde || (m.meta.avisos || []).some((a) => a.tipo === "parcial");
    if (fim >= "2026-10" && marcado && m.meta.id !== "catalogo_dados" && !/provisório|parcial/.test(r.textoTela)) falhas.push(`${m.meta.id}: dado de ${fim} sem rótulo provisório ou parcial na tela`);
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
  if (largura === 390) {
    // menu no celular (rodada A2): o ☰ abre a lista inteira (com "Mais temas"), dentro da tela e sem rolagem lateral
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.click(".menu-celular");
    await page.waitForTimeout(150);
    const m = await page.evaluate(() => { const l = document.getElementById("cabecalho-nav").getBoundingClientRect();
      const itens = [...document.querySelectorAll("#cabecalho-nav a.nav-item")].filter((a) => a.getBoundingClientRect().height > 0).map((a) => a.textContent);
      return { esq: l.left, dir: l.right, larg: document.documentElement.clientWidth, rolagem: document.documentElement.scrollWidth > document.documentElement.clientWidth, itens }; });
    if (m.esq < 0 || m.dir > m.larg || m.rolagem || m.itens.length !== MENU.length - 1) falhas.push(`menu do celular: ${JSON.stringify(m)}`);
    await page.click(".menu-celular");
  }
  falhas.unshift(...erros.map((e) => `erro: ${e}`));
  await ctx.close();
  return { modo, falhas, altura: alturaRecolhida, dicas, toqueFecha, folgas: r.folgas };
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
  const r = await page.evaluate(() => ({ erroBloco: document.querySelectorAll(".erro-bloco").length, svgs: document.querySelectorAll('[id^="chart-"] svg.chart-svg').length }));
  await ctx.close();
  return { ok: r.erroBloco > 0 && (r.svgs > 0 || !!def.semGraficos) && erros.length === 0, ...r, errosDePagina: erros.length };
}

async function testeRedirecionamento(browser, base, def) {
  if (!def.enderecoAntigo) return null;
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  // parâmetro neutro: os filtros não o tocam, então ele precisa chegar intacto à tela nova
  await page.goto(base + def.enderecoAntigo + "?origem=redirecionamento", { waitUntil: "networkidle" });
  const url = new URL(page.url());
  await ctx.close();
  return { ok: url.pathname.split("/").pop() === def.pagina.split("/").pop() && url.search === "?origem=redirecionamento", destino: url.pathname + url.search };
}


// ------------------------------------------------------------------ extras do clima (cartões do rio)
// Rodada 118: os cartões do rio leem a telemetria recente (provisória, sem a trava do evento). Textos iguais ao JSON;
// data/hora da última leitura visível; rótulo "provisório" nos cartões, na tabela, nas faixas dos gráficos e na dica
// (cursor e toque); máxima do período só como leitura (sem "pico", "recorde" nem tempo de retorno); a tabela chega à
// última leitura, inclusive em outubro e novembro de 2026.
async function testesExtrasClima(browser, base, prod) {
  const res = [];
  const rio = prod.rio, m = rio.meta;
  const janelaDias = rioJanela(rio);
  const atualizado = (m.ultima_leitura || "").replace(/^\d{4}-(\d{2})-(\d{2}).*$/, "$2/$1");
  const dh = (s) => (s ? `${dataBR(s)} ${s.slice(11, 16)}` : "—");
  const janela = `${m.fonte_curta} · ${dataBR(janelaDias[0].dia)} a ${dataBR(janelaDias[janelaDias.length - 1].dia)}`;
  const avisosEsperados = m.avisos.filter((a) => ["preliminar", "evento_em_curso"].includes(a.tipo) || a.texto.startsWith("não é dado")).map((a) => a.texto);
  const d = rio.dados[rio.dados.length - 1];
  const mx = m.maxima_periodo;
  const maximaEsperada = `Máxima do período (${dataBR(m.periodo.inicio)} a ${dataBR(m.periodo.fim)}), leitura de telemetria: nível ${fmtInt(mx.nivel_cm)} cm em ${dh(mx.nivel_data_hora)} · vazão ${fmtInt(mx.vazao_m3s)} m³/s em ${dh(mx.vazao_data_hora)} · provisório`;
  const ultimaEsperada = `Última leitura: ${dh(m.ultima_leitura)} · nível ${fmtInt(d.nivel_ultimo_cm)} cm · vazão ${fmtInt(d.vazao_ultima_m3s)} m³/s · provisório`;
  const nProv = janelaDias.filter((r) => rioProvisorio(rio, r)).length;
  for (const largura of [1280, 390]) {
    const { ctx, page, erros } = await abrir(browser, base + "dashboards/clima.html", { largura });
    const r = await page.evaluate(() => {
      const t = (id) => (document.getElementById(id) || {}).innerText || "";
      const tiles = [...document.querySelectorAll(".kpi-tile")];
      const rioTile = tiles[tiles.length - 1];
      const dias = [...document.querySelectorAll("#tabelaNivel tbody tr td:first-child")].map((td) => td.textContent);
      const situacao = [...document.querySelectorAll("#tabelaNivel tbody tr td:last-child")].map((td) => td.textContent);
      const caixa = document.getElementById("aviso-rio").getBoundingClientRect();
      const rotulos = [...document.querySelectorAll(".card-title .rotulo-provisorio")].filter((e) => e.getBoundingClientRect().height > 0).map((e) => e.textContent);
      const faixas = ["nivel", "vazao"].map((g) => [...document.querySelectorAll(`#chart-${g} svg text.trecho-rotulo`)].map((e) => e.textContent).join(","));
      const secao = [...document.querySelectorAll("#nota-rio, #aviso-rio, #ultima-leitura-rio, #maxima-rio")].map((e) => e.innerText).join(" ");
      return { nota: t("nota-rio"), metaNivel: t("meta-nivel"), metaVazao: t("meta-vazao"), avisos: t("aviso-rio"), avisoVisivel: caixa.height > 0,
               ultima: t("ultima-leitura-rio"), maxima: t("maxima-rio"), rotulos, faixas, secao,
               kpiLabel: rioTile.querySelector(".kpi-label").innerText, kpiSub: rioTile.querySelector(".kpi-sub").innerText,
               amostra: /amostra/i.test(document.body.innerText), resumo: document.querySelector("details:has(#tabelaNivel) summary").innerText, dias, situacao };
    });
    // dica no último dia (provisório) por toque a 390 px e por cursor a 1280 px
    const pos = await page.evaluate(() => {
      const h = document.querySelector("#chart-nivel svg rect[fill='transparent']");
      h.scrollIntoView({ block: "center" });
      const b = h.getBoundingClientRect();
      return { x: b.right - 1, y: b.top + b.height / 2 };
    });
    if (largura === 390) await page.touchscreen.tap(pos.x, pos.y); else await page.mouse.move(pos.x, pos.y);
    const dica = await lerDica(page, "nivel");
    const iso = (br) => br.replace(/^(\d{2})\/(\d{2})\/(\d{4})$/, "$3-$2-$1");
    const falhas = [];
    if (r.nota !== `${m.titulo}. ${m.fonte}.`) falhas.push(`nota: ${r.nota}`);
    if (r.metaNivel !== janela || r.metaVazao !== janela) falhas.push(`janela: ${r.metaNivel} | ${r.metaVazao}`);
    for (const a of avisosEsperados) if (!r.avisos.includes(a)) falhas.push(`aviso ausente: ${a}`);
    if (!r.avisos.includes("dados de telemetria, não consolidados")) falhas.push("aviso de telemetria ausente");
    if (!r.avisoVisivel) falhas.push("caixa de aviso sem altura");
    if (!atualizado || !r.avisos.includes(`atualizado em ${atualizado}`)) falhas.push(`"atualizado em" diferente da última leitura (${atualizado})`);
    if (/em preparação|série até/.test(r.avisos)) falhas.push("aviso da trava do consolidado na telemetria");
    if (r.ultima !== ultimaEsperada) falhas.push(`última leitura: ${r.ultima}`);
    if (r.maxima !== maximaEsperada) falhas.push(`máxima do período: ${r.maxima}`);
    if (/\bpico\b|recorde|tempo de retorno|anos de retorno|históric/i.test(r.secao)) falhas.push("texto interpretativo na seção do rio");
    if (JSON.stringify(r.rotulos) !== JSON.stringify(["provisório", "provisório"])) falhas.push(`rótulos nos cartões: ${JSON.stringify(r.rotulos)}`);
    if (nProv && r.faixas.some((f) => f !== "provisório")) falhas.push(`faixa provisória nos gráficos: ${JSON.stringify(r.faixas)}`);
    if (!dica.visivel || !dica.texto.endsWith(" provisório")) falhas.push(`dica do último dia (${largura === 390 ? "toque" : "cursor"}): "${dica.texto}"`);
    if (r.kpiLabel !== `Nível · ${dh(m.ultima_leitura)}` || r.kpiSub !== `vazão ${fmtInt(d.vazao_ultima_m3s)} m³/s · provisório`) falhas.push(`indicador: ${r.kpiLabel} | ${r.kpiSub}`);
    if (r.amostra) falhas.push("texto de amostra na página");
    if (r.resumo !== "Ver nível e vazão diários do rio (telemetria, provisório)") falhas.push(`resumo da tabela: ${r.resumo}`);
    if (r.dias.length !== janelaDias.length || iso(r.dias[0]) !== m.dados_ate) falhas.push(`tabela: ${r.dias.length} linhas, primeira ${r.dias[0]}`);
    if (r.situacao.filter((x) => x === "provisório").length !== nProv) falhas.push("coluna Situação difere de fim_serie_ana");
    if (m.dados_ate >= "2026-11-01" && !(r.dias.some((x) => iso(x).startsWith("2026-10")) && r.dias.some((x) => iso(x).startsWith("2026-11")))) falhas.push("tabela sem outubro e novembro de 2026");
    res.push({ nome: `clima: cartões do rio na telemetria provisória, última leitura ${dh(m.ultima_leitura)}, máxima do período, rótulo e dica "provisório" (${largura} px)`, ok: !falhas.length && erros.length === 0, detalhe: falhas.concat(erros).join(" | ") });
    await ctx.close();
  }
  return res;
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
    // a 390 px a lista das unidades fica recolhida; com ?unidade= abre sozinha e mostra o cartão destacado
    const a = await abrir(browser, pag, { largura: 390 });
    const fechada = await a.page.evaluate(() => ({ aberta: document.getElementById("detalhesUnidades").open, resumo: document.getElementById("resumoUnidades").textContent }));
    await a.ctx.close();
    const b = await abrir(browser, `${pag}?unidade=${valida}`, { largura: 390 });
    const aberta = await b.page.evaluate((c) => {
      const tr = document.querySelector(`#tabelaUnidades tr[data-cnes="${c}"]`); const r = tr.getBoundingClientRect();
      return { aberta: document.getElementById("detalhesUnidades").open, visivel: r.height > 0 && r.top < innerHeight && r.bottom > 0 };
    }, valida);
    await b.ctx.close();
    const c1280 = await abrir(browser, pag, { largura: 1280 });
    const desktop = await c1280.page.evaluate(() => ({ aberta: document.getElementById("detalhesUnidades").open, resumoVisivel: getComputedStyle(document.getElementById("resumoUnidades")).display !== "none" }));
    await c1280.ctx.close();
    const ok = !fechada.aberta && fechada.resumo === `Ver as ${prod.unidades.dados.length} unidades` && aberta.aberta && aberta.visivel && desktop.aberta && !desktop.resumoVisivel;
    res.push({ nome: "390 px: lista recolhida em \"Ver as N unidades\"; ?unidade= abre e mostra o cartão; 1280 px sempre aberta", ok, detalhe: JSON.stringify({ fechada, aberta, desktop }) });
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
    const ok = r.ultimaCnes === "4126947" && /fora das comparações/.test(r.classe) && r.numeros.every((x) => !/\d/.test(x)) && r.n === 22 && !r.palavraCobertura && r.mapa === "../mapa.html?camada=unidadesSaude&unidade=4126947";
    res.push({ nome: "Prisional na última linha, sem números; 22 linhas; sem a palavra \"cobertura\"; link do mapa", ok, detalhe: JSON.stringify(r) });
    // o link do mapa abre o portal mesmo sem o portal ler o parâmetro
    const resp = await page.request.get(new URL(r.mapa, page.url()).toString());
    res.push({ nome: "\"ver no mapa\" abre o portal", ok: resp.status() === 200, detalhe: `HTTP ${resp.status()}` });
    await ctx.close();
  }
  return res;
}

// ?periodo= válido abre os blocos recolhidos que ele afeta (a 390 px); os outros continuam fechados
// Território sem o meio físico do BDiA (rodada A5): nenhum gráfico, tabela ou texto do BDiA na tela; o produto segue
// gerado, no manifesto com telas vazio, no catálogo e com CSV
const ID_BDIA = "meio-fisico_ibge-bdia_atual_classes";
async function testesExtrasTerritorio(browser, base) {
  const { ctx, page, erros } = await abrir(browser, base + "dashboards/territorio.html", {});
  await page.waitForSelector("#dados-tela summary", { timeout: 10000 }).catch(() => null);  // "Dados desta tela" entra na busca
  await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = true; }));
  const r = await page.evaluate(() => ({
    graficos: ["geologia", "geomorfologia", "vegetacao-bdia", "pedologia"].filter((g) => document.getElementById(`chart-${g}`)),
    tabela: !!document.getElementById("tabelaPedologia"), detMeio: !!document.getElementById("det-meio"),
    // "vegetação nativa" fica de fora: a nota do uso do solo usa o termo para as classes naturais do MapBiomas
    texto: (document.body.innerText.match(/BDiA|meio físico|geologia|geomorfologia|pedologia|vegetação \(BDiA\)/gi) || []),
    titulo: document.querySelector(".masthead h1").textContent,
  }));
  // "meio físico" só no nome da tela (h1), enquanto o nome não mudar; BDiA e os quatro temas, em lugar nenhum
  const noTexto = r.texto.filter((s) => /meio físico/i.test(s)).length - (r.titulo.match(/meio físico/gi) || []).length;
  const outros = r.texto.filter((s) => !/meio físico/i.test(s));
  const okTela = !r.graficos.length && !r.tabela && !r.detMeio && !outros.length && noTexto <= 0 && erros.length === 0;
  const man = JSON.parse(fs.readFileSync(path.join(RAIZ, "data", "paineis", "manifesto.json"), "utf8")).produtos.find((x) => x.id === ID_BDIA);
  const cat = JSON.parse(fs.readFileSync(path.join(RAIZ, "data", "paineis", "catalogo_dados.json"), "utf8"));
  const okDados = !!man && Array.isArray(man.telas) && man.telas.length === 0 && !!man.csv && fs.existsSync(path.join(RAIZ, "data", "paineis", man.csv)) &&
    !!cat.sobre_produtos[ID_BDIA] && cat.dados.some((x) => (x.produtos || "").split(" ").includes(ID_BDIA));
  await ctx.close();
  return [
    { nome: "território sem meio físico do BDiA: nenhum gráfico, tabela nem texto do BDiA na tela", ok: okTela, detalhe: JSON.stringify({ ...r, erros }) },
    { nome: "produto do BDiA segue gerado: manifesto com telas vazio, CSV, catálogo e \"Sobre este dado\"", ok: okDados, detalhe: JSON.stringify(man) },
  ];
}

async function testePeriodoRecolhivel(browser, base, pagina, esperado) {
  const { ctx, page, erros } = await abrir(browser, `${base}${pagina}?periodo=2015-2020`, { largura: 390 });
  const alturaRecolhida = await page.evaluate(() => document.documentElement.scrollHeight);
  const estado = await page.evaluate(() => Object.fromEntries(Array.from(document.querySelectorAll("details.recolhivel")).map((d) => [d.id, d.open])));
  await ctx.close();
  const ok = Object.entries(esperado).every(([id, aberto]) => estado[id] === aberto) && erros.length === 0;
  return [{ nome: `390 px com ?periodo=2015-2020: blocos ${JSON.stringify(esperado)}`, ok, detalhe: JSON.stringify(estado) }];
}

// setorização: contagem suprimida no JSON aparece como "<5" (JSON simulado)
async function testeSuprimidoSetor(browser, base, prod) {
  const id = prod.setor.meta.id;
  const rotas = [[`**/data/paineis/${id}.json`, async (route) => {
    const j = JSON.parse(JSON.stringify({ ...prod.setor.meta, dados: prod.setor.dados }));
    j.dados[0].pessoas = null; j.suprimidos = [{ linha: 0, campo: "pessoas", motivo: "primaria" }];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(j) });
  }]];
  const { ctx, page, erros } = await abrir(browser, base + "dashboards/riscos.html", { rotas });
  const v = await page.evaluate(() => document.querySelector("#tabelaSetores tbody tr").cells[7].textContent);
  await ctx.close();
  return [{ nome: "setorização: contagem suprimida aparece como \"<5\" (JSON simulado)", ok: v === "<5" && erros.length === 0, detalhe: v }];
}

// ------------------------------------------------------------------ extras da cheia e saúde
async function testesExtrasCheia(browser, base, prod) {
  const res = [];
  const pag = base + "dashboards/cheia-saude.html";
  {
    // rodada 121: "rio agora" lê a telemetria recente (provisória), nunca a última leitura consolidada
    const { ctx, page, erros } = await abrir(browser, pag, {});
    const f = await page.evaluate(() => ({ texto: document.getElementById("faixa-rio").innerText.replace(/\s+/g, " ").trim(), prov: !!document.querySelector("#faixa-rio .rotulo-provisorio") }));
    await ctx.close();
    const u = prod.rio.dados[prod.rio.dados.length - 1], dh = prod.rio.meta.ultima_leitura;
    const esp = `nível ${fmtInt(u.nivel_ultimo_cm)} cm em ${dataBR(dh)} ${dh.slice(11, 16)} · vazão ${fmtInt(u.vazao_ultima_m3s)} m³/s`;
    const ok = f.texto.includes(esp) && f.prov && /não é dado em tempo real/.test(f.texto) && !/pico|duração|posição|retorno|recorde|em preparação/i.test(f.texto);
    res.push({ nome: "cheia e saúde: \"rio agora\" com a última leitura da telemetria recente, rótulo provisório; sem pico nem tempo de retorno", ok: ok && erros.length === 0, detalhe: f.texto });
  }
  const espera = JSON.parse(fs.readFileSync(path.join(RAIZ, "data", "paineis", "paineis_espera.json"), "utf8"));
  for (const largura of [1280, 390]) {
    const { ctx, page, erros } = await abrir(browser, `${pag}?cota=952`, { largura });
    const r = await page.evaluate(() => {
      const img = document.getElementById("mini-mapa");
      return { pressionado: [...document.querySelectorAll("#seletor-cota button[aria-pressed=true]")].map((b) => b.textContent),
        src: img.getAttribute("src"), alt: img.alt, carregada: img.complete && img.naturalWidth > 0, ativa: [...document.querySelectorAll("#tabelaCotas tr.linha-ativa")].map((tr) => tr.dataset.cota),
        chip: document.getElementById("chips-filtro").innerText, link: document.getElementById("link-mapa").getAttribute("href"), filtrosAbertos: document.getElementById("det-filtros").open,
        refAtiva: [...document.querySelectorAll("#chart-maximas line.linha-referencia")].map((l) => l.getAttribute("stroke")) };
    });
    const c = prod.cotas.dados.find((x) => x.cota_cm === 952);
    const ok = JSON.stringify(r.pressionado) === '["952 cm"]' && r.src === `../data/paineis/${c.imagem}` && r.alt === c.alt && r.carregada &&
      JSON.stringify(r.ativa) === '["952"]' && /Cota: 952 cm/.test(r.chip) && r.link === "../mapa.html?camada=cotasInundacao,unidadesSaude&cota=952" && r.filtrosAbertos === (largura !== 390) && new Set(r.refAtiva).size === 2;
    res.push({ nome: `?cota=952 (${largura} px): botão, mini-mapa com texto alternativo, linha da tabela, chip e link do mapa`, ok: ok && erros.length === 0, detalhe: JSON.stringify(r) });
    if (largura === 1280) {
      const resp = await page.request.get(new URL(r.link, page.url()).toString());
      res.push({ nome: "\"abrir no mapa\" abre o portal", ok: resp.status() === 200, detalhe: `HTTP ${resp.status()}` });
    }
    await ctx.close();
  }
  {
    const { ctx, page, erros } = await abrir(browser, `${pag}?cota=999`, {});
    const r = await page.evaluate(() => ({ avisos: [...document.querySelectorAll(".cabecalho-avisos .aviso")].map((e) => e.textContent), url: location.search,
      chip: document.getElementById("chips-filtro").innerText, pressionado: [...document.querySelectorAll("#seletor-cota button[aria-pressed=true]")].map((b) => b.textContent) }));
    const maior = prod.cotas.dados[prod.cotas.dados.length - 1].cota_cm;
    res.push({ nome: "?cota=999 gera aviso, sai da URL e a tela usa a maior cota", ok: r.avisos.includes("Parâmetro ignorado: cota=999") && r.url === "" && r.chip === "" && r.pressionado[0] === `${fmtInt(maior)} cm` && erros.length === 0, detalhe: JSON.stringify(r) });
    // clique no seletor muda o filtro
    await page.click("#seletor-cota button:first-child");
    const depois = await page.evaluate(() => ({ url: location.search, src: document.getElementById("mini-mapa").getAttribute("src") }));
    const menor = prod.cotas.dados[0];
    res.push({ nome: "clicar numa cota atualiza URL e mini-mapa", ok: depois.url === `?cota=${menor.cota_cm}` && depois.src.endsWith(menor.imagem), detalhe: JSON.stringify(depois) });
    await ctx.close();
  }
  {
    const { ctx, page, erros } = await abrir(browser, pag, {});
    const r = await page.evaluate(() => ({
      moradores: document.getElementById("espera-moradores").innerText, acesso: document.getElementById("espera-acesso").innerText,
      espera: [...document.querySelectorAll("#lista-espera li")].map((li) => li.innerText.replace(/\s+/g, " ").trim()),
      prisionalNaTabela: !!document.querySelector('#tabelaUnidades tr[data-cnes="4126947"]'), notaPrisional: document.getElementById("nota-prisional").innerText,
      frase: document.getElementById("frase-unidades").innerText, links: [...document.querySelectorAll("#tabelaUnidades a")].map((a) => a.getAttribute("href")),
      metaDiario: document.getElementById("meta-diario").innerText, eixoMax: [...document.querySelectorAll("#chart-maximas svg text.axis-label")].map((t) => t.textContent),
      pico: /\bpico\b/i.test(document.body.innerText), avisosEvento: [...document.querySelectorAll(".aviso-evento_em_curso")].map((e) => e.textContent) }));
    const semNumero = (s) => !/\d/.test(s.replace(/\b[A-Z]{2}\d{2}\b/g, ""));
    const idsM = ["IN09", "IN10", "IN11"], idsA = ["SA09", "SA11", "SA12", "SA13", "SA15"];
    res.push({ nome: "caixas \"em conferência\" com os ids em espera e sem nenhum número", ok: idsM.every((i) => r.moradores.includes(i)) && idsA.every((i) => r.acesso.includes(i)) && semNumero(r.moradores) && semNumero(r.acesso), detalhe: `${r.moradores} | ${r.acesso}` });
    const esp = espera.ativos.map((a) => `${a.id} ${a.nome} — ${a.motivo}`);
    res.push({ nome: `lista "o que falta conferir" igual a paineis_espera.json (${esp.length} itens, id, nome e motivo)`, ok: JSON.stringify(r.espera) === JSON.stringify(esp), detalhe: JSON.stringify(r.espera.slice(0, 3)) });
    const comp = prod.unidades.dados.filter((u) => !u.fora_das_comparacoes);
    res.push({ nome: "Prisional fora da tabela e em nota; frase \"0 de 21\"; links \"ver na saúde\" com ?unidade=", ok: !r.prisionalNaTabela && r.notaPrisional.includes("4126947") && r.frase.startsWith(prod.cotas.meta.notas.cs_frase_unidades.replace(/^./, (s) => s.toUpperCase())) &&
      JSON.stringify(r.links) === JSON.stringify(comp.map((u) => `saude.html?unidade=${u.cnes}`)) && erros.length === 0, detalhe: JSON.stringify({ n: r.links.length, nota: r.notaPrisional }) });
    const resp = await page.request.get(new URL(r.links[0], page.url()).toString());
    res.push({ nome: "\"ver na saúde\" abre a tela de saúde", ok: resp.status() === 200, detalhe: `HTTP ${resp.status()}` });
    // trava do evento como está nos dados de hoje: série cortada, máximas sem o ano do evento, sem pico
    const ev = prod.diario.meta.evento_em_curso;
    const ultDia = prod.diario.dados[prod.diario.dados.length - 1].dia, ultAno = prod.maximas.dados[prod.maximas.dados.length - 1].ano;
    // corte de outubro de 2026 (MES_DO_EVENTO_COM_CORTE em dados_rio.py): nenhum dia de outubro no JSON e o aviso "série até 30/09/2026" na tela
    const avisoCorte = (prod.diario.meta.avisos.find((a) => a.tipo === "evento_em_curso") || {}).texto || "";
    const okEv = ev ? r.metaDiario.endsWith(dataBR(ultDia)) && r.eixoMax.includes(String(ultAno)) && !r.eixoMax.includes(String(ultAno + 1)) && !r.pico && r.avisosEvento.length >= 2 &&
                      prod.diario.dados.every((x) => x.dia < "2026-10-01") && avisoCorte.startsWith(`série até ${dataBR(ultDia)}; `) && r.avisosEvento.some((t) => t.includes(avisoCorte))
                    : r.avisosEvento.length === 0;
    res.push({ nome: `evento ${ev ? "em curso" : "não em curso"} nos dados: série até ${dataBR(ultDia)}, máximas até ${ultAno}, avisos ${r.avisosEvento.length}, sem pico`, ok: okEv, detalhe: JSON.stringify({ meta: r.metaDiario, avisos: r.avisosEvento }) });
    await ctx.close();
  }
  {
    // (i) leitura simulada sem evento: nenhum aviso "em preparação" nas séries do rio
    const semEv = (pr) => ({ ...pr.meta, evento_em_curso: false, avisos: pr.meta.avisos.filter((a) => a.tipo !== "evento_em_curso"), dados: pr.dados });
    const rotas = [prod.maximas, prod.diario].map((pr) => [`**/data/paineis/${pr.meta.id}.json`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(semEv(pr)) })]);
    const { ctx, page, erros } = await abrir(browser, pag, { rotas });
    const r = await page.evaluate(() => ({ avisos: [...document.querySelectorAll(".aviso-evento_em_curso")].length, preparacao: /em preparação/.test(document.body.innerText) }));
    res.push({ nome: "séries simuladas sem evento em curso: nenhum aviso \"em preparação\"", ok: r.avisos === 0 && !r.preparacao && erros.length === 0, detalhe: JSON.stringify(r) });
    await ctx.close();
  }
  {
    // tempo de retorno nas frases e área principal (rodada 115, parte B): cada cota, regra calculada dos JSON
    const lim = prod.cotas.meta.dif_tr_cerca_anos;
    const vistos = [];
    let ok = typeof lim === "number";
    for (const r of prod.cotas.dados) {
      const { ctx, page, erros } = await abrir(browser, `${pag}?cota=${r.cota_cm}`, {});
      const t = await page.evaluate(() => ({ meta: document.getElementById("meta-mapa").innerText, th: [...document.querySelectorAll("#tabelaCotas th")].map((e) => e.textContent),
        notas: [...document.querySelectorAll("[data-nota]")].map((e) => e.innerText) }));
      const esperado = `${prod.cotas.meta.fonte_curta} · tempo de retorno ${trNaFrase(r, lim)}`;
      vistos.push(t.meta);
      ok = ok && t.meta === esperado && t.th.indexOf("Área no município") >= 0 && t.th.indexOf("Área no município") < t.th.indexOf("Mancha inteira") &&
        t.notas.includes(prod.cotas.meta.notas.cs_nota_area) && /inclui o leito do rio e a margem fora do município/.test(prod.cotas.meta.notas.cs_nota_area) && erros.length === 0;
      await ctx.close();
    }
    const dif = prod.cotas.dados.filter((r) => r.tr_anos_documento !== r.tr_anos_camada && Math.abs(r.tr_anos_camada - r.tr_anos_documento) < lim);
    res.push({ nome: `tempo de retorno nas frases: "cerca de N anos" em ${dif.map((r) => fmtInt(r.cota_cm)).join(", ") || "nenhuma cota"} (divergência < ${fmt1(lim)} ano); área no município antes da mancha inteira, com a nota`,
      ok: ok && dif.every((r) => vistos.some((v) => v.includes("cerca de"))), detalhe: JSON.stringify(vistos) });
  }
  return res;
}

// ------------------------------------------------------------------ ids estáveis e manifesto (rodada 120)
const MANIFESTO = () => JSON.parse(fs.readFileSync(path.join(RAIZ, "data", "paineis", "manifesto.json"), "utf8"));

// ids da constante IDS da página (texto entre aspas dentro de "const IDS = {...}")
function idsDaPagina(def) {
  const html = fs.readFileSync(path.join(RAIZ, def.pagina), "utf8");
  const m = /const IDS = \{([\s\S]*?)\};/.exec(html);
  return m ? [...m[1].matchAll(/'([^']+)'/g)].map((x) => x[1]) : [];
}

// cada id da tela (no teste e na página) existe no manifesto
function testeIdsNoManifesto(def) {
  const publicados = new Set(MANIFESTO().produtos.map((p) => p.id));
  const doTeste = Object.values(def.produtos), daPagina = idsDaPagina(def);
  const faltam = [...new Set([...doTeste, ...daPagina])].filter((id) => !publicados.has(id));
  const soNaPagina = daPagina.filter((id) => !doTeste.includes(id));
  return { ok: daPagina.length > 0 && faltam.length === 0, n: daPagina.length, detalhe: JSON.stringify({ faltam, soNaPagina }) };
}

// a página servida com os ids antigos (campo "substitui") carrega os mesmos produtos, sem erro; e um id
// que não existe vira erro no bloco (e o console registra "produto não está no manifesto")
async function testeIdAntigo(browser, base, def) {
  const porNovo = Object.fromEntries(MANIFESTO().produtos.filter((p) => p.substitui).map((p) => [p.id, p.substitui]));
  const ids = idsDaPagina(def).filter((id) => porNovo[id]);
  if (!ids.length) return null;
  const servir = async (troca) => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    const erros = [];
    page.on("pageerror", (e) => erros.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") erros.push(m.text()); });
    await page.route(`**/${def.pagina}`, async (route) => {
      const resp = await route.fetch();
      let corpo = await resp.text();
      for (const [a, b] of troca) corpo = corpo.split(`'${a}'`).join(`'${b}'`);
      await route.fulfill({ response: resp, body: corpo });
    });
    await page.goto(base + def.pagina, { waitUntil: "networkidle" });
    await page.waitForFunction(() => document.documentElement.dataset.pronto === "1", null, { timeout: 10000 });
    const r = await page.evaluate(() => ({ carregados: window.Paineis.produtosCarregados.map((m) => m.id),
      erroBloco: [...document.querySelectorAll(".erro-bloco")].map((e) => e.textContent) }));
    await ctx.close();
    return { ...r, erros };
  };
  const a = await servir(ids.map((id) => [id, porNovo[id]]));
  const okAntigo = a.erroBloco.length === 0 && a.erros.length === 0 && ids.every((id) => a.carregados.includes(id));
  const b = await servir([[ids[0], "produto_inexistente_serie_anual"]]);
  // a mensagem no bloco é a da página (algumas mostram o id, outras o erro); basta haver erro no bloco
  const okInexistente = b.erroBloco.length > 0 && b.erros.some((t) => t.includes("não está no manifesto"));
  return { ok: okAntigo && okInexistente, n: ids.length, detalhe: JSON.stringify({ antigo: a, inexistente: b.erroBloco }) };
}

// ------------------------------------------------------------------ início e catálogo (rodada A2)
const TEXTOS_INICIO = () => JSON.parse(fs.readFileSync(path.join(RAIZ, "config", "textos_inicio.json"), "utf8"));
const dataDados = (s) => { const m = /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?/.exec(String(s || "")); return !m ? String(s) : m[3] ? `${m[3]}/${m[2]}/${m[1]}` : m[2] ? `${m[2]}/${m[1]}` : m[1]; };

// números esperados na faixa do rio: código da estação, nível, data, hora, vazão e a data do aviso de método
const NUMEROS_FAIXA = (rio) => {
  const avisos = rio.meta.avisos.filter((a) => a.tipo === "metodo" && /tempo real/.test(a.texto)).map((a) => a.texto).join(" ");
  return 5 + (avisos.match(/\d[\d.,:/]*/g) || []).length;
};

async function linksRespondem(page, seletor) {
  const hrefs = await page.evaluate((s) => [...new Set([...document.querySelectorAll(s)].map((a) => a.href).filter((h) => h.startsWith(location.origin)))], seletor);
  const ruins = [];
  for (const h of hrefs) { const st = (await page.request.get(h)).status(); if (st !== 200) ruins.push(`${st} ${h}`); }
  return { n: hrefs.length, ruins };
}

async function testesExtrasInicio(browser, base, prod) {
  const res = [];
  const tx = TEXTOS_INICIO(), resumo = prod.resumo, rio = prod.rio;
  const { ctx, page, erros } = await abrir(browser, base + "index.html", {});
  const r = await page.evaluate(() => ({
    textos: Object.fromEntries([...document.querySelectorAll("[data-texto]")].map((e) => [e.dataset.texto, e.textContent])),
    cartoes: [...document.querySelectorAll(".cartao-inicio[id^=cartao-]")].map((c) => ({ id: c.id.replace("cartao-", ""), titulo: (c.querySelector(".cartao-inicio-titulo") || {}).textContent || "",
      rotulos: [...c.querySelectorAll(".indicador-inicio .kpi-label")].map((e) => e.textContent), metas: [...c.querySelectorAll(".indicador-meta")].map((e) => e.textContent.replace(/\s+/g, " ").trim()),
      avisos: [...c.querySelectorAll(".indicador-aviso")].map((e) => e.textContent), data: (c.querySelector(".cartao-inicio-data") || {}).textContent || "" })),
    faixa: document.getElementById("faixa-rio").innerText.replace(/\s+/g, " ").trim(),
    linkCheia: (document.querySelector("#faixa-rio a") || {}).getAttribute ? document.querySelector("#faixa-rio a").getAttribute("href") : null,
    provisorio: !!document.querySelector("#faixa-rio .rotulo-provisorio"),
    contagens: [...document.querySelectorAll(".cartao-inicio-contagem")].map((e) => e.textContent),
    projeto: [...document.querySelectorAll("#cartao-projeto a")].map((a) => [a.getAttribute("href"), a.target]),
  }));
  // textos iguais ao arquivo
  const tOk = ["etiqueta", "titulo", "frase", "paragrafo", "aviso_escopo"].every((k) => r.textos[k] === tx[k]);
  res.push({ nome: "início: textos iguais a config/textos_inicio.json (etiqueta, título, frase, parágrafo, aviso de escopo)", ok: tOk, detalhe: JSON.stringify(r.textos).slice(0, 300) });
  // cartões iguais ao JSON
  const dif = [];
  for (const d of [...tx.dimensoes, ...tx.cruzamentos]) {
    const c = r.cartoes.find((x) => x.id === d.id), linhas = resumo.dados.filter((x) => x.dimensao === d.id).sort((a, b) => a.ordem - b.ordem);
    if (!c) { dif.push(`${d.id}: cartão ausente`); continue; }
    if (c.titulo !== d.nome) dif.push(`${d.id}: título ${c.titulo}`);
    if (JSON.stringify(c.rotulos) !== JSON.stringify(linhas.map((x) => x.rotulo))) dif.push(`${d.id}: rótulos ${JSON.stringify(c.rotulos)}`);
    const metas = linhas.map((x) => `${x.fonte_curta} · ${x.periodo}`);
    if (!c.metas.every((m, i) => m.startsWith(metas[i]))) dif.push(`${d.id}: fonte/período ${JSON.stringify(c.metas)}`);
    if (JSON.stringify(c.avisos) !== JSON.stringify(linhas.filter((x) => x.aviso).map((x) => x.aviso))) dif.push(`${d.id}: avisos ${JSON.stringify(c.avisos)}`);
    if (linhas.length && c.data !== `dados até ${dataDados(linhas.map((x) => String(x.dados_ate)).sort().pop())}`) dif.push(`${d.id}: data ${c.data}`);
  }
  res.push({ nome: `início: ${tx.dimensoes.length + tx.cruzamentos.length} cartões iguais a inicio_resumo.json (rótulo, valor, fonte, período, aviso e data)`, ok: !dif.length && erros.length === 0, detalhe: dif.join(" | ") + erros.join(" | ") });
  // faixa do rio: última leitura da telemetria, provisória, sem pico/duração/posição/tempo de retorno
  const u = rio.dados[rio.dados.length - 1], dh = rio.meta.ultima_leitura;
  const esperado = `nível ${fmtInt(u.nivel_ultimo_cm)} cm em ${dataBR(dh)} ${dh.slice(11, 16)} · vazão ${fmtInt(u.vazao_ultima_m3s)} m³/s`;
  // rodada A3: link "ver cheia e saúde →"; nenhum texto do evento de outubro nem número além da leitura
  const numerosFaixa = (r.faixa.match(/\d[\d.,:/]*/g) || []).length;
  const fOk = r.faixa.includes(esperado) && r.provisorio && /não é dado em tempo real/.test(r.faixa) && !/pico|duração|posição|retorno|recorde|outubro|evento/i.test(r.faixa) &&
    r.linkCheia === "dashboards/cheia-saude.html" && r.faixa.endsWith("ver cheia e saúde →") && numerosFaixa === NUMEROS_FAIXA(rio);
  res.push({ nome: "início: faixa do rio com a última leitura da telemetria, rótulo \"provisório\", \"não é dado em tempo real\" e link \"ver cheia e saúde →\"; sem pico, tempo de retorno nem evento", ok: fOk, detalhe: r.faixa });
  // contagens do gerador
  const c = resumo.meta.contagens;
  const cOk = r.contagens.some((x) => x.includes(`${c.telas} telas`) && x.includes(`${c.conjuntos_dados} conjuntos de dados`) && x.includes(`${c.fontes} fontes`)) && r.contagens.some((x) => x.includes(`${c.grupos_camadas} grupos de camadas`));
  res.push({ nome: `início: contagens do gerador (${c.telas} telas, ${c.conjuntos_dados} conjuntos, ${c.fontes} fontes, ${c.grupos_camadas} grupos de camadas)`, ok: cOk, detalhe: JSON.stringify(r.contagens) });
  // links internos respondem 200; os do projeto abrem em nova aba
  const l = await linksRespondem(page, "a[href]");
  res.push({ nome: `início: ${l.n} links internos respondem 200; ${tx.projeto.links.length} links do projeto em nova aba`, ok: !l.ruins.length && r.projeto.length === tx.projeto.links.length && r.projeto.every(([, alvo]) => alvo === "_blank"), detalhe: l.ruins.join(" | ") });
  await ctx.close();
  // nenhum número no HTML-fonte (fora dos <script>)
  const html = fs.readFileSync(path.join(RAIZ, "index.html"), "utf8").replace(/<script\b[\s\S]*?<\/script>|<!--[\s\S]*?-->/g, "");
  const nums = html.replace(/<[^>]+>/g, " ").match(/\d+/g) || [];
  res.push({ nome: "início: nenhum número no HTML-fonte (fora dos scripts)", ok: !nums.length, detalhe: JSON.stringify(nums) });
  return res;
}

async function testesExtrasCatalogo(browser, base, prod) {
  const res = [];
  const cat = prod.catalogo;
  const { ctx, page, erros } = await abrir(browser, base + "dashboards/catalogo.html", {});
  const r = await page.evaluate(() => ({ n: document.querySelectorAll("#tabelaCatalogo tbody tr").length,
    fontes: [...document.querySelectorAll("#tabelaCatalogo .catalogo-fonte")].map((e) => e.textContent),
    botoes: [...document.querySelectorAll("#seletor-dimensao button")].map((b) => b.textContent),
    supressao: document.getElementById("como-ler-supressao").textContent, atual: (document.querySelector('#cabecalho-nav [aria-current="page"]') || {}).textContent }));
  const ok1 = r.n === cat.dados.length && JSON.stringify(r.fontes) === JSON.stringify(cat.dados.map((x) => x.fonte)) &&
    JSON.stringify(r.botoes) === JSON.stringify(["Todas", ...cat.meta.dimensoes]) && r.supressao.includes(`"<${cat.meta.limiar_supressao}"`) && r.atual === "Dados";
  res.push({ nome: `catálogo: ${cat.dados.length} linhas iguais a catalogo_dados.json, filtro com ${cat.meta.dimensoes.length} dimensões, "Como ler" com o limiar, "Dados" marcado no menu`, ok: ok1 && erros.length === 0, detalhe: JSON.stringify({ ...r, fontes: r.fontes.length }) + erros.join(" | ") });
  // filtro: cada dimensão mostra só as linhas dela
  const difs = [];
  for (const d of cat.meta.dimensoes) {
    await page.click(`#seletor-dimensao button[data-dim="${d}"]`);
    const vis = await page.evaluate(() => [...document.querySelectorAll("#tabelaCatalogo tbody tr")].filter((tr) => !tr.hidden).length);
    const esp = cat.dados.filter((x) => x.dimensoes.split(" · ").includes(d)).length;
    if (vis !== esp) difs.push(`${d}: ${vis} ≠ ${esp}`);
  }
  res.push({ nome: "catálogo: filtro por dimensão mostra só as linhas da dimensão", ok: !difs.length, detalhe: difs.join(" | ") });
  // downloads e links respondem 200
  await page.click('#seletor-dimensao button[data-dim=""]');
  const l = await linksRespondem(page, "#tabelaCatalogo a[href], #rodape-comum a[href]");
  res.push({ nome: `catálogo: ${l.n} links e downloads respondem 200`, ok: !l.ruins.length, detalhe: l.ruins.join(" | ") });
  await ctx.close();
  return res;
}

// "Dados desta tela" no rodapé (rodada A2): um item por produto da tela, CSV e JSON respondendo 200
async function testeDadosDaTela(browser, base, def) {
  const { ctx, page, erros } = await abrir(browser, base + def.pagina, {});
  await page.waitForSelector("#dados-tela summary", { timeout: 10000 }).catch(() => null);
  const tela = await page.evaluate(() => document.documentElement.dataset.tela);
  const r = await page.evaluate(() => ({ itens: [...document.querySelectorAll("#dados-tela .lista-dados-tela > li")].map((li) => ({
    titulo: li.querySelector(".dado-titulo").textContent, links: [...li.querySelectorAll(".dado-baixar a")].map((a) => a.textContent),
    sobre: [...li.querySelectorAll(".sobre-dado dt")].map((e) => e.textContent) })) }));
  const man = MANIFESTO();
  const esperados = man.produtos.filter((i) => (i.telas || []).includes(tela) || Object.values(def.produtos).includes(i.id));
  const l = await linksRespondem(page, "#dados-tela a[href]");
  await ctx.close();
  const ok = r.itens.length === esperados.length && r.itens.every((x) => x.links.includes("Baixar JSON") && x.sobre.includes("Licença") && x.sobre.includes("Método")) &&
    esperados.every((i) => r.itens.some((x) => x.titulo === i.titulo) && (!i.csv || r.itens.find((x) => x.titulo === i.titulo).links.includes("Baixar CSV"))) && !l.ruins.length && erros.length === 0;
  return { ok, n: r.itens.length, links: l.n, detalhe: JSON.stringify({ itens: r.itens.length, esperados: esperados.length, ruins: l.ruins }) };
}

// itens do menu, na ordem (rodada A2)
const MENU = ["Início", "Mapa", "Cheia e saúde", "Clima", "Saúde", "Mais temas", "População", "Território", "Economia", "Riscos", "Dados", "ClimaPampa ↗"];

// endereços que redirecionam (rodada A2): síntese incorporada ao início; pasta dashboards/ leva ao início
const REDIRECIONAMENTOS = [
  ["index.html?cota=952#x", "/mapa.html?cota=952#x"],
  ["index.html?origem=t", "/index.html?origem=t"],
  ["index.html#x", "/index.html#x"],
  ["dashboards/sintese.html?cota=952&origem=r#x", "/dashboards/cheia-saude.html?cota=952&origem=r#x"],
  ["dashboards/sintese.html?origem=r", "/index.html?origem=r"],
  ["dashboards/sintese.html?cota=950", "/index.html"],
  ["dashboards/index.html?origem=redirecionamento", "/index.html?origem=redirecionamento"],
];
async function testeRedirecionamentosA2(browser, base) {
  const res = [];
  for (const [de, para] of REDIRECIONAMENTOS) {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto(base + de, { waitUntil: "networkidle" });
    await page.waitForTimeout(300);
    const u = new URL(page.url());
    await ctx.close();
    // a tela de destino pode limpar só os filtros que ela trata; aqui os parâmetros são neutros ou válidos
    // mesma página, mesmo # e os mesmos parâmetros (a ordem pode mudar: a tela regrava a busca ao ler os filtros)
    const esp = new URL(para, base), norm = (x) => [...x.searchParams].map((kv) => kv.join("=")).sort().join("&");
    const obtido = u.pathname + u.search + u.hash;
    res.push({ nome: `${de} → ${para}`, ok: u.pathname === esp.pathname && u.hash === esp.hash && norm(u) === norm(esp), detalhe: obtido });
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
    const im = testeIdsNoManifesto(def);
    console.log(`[${im.ok ? "ok" : "FALHA"}] ${im.n} ids da página e do teste existem no manifesto`);
    if (!im.ok) { total++; console.log(`    - ${im.detalhe}`); }
    const ia = await testeIdAntigo(browser, base, def);
    if (ia) {
      console.log(`[${ia.ok ? "ok" : "FALHA"}] ${ia.n} id(s) antigo(s) resolvem pelo "substitui"; id inexistente vira erro no bloco`);
      if (!ia.ok) { total++; console.log(`    - ${ia.detalhe}`); }
    }
    const dt = await testeDadosDaTela(browser, base, def);
    console.log(`[${dt.ok ? "ok" : "FALHA"}] "Dados desta tela": ${dt.n} produtos com CSV/JSON e "Sobre este dado"; ${dt.links} downloads respondem 200`);
    if (!dt.ok) { total++; console.log(`    - ${dt.detalhe}`); }
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
  if (!nomes.length) {
    console.log("\n== redirecionamentos");
    for (const x of await testeRedirecionamentosA2(browser, base)) {
      console.log(`[${x.ok ? "ok" : "FALHA"}] ${x.nome}`);
      if (!x.ok) { total++; console.log(`    - ${x.detalhe}`); }
    }
  }
  await browser.close();
  server.close();
  console.log(`\nscreenshots: ${saida}`);
  console.log(total ? `\n${total} falha(s)` : "\ntodas as verificações passaram");
  process.exit(total ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
