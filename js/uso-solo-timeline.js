/**
 * Slider de ano do uso do solo (MapBiomas, 1985-2024). Troca o
 * L.imageOverlay ativo usando os bounds em WGS84 já calculados em
 * data/geoportal/uso-solo/bounds.json (gerado por
 * scripts/geoportal/gerar_png_uso_solo.py a partir do raster reprojetado
 * — o bbox ali é do raster já em EPSG:4326, não uma conversão só dos
 * cantos do UTM original, então alinha corretamente com L.imageOverlay).
 *
 * Só depende de App.map (inicializado em map-init.js, que roda antes
 * deste script) — não depende dos GeoJSON de layers.js, por isso não
 * espera o evento "climapampa:camadas-prontas".
 */

// paleta oficial MapBiomas por código de classe, replicada de
// scripts/geoportal/paleta_mapbiomas.py — mantida em sincronia manualmente,
// já que o front-end não roda Python. Código 0 (não observado) é omitido
// da legenda por ser tratado como nodata/transparente no PNG.
const LEGENDA_USO_SOLO = [
  { cor: "#1f8d49", nome: "Formação Florestal" },
  { cor: "#7a5900", nome: "Floresta Plantada" },
  { cor: "#519799", nome: "Área Úmida" },
  { cor: "#d6bc74", nome: "Formação Campestre" },
  { cor: "#ffefc3", nome: "Mosaico de Usos" },
  { cor: "#d4271e", nome: "Área Urbana" },
  { cor: "#db4d4f", nome: "Outra Área não Vegetada" },
  { cor: "#ffaa5f", nome: "Afloramento Rochoso" },
  { cor: "#2532e4", nome: "Rio, Lago e Oceano" },
  { cor: "#f5b3c8", nome: "Soja" },
  { cor: "#c71585", nome: "Arroz" },
  { cor: "#f54ca9", nome: "Outras Culturas Temporárias" },
];

let anosDisponiveis = [];
let boundsPorAno = {};
let overlayAtual = null;

function preencherLegenda() {
  const lista = document.getElementById("legenda-uso-solo");
  lista.innerHTML = LEGENDA_USO_SOLO.map(
    ({ cor, nome }) => `<li><span class="swatch" style="background:${cor}"></span>${nome}</li>`
  ).join("");
}

function exibirAno(indice) {
  const ano = anosDisponiveis[indice];
  if (ano === undefined) return;

  document.getElementById("rotulo-ano").textContent = String(ano);

  const checkbox = document.getElementById("checkbox-uso-solo");
  if (overlayAtual) {
    window.App.map.removeLayer(overlayAtual);
    overlayAtual = null;
  }
  if (!checkbox.checked) return;

  const b = boundsPorAno[String(ano)];
  if (!b) return;

  overlayAtual = L.imageOverlay(
    `data/geoportal/uso-solo/${ano}.png`,
    L.latLngBounds([b.south, b.west], [b.north, b.east]),
    { opacity: 0.75 }
  ).addTo(window.App.map);
}

async function iniciarTimelineUsoSolo() {
  try {
    const resposta = await fetch("data/geoportal/uso-solo/bounds.json");
    if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
    const dados = await resposta.json();
    anosDisponiveis = dados.anos_disponiveis || [];
    boundsPorAno = dados.bounds || {};
  } catch (erro) {
    console.error("Erro ao carregar bounds do uso do solo:", erro);
    return;
  }

  preencherLegenda();

  const slider = document.getElementById("slider-ano");
  slider.max = String(Math.max(anosDisponiveis.length - 1, 0));
  slider.disabled = anosDisponiveis.length === 0;
  slider.value = String(anosDisponiveis.length - 1); // começa no ano mais recente

  slider.addEventListener("input", (evento) => exibirAno(Number(evento.target.value)));
  document.getElementById("checkbox-uso-solo").addEventListener("change", () => exibirAno(Number(slider.value)));

  exibirAno(anosDisponiveis.length - 1);
}

iniciarTimelineUsoSolo();
