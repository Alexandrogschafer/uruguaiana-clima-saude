/**
 * Estado do portal na URL, com os mesmos nomes de parâmetro dos painéis (dashboards/):
 *
 *   cota     cota de inundação em cm, validada contra estatisticas-por-cota.json (cotas_disponiveis_cm);
 *            liga a mancha e põe o controle deslizante na cota
 *   unidade  CNES de 7 dígitos, validado contra a camada das unidades; centraliza e abre o popup
 *   camada   lista separada por vírgula de chaves reais de camada (ver CAMADAS_URL abaixo); liga cada uma.
 *            Chave desconhecida sai da lista
 *   periodo  AAAA, AAAA-AAAA ou AAAA-MM: só o uso do solo tem controle de ano no portal; o ano (o último
 *            disponível dentro da faixa) vai para o controle e a camada é ligada
 *   bairro   CD_BAIRRO do IBGE (10 dígitos), validado contra bairros.geojson; liga os bairros, aproxima e
 *            destaca o bairro. "municipio" é aceito e não faz nada
 *   cenario  ignorado (os cenários das pontes coincidem)
 *
 * Valor inválido: é ignorado, sai da URL e aparece num aviso discreto, que pode ser fechado.
 * O estado volta para a URL com history.replaceState quando o usuário muda a cota, liga ou desliga uma
 * camada, muda o ano do uso do solo ou abre o popup de uma unidade. Em `camada` vão só as camadas ligadas
 * além das que já abrem ligadas. Nenhum código de município fica neste arquivo: tudo vem dos GeoJSON/JSON.
 */

