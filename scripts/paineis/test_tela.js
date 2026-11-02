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
const dec1 = (n) => n.toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const DIA = 864e5;
const diaNum = (iso) => Math.round(Date.parse(iso + "T00:00:00Z") / DIA);
const diaTxt = (n) => { const d = new Date(n * DIA); return `${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}/${String(d.getUTCFullYear()).slice(2)}`; };
// regra das frases do tempo de retorno (B.1 da rodada 115): camada e documento diferem menos que o limite do JSON → "cerca de N anos"
const trNaFrase = (r, lim) => {
  const c = r.tr_anos_camada, d = r.tr_anos_documento;
  return d != null && c !== d && Math.abs(c - d) < lim ? `cerca de ${fmtInt(Math.round((c + d) / 2))} anos` : `${dec1(c)} anos`;
};
const dataBR = (iso) => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || ""); return m ? `${m[3]}/${m[2]}/${m[1]}` : iso || ""; };

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
  populacao: {
    pagina: "dashboards/populacao.html", enderecoAntigo: "dashboards/painel-demografia.html",
    produtos: { serie: "populacao_ibge_1970-2025_anual", ind: "demografia_ibge_1970-2022_indicadores", pir: "demografia_ibge_2000-2010-2022_piramides" },
    recolhiveis: ["det-piramides"], extras: async (browser, base, prod) => testePeriodoRecolhivel(browser, base, "dashboards/populacao.html", { "det-piramides": false }),
    kpisCompletos: true,
    kpis: (p) => kpisTexto(p.serie.meta.kpis),
    graficos: (p) => {
      const fi = p.ind.meta.fonte_curta, fp = p.pir.meta.fonte_curta, I = p.ind.dados;
      const tl = (campo, nome, fmt) => I.map((r) => [r.periodo, [[fmt(r[campo]), `${nome} · ${fi}`]]]);
      const g = {
        populacao: p.serie.dados.map((r) => [r.ano, [[fmtInt(r.populacao), `População total · ${p.serie.meta.fonte_curta}`]]]),
        envelhecimento: tl("indice_envelhecimento", "Índice de envelhecimento", fmt1),
        dependencia: tl("razao_dependencia", "Razão de dependência", fmt1),
        "urbano-rural": I.map((r) => [r.periodo, [[fmt1(r.pct_urbano) + "%", `Urbana · ${fi}`], [fmt1(r.pct_rural) + "%", `Rural · ${fi}`]]]),
        "criancas-abs": tl("pop_0_4", "Crianças 0-4", fmtInt), "criancas-pct": tl("pct_pop_0_4", "Crianças 0-4 (%)", (v) => fmt1(v) + "%"),
        "idosos-abs": tl("pop_60_mais", "Idosos 60+", fmtInt), "idosos-pct": tl("pct_pop_60_mais", "Idosos 60+ (%)", (v) => fmt1(v) + "%"),
      };
      for (const ano of [...new Set(p.pir.dados.map((r) => r.ano))]) {
        g[`piramide-${ano}`] = p.pir.dados.filter((r) => r.ano === ano).reverse().map((r) => [r.grupo_idade, [[fmtInt(r.homens), `Homens · ${fp}`], [fmtInt(r.mulheres), `Mulheres · ${fp}`]]]);
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
    produtos: { serie: "uso-solo_mapbiomas_1985-2024_classes", classes: "uso-solo_mapbiomas_2024_todas-classes", meio: "meio-fisico_ibge-bdia_atual_classes",
                pocos: "pocos_siagas_atual_aquifero", app: "app-hidrica_ana-bho-mapbiomas_2024_curso", urb: "areas-urbanizadas_ibge_2019_classe" },
    recolhiveis: ["det-uso", "det-meio"],
    extras: async (browser, base, prod) => testePeriodoRecolhivel(browser, base, "dashboards/territorio.html", { "det-uso": true, "det-meio": false }),
    kpisCompletos: true,
    kpis: (p) => kpisTexto([...p.serie.meta.kpis_uso, ...p.serie.meta.kpis_meio]),
    graficos: (p) => {
      const S = p.serie.dados, fs = p.serie.meta.fonte_curta, fm = p.meio.meta.fonte_curta;
      const uso = (classe, nome, fmt) => S.map((r) => [r.ano, [[fmt(r[classe]), `${nome} · ${fs}`]]]);
      const tema = (t) => p.meio.dados.filter((r) => r.tema === t).map((r) => [r.rotulo, [[fmtInt(r.area_km2), `km² · ${fm}`]]]);
      return {
        campestre: uso("Formação Campestre", "Formação Campestre", fmtInt), arroz: uso("Arroz", "Arroz", fmtInt), urbano: uso("Área Urbanizada", "Área urbanizada", fmt1),
        mosaico: uso("Mosaico de Usos", "Mosaico de Usos", fmtInt), "outras-lavouras": uso("Outras Lavouras Temporárias", "Outras Lavouras Temporárias", fmtInt),
        florestal: uso("Formação Florestal", "Formação Florestal", fmtInt),
        "uso-solo-2024": p.classes.dados.map((r) => [r.classe, [[fmtInt(r.area_km2) + " km²", `km² · ${p.classes.meta.fonte_curta}`]]]),
        geologia: tema("geologia"), geomorfologia: tema("geomorfologia"), "vegetacao-bdia": tema("vegetacao"), pedologia: tema("pedologia_grafico"),
        "pocos-aquifero": p.pocos.dados.map((r) => [r.aquifero, [[fmtInt(r.pocos), `poços · ${p.pocos.meta.fonte_curta}`]]]),
        "app-curso": p.app.dados.map((r) => [r.rotulo, [[fmtInt(r.area_km2) + " km²", `km² · ${p.app.meta.fonte_curta}`]]]),
        urbanizadas: p.urb.dados.map((r) => [r.tipo === r.densidade ? r.tipo : `${r.tipo} (${r.densidade.toLowerCase()})`, [[fmt1(r.area_km2) + " km²", `km² · ${p.urb.meta.fonte_curta}`]]]),
      };
    },
    tabelas: (p) => ({
      tabelaUso: p.serie.dados.map((r) => [String(r.ano), fmt1(r["Formação Campestre"]), fmt1(r["Arroz"]), fmt1(r["Área Urbanizada"])]),
      tabelaPedologia: p.meio.dados.filter((r) => r.tema === "pedologia").map((r) => [r.classe, fmt1(r.area_km2), fmtInt(r.n_feicoes)]),
    }),
  },
  economia: {
    pagina: "dashboards/economia.html",
    produtos: { censo: "censo_ibge_2022_situacao-setor", pib: "pib_ibge_2002-2023_anual", pam: "agropecuaria_ibge_1974-2024_anual", caged: "emprego_caged_2020-2026_mensal",
                rais: "emprego_rais_2025_secao", idhm: "idhm_atlas_1991-2010_censos", cadunico: "cadunico_mds_2012-2026_anual", siconfi: "despesas_siconfi_2015-2026_anual" },
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
        "pop-situacao": p.censo.dados.map((r) => [r.situacao, [[fmtInt(r.populacao), `habitantes · ${f("censo")}`]]]),
        "pib-per-capita": ser(pc, (r) => r.ano, [["pib_per_capita", "PIB per capita", (v) => "R$ " + fmtInt(v), "pib"]]),
        "pib-composicao": ser(cp, (r) => r.ano, [["vab_servicos_pct", "Serviços"], ["vab_administracao_publica_pct", "Adm. pública"], ["vab_agropecuaria_pct", "Agropecuária"], ["vab_industria_pct", "Indústria"]].map(([c, n]) => [c, n, (v) => fmt1(v) + "%", "pib"])),
        "pam-area": ser(p.pam.dados, (r) => r.ano, [["arroz_area_colhida_ha", "Arroz", (v) => fmtInt(v) + " ha", "pam"], ["soja_area_colhida_ha", "Soja", (v) => fmtInt(v) + " ha", "pam"]]),
        "ppm-rebanho": ser(p.pam.dados, (r) => r.ano, [["bovino_cabecas", "Bovino", fmtInt, "pam"], ["ovino_cabecas", "Ovino", fmtInt, "pam"]]),
        "caged-fluxo": ser(p.caged.dados, (r) => r.rotulo, [["admissoes", "Admissões", fmtInt, "caged"], ["desligamentos", "Desligamentos", fmtInt, "caged"]]),
        "caged-saldo": ser(p.caged.dados, (r) => r.rotulo, [["saldo_acumulado", "Saldo acumulado", fmtInt, "caged"]]),
        "rais-setor": p.rais.meta.grafico.map((r) => [r.rotulo, [[fmtInt(r.vinculos), `vínculos · ${f("rais")}`]]]),
        "idhm-componentes": ser(p.idhm.dados, (r) => r.ano, [["idhm", "IDHM"], ["idhm_longevidade", "Longevidade"], ["idhm_renda", "Renda"], ["idhm_educacao", "Educação"]].map(([c, n]) => [c, n, fmt3, "idhm"])),
        "idhm-brasil": ser(p.idhm.dados, (r) => r.ano, [["idhm", "Uruguaiana", fmt3, "idhm"], ["idhm_brasil", "Brasil", fmt3, "idhm"]]),
        "cadunico-familias": ser(p.cadunico.dados, (r) => r.rotulo, [["familias_cadastradas", "Famílias cadastradas", fmtInt, "cadunico"]]),
        "cadunico-taxa": ser(p.cadunico.dados, (r) => r.rotulo, [["taxa_atualizacao_pct", "Taxa de atualização", (v) => fmt1(v) + "%", "cadunico"]]),
        "siconfi-saude-pc": ser(sf, (r) => r.ano, [["saude_per_capita_reais", "Saúde per capita", (v) => "R$ " + fmtInt(v), "siconfi"]]),
        "siconfi-assist-pc": ser(sf, (r) => r.ano, [["assistencia_social_per_capita_reais", "Assistência Social per capita", (v) => "R$ " + fmt1(v), "siconfi"]]),
        "siconfi-pct-pib": ser(sp, (r) => r.ano, [["saude_pct_pib", "Saúde — % do PIB", (v) => fmt1(v) + "%", "siconfi"]]),
      };
    },
    tabelas: (p) => ({ tabelaRais: p.rais.dados.map((r) => [r.rotulo, fmtInt(r.vinculos), fmt1((100 * r.vinculos) / p.rais.meta.total) + "%"]) }),
  },
  riscos: {
    pagina: "dashboards/riscos.html",
    produtos: { s2id: "desastres_s2id_2014-2025_anual", cprm: "risco-geologico_cprm_2021_classe", aneel: "energia_aneel_2000-2026_anual",
                setor: "setorizacao-risco_sgb_2014_setor", bater: "areas-de-risco_ibge_2018_bater", indisp: "infraestrutura_indisponivel_atual" },
    recolhiveis: ["det-setorizacao", "det-indisponivel"], extras: async (browser, base, prod) => testeSuprimidoSetor(browser, base, prod),
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
  sintese: {
    pagina: "dashboards/sintese.html",
    enderecoAntigo: "dashboards/index.html",
    semGraficos: true,
    produtos: { rio: "rio_ana-77150000_ultima-leitura", cotas: "inundacao_sgb_cotas", precip: "clima_precipitacao-merge_2010-2025_anual",
                inmet: "clima_inmet-a809_2007-2025_anual", sim: "saude_sim_2010-2024_anual", sih: "saude_sih_2010-2025_anual",
                sinan: "saude_sinan_2010-2026_anual", ind: "demografia_ibge_1970-2022_indicadores" },
    recolhiveis: [],
    kpis: () => [], graficos: () => ({}), tabelas: () => ({}),
    extras: async (browser, base, prod) => testesExtrasSintese(browser, base, prod),
  },
  "cheia-saude": {
    pagina: "dashboards/cheia-saude.html",
    produtos: { maximas: "rio_ana-77150000_maximas-anuais", diario: "rio_ana-77150000_ultimos-365-dias_diario", cotas: "inundacao_sgb_cotas",
                unidades: "inundacao_sgb_unidades-cotas", setor: "setorizacao-risco_sgb_2014_setor", bater: "areas-de-risco_ibge_2018_bater" },
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
    document.querySelectorAll('[id^="chart-"] svg').forEach((svg) => {
      const h = svg.dataset.alturaEixo ? +svg.dataset.alturaEixo : +svg.getAttribute("height");
      const cx = Array.from(svg.querySelectorAll("text.axis-label, text.axis-x")).filter((t) => Math.abs(+t.getAttribute("y") - (h - 6)) < 3)
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
      kpisCompletos: Array.from(document.querySelectorAll(".kpi-tile")).map((e) => e.innerText.replace(/\s+/g, " ").trim()),
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
  const kObtido = def.kpisCompletos ? r.kpisCompletos : r.kpis;
  if (JSON.stringify(kObtido) !== JSON.stringify(kEsp)) falhas.push(`indicadores ${JSON.stringify(kObtido)} ≠ ${JSON.stringify(kEsp)}`);
  for (const [t, linhas] of Object.entries(def.tabelas(prod))) {
    if (JSON.stringify(r.tabelas[t]) !== JSON.stringify(linhas)) falhas.push(`tabela ${t} difere do JSON`);
  }
  for (const [g, rot] of Object.entries(def.rotulosFinais ? def.rotulosFinais(prod) : {})) {
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
  const r = await page.evaluate(() => ({ erroBloco: document.querySelectorAll(".erro-bloco").length, svgs: document.querySelectorAll('[id^="chart-"] svg').length }));
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
    const ok = r.ultimaCnes === "4126947" && /fora das comparações/.test(r.classe) && r.numeros.every((x) => !/\d/.test(x)) && r.n === 22 && !r.palavraCobertura && r.mapa === "../index.html?camada=unidadesSaude&unidade=4126947";
    res.push({ nome: "Prisional na última linha, sem números; 22 linhas; sem a palavra \"cobertura\"; link do mapa", ok, detalhe: JSON.stringify(r) });
    // o link do mapa abre o portal mesmo sem o portal ler o parâmetro
    const resp = await page.request.get(new URL(r.mapa, page.url()).toString());
    res.push({ nome: "\"ver no mapa\" abre o portal", ok: resp.status() === 200, detalhe: `HTTP ${resp.status()}` });
    await ctx.close();
  }
  return res;
}

// ?periodo= válido abre os blocos recolhidos que ele afeta (a 390 px); os outros continuam fechados
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

// ------------------------------------------------------------------ extras da síntese
// JSON da última leitura simulado num dos três estados: (i) normal e recente, (ii) evento em curso, (iii) leitura antiga
function leituraSimulada(meta, estado) {
  const rt = { tipo: "metodo", texto: "não é dado em tempo real; atualizado em 20/10" };
  if (estado === "evento") return { ...meta, evento_em_curso: true, avisos: [{ tipo: "evento_em_curso", texto: "cheia de outubro de 2026: números em preparação" }, rt],
    dados: [{ estado: "evento_em_curso", data_hora: null, nivel_cm: null, vazao_m3s: null, idade_dias: null }] };
  const idade = estado === "antiga" ? 5 : 1;
  return { ...meta, evento_em_curso: false, avisos: [rt, ...(estado === "antiga" ? [{ tipo: "parcial", texto: "leitura antiga" }] : [])],
    dados: [{ estado: "normal", data_hora: "2026-10-20 18:00:00", nivel_cm: 512.0, vazao_m3s: 5400.0, idade_dias: idade }] };
}

async function testesExtrasSintese(browser, base, prod) {
  const res = [];
  const pag = base + "dashboards/sintese.html";
  {
    const { ctx, page, erros } = await abrir(browser, pag, {});
    const r = await page.evaluate(() => {
      const li = (id) => [...document.querySelectorAll(`#${id} li`)].map((e) => e.innerText.replace(/\s+/g, " ").trim());
      return { faixa: document.getElementById("faixa-rio").innerText.trim(), cheia: li("lista-cheia"), clima: li("lista-clima"), saude: li("lista-saude"),
        contexto: li("lista-contexto"), atualizacao: li("lista-atualizacao"), aviso: document.getElementById("aviso-cheia").innerText.trim(),
        emBreve: /em breve/i.test(document.getElementById("cabecalho-comum").innerText), marca: document.querySelector(".cabecalho-marca").getAttribute("href"),
        nav: [...document.querySelectorAll(".cabecalho-nav a.nav-item")].map((a) => a.getAttribute("href")) };
    });
    const p = prod, ult = (x) => x.dados[x.dados.length - 1];
    const ordTr = [...p.cotas.dados].sort((a, b) => a.tr_anos_camada - b.tr_anos_camada);
    const trS = (r) => trNaFrase(r, p.cotas.meta.dif_tr_cerca_anos).replace(/ anos$/, "");
    const inm = ult(p.inmet), parcial = p.inmet.meta.avisos.find((a) => a.tipo === "parcial" && a.onde && a.onde.ano === inm.ano);
    const fech = p.sinan.dados.filter((x) => !x.preliminar), sn = fech[fech.length - 1], pre = p.sinan.dados.find((x) => x.preliminar);
    const ind = ult(p.ind);
    const fr = p.rio.dados[0];
    const esperado = {
      faixa: fr.estado === "evento_em_curso" ? `⚠ Rio Uruguai (${p.rio.meta.fonte_curta}): ${p.rio.meta.avisos.map((a) => a.texto).join(" · ")}` : null,
      cheia: [`${p.cotas.dados.length} manchas do SGB (cotas ${p.cotas.dados.map((x) => fmtInt(x.cota_cm)).join(", ")} cm), tempo de retorno de ${trS(ordTr[0])} a ${trS(ordTr[ordTr.length - 1])} anos`,
              p.cotas.meta.notas.cs_frase_unidades],
      clima: [`Chuva em ${ult(p.precip).ano}: ${fmtInt(ult(p.precip).merge_mm)} mm (${p.precip.meta.fonte_curta})`,
              `Dias com máxima ≥ 35 °C em ${inm.ano}: ${fmtInt(inm.dias_calor_extremo)} (${p.inmet.meta.fonte_curta})${parcial ? ` · ${parcial.texto}` : ""}`],
      saude: [`Óbitos em ${ult(p.sim).ano}: ${fmtInt(ult(p.sim).obitos_total)}, dos quais ${fmtInt(ult(p.sim).obitos_respiratorios)} por causas respiratórias (${p.sim.meta.fonte_curta})`,
              `Internações respiratórias em ${ult(p.sih).ano}: ${fmtInt(ult(p.sih).internacoes_respiratorias)} (${p.sih.meta.fonte_curta})`,
              `Notificações de dengue em ${sn.ano}: ${fmtInt(sn.dengue)}${pre ? `; em ${pre.ano} (preliminar): ${fmtInt(pre.dengue)}` : ""} (${p.sinan.meta.fonte_curta})`],
      contexto: [`População (Censo ${ind.periodo}): ${fmtInt(ind.populacao_total)} habitantes`, `${fmt1(ind.pct_urbano)}% urbana · índice de envelhecimento ${fmt1(ind.indice_envelhecimento)}`],
    };
    const dif = Object.entries(esperado).filter(([k, v]) => v !== null && JSON.stringify(r[k]) !== JSON.stringify(v)).map(([k, v]) => `${k}: ${JSON.stringify(r[k])} ≠ ${JSON.stringify(v)}`);
    res.push({ nome: "síntese: faixa do rio e cartões iguais aos JSON", ok: !dif.length && erros.length === 0, detalhe: dif.join(" | ") + erros.join(" | ") });
    const datas = Object.values(prod).every((x) => r.atualizacao.some((l) => l.endsWith(x.meta.dados_ate.length === 10 ? dataBR(x.meta.dados_ate) : x.meta.dados_ate)));
    res.push({ nome: "síntese: datas de atualização de cada produto; aviso \"em conferência\"", ok: datas && r.atualizacao.length === Object.keys(prod).length && r.aviso === "pessoas expostas e acesso: em conferência", detalhe: JSON.stringify(r.atualizacao) });
    res.push({ nome: "cabeçalho: Síntese e Cheia e saúde ativos, marca leva à síntese, nenhum \"em breve\"", ok: !r.emBreve && r.marca === "sintese.html" && r.nav.includes("sintese.html") && r.nav.includes("cheia-saude.html"), detalhe: JSON.stringify(r) });
    await ctx.close();
  }
  // leituras simuladas na faixa "rio agora"
  for (const [estado, nome] of [["normal", "(i) abaixo de 800 cm e recente"], ["evento", "(ii) evento em curso"], ["antiga", "(iii) leitura com mais de 3 dias"]]) {
    const j = leituraSimulada(prod.rio.meta, estado);
    const rotas = [[`**/data/paineis/${prod.rio.meta.id}.json`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(j) })]];
    const { ctx, page, erros } = await abrir(browser, pag, { rotas });
    const f = await page.evaluate(() => ({ texto: document.getElementById("faixa-rio").innerText.trim(), evento: document.getElementById("faixa-rio").classList.contains("faixa-evento") }));
    await ctx.close();
    let ok;
    if (estado === "evento") ok = f.evento && /números em preparação/.test(f.texto) && !/\bcm\b|m³\/s|pico/.test(f.texto);
    else ok = !f.evento && f.texto.includes("512 cm em 20/10/2026 18:00") && f.texto.includes("vazão 5.400 m³/s") && f.texto.includes("não é dado em tempo real; atualizado em 20/10") && (estado === "antiga") === f.texto.includes("leitura antiga");
    res.push({ nome: `faixa do rio com leitura simulada ${nome}`, ok: ok && erros.length === 0, detalhe: f.texto });
  }
  {
    const a = await abrir(browser, `${pag}?cota=952`, {});
    const r1 = await a.page.evaluate(() => ({ chip: document.getElementById("chips-filtro").innerText, link: document.querySelector('#cartao-cheia a[href^="cheia-saude"]').getAttribute("href"),
      mapa: document.querySelector('#cartao-cheia a[href^="../index"]').getAttribute("href") }));
    await a.ctx.close();
    const b = await abrir(browser, `${pag}?cota=950`, {});
    const r2 = await b.page.evaluate(() => ({ avisos: [...document.querySelectorAll(".cabecalho-avisos .aviso")].map((e) => e.textContent), url: location.search, chip: document.getElementById("chips-filtro").innerText }));
    await b.ctx.close();
    const ok = /Cota: 952 cm/.test(r1.chip) && r1.link === "cheia-saude.html?cota=952" && r1.mapa === "../index.html?camada=cotasInundacao,unidadesSaude&cota=952" &&
      r2.avisos.includes("Parâmetro ignorado: cota=950") && r2.url === "" && r2.chip === "";
    res.push({ nome: "síntese: ?cota= válido vira chip e segue nos links; inválido gera aviso e sai da URL", ok, detalhe: JSON.stringify({ r1, r2 }) });
  }
  return res;
}

// ------------------------------------------------------------------ extras da cheia e saúde
async function testesExtrasCheia(browser, base, prod) {
  const res = [];
  const pag = base + "dashboards/cheia-saude.html";
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
      JSON.stringify(r.ativa) === '["952"]' && /Cota: 952 cm/.test(r.chip) && r.link === "../index.html?camada=cotasInundacao,unidadesSaude&cota=952" && r.filtrosAbertos === (largura !== 390) && new Set(r.refAtiva).size === 2;
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
