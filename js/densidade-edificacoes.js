/**
 * Camada "Densidade de edificações (grade 250 m)" — grupo "Base territorial".
 * Isolada do carregamento principal de js/layers.js, como js/cobertura-movel.js:
 * o GeoJSON só é buscado na primeira vez que o checkbox é marcado, e a camada
 * não entra em window.App.layers (fica em window.App.densidadeEdificacoes).
 *
 * Produto provisório (status "extraido"), gerado por
 * scripts/processamento/densidade_edificacoes.py: só o quadrado da célula e os
 * agregados — nenhuma edificação individual, nenhum rótulo.
 *
 * Classes por limites redondos de edificações/ha; as cores repetem as da
 * legenda estática em index.html (sincronia manual, como nos demais grupos).
 * Reaproveita onEachFeatureComPopup/CAMPOS_LEGIVEIS.densidadeEdificacoes de
 * layers.js — precisa carregar depois dele.
 */

const ATRIBUICAO_EDIFICACOES =
  "Edificações: Overture Maps Foundation (inclui © contribuidores do OpenStreetMap, Microsoft e Google)";
const CLASSES_DENSIDADE_EDIFICACOES = [
  { limite: 2, cor: "#ffffd4" },
  { limite: 5, cor: "#fee391" },
  { limite: 10, cor: "#fec44f" },
  { limite: 20, cor: "#fe9929" },
  { limite: 30, cor: "#d95f0e" },
  { limite: Infinity, cor: "#993404" },
];

let camadaDensidadeEdificacoes = null;

function corDensidadeEdificacoes(valor) {
  return CLASSES_DENSIDADE_EDIFICACOES.find((c) => valor < c.limite).cor;
}

async function carregarDensidadeEdificacoes() {
  const resposta = await fetch("data/geoportal/densidade-edificacoes-250m.geojson");
  if (!resposta.ok) throw new Error(`falha ao carregar densidade-edificacoes-250m.geojson: HTTP ${resposta.status}`);
  const geojson = await resposta.json();
  camadaDensidadeEdificacoes = L.geoJSON(geojson, {
    attribution: ATRIBUICAO_EDIFICACOES,
    style: (feature) => ({
      stroke: false,
      fillColor: corDensidadeEdificacoes(feature.properties.densidade_ed_ha),
      fillOpacity: 0.75,
    }),
    onEachFeature: onEachFeatureComPopup("densidadeEdificacoes"),
  });
  window.App.densidadeEdificacoes = camadaDensidadeEdificacoes;
}

function iniciarDensidadeEdificacoes() {
  const checkbox = document.getElementById("checkbox-densidade-edificacoes");
  if (!checkbox) return;

  checkbox.addEventListener("change", async (evento) => {
    if (!evento.target.checked) {
      if (camadaDensidadeEdificacoes) window.App.map.removeLayer(camadaDensidadeEdificacoes);
      return;
    }
    try {
      if (!camadaDensidadeEdificacoes) await carregarDensidadeEdificacoes();
      camadaDensidadeEdificacoes.addTo(window.App.map);
    } catch (erro) {
      console.error("Erro ao carregar a densidade de edificações:", erro);
      evento.target.checked = false;
    }
  });
}

iniciarDensidadeEdificacoes();
