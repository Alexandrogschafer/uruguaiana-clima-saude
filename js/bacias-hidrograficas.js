/**
 * Dropdown "Nível de bacia hidrográfica" (Otto Pfafstetter 1-7, BHO/ANA).
 * Carrega cada nível sob demanda (lazy fetch, com cache em memória) em vez
 * de buscar os 7 GeoJSON junto com o resto das camadas — nível 7 sozinho
 * já tem ~3,9 MB mesmo simplificado (ver
 * scripts/geoportal/converter_hidrografia_bho.py). Só um nível fica visível
 * por vez; troca de nível remove o anterior do mapa.
 *
 * Reaproveita onEachFeatureComPopup/CAMPOS_LEGIVEIS.bacia definidos em
 * layers.js (mesmo escopo global, sem module system no projeto) — precisa
 * carregar depois de layers.js na página, mas não depende do fetch
 * assíncrono das outras camadas ter terminado.
 */

const CACHE_GEOJSON_BACIAS = {};
let camadaBaciaAtual = null;

const ESTILO_BACIA = { color: "#6d4c1f", weight: 2, fillColor: "#c9a66b", fillOpacity: 0.15 };

async function carregarNivelBacia(nivel) {
  if (CACHE_GEOJSON_BACIAS[nivel]) return CACHE_GEOJSON_BACIAS[nivel];

  const resposta = await fetch(`data/geoportal/bacias/nivel${nivel}.geojson`);
  if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
  const geojson = await resposta.json();

  CACHE_GEOJSON_BACIAS[nivel] = geojson;
  return geojson;
}

async function selecionarNivelBacia(nivel) {
  const mapa = window.App.map;

  if (camadaBaciaAtual) {
    mapa.removeLayer(camadaBaciaAtual);
    camadaBaciaAtual = null;
  }

  if (!nivel) return;

  try {
    const geojson = await carregarNivelBacia(nivel);
    camadaBaciaAtual = L.geoJSON(geojson, {
      style: ESTILO_BACIA,
      onEachFeature: onEachFeatureComPopup("bacia"),
    }).addTo(mapa);
  } catch (erro) {
    console.error(`Erro ao carregar bacias hidrográficas nível ${nivel}:`, erro);
  }
}

function iniciarSeletorBacias() {
  const select = document.getElementById("select-nivel-bacia");
  select.addEventListener("change", (evento) => selecionarNivelBacia(evento.target.value));

  // "?" ao lado do dropdown: explicação completa (mesmo texto do title, pra
  // toque em mobile, onde o title não abre com hover) alternando ao clicar
  const botaoAjuda = document.getElementById("botao-ajuda-bacias");
  const textoAjuda = document.getElementById("texto-ajuda-bacias");
  botaoAjuda.addEventListener("click", () => {
    const aberto = !textoAjuda.hidden;
    textoAjuda.hidden = aberto;
    botaoAjuda.setAttribute("aria-expanded", String(!aberto));
  });
}

iniciarSeletorBacias();
