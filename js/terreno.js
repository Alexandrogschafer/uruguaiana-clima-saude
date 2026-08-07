/**
 * Seção "Terreno": hipsometria e relevo sombreado (hillshade), como
 * L.imageOverlay independentes, cada um com seu próprio checkbox — ambos
 * desligados por padrão, pensados pra funcionar bem sobrepostos (relevo
 * sombreado por cima em blend mode multiply, opacidade reduzida).
 *
 * PNGs só são requisitados na primeira vez que o checkbox correspondente é
 * ligado — o <img> do L.imageOverlay só carrega quando a camada é
 * adicionada ao mapa, então basta criar a camada sob demanda (hillshade.png
 * tem ~3 MB, não faz sentido buscar de cara junto com o resto).
 */

let camadaHipsometria = null;
let camadaHillshade = null;
let boundsTerrenoPromise = null;

function carregarBoundsTerreno() {
  if (!boundsTerrenoPromise) {
    boundsTerrenoPromise = fetch("data/geoportal/terreno/bounds.json")
      .then((resposta) => {
        if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
        return resposta.json();
      })
      .then((dados) => L.latLngBounds([dados.south, dados.west], [dados.north, dados.east]));
  }
  return boundsTerrenoPromise;
}

async function alternarHipsometria(ligado) {
  const mapa = window.App.map;
  if (!ligado) {
    if (camadaHipsometria) mapa.removeLayer(camadaHipsometria);
    return;
  }
  try {
    const bounds = await carregarBoundsTerreno();
    if (!camadaHipsometria) {
      camadaHipsometria = L.imageOverlay("data/geoportal/terreno/hipsometria.png", bounds, { opacity: 0.7 });
    }
    camadaHipsometria.addTo(mapa);
  } catch (erro) {
    console.error("Erro ao carregar hipsometria:", erro);
  }
}

async function alternarHillshade(ligado) {
  const mapa = window.App.map;
  if (!ligado) {
    if (camadaHillshade) mapa.removeLayer(camadaHillshade);
    return;
  }
  try {
    const bounds = await carregarBoundsTerreno();
    if (!camadaHillshade) {
      camadaHillshade = L.imageOverlay("data/geoportal/terreno/hillshade.png", bounds, {
        opacity: 0.4,
        className: "overlay-hillshade-multiply",
      });
    }
    camadaHillshade.addTo(mapa);
  } catch (erro) {
    console.error("Erro ao carregar relevo sombreado:", erro);
  }
}

function iniciarTerreno() {
  document.getElementById("checkbox-hipsometria").addEventListener("change", (evento) => alternarHipsometria(evento.target.checked));
  document.getElementById("checkbox-hillshade").addEventListener("change", (evento) => alternarHillshade(evento.target.checked));
}

iniciarTerreno();
