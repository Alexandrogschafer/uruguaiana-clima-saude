/**
 * Carrega os GeoJSON de data/geoportal/, estiliza cada camada e monta o
 * controle de camadas (embutido na seção "Camadas" do painel, não na
 * caixa flutuante padrão do Leaflet — o container do L.control.layers é
 * reaproveitado dentro do nosso próprio painel).
 *
 * saudeCnes e saudeOsm não entram no L.control.layers: as duas ficam
 * sempre no mapa desde o carregamento, e a visibilidade (por tipo de
 * estabelecimento ou por fonte) é controlada só pela seção "Saúde"
 * (filtro-saude.js) — evita ter dois controles pra mesma camada.
 */

const DIR_DADOS = "data/geoportal";

// cores por categoria de estabelecimento de saúde — usadas tanto no estilo
// dos pontos aqui quanto na legenda/checkboxes de filtro-saude.js
const CORES_TIPO_SAUDE = {
  clinica_ambulatorio: "#2b8cbe",
  farmacia: "#31a354",
  ubs_esf: "#e6550d",
  laboratorio_apoio_diagnostico: "#756bb1",
  outro: "#969696",
  vigilancia_saude: "#c51b8a",
  hospital: "#de2d26",
};

const ROTULOS_TIPO_SAUDE = {
  clinica_ambulatorio: "Clínica / Ambulatório",
  farmacia: "Farmácia",
  ubs_esf: "UBS / ESF",
  laboratorio_apoio_diagnostico: "Laboratório / Apoio diagnóstico",
  outro: "Outro",
  vigilancia_saude: "Vigilância em saúde",
  hospital: "Hospital",
};

window.App.coresTipoSaude = CORES_TIPO_SAUDE;
window.App.rotulosTipoSaude = ROTULOS_TIPO_SAUDE;

