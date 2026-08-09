/**
 * Grupo "Meio físico" (versão preliminar, para revisão visual): geologia,
 * geomorfologia, pedologia e vegetação (BDiA/IBGE, escala 1:250.000),
 * poços de água subterrânea (SIAGAS/CPRM) e APP hídrica calculada.
 *
 * Todas as 6 camadas começam desligadas (--forcar visual mínimo até que
 * alguém decida o que fica) e são buscadas sob demanda, na primeira vez
 * que o checkbox correspondente é ligado — mesmo padrão de lazy-fetch já
 * usado em terreno.js (PNGs) e bacias-hidrograficas.js (níveis Otto).
 *
 * Reaproveita onEachFeatureComPopup/CAMPOS_LEGIVEIS/limparRotuloClasse/
 * ehClasseAgua/PALETA_CATEGORICA_SET3/COR_AGUA_CATEGORICA definidos em
 * layers.js (mesmo escopo global, sem module system no projeto) — precisa
 * carregar depois de layers.js na página.
 */

const DIR_DADOS_MEIO_FISICO = "data/geoportal";

// as 4 camadas categóricas do BDiA — mesmo método de classificação/legenda,
// só muda o arquivo e o campo de classe (geologia não tem campo "legenda")
const CAMADAS_BDIA = [
  { chave: "geologia", arquivo: "geologia.geojson", rotulo: "Geologia", campoClasse: "nm_unidade", escalaGrosseira: true },
  { chave: "geomorfologia", arquivo: "geomorfologia.geojson", rotulo: "Geomorfologia", campoClasse: "legenda", escalaGrosseira: true },
  { chave: "pedologia", arquivo: "pedologia.geojson", rotulo: "Pedologia", campoClasse: "legenda", escalaGrosseira: true },
  { chave: "vegetacao", arquivo: "vegetacao.geojson", rotulo: "Vegetação", campoClasse: "legenda", escalaGrosseira: true },
];

const CORES_APP_HIDRICA = { rio_uruguai: "#0e7490", rede_local: "#65a30d" };

const cacheJSON = {};
const estadoCamadasBdia = {}; // chave -> { layer, paleta, classesOrdenadas, areasPorClasse, rotulo }
let camadaPocosSiagas = null;
let camadaAppHidrica = null;
let ocupacaoAppHidrica = null;

async function buscarJSON(nomeArquivo) {
  if (cacheJSON[nomeArquivo]) return cacheJSON[nomeArquivo];
  const resposta = await fetch(`${DIR_DADOS_MEIO_FISICO}/${nomeArquivo}`);
  if (!resposta.ok) throw new Error(`falha ao carregar ${nomeArquivo}: HTTP ${resposta.status}`);
  const dados = await resposta.json();
  cacheJSON[nomeArquivo] = dados;
  return dados;
}

// ---------- camadas categóricas (geologia/geomorfologia/pedologia/vegetação) ----------
//
// A área por classe usada na legenda vem de {tema}-classes.json (calculada em
// Python a partir da geometria já recortada, EPSG:31981 — ver
// scripts/geoportal/converter_meio_fisico.py), NÃO somando o atributo
// ar_poli_km de cada feição do GeoJSON: esse atributo é a área do polígono
// inteiro na fonte nacional, antes do recorte municipal — somá-lo (erro
// encontrado na primeira versão desta camada) infla muito a área exibida
// (ex. "Serra Geral" aparecia com ~14.766 km² em vez dos ~4.650 km² reais
// dentro do município).

function montarPaletaClasses(classesOrdenadas) {
  const paleta = {};
  let indiceCor = 0;
  classesOrdenadas.forEach((classe) => {
    if (ehClasseAgua(classe)) {
      paleta[classe] = COR_AGUA_CATEGORICA;
    } else {
      paleta[classe] = PALETA_CATEGORICA_SET3[indiceCor % PALETA_CATEGORICA_SET3.length];
      indiceCor += 1;
    }
  });
  return paleta;
}