(function () {
  const App = window.App;

  // chaves de camada aceitas em ?camada= : as de window.App.layers (layers.js) e as dos scripts de cada
  // camada carregada sob demanda (meio-fisico.js, uso-solo-timeline.js, terreno.js, cobertura-movel.js,
  // densidade-edificacoes.js). O limite municipal fica sempre ligado e as bacias têm seletor de nível: não entram.
  function montarCamadasUrl() {
    const caixa = (id) => document.getElementById(id);
    const porCaixa = (id) => ({
      ligado: () => !!caixa(id) && caixa(id).checked,
      ligar: () => { if (caixa(id) && !caixa(id).checked) caixa(id).click(); },
    });
    const anoDensidade = (ano) => ({
      ligado: () => caixa("checkbox-densidade-populacional").checked &&
        (document.querySelector('input[name="ano-densidade"]:checked') || {}).value === ano,
      ligar: () => {
        const radio = document.querySelector(`input[name="ano-densidade"][value="${ano}"]`);
        if (radio && !radio.checked) radio.click();
        if (!caixa("checkbox-densidade-populacional").checked) caixa("checkbox-densidade-populacional").click();
      },
    });
    const porFiltro = (idTodos, idLista) => ({
      ligado: () => Array.from(document.querySelectorAll(`#${idLista} input[type=checkbox]`)).some((el) => el.checked),
      ligar: () => { if (!caixa(idTodos).checked) caixa(idTodos).click(); },
    });

    const camadas = {
      densidadePopulacional: anoDensidade("2022"),
      densidadePopulacional2010: anoDensidade("2010"),
      densidadePopulacional2000: anoDensidade("2000"),
      unidadesSaude: porFiltro("checkbox-saude-todos", "filtro-tipo-saude"),
      escolasInep: porFiltro("checkbox-escolas-todos", "filtro-tipo-escola"),
    };
    // caixas montadas por montarTogglesCamadas (layers.js), marcadas com data-camada = chave em App.layers
    document.querySelectorAll("input[data-camada]").forEach((el) => {
      camadas[el.dataset.camada] = porCaixa(el.id);
    });
    ["geologia", "geomorfologia", "pedologia", "vegetacao", "pocosSiagas", "appHidrica"].forEach((chave) => {
      if (caixa(`checkbox-meio-fisico-${chave}`)) camadas[chave] = porCaixa(`checkbox-meio-fisico-${chave}`);
    });
    Object.entries({
      usoSolo: "checkbox-uso-solo",
      hipsometria: "checkbox-hipsometria",
      hillshade: "checkbox-hillshade",
      coberturaMovel: "checkbox-cobertura-movel",
      densidadeEdificacoes: "checkbox-densidade-edificacoes",
    }).forEach(([chave, id]) => { if (caixa(id)) camadas[chave] = porCaixa(id); });
    return camadas;
  }

  // ---------- URL ----------

  function lerParametros() {
    return new URLSearchParams(window.location.search);
  }

  function gravarParametros(mudancas) {
    const url = new URL(window.location.href);
    Object.entries(mudancas).forEach(([chave, valor]) => {
      if (valor === null || valor === undefined || valor === "") url.searchParams.delete(chave);
      else url.searchParams.set(chave, valor);
    });
    const busca = url.searchParams.toString().replace(/%2C/gi, ",");
    window.history.replaceState(window.history.state, "", `${url.pathname}${busca ? `?${busca}` : ""}${url.hash}`);
  }

  // ---------- aviso discreto ----------

  function mostrarAviso(ignorados) {
    if (!ignorados.length) return;
    let aviso = document.getElementById("aviso-url");
    if (!aviso) {
      aviso = document.createElement("div");
      aviso.id = "aviso-url";
      aviso.setAttribute("role", "status");
      aviso.style.cssText =
        "position:absolute;z-index:1000;bottom:28px;left:50%;transform:translateX(-50%);max-width:min(92vw,520px);" +
        "background:#fffbeb;color:#713f12;border:1px solid #f59e0b;border-radius:6px;padding:6px 8px 6px 12px;" +
        "font:13px/1.35 system-ui,sans-serif;box-shadow:0 2px 6px rgba(0,0,0,.18);display:flex;gap:10px;align-items:flex-start;";
      const texto = document.createElement("span");
      texto.className = "aviso-url-texto";
      const fechar = document.createElement("button");
      fechar.type = "button";
      fechar.textContent = "✕";
      fechar.setAttribute("aria-label", "Fechar aviso");
      fechar.style.cssText = "border:0;background:transparent;color:inherit;font-size:14px;cursor:pointer;padding:0 2px;line-height:1;";
      fechar.addEventListener("click", () => aviso.remove());
      aviso.append(texto, fechar);
      document.body.appendChild(aviso);
    }
    aviso.querySelector(".aviso-url-texto").textContent =
      `Parâmetro ignorado: ${ignorados.join(" · ")} (valor não encontrado no mapa).`;
  }

  // ---------- aplicação dos parâmetros ----------

  function anoDoPeriodo(periodo, anos) {
    let m = /^(\d{4})$/.exec(periodo) || /^(\d{4})-(0[1-9]|1[0-2])$/.exec(periodo);
    if (m) return anos.includes(Number(m[1])) ? Number(m[1]) : null;
    m = /^(\d{4})-(\d{4})$/.exec(periodo);
    if (!m || Number(m[1]) > Number(m[2])) return null;
    const dentro = anos.filter((a) => a >= Number(m[1]) && a <= Number(m[2]));
    return dentro.length ? Math.max(...dentro) : null;
  }

  function marcadorDaUnidade(cnes) {
    return App.layers.unidadesSaude.getLayers().find((l) => String(l.feature.properties.cnes) === cnes) || null;
  }

  function destacarBairro(codigo) {
    const camada = App.layers.bairros;
    const alvo = camada.getLayers().find((l) => String(l.feature.properties.CD_BAIRRO) === codigo);
    if (!alvo) return false;
    if (!App.map.hasLayer(camada)) {
      const caixa = document.querySelector('input[data-camada="bairros"]');
      if (caixa) caixa.click();
    }
    alvo.setStyle({ weight: 4, color: "#7c2d12", fill: true, fillColor: "#f59e0b", fillOpacity: 0.15 });
    App.map.fitBounds(alvo.getBounds(), { padding: [24, 24], maxZoom: 15 });
    return true;
  }

  async function iniciar() {
    const [infoCota, infoUsoSolo] = await Promise.all([App.cotaPronta, App.usoSoloPronto]);
    const camadas = montarCamadasUrl();
    const padrao = new Set(Object.keys(camadas).filter((k) => camadas[k].ligado()));
    const p = lerParametros();
    const ignorados = [];
    const limpar = {};

    // camada
    if (p.has("camada")) {
      const pedidas = p.get("camada").split(",").map((s) => s.trim()).filter(Boolean);
      const validas = pedidas.filter((k) => Object.prototype.hasOwnProperty.call(camadas, k));
      pedidas.filter((k) => !validas.includes(k)).forEach((k) => ignorados.push(`camada=${k}`));
      validas.forEach((k) => camadas[k].ligar());
      if (validas.length !== pedidas.length) limpar.camada = validas.join(",");
    }

    // cota
    if (p.has("cota")) {
      const cota = Number(p.get("cota"));
      if (/^\d+$/.test(p.get("cota")) && infoCota.cotas.includes(cota)) {
        if (camadas.cotasInundacao) camadas.cotasInundacao.ligar();
        infoCota.definir(cota);
      } else {
        ignorados.push(`cota=${p.get("cota")}`);
        limpar.cota = null;
      }
    }

    // periodo: só o uso do solo tem controle de ano
    if (p.has("periodo")) {
      const ano = anoDoPeriodo(p.get("periodo"), infoUsoSolo.anos);
      if (ano !== null) {
        if (camadas.usoSolo) camadas.usoSolo.ligar();
        infoUsoSolo.definirAno(ano);
      } else {
        ignorados.push(`periodo=${p.get("periodo")}`);
        limpar.periodo = null;
      }
    }

    // bairro
    if (p.has("bairro") && p.get("bairro") !== "municipio") {
      if (!(/^\d{10}$/.test(p.get("bairro")) && destacarBairro(p.get("bairro")))) {
        ignorados.push(`bairro=${p.get("bairro")}`);
        limpar.bairro = null;
      }
    }

    // unidade (por último: a vista e o popup da unidade prevalecem)
    if (p.has("unidade")) {
      const cnes = p.get("unidade");
      const marcador = /^\d{7}$/.test(cnes) ? marcadorDaUnidade(cnes) : null;
      if (marcador && App.map.hasLayer(marcador)) {
        App.map.setView(marcador.getLatLng(), Math.max(App.map.getZoom(), 15), { animate: false });
        marcador.openPopup();
      } else {
        ignorados.push(`unidade=${cnes}`);
        limpar.unidade = null;
      }
    }

    if (Object.keys(limpar).length) gravarParametros(limpar);
    mostrarAviso(ignorados);

    // ---------- estado → URL ----------
    const gravarCamadas = () => {
      const extras = Object.keys(camadas).filter((k) => !padrao.has(k) && camadas[k].ligado());
      gravarParametros({ camada: extras.join(",") });
    };
    document.getElementById("painel").addEventListener("change", (evento) => {
      if (evento.target.matches("input[type=checkbox], input[type=radio]")) gravarCamadas();
    });
    infoCota.aoMudar((cota) => gravarParametros({ cota: String(cota) }));
    infoUsoSolo.aoMudar((ano) => { if (camadas.usoSolo && camadas.usoSolo.ligado()) gravarParametros({ periodo: String(ano) }); });
    App.map.on("popupopen", (evento) => {
      const origem = evento.popup._source;
      if (origem && origem.feature && App.layers.unidadesSaude.hasLayer(origem)) {
        gravarParametros({ unidade: String(origem.feature.properties.cnes) });
      }
    });
    App.map.on("popupclose", (evento) => {
      const origem = evento.popup._source;
      if (origem && origem.feature && App.layers.unidadesSaude.hasLayer(origem) &&
          lerParametros().get("unidade") === String(origem.feature.properties.cnes)) {
        gravarParametros({ unidade: null });
      }
    });
    document.documentElement.dataset.urlPronta = "1";
  }

  window.addEventListener("climapampa:camadas-prontas", () => {
    iniciar().catch((erro) => console.error("Erro ao aplicar os parâmetros da URL:", erro));
  });
})();
