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
    recolhiveis: ["det-meio"],
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
    recolhiveis: [], extras: async (browser, base, prod) => testeSuprimidoSetor(browser, base, prod),
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
      const c = await page.evaluate(([id, i, n, tipo]) => {
        const rects = document.querySelectorAll(`#chart-${id} svg rect[fill="transparent"]`);
        const alvo = tipo > 1 ? rects[i] : rects[0];
        if (i === 0 || tipo > 1) alvo.scrollIntoView({ block: "center" });
        const b = alvo.getBoundingClientRect();
        if (tipo > 1) return { x: b.left + b.width / 2, y: b.top + b.height * 0.9 };
        // eixo em escala real de tempo: posição pelo ano do ponto
        if (alvo.dataset.anos) {
          const anos = alvo.dataset.anos.split(",").map(Number), mn = +alvo.dataset.min, mx = +alvo.dataset.max;
          return { x: b.left + (b.width * (anos[i] - mn)) / (mx - mn), y: b.top + b.height / 2 };
        }
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
  return { ok: r.erroBloco > 0 && r.svgs > 0 && erros.length === 0, ...r, errosDePagina: erros.length };
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
    const ok = r.ultimaCnes === "4126947" && /fora das comparações/.test(r.classe) && r.numeros.every((x) => !/\d/.test(x)) && r.n === 22 && !r.palavraCobertura && r.mapa === "../index.html?camada=unidades&unidade=4126947";
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