function construirCamadaCategorica(chaveCamada, geojson, campoClasse, paleta) {
  return L.geoJSON(geojson, {
    style: (feature) => {
      const rotulo = limparRotuloClasse(feature.properties[campoClasse]);
      return { color: "#4b5563", weight: 0.6, fillColor: paleta[rotulo] || "#9ca3af", fillOpacity: 0.7 };
    },
    onEachFeature: onEachFeatureComPopup(chaveCamada),
  });
}

function construirLegendaCategoricaHtml(rotuloCamada, paleta, classesOrdenadas, areasPorClasse) {
  const linhas = classesOrdenadas
    .map((classe) => {
      const area = areasPorClasse[classe];
      const areaTexto = area ? ` (${area.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} km²)` : "";
      return `<div class="legenda-linha"><span class="swatch" style="background:${paleta[classe]}"></span>${classe}${areaTexto}</div>`;
    })
    .join("");
  return `<div class="legenda-bloco"><strong>${rotuloCamada}</strong>${linhas}</div>`;
}

function construirLegendaOcupacaoAppHtml(dados) {
  return `<div class="legenda-bloco">
    <strong>APP hídrica — ocupação (MapBiomas 2024)</strong>
    <div class="legenda-linha"><span class="swatch" style="background:${CORES_APP_HIDRICA.rio_uruguai}"></span>Rio Uruguai (faixa de 200m)</div>
    <div class="legenda-linha"><span class="swatch" style="background:${CORES_APP_HIDRICA.rede_local}"></span>Rede de drenagem local (faixa de 30m)</div>
    <p class="popup-nota">${dados.pct_area_app_ocupada_por_classes_antropicas}% da área da APP é classe antrópica (${dados.pct_area_app_antropica_sobre_area_terrestre_excl_agua}% excluindo a lâmina d'água). Aproximação metodológica, não delimitação técnica.</p>
  </div>`;
}

function atualizarLegendaMeioFisico() {
  const container = document.getElementById("legenda-meio-fisico");
  if (!container) return;

  const blocos = [];
  CAMADAS_BDIA.forEach((def) => {
    const checkbox = document.getElementById(`checkbox-meio-fisico-${def.chave}`);
    const estado = estadoCamadasBdia[def.chave];
    if (checkbox?.checked && estado) {
      blocos.push(construirLegendaCategoricaHtml(estado.rotulo, estado.paleta, estado.classesOrdenadas, estado.areasPorClasse));
    }
  });

  const checkboxApp = document.getElementById("checkbox-meio-fisico-appHidrica");
  if (checkboxApp?.checked && ocupacaoAppHidrica) {
    blocos.push(construirLegendaOcupacaoAppHtml(ocupacaoAppHidrica));
  }

  container.innerHTML = blocos.join("");
}

async function alternarCamadaBdia(def, ligado) {
  const mapa = window.App.map;
  if (!ligado) {
    if (estadoCamadasBdia[def.chave]) mapa.removeLayer(estadoCamadasBdia[def.chave].layer);
    atualizarLegendaMeioFisico();
    return;
  }
  try {
    if (!estadoCamadasBdia[def.chave]) {
      const [geojson, classesInfo] = await Promise.all([
        buscarJSON(def.arquivo),
        buscarJSON(`${def.chave}-classes.json`),
      ]);
      const paleta = montarPaletaClasses(classesInfo.classes_ordenadas);
      const layer = construirCamadaCategorica(def.chave, geojson, def.campoClasse, paleta);
      estadoCamadasBdia[def.chave] = {
        layer,
        paleta,
        classesOrdenadas: classesInfo.classes_ordenadas,
        areasPorClasse: classesInfo.area_km2_por_classe,
        rotulo: def.rotulo,
      };
    }
    estadoCamadasBdia[def.chave].layer.addTo(mapa);
    atualizarLegendaMeioFisico();
  } catch (erro) {
    console.error(`Erro ao carregar camada de meio físico (${def.chave}):`, erro);
  }
}