// nomes de campo legíveis, por camada — usados na montagem dos popups
const CAMPOS_LEGIVEIS = {
  densidadePopulacional: {
    CD_SETOR: "Código do setor",
    NM_BAIRRO: "Bairro",
    NM_DIST: "Distrito",
    populacao_total: "População total",
    AREA_KM2: "Área (km²)",
    densidade_hab_km2: "Densidade populacional (hab/km²)",
  },
  criancas0a4: {
    CD_SETOR: "Código do setor",
    NM_BAIRRO: "Bairro",
    NM_DIST: "Distrito",
    populacao_total: "População total do setor",
    pct_populacao_0_a_4_anos: "% população 0-4 anos (Censo)",
    estimativa_criancas_0_a_4: "Crianças 0-4 anos (estimativa)",
  },
  idosos60Mais: {
    CD_SETOR: "Código do setor",
    NM_BAIRRO: "Bairro",
    NM_DIST: "Distrito",
    populacao_total: "População total do setor",
    pct_populacao_60_anos_ou_mais: "% população 60+ anos (Censo)",
    estimativa_idosos_60_mais: "Idosos 60+ anos (estimativa)",
  },
  setoresInundacao: {
    CD_SETOR: "Código do setor",
    cota_cm: "Cota de inundação (cm)",
    tr_anos: "Período de retorno (anos)",
    area_setor_km2: "Área do setor (km²)",
    area_intersecao_km2: "Área inundada no setor (km²)",
    pct_area_coberta: "% da área do setor coberta",
    metodo_estimativa_uso_solo: "Método de estimativa",
    populacao_total: "População total do setor",
    "populacao_estimada_area-proporcional": "População estimada (área-proporcional)",
    "populacao_estimada_ponderada_uso-solo": "População estimada (ponderada por uso do solo)",
    pct_populacao_0_a_4_anos: "% população 0-4 anos",
    pct_populacao_60_anos_ou_mais: "% população 60+ anos",
  },
  cotasInundacao: {
    cota_cm: "Cota de inundação (cm)",
    tr_anos: "Período de retorno (anos)",
    bacia: "Bacia",
    municipio: "Município",
    estado: "Estado",
  },
  saudeCnes: {
    nome_fantasia: "Nome",
    nome_empresarial: "Razão social",
    tipo_unidade_categoria: "Categoria",
    tipo_unidade: "Tipo de unidade (CNES)",
    endereco: "Endereço",
    cep: "CEP",
    telefone: "Telefone",
    atende_sus: "Atende SUS",
    gestao: "Gestão",
    cnes: "Código CNES",
  },
  saudeOsm: {
    name: "Nome",
    amenity: "Categoria (OSM)",
    healthcare: "Tipo de assistência",
    "healthcare:speciality": "Especialidade",
    "addr:street": "Rua",
    "addr:housenumber": "Número",
    "addr:suburb": "Bairro",
    "addr:postcode": "CEP",
    dispensing: "Dispensa medicamentos",
    website: "Site",
    "ref:CNES": "Código CNES (referência)",
  },
  estacoesClima: {
    nome_estacao: "Nome da estação",
    codigo_estacao: "Código da estação",
    situacao: "Situação",
    tipo_estacao: "Tipo de estação",
    altitude_m: "Altitude (m)",
    data_inicio_operacao: "Início de operação",
    distancia_centroide_km: "Distância ao centro do município (km)",
  },
  malhaViaria: {
    name: "Nome da via",
    highway: "Tipo de via",
  },
  redeHidrografica: {
    cocursodag: "Código do curso d'água (BHO)",
    nuordemcda: "Ordem do curso d'água",
  },
  bacia: {
    codigo_otto: "Código Otto Pfafstetter",
    nivel_otto: "Nível Otto Pfafstetter",
  },
  geologia: {
    nm_unidade: "Unidade geológica",
    letra_simb: "Símbolo",
    nm_lito1: "Litologia principal",
    nm_lito2: "Litologia secundária",
    nm_tempo_g: "Idade geológica",
    nm_provincia: "Província estrutural",
    ar_poli_km: "Área do polígono na fonte original (km², antes do recorte municipal)",
  },
  geomorfologia: {
    legenda: "Unidade geomorfológica",
    categoria: "Categoria",
    natureza: "Natureza",
    forma: "Forma do relevo",
    dens_dren: "Densidade de drenagem",
    niv_alt: "Nível altimétrico",
    compartimento: "Compartimento",
    ar_poli_km: "Área do polígono na fonte original (km², antes do recorte municipal)",
  },
  pedologia: {
    legenda: "Classe de solo",
    nom_unidad: "Unidade pedológica",
    cod_simbol: "Símbolo",
    textura: "Textura",
    relevo: "Relevo associado",
    erosao: "Suscetibilidade à erosão",
    ar_poli_km: "Área do polígono na fonte original (km², antes do recorte municipal)",
  },
  vegetacao: {
    legenda: "Fitofisionomia / uso",
    clas_domi: "Classe dominante",
    leg_sup: "Legenda de superfície",
    ar_poli_km: "Área do polígono na fonte original (km², antes do recorte municipal)",
  },
  pocosSiagas: {
    codigo_poco: "Código do poço (SIAGAS)",
    nome_poco: "Nome/identificação",
    local_poco: "Local",
    profundidade_m: "Profundidade (m)",
    nivel_estatico_m: "Nível estático (m)",
    nivel_dinamico_m: "Nível dinâmico (m)",
    vazao_especifica_m3h_m: "Vazão específica (m³/h por m)",
    aquifero: "Aquífero captado",
    situacao: "Situação",
    uso_agua: "Uso da água",
  },
  appHidrica: {
    curso_dagua: "Curso d'água",
    classe_largura_codigo_florestal: "Classe de largura (Código Florestal)",
    faixa_app_m: "Faixa de APP (m)",
    area_km2: "Área (km²)",
  },
};

// camadas em que campo vazio deve aparecer no popup como "sem dado" em vez
// de ser ocultado — usado nos poços SIAGAS pra deixar claro que a ausência
// de um atributo (ex. vazão específica, ~58% de dado faltante na fonte) é
// informação relevante, não um poço com popup "incompleto" por acidente
const CAMPOS_MOSTRAR_SEM_DADO = new Set(["pocosSiagas"]);

// ---------- utilitários de texto compartilhados (também usados por meio-fisico.js) ----------

