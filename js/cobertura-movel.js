/**
 * Camada "Cobertura móvel (ANATEL)" — grupo próprio, deliberadamente
 * ISOLADA do carregamento principal de js/layers.js (não entra no
 * Promise.all de iniciarCamadas() nem em window.App.layers): fetch
 * tardio (lazy), só na primeira vez que o checkbox é marcado — mesmo
 * padrão já usado em js/meio-fisico.js / js/bacias-hidrograficas.js /
 * js/terreno.js. Essa camada já quebrou o geoportal numa tentativa
 * anterior (revertida); o isolamento é deliberado para reduzir o raio de
 * alcance de qualquer problema nela a ela mesma, não ao resto do mapa.
 *
 * Troca de período re-estiliza a camada já carregada via setStyle, sem
 * refetch — mesmo princípio do slider de cota de inundação
 * (js/inundacao-slider.js). Reaproveita onEachFeatureComPopup/
 * CAMPOS_LEGIVEIS.coberturaMovel/garantirPatternSemDado definidos em
 * layers.js (mesmo escopo global, sem module system) — precisa carregar
 * depois de layers.js na página.
 */

const LIMIAR_ALERTA_COBERTURA_PCT = 50;
const ESTILO_COBERTURA_ALERTA = { color: "#991b1b", weight: 1, fillColor: "#ef4444", fillOpacity: 0.65 };
const ESTILO_COBERTURA_OK = { color: "#065f46", weight: 1, fillColor: "#a7f3d0", fillOpacity: 0.35 };
const ESTILO_COBERTURA_SEM_DADO = { color: "#6b6a64", weight: 1, fillColor: "url(#hachura-sem-dado)", fillOpacity: 1 };

let camadaCoberturaMovel = null;
let metadadosPeriodosCobertura = null;
let campoPeriodoCoberturaAtual = null;

function estiloSetorCobertura(feature) {
  const p = feature.properties;
  if (p.sem_dado) return ESTILO_COBERTURA_SEM_DADO;
  const valor = p[campoPeriodoCoberturaAtual];
  if (valor === null || valor === undefined) return ESTILO_COBERTURA_SEM_DADO;
  return valor < LIMIAR_ALERTA_COBERTURA_PCT ? ESTILO_COBERTURA_ALERTA : ESTILO_COBERTURA_OK;
}

function aplicarPeriodoCobertura(campoPeriodo) {
  campoPeriodoCoberturaAtual = campoPeriodo;
  if (!camadaCoberturaMovel) return;
  camadaCoberturaMovel.eachLayer((camada) => camada.setStyle(estiloSetorCobertura(camada.feature)));
}

function montarSeletorPeriodoCobertura() {
  const select = document.getElementById("select-periodo-cobertura");
  select.innerHTML = Object.entries(metadadosPeriodosCobertura.campos_por_periodo)
    .map(([periodo, campo]) => `<option value="${campo}">${periodo}</option>`)
    .join("");
  select.value = metadadosPeriodosCobertura.campo_periodo_mais_recente;
  select.addEventListener("change", (evento) => aplicarPeriodoCobertura(evento.target.value));
  document.getElementById("seletor-periodo-cobertura").style.display = "flex";
}

async function buscarJSONCobertura(nomeArquivo) {
  const resposta = await fetch(`data/geoportal/${nomeArquivo}`);
  if (!resposta.ok) throw new Error(`falha ao carregar ${nomeArquivo}: HTTP ${resposta.status}`);
  return resposta.json();
}

async function carregarCoberturaMovel() {
  const [geojson, periodos] = await Promise.all([
    buscarJSONCobertura("cobertura-movel-anatel.geojson"),
    buscarJSONCobertura("cobertura-movel-anatel-periodos.json"),
  ]);

  metadadosPeriodosCobertura = periodos;
  campoPeriodoCoberturaAtual = periodos.campo_periodo_mais_recente;

  garantirPatternSemDado(window.App.map);
  camadaCoberturaMovel = L.geoJSON(geojson, {
    style: estiloSetorCobertura,
    onEachFeature: onEachFeatureComPopup("coberturaMovel"),
  }).addTo(window.App.map);

  montarSeletorPeriodoCobertura();
}

function iniciarCoberturaMovel() {
  const checkbox = document.getElementById("checkbox-cobertura-movel");

  checkbox.addEventListener("change", async (evento) => {
    if (!evento.target.checked) {
      if (camadaCoberturaMovel) window.App.map.removeLayer(camadaCoberturaMovel);
      return;
    }
    try {
      if (!camadaCoberturaMovel) {
        await carregarCoberturaMovel();
      } else {
        camadaCoberturaMovel.addTo(window.App.map);
      }
    } catch (erro) {
      console.error("Erro ao carregar cobertura móvel (ANATEL):", erro);
      evento.target.checked = false;
    }
  });
}

iniciarCoberturaMovel();