// ---------- poços SIAGAS (pontual) ----------

async function alternarPocosSiagas(ligado) {
  const mapa = window.App.map;
  if (!ligado) {
    if (camadaPocosSiagas) mapa.removeLayer(camadaPocosSiagas);
    return;
  }
  try {
    if (!camadaPocosSiagas) {
      const geojson = await buscarJSON("pocos-agua-subterranea.geojson");
      camadaPocosSiagas = L.geoJSON(geojson, {
        pointToLayer: (feature, latlng) =>
          L.circleMarker(latlng, { radius: 5, weight: 1, color: "#1e3a8a", fillColor: "#60a5fa", fillOpacity: 0.9 }),
        onEachFeature: onEachFeatureComPopup("pocosSiagas"),
      });
    }
    camadaPocosSiagas.addTo(mapa);
  } catch (erro) {
    console.error("Erro ao carregar poços SIAGAS:", erro);
  }
}

// ---------- APP hídrica calculada (2 feições: rede local / rio Uruguai) ----------

async function alternarAppHidrica(ligado) {
  const mapa = window.App.map;
  if (!ligado) {
    if (camadaAppHidrica) mapa.removeLayer(camadaAppHidrica);
    atualizarLegendaMeioFisico();
    return;
  }
  try {
    if (!camadaAppHidrica) {
      const geojson = await buscarJSON("app-hidrica.geojson");
      camadaAppHidrica = L.geoJSON(geojson, {
        style: (feature) => {
          const cor = CORES_APP_HIDRICA[feature.properties.curso_dagua] || "#4b5563";
          return { color: cor, weight: 1, fillColor: cor, fillOpacity: 0.35 };
        },
        onEachFeature: onEachFeatureComPopup("appHidrica"),
      });
    }
    if (!ocupacaoAppHidrica) {
      ocupacaoAppHidrica = await buscarJSON("app-hidrica-ocupacao.json");
    }
    camadaAppHidrica.addTo(mapa);
    atualizarLegendaMeioFisico();
  } catch (erro) {
    console.error("Erro ao carregar APP hídrica:", erro);
  }
}

// ---------- montagem dos checkboxes ----------

function montarCheckboxesMeioFisico() {
  const container = document.getElementById("container-camadas-meio-fisico");
  if (!container) return;

  const definicoes = [
    ...CAMADAS_BDIA.map((def) => ({
      id: `checkbox-meio-fisico-${def.chave}`,
      rotulo: def.rotulo,
      titulo: def.escalaGrosseira
        ? "Escala 1:250.000 — leitura regional/contextual, não decisão em nível de microárea."
        : null,
      aoAlternar: (ligado) => alternarCamadaBdia(def, ligado),
    })),
    {
      id: "checkbox-meio-fisico-pocosSiagas",
      rotulo: "Poços de água subterrânea (SIAGAS)",
      titulo: null,
      aoAlternar: alternarPocosSiagas,
    },
    {
      id: "checkbox-meio-fisico-appHidrica",
      rotulo: "APP hídrica (calculada — aproximação)",
      titulo: "Aproximação metodológica (largura assumida/estimada) — não usar para fiscalização ou licenciamento.",
      aoAlternar: alternarAppHidrica,
    },
  ];

  container.innerHTML = definicoes
    .map(
      (def) => `
      <label class="checkbox-linha"${def.titulo ? ` title="${def.titulo}"` : ""}>
        <input type="checkbox" id="${def.id}" />
        ${def.rotulo}${def.titulo ? ' <span class="indicador-escala" aria-hidden="true">ⓘ</span>' : ""}
      </label>`
    )
    .join("");

  definicoes.forEach((def) => {
    document.getElementById(def.id).addEventListener("change", (evento) => def.aoAlternar(evento.target.checked));
  });
}

function iniciarMeioFisico() {
  montarCheckboxesMeioFisico();
}

iniciarMeioFisico();