function normalizarTexto(texto) {
  return String(texto ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function ehClasseAgua(rotulo) {
  return normalizarTexto(rotulo).includes("agua");
}

// alguns campos "legenda" da fonte BDiA vêm com um dígito de ordenação
// grudado no início (ex. "5Corpo d'água continental", "2Planalto da
// Campanha") — removido só para exibição, o valor original é preservado
// no popup (campo bruto, sem essa limpeza)
function limparRotuloClasse(rotulo) {
  return String(rotulo ?? "").replace(/^\d+/, "").trim() || "Sem classificação";
}

// paleta categórica (ColorBrewer Set3, 12 cores de bom contraste entre si)
// usada nas 4 camadas do BDiA (geologia/geomorfologia/pedologia/vegetação);
// classes de água (qualquer rótulo contendo "água") sempre recebem a mesma
// cor fixa em vez de uma cor da paleta, pra ficar visualmente consistente
// entre as 4 camadas (todas têm uma classe "Corpo d'água continental")
const PALETA_CATEGORICA_SET3 = [
  "#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3", "#fdb462",
  "#b3de69", "#fccde5", "#d9d9d9", "#bc80bd", "#ccebc5", "#ffed6f",
];
const COR_AGUA_CATEGORICA = "#3987e5";

const CAMPO_TITULO = {
  densidadePopulacional: (p) => `Setor ${p.CD_SETOR ?? ""}`.trim(),
  criancas0a4: (p) => `Setor ${p.CD_SETOR ?? ""}`.trim(),
  idosos60Mais: (p) => `Setor ${p.CD_SETOR ?? ""}`.trim(),
  setoresInundacao: (p) => `Setor ${p.CD_SETOR ?? ""} — cota ${p.cota_cm ?? "?"} cm`,
  cotasInundacao: (p) => `Mancha de inundação — cota ${p.cota_cm ?? "?"} cm`,
  saudeCnes: (p) => p.nome_fantasia || p.nome_empresarial || "Estabelecimento de saúde",
  saudeOsm: (p) => p.name || "Estabelecimento de saúde (OSM)",
  estacoesClima: (p) => p.nome_estacao || "Estação climatológica",
  malhaViaria: (p) => p.name || "Via sem nome",
  redeHidrografica: (p) => `Curso d'água ${p.cocursodag ?? ""}`.trim(),
  bacia: (p) => `Bacia ${p.codigo_otto ?? ""}`.trim(),
  geologia: (p) => limparRotuloClasse(p.nm_unidade),
  geomorfologia: (p) => limparRotuloClasse(p.legenda),
  pedologia: (p) => limparRotuloClasse(p.legenda),
  vegetacao: (p) => limparRotuloClasse(p.legenda),
  pocosSiagas: (p) => p.nome_poco || `Poço ${p.codigo_poco ?? ""}`.trim() || "Poço SIAGAS",
  appHidrica: (p) => (p.curso_dagua === "rio_uruguai" ? "APP — Rio Uruguai" : "APP — rede de drenagem local"),
};

// notas fixas exibidas no fim do popup de algumas camadas — usadas aqui pra
// deixar explícito que o valor absoluto de crianças/idosos é uma estimativa
// derivada do percentual do Censo (não contagem direta), e pra avisar
// quando o setor não tem dado por sigilo censitário (ver
// scripts/geoportal/converter_setores_demografia.py)
const NOTAS_POPUP = {
  criancas0a4: (p) =>
    p.sem_dado
      ? "Sem dado disponível para este setor (sigilo censitário)."
      : "Valor absoluto estimado a partir do percentual do Censo (não é contagem direta).",
  idosos60Mais: (p) =>
    p.sem_dado
      ? "Sem dado disponível para este setor (sigilo censitário)."
      : "Valor absoluto estimado a partir do percentual do Censo (não é contagem direta).",
};

function formatarValor(chave, valor) {
  if (chave === "curso_dagua") {
    return valor === "rio_uruguai" ? "Rio Uruguai (faixa de 200m)" : "Rede de drenagem local (faixa de 30m)";
  }
  if (valor === null || valor === undefined || valor === "") return null;
  if (typeof valor === "number") {
    if (Number.isNaN(valor)) return null;
    const texto = valor.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
    return chave.startsWith("pct_") ? `${texto}%` : texto;
  }
  return String(valor);
}

function construirPopup(chaveCamada, propriedades) {
  const campos = CAMPOS_LEGIVEIS[chaveCamada] || {};
  const titulo = (CAMPO_TITULO[chaveCamada] || (() => "Feição"))(propriedades);
  const mostrarSemDado = CAMPOS_MOSTRAR_SEM_DADO.has(chaveCamada);

  const linhas = Object.entries(campos)
    .map(([chave, rotulo]) => {
      const valorFormatado = formatarValor(chave, propriedades[chave]);
      if (valorFormatado === null) {
        return mostrarSemDado ? [rotulo, "sem dado"] : null;
      }
      return [rotulo, valorFormatado];
    })
    .filter((linha) => linha !== null)
    .map(([rotulo, valor]) => `<tr><td>${rotulo}</td><td>${valor}</td></tr>`)
    .join("");

  const funcaoNota = NOTAS_POPUP[chaveCamada];
  const nota = funcaoNota ? `<p class="popup-nota">${funcaoNota(propriedades)}</p>` : "";

  return `<div class="popup-titulo">${titulo}</div><table class="popup-tabela">${linhas}</table>${nota}`;
}

function onEachFeatureComPopup(chaveCamada) {
  return (feature, layer) => {
    layer.bindPopup(() => construirPopup(chaveCamada, feature.properties || {}));
  };
}

// ---------- choropleth dos setores censitários (densidade/crianças/idosos) ----------
//
// As 3 camadas usam quebras por quantil (5 classes) calculadas a partir dos
// valores presentes no próprio GeoJSON — não há classificação por natural
// breaks (Jenks) pra evitar depender de biblioteca extra só pra isso.
// Setores com sem_dado=true (sigilo censitário, ver
// scripts/geoportal/converter_setores_demografia.py) ficam fora do cálculo
// das quebras e recebem hachura neutra em vez de cor da rampa.

// rampas sequenciais (1 matiz, claro->escuro) — hue base de cada uma é o
// mesmo das 3 primeiras cores categóricas do design system (azul/laranja/água)
const RAMPA_DENSIDADE = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"];
const RAMPA_CRIANCAS = ["#fce4d6", "#f7b892", "#eb6834", "#b84f27", "#7a341a"];
const RAMPA_IDOSOS = ["#d3f3e6", "#8fdcbd", "#1baf7a", "#12805a", "#0a4d36"];

const CAMPO_CLASSIFICACAO = {
  densidadePopulacional: {
    campo: "densidade_hab_km2",
    rotulo: "Densidade populacional",
    rampa: RAMPA_DENSIDADE,
    formatar: (v) => `${Math.round(v).toLocaleString("pt-BR")} hab/km²`,
  },
  criancas0a4: {
    campo: "pct_populacao_0_a_4_anos",
    rotulo: "Crianças (0-4 anos)",
    rampa: RAMPA_CRIANCAS,
    formatar: (v) => `${v.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`,
  },
  idosos60Mais: {
    campo: "pct_populacao_60_anos_ou_mais",
    rotulo: "Idosos (60+ anos)",
    rampa: RAMPA_IDOSOS,
    formatar: (v) => `${v.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`,
  },
};

const quebrasPorCamada = {};

function calcularQuebrasQuantil(valores, nClasses) {
  const ordenados = [...valores].sort((a, b) => a - b);
  const quebras = [];
  for (let i = 1; i < nClasses; i++) {
    const indice = Math.floor((i / nClasses) * (ordenados.length - 1));
    quebras.push(ordenados[indice]);
  }
  return quebras;
}

function classeParaValor(valor, quebras) {
  for (let i = 0; i < quebras.length; i++) {
    if (valor <= quebras[i]) return i;
  }
  return quebras.length;
}

// injeta, uma única vez, o padrão SVG de hachura usado nos setores sem_dado
// — tom sobre tom em cinza neutro (não usa cor de nenhuma rampa), pra não
// ser confundido com nenhuma classe de valor real
function garantirPatternSemDado(mapa) {
  const svg = mapa.getPane("overlayPane").querySelector("svg");
  if (!svg || svg.querySelector("#hachura-sem-dado")) return;

  let defs = svg.querySelector("defs");
  if (!defs) {
    defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    svg.insertBefore(defs, svg.firstChild);
  }

  const pattern = document.createElementNS("http://www.w3.org/2000/svg", "pattern");
  pattern.setAttribute("id", "hachura-sem-dado");
  pattern.setAttribute("width", "6");
  pattern.setAttribute("height", "6");
  pattern.setAttribute("patternUnits", "userSpaceOnUse");
  pattern.setAttribute("patternTransform", "rotate(45)");

  const fundo = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  fundo.setAttribute("width", "6");
  fundo.setAttribute("height", "6");
  fundo.setAttribute("fill", "#e4e2dc");

  const linha = document.createElementNS("http://www.w3.org/2000/svg", "line");
  linha.setAttribute("x1", "0");
  linha.setAttribute("y1", "0");
  linha.setAttribute("x2", "0");
  linha.setAttribute("y2", "6");
  linha.setAttribute("stroke", "#9a9890");
  linha.setAttribute("stroke-width", "3");

  pattern.appendChild(fundo);
  pattern.appendChild(linha);
  defs.appendChild(pattern);
}

function construirCamadaChoropleth(chaveCamada, geojson) {
  const { campo, rampa } = CAMPO_CLASSIFICACAO[chaveCamada];

  const valores = geojson.features
    .map((f) => f.properties[campo])
    .filter((v) => typeof v === "number" && !Number.isNaN(v));
  const quebras = calcularQuebrasQuantil(valores, rampa.length);
  quebrasPorCamada[chaveCamada] = quebras;

  const estiloFeature = (feature) => {
    const p = feature.properties;
    if (p.sem_dado) {
      return { color: "#6b6a64", weight: 1, fillColor: "url(#hachura-sem-dado)", fillOpacity: 1 };
    }
    const classe = classeParaValor(p[campo], quebras);
    return { color: "#4b5563", weight: 1, fillColor: rampa[classe], fillOpacity: 0.75 };
  };

  return L.geoJSON(geojson, {
    style: estiloFeature,
    onEachFeature: onEachFeatureComPopup(chaveCamada),
  });
}

function construirLegendaHtml(chaveCamada) {
  const cfg = CAMPO_CLASSIFICACAO[chaveCamada];
  const quebras = quebrasPorCamada[chaveCamada];
  if (!cfg || !quebras) return "";

  const linhas = cfg.rampa
    .map((cor, i) => {
      const min = i === 0 ? null : quebras[i - 1];
      const max = i === cfg.rampa.length - 1 ? null : quebras[i];
      let rotuloFaixa;
      if (min === null) rotuloFaixa = `até ${cfg.formatar(max)}`;
      else if (max === null) rotuloFaixa = `acima de ${cfg.formatar(min)}`;
      else rotuloFaixa = `${cfg.formatar(min)} – ${cfg.formatar(max)}`;
      return `<div class="legenda-linha"><span class="swatch" style="background:${cor}"></span>${rotuloFaixa}</div>`;
    })
    .join("");

  const linhaSemDado =
    '<div class="legenda-linha"><span class="swatch swatch-sem-dado"></span>Sem dado (sigilo censitário)</div>';

  return `<div class="legenda-bloco"><strong>${cfg.rotulo}</strong>${linhas}${linhaSemDado}</div>`;
}

function atualizarLegendaDemografia() {
  const mapa = window.App.map;
  const container = document.getElementById("legenda-demografia");
  if (!container) return;
  const ativos = Object.keys(CAMPO_CLASSIFICACAO).filter(
    (chave) => window.App.layers[chave] && mapa.hasLayer(window.App.layers[chave])
  );
  container.innerHTML = ativos.map(construirLegendaHtml).join("");
}

async function buscarGeoJSON(nomeArquivo) {
  const resposta = await fetch(`${DIR_DADOS}/${nomeArquivo}`);
  if (!resposta.ok) {
    throw new Error(`falha ao carregar ${nomeArquivo}: HTTP ${resposta.status}`);
  }
  return resposta.json();
}

/**
 * Monta uma lista de checkboxes simples (não usa L.control.layers — cada
 * grupo de nível superior do painel tem seu próprio container de toggles,
 * então um checkbox HTML normal dá controle total sobre onde ele aparece,
 * ao contrário do widget único do Leaflet que só suporta uma lista plana).
 *
 * `definicoes`: array de { layer, rotulo, ligado, aoAlternar? } — aplica o
 * estado inicial (`ligado`) no mapa imediatamente, e chama `aoAlternar()`
 * (se houver) a cada mudança, além de add/removeLayer.
 */
function montarTogglesCamadas(idContainer, definicoes) {
  const mapa = window.App.map;
  const container = document.getElementById(idContainer);
  if (!container) return;

  container.innerHTML = definicoes
    .map((def, indice) => `
      <label class="checkbox-linha">
        <input type="checkbox" data-indice="${indice}" ${def.ligado ? "checked" : ""} />
        ${def.rotulo}
      </label>`)
    .join("");

  container.querySelectorAll("input[type=checkbox]").forEach((checkbox) => {
    const def = definicoes[Number(checkbox.dataset.indice)];
    if (def.ligado) mapa.addLayer(def.layer);
    else mapa.removeLayer(def.layer);

    checkbox.addEventListener("change", (evento) => {
      if (evento.target.checked) mapa.addLayer(def.layer);
      else mapa.removeLayer(def.layer);
      if (def.aoAlternar) def.aoAlternar();
    });
  });
}

async function iniciarCamadas() {
  const mapa = window.App.map;

  try {
    const [
      limiteMunicipalGeoJSON,
      densidadePopulacionalGeoJSON,
      criancas0a4GeoJSON,
      idosos60MaisGeoJSON,
      setoresInundacaoGeoJSON,
      cotasInundacaoGeoJSON,
      saudeCnesGeoJSON,
      saudeOsmGeoJSON,
      estacoesClimaGeoJSON,
      malhaViariaGeoJSON,
      redeHidrograficaGeoJSON,
    ] = await Promise.all([
      buscarGeoJSON("limite-municipal.geojson"),
      buscarGeoJSON("densidade-populacional.geojson"),
      buscarGeoJSON("criancas-0-4.geojson"),
      buscarGeoJSON("idosos-60-mais.geojson"),
      buscarGeoJSON("setores-inundacao.geojson"),
      buscarGeoJSON("cotas-inundacao.geojson"),
      buscarGeoJSON("saude-cnes.geojson"),
      buscarGeoJSON("saude-osm.geojson"),
      buscarGeoJSON("estacoes-clima.geojson"),
      buscarGeoJSON("malha-viaria.geojson"),
      buscarGeoJSON("rede-hidrografica.geojson"),
    ]);

    // limite municipal: contorno de referência, sempre visível, não interativo
    const limiteMunicipal = L.geoJSON(limiteMunicipalGeoJSON, {
      style: { color: "#123c26", weight: 2, dashArray: "4 4", fill: false },
      interactive: false,
    }).addTo(mapa);
    mapa.fitBounds(limiteMunicipal.getBounds(), { padding: [16, 16] });
    garantirPatternSemDado(mapa);

    // só "Densidade populacional" começa visível — as 3 camadas cobrem a
    // mesma geometria dos setores censitários, então manter as 3 ligadas
    // por padrão só sobreporia choropleths sem ganho de leitura
    // (visibilidade inicial real é aplicada por montarTogglesCamadas mais
    // abaixo — aqui as camadas só são construídas, sem addTo(mapa))
    const densidadePopulacional = construirCamadaChoropleth("densidadePopulacional", densidadePopulacionalGeoJSON);
    const criancas0a4 = construirCamadaChoropleth("criancas0a4", criancas0a4GeoJSON);
    const idosos60Mais = construirCamadaChoropleth("idosos60Mais", idosos60MaisGeoJSON);

    const setoresInundacao = L.geoJSON(setoresInundacaoGeoJSON, {
      style: { color: "#c2410c", weight: 1, fillColor: "#fb8500", fillOpacity: 0.45 },
      onEachFeature: onEachFeatureComPopup("setoresInundacao"),
    });

    const cotasInundacao = L.geoJSON(cotasInundacaoGeoJSON, {
      style: { color: "#1d4ed8", weight: 1, fillColor: "#2563eb", fillOpacity: 0.3 },
      onEachFeature: onEachFeatureComPopup("cotasInundacao"),
    });

    const saudeCnes = L.geoJSON(saudeCnesGeoJSON, {
      pointToLayer: (feature, latlng) => {
        const cor = CORES_TIPO_SAUDE[feature.properties.tipo_unidade_categoria] || "#374151";
        return L.circleMarker(latlng, {
          radius: 5,
          weight: 1,
          color: "#1f2933",
          fillColor: cor,
          fillOpacity: 0.9,
        });
      },
      onEachFeature: onEachFeatureComPopup("saudeCnes"),
    }).addTo(mapa);

    const saudeOsm = L.geoJSON(saudeOsmGeoJSON, {
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, { radius: 6, weight: 1, color: "#115e59", fillColor: "#0ea5a5", fillOpacity: 0.85 }),
      style: { color: "#115e59", weight: 1, fillColor: "#0ea5a5", fillOpacity: 0.35 },
      onEachFeature: onEachFeatureComPopup("saudeOsm"),
    }).addTo(mapa);

    const estacoesClima = L.geoJSON(estacoesClimaGeoJSON, {
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, { radius: 7, weight: 1, color: "#92400e", fillColor: "#f59e0b", fillOpacity: 0.95 }),
      onEachFeature: onEachFeatureComPopup("estacoesClima"),
    });

    // malha viária: camada de contexto, desligada por padrão (não é addTo(mapa) aqui)
    const malhaViaria = L.geoJSON(malhaViariaGeoJSON, {
      style: { color: "#6b7280", weight: 1.5, opacity: 0.7 },
      onEachFeature: onEachFeatureComPopup("malhaViaria"),
    });

    // rede hidrográfica: linha azul fina, contexto, desligada por padrão
    const redeHidrografica = L.geoJSON(redeHidrograficaGeoJSON, {
      style: { color: "#2563eb", weight: 1, opacity: 0.85 },
      onEachFeature: onEachFeatureComPopup("redeHidrografica"),
    });

    window.App.layers = {
      limiteMunicipal,
      densidadePopulacional,
      criancas0a4,
      idosos60Mais,
      setoresInundacao,
      cotasInundacao,
      saudeCnes,
      saudeOsm,
      estacoesClima,
      malhaViaria,
      redeHidrografica,
    };

    // grupo "Demografia" — as 3 camadas cobrem a mesma geometria dos
    // setores censitários, só "Densidade populacional" começa ligada
    montarTogglesCamadas("container-camadas-demografia", [
      { layer: densidadePopulacional, rotulo: "Densidade populacional", ligado: true, aoAlternar: atualizarLegendaDemografia },
      { layer: criancas0a4, rotulo: "Crianças (0-4 anos)", ligado: false, aoAlternar: atualizarLegendaDemografia },
      { layer: idosos60Mais, rotulo: "Idosos (60+ anos)", ligado: false, aoAlternar: atualizarLegendaDemografia },
    ]);
    atualizarLegendaDemografia();

    // grupo "Inundação" — mesmo estado inicial de antes (ambas ligadas)
    montarTogglesCamadas("container-camadas-inundacao", [
      { layer: setoresInundacao, rotulo: "Setores expostos à inundação", ligado: true },
      { layer: cotasInundacao, rotulo: "Mancha de inundação (contorno real)", ligado: true },
    ]);

    // grupo "Hidrografia e terreno" — subseção "Contexto"
    montarTogglesCamadas("container-camadas-hidro-contexto", [
      { layer: redeHidrografica, rotulo: "Rede hidrográfica", ligado: false },
      { layer: estacoesClima, rotulo: "Estações climatológicas (INMET)", ligado: true },
    ]);

    // grupo "Malha viária"
    montarTogglesCamadas("container-camadas-malha-viaria", [
      { layer: malhaViaria, rotulo: "Malha viária (contexto)", ligado: false },
    ]);

    window.dispatchEvent(new CustomEvent("climapampa:camadas-prontas"));
  } catch (erro) {
    console.error("Erro ao carregar camadas do geoportal:", erro);
    document.getElementById("container-camadas-demografia").innerHTML =
      '<p class="secao-ajuda">Não foi possível carregar as camadas. Veja o console para detalhes.</p>';
  }
}

iniciarCamadas();
