/**
 * Filtro de escolas (INEP) — mesmo padrão de UI do filtro de saúde
 * (js/filtro-saude.js): checkboxes por categoria com swatch de cor +
 * "Marcar/desmarcar todos", escondendo/mostrando feições via
 * addLayer/removeLayer no próprio L.geoJSON em vez de opacidade (a feição
 * escondida também some da interatividade, não só visualmente).
 *
 * Diferença em relação ao filtro de saúde: só uma dimensão de filtro
 * (dependência administrativa — não há uma segunda fonte complementar
 * tipo OSM aqui), e todas as categorias começam DESMARCADAS — escolasInep
 * já é sempre montada no mapa desde o carregamento (js/layers.js), então
 * começar com 0 categorias selecionadas é o que faz a camada aparentar
 * "desligada por padrão", mesmo padrão das demais camadas novas do
 * geoportal (DNIT, CAR).
 */

let todosOsMarcadoresEscolas = [];

function checkboxesTipoEscola() {
  return Array.from(document.querySelectorAll("#filtro-tipo-escola input[type=checkbox]"));
}

function checkboxEscolasTodos() {
  return document.getElementById("checkbox-escolas-todos");
}

function categoriasEscolaSelecionadas() {
  return new Set(checkboxesTipoEscola().filter((el) => el.checked).map((el) => el.value));
}

function aplicarFiltroEscolas() {
  const selecionadas = categoriasEscolaSelecionadas();
  const camadaEscolas = window.App.layers.escolasInep;

  todosOsMarcadoresEscolas.forEach((marcador) => {
    const categoria = marcador.feature.properties.dependencia_administrativa;
    if (selecionadas.has(categoria)) {
      camadaEscolas.addLayer(marcador);
    } else {
      camadaEscolas.removeLayer(marcador);
    }
  });
}

// reflete o estado agregado dos checkboxes individuais no "marcar/desmarcar
// todos" — inclusive o estado indeterminado, quando só parte está marcada
function atualizarCheckboxEscolasTodos() {
  const estados = checkboxesTipoEscola().map((el) => el.checked);
  const checkboxTodos = checkboxEscolasTodos();
  checkboxTodos.checked = estados.length > 0 && estados.every(Boolean);
  checkboxTodos.indeterminate = !estados.every(Boolean) && estados.some(Boolean);
}

function montarCheckboxesTipoEscola() {
  const container = document.getElementById("filtro-tipo-escola");
  const { rotulosDependenciaEscola, coresDependenciaEscola } = window.App;

  container.innerHTML = Object.entries(rotulosDependenciaEscola)
    .map(
      ([chave, rotulo]) => `
      <label class="checkbox-linha">
        <input type="checkbox" value="${chave}" />
        <span class="swatch" style="background:${coresDependenciaEscola[chave]}"></span>
        ${rotulo}
      </label>`
    )
    .join("");

  container.querySelectorAll("input[type=checkbox]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      aplicarFiltroEscolas();
      atualizarCheckboxEscolasTodos();
    });
  });
}

function iniciarFiltroEducacao() {
  todosOsMarcadoresEscolas = window.App.layers.escolasInep.getLayers();
  montarCheckboxesTipoEscola();
  aplicarFiltroEscolas(); // nenhuma categoria marcada ainda -> remove todos os marcadores

  checkboxEscolasTodos().addEventListener("change", (evento) => {
    const marcar = evento.target.checked;
    checkboxesTipoEscola().forEach((el) => (el.checked = marcar));
    aplicarFiltroEscolas();
    atualizarCheckboxEscolasTodos();
  });

  atualizarCheckboxEscolasTodos();
}

window.addEventListener("climapampa:camadas-prontas", iniciarFiltroEducacao);
