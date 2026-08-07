/**
 * Carrega os GeoJSON de data/geoportal/, estiliza cada camada e monta o
 * controle de camadas (embutido na seção "Camadas" do painel, não na
 * caixa flutuante padrão do Leaflet — o container do L.control.layers é
 * reaproveitado dentro do nosso próprio painel).
 *
 * saude-osm não entra no L.control.layers: sua visibilidade é controlada
 * só pelo checkbox da seção "Saúde" (filtro-saude.js), pra não duplicar o
 * mesmo controle em dois lugares do painel.
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
  setoresVulnerabilidade: {
    CD_SETOR: "Código do setor",
    SITUACAO: "Situação",
    AREA_KM2: "Área (km²)",
    NM_BAIRRO: "Bairro",
    NM_DIST: "Distrito",
    populacao_total: "População total",
    domicilios_particulares_ocupados: "Domicílios particulares ocupados",
    densidade_demografica_hab_km2: "Densidade demográfica (hab/km²)",
    pct_populacao_0_a_4_anos: "% população 0-4 anos",
    pct_populacao_60_anos_ou_mais: "% população 60+ anos",
    rendimento_medio_domiciliar_per_capita_reais_municipio: "Renda média domiciliar per capita (R$, município)",
    pct_domicilios_agua_inadequada_municipio: "% domicílios com água inadequada (município)",
    pct_domicilios_esgoto_inadequado_municipio: "% domicílios com esgoto inadequado (município)",
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
};

const CAMPO_TITULO = {
  setoresVulnerabilidade: (p) => `Setor ${p.CD_SETOR ?? ""}`.trim(),
  setoresInundacao: (p) => `Setor ${p.CD_SETOR ?? ""} — cota ${p.cota_cm ?? "?"} cm`,
  cotasInundacao: (p) => `Mancha de inundação — cota ${p.cota_cm ?? "?"} cm`,
  saudeCnes: (p) => p.nome_fantasia || p.nome_empresarial || "Estabelecimento de saúde",
  saudeOsm: (p) => p.name || "Estabelecimento de saúde (OSM)",
  estacoesClima: (p) => p.nome_estacao || "Estação climatológica",
  malhaViaria: (p) => p.name || "Via sem nome",
};

function formatarValor(chave, valor) {
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

  const linhas = Object.entries(campos)
    .map(([chave, rotulo]) => [rotulo, formatarValor(chave, propriedades[chave])])
    .filter(([, valor]) => valor !== null)
    .map(([rotulo, valor]) => `<tr><td>${rotulo}</td><td>${valor}</td></tr>`)
    .join("");

  return `<div class="popup-titulo">${titulo}</div><table class="popup-tabela">${linhas}</table>`;
}

function onEachFeatureComPopup(chaveCamada) {
  return (feature, layer) => {
    layer.bindPopup(() => construirPopup(chaveCamada, feature.properties || {}));
  };
}

async function buscarGeoJSON(nomeArquivo) {
  const resposta = await fetch(`${DIR_DADOS}/${nomeArquivo}`);
  if (!resposta.ok) {
    throw new Error(`falha ao carregar ${nomeArquivo}: HTTP ${resposta.status}`);
  }
  return resposta.json();
}

async function iniciarCamadas() {
  const mapa = window.App.map;

  try {
    const [
      limiteMunicipalGeoJSON,
      setoresVulnerabilidadeGeoJSON,
      setoresInundacaoGeoJSON,
      cotasInundacaoGeoJSON,
      saudeCnesGeoJSON,
      saudeOsmGeoJSON,
      estacoesClimaGeoJSON,
      malhaViariaGeoJSON,
    ] = await Promise.all([
      buscarGeoJSON("limite-municipal.geojson"),
      buscarGeoJSON("setores-vulnerabilidade.geojson"),
      buscarGeoJSON("setores-inundacao.geojson"),
      buscarGeoJSON("cotas-inundacao.geojson"),
      buscarGeoJSON("saude-cnes.geojson"),
      buscarGeoJSON("saude-osm.geojson"),
      buscarGeoJSON("estacoes-clima.geojson"),
      buscarGeoJSON("malha-viaria.geojson"),
    ]);

    // limite municipal: contorno de referência, sempre visível, não interativo
    const limiteMunicipal = L.geoJSON(limiteMunicipalGeoJSON, {
      style: { color: "#123c26", weight: 2, dashArray: "4 4", fill: false },
      interactive: false,
    }).addTo(mapa);
    mapa.fitBounds(limiteMunicipal.getBounds(), { padding: [16, 16] });

    const setoresVulnerabilidade = L.geoJSON(setoresVulnerabilidadeGeoJSON, {
      style: { color: "#4b5563", weight: 1, fillColor: "#a3b899", fillOpacity: 0.35 },
      onEachFeature: onEachFeatureComPopup("setoresVulnerabilidade"),
    }).addTo(mapa);

    const setoresInundacao = L.geoJSON(setoresInundacaoGeoJSON, {
      style: { color: "#c2410c", weight: 1, fillColor: "#fb8500", fillOpacity: 0.45 },
      onEachFeature: onEachFeatureComPopup("setoresInundacao"),
    }).addTo(mapa);

    const cotasInundacao = L.geoJSON(cotasInundacaoGeoJSON, {
      style: { color: "#1d4ed8", weight: 1, fillColor: "#2563eb", fillOpacity: 0.3 },
      onEachFeature: onEachFeatureComPopup("cotasInundacao"),
    }).addTo(mapa);

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
    }).addTo(mapa);

    // malha viária: camada de contexto, desligada por padrão (não é addTo(mapa) aqui)
    const malhaViaria = L.geoJSON(malhaViariaGeoJSON, {
      style: { color: "#6b7280", weight: 1.5, opacity: 0.7 },
      onEachFeature: onEachFeatureComPopup("malhaViaria"),
    });

    window.App.layers = {
      limiteMunicipal,
      setoresVulnerabilidade,
      setoresInundacao,
      cotasInundacao,
      saudeCnes,
      saudeOsm,
      estacoesClima,
      malhaViaria,
    };

    const controleCamadas = L.control.layers(
      null,
      {
        "Vulnerabilidade socioeconômica (setores censitários)": setoresVulnerabilidade,
        "Setores expostos à inundação": setoresInundacao,
        "Mancha de inundação (contorno real)": cotasInundacao,
        "Saúde — CNES": saudeCnes,
        "Estações climatológicas (INMET)": estacoesClima,
        "Malha viária (contexto)": malhaViaria,
      },
      { collapsed: false }
    ).addTo(mapa);

    // reaproveita o DOM do controle padrão do Leaflet dentro do nosso painel
    const container = document.getElementById("container-controle-camadas");
    container.innerHTML = "";
    container.appendChild(controleCamadas.getContainer());

    window.dispatchEvent(new CustomEvent("climapampa:camadas-prontas"));
  } catch (erro) {
    console.error("Erro ao carregar camadas do geoportal:", erro);
    document.getElementById("container-controle-camadas").innerHTML =
      '<p class="secao-ajuda">Não foi possível carregar as camadas. Veja o console para detalhes.</p>';
  }
}

iniciarCamadas();
