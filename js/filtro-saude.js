/**
 * Filtro de estabelecimentos de saúde — único controle pra essa camada no
 * painel (o toggle genérico "Saúde — CNES" que existia em Camadas foi
 * removido: os dois controles pra mesma camada faziam os pontos só
 * aparecerem com ambos ativos, o que não era intuitivo). Checkboxes por
 * tipo_unidade_categoria (CNES, confirmado na etapa 0 do pedido como a
 * coluna certa pro filtro — só 7 categorias normalizadas, ao contrário de
 * tipo_unidade/tipo_estabelecimento que têm 18-19 valores brutos cada) +
 * checkbox de visibilidade do saude-osm, todos marcados por padrão, mais
 * um checkbox "Marcar/desmarcar todos" no topo.
 *
 * saudeCnes e saudeOsm não aparecem no L.control.layers de layers.js de
 * propósito — as duas ficam sempre no mapa desde o carregamento, e a
 * visibilidade é controlada só por aqui.
 *
 * Esconder/mostrar por categoria usa addLayer/removeLayer no próprio
 * L.geoJSON (que é um FeatureGroup) em vez de truque de opacidade — assim
 * a feição escondida também some da interatividade (clique/popup), não só
 * visualmente.
 */

let todosOsMarcadoresSaudeCnes = [];

function checkboxesTipoSaude() {
  return Array.from(document.querySelectorAll("#filtro-tipo-saude input[type=checkbox]"));
}

function checkboxSaudeOsm() {
  return document.getElementById("checkbox-saude-osm");
}

function checkboxSaudeTodos() {
  return document.getElementById("checkbox-saude-todos");
}

function categoriasSelecionadas() {
  return new Set(checkboxesTipoSaude().filter((el) => el.checked).map((el) => el.value));
}

function aplicarFiltroSaudeCnes() {
  const selecionadas = categoriasSelecionadas();
  const camadaSaudeCnes = window.App.layers.saudeCnes;

  todosOsMarcadoresSaudeCnes.forEach((marcador) => {
    const categoria = marcador.feature.properties.tipo_unidade_categoria;
    if (selecionadas.has(categoria)) {
      camadaSaudeCnes.addLayer(marcador);
    } else {
      camadaSaudeCnes.removeLayer(marcador);
    }
  });
}

function aplicarFiltroSaudeOsm() {
  const camadaSaudeOsm = window.App.layers.saudeOsm;
  if (checkboxSaudeOsm().checked) {
    window.App.map.addLayer(camadaSaudeOsm);
  } else {
    window.App.map.removeLayer(camadaSaudeOsm);
  }
}

// reflete o estado agregado dos checkboxes individuais no "marcar/desmarcar
// todos" — inclusive o estado indeterminado, quando só parte está marcada
function atualizarCheckboxSaudeTodos() {
  const estados = [...checkboxesTipoSaude(), checkboxSaudeOsm()].map((el) => el.checked);
  const checkboxTodos = checkboxSaudeTodos();
  checkboxTodos.checked = estados.every(Boolean);
  checkboxTodos.indeterminate = !estados.every(Boolean) && estados.some(Boolean);
}

function montarCheckboxesTipoSaude() {
  const container = document.getElementById("filtro-tipo-saude");
  const { rotulosTipoSaude, coresTipoSaude } = window.App;

  container.innerHTML = Object.entries(rotulosTipoSaude)
    .map(
      ([chave, rotulo]) => `
      <label class="checkbox-linha">
        <input type="checkbox" value="${chave}" checked />
        <span class="swatch" style="background:${coresTipoSaude[chave]}"></span>
        ${rotulo}
      </label>`
    )
    .join("");

  container.querySelectorAll("input[type=checkbox]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      aplicarFiltroSaudeCnes();
      atualizarCheckboxSaudeTodos();
    });
  });
}

function iniciarFiltroSaude() {
  todosOsMarcadoresSaudeCnes = window.App.layers.saudeCnes.getLayers();
  montarCheckboxesTipoSaude();

  checkboxSaudeOsm().addEventListener("change", () => {
    aplicarFiltroSaudeOsm();
    atualizarCheckboxSaudeTodos();
  });

  checkboxSaudeTodos().addEventListener("change", (evento) => {
    const marcar = evento.target.checked;
    checkboxesTipoSaude().forEach((el) => (el.checked = marcar));
    checkboxSaudeOsm().checked = marcar;
    aplicarFiltroSaudeCnes();
    aplicarFiltroSaudeOsm();
    atualizarCheckboxSaudeTodos();
  });

  atualizarCheckboxSaudeTodos();
}

window.addEventListener("climapampa:camadas-prontas", iniciarFiltroSaude);
