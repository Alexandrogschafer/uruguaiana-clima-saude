/**
 * Filtro das unidades de saúde (ESF e UBS) — único controle pra essa camada
 * no painel. Checkboxes por classe da unidade (ESF, UBS, a confirmar, sem
 * classe), todos marcados por padrão, mais um checkbox "Marcar/desmarcar
 * todos" no topo.
 *
 * 2026-10-02: o portal passou a ter uma só camada de saúde, a das 23
 * unidades da atenção primária do cadastro revisado pela equipe do projeto
 * (versão 4, 2026). O filtro por categoria do CNES e o checkbox do
 * OpenStreetMap saíram junto com essas camadas.
 *
 * unidadesSaude não aparece no L.control.layers de layers.js de propósito —
 * fica sempre no mapa desde o carregamento, e a visibilidade é controlada só
 * por aqui.
 *
 * Esconder/mostrar por classe usa addLayer/removeLayer no próprio L.geoJSON
 * (que é um FeatureGroup) em vez de truque de opacidade — assim a feição
 * escondida também some da interatividade (clique/popup), não só
 * visualmente.
 */

let todosOsMarcadoresUnidadesSaude = [];

function checkboxesTipoSaude() {
  return Array.from(document.querySelectorAll("#filtro-tipo-saude input[type=checkbox]"));
}

function checkboxSaudeTodos() {
  return document.getElementById("checkbox-saude-todos");
}

function classesSelecionadas() {
  return new Set(checkboxesTipoSaude().filter((el) => el.checked).map((el) => el.value));
}

function aplicarFiltroUnidadesSaude() {
  const selecionadas = classesSelecionadas();
  const camada = window.App.layers.unidadesSaude;

  todosOsMarcadoresUnidadesSaude.forEach((marcador) => {
    if (selecionadas.has(marcador.feature.properties.classe)) {
      camada.addLayer(marcador);
    } else {
      camada.removeLayer(marcador);
    }
  });
}

// reflete o estado agregado dos checkboxes individuais no "marcar/desmarcar
// todos" — inclusive o estado indeterminado, quando só parte está marcada
function atualizarCheckboxSaudeTodos() {
  const estados = checkboxesTipoSaude().map((el) => el.checked);
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
      aplicarFiltroUnidadesSaude();
      atualizarCheckboxSaudeTodos();
    });
  });
}

function iniciarFiltroSaude() {
  todosOsMarcadoresUnidadesSaude = window.App.layers.unidadesSaude.getLayers();
  montarCheckboxesTipoSaude();

  checkboxSaudeTodos().addEventListener("change", (evento) => {
    const marcar = evento.target.checked;
    checkboxesTipoSaude().forEach((el) => (el.checked = marcar));
    aplicarFiltroUnidadesSaude();
    atualizarCheckboxSaudeTodos();
  });

  atualizarCheckboxSaudeTodos();
}

window.addEventListener("climapampa:camadas-prontas", iniciarFiltroSaude);
