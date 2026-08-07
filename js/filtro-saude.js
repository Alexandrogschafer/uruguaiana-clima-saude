/**
 * Filtro de estabelecimentos de saúde: checkboxes por tipo_unidade_categoria
 * (CNES, confirmado na etapa 0 do pedido como a coluna certa pro filtro —
 * só 7 categorias normalizadas, ao contrário de tipo_unidade/tipo_estabelecimento
 * que têm 18-19 valores brutos cada) + checkbox de visibilidade do saude-osm.
 *
 * saude-osm não aparece no L.control.layers de layers.js de propósito —
 * sua visibilidade é controlada só por aqui, pra não duplicar o mesmo
 * controle em dois lugares do painel.
 *
 * Esconder/mostrar por categoria usa addLayer/removeLayer no próprio
 * L.geoJSON (que é um FeatureGroup) em vez de truque de opacidade — assim
 * a feição escondida também some da interatividade (clique/popup), não só
 * visualmente.
 */

let todosOsMarcadoresSaudeCnes = [];

function categoriasSelecionadas() {
  return new Set(
    Array.from(document.querySelectorAll("#filtro-tipo-saude input[type=checkbox]:checked")).map((el) => el.value)
  );
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
    checkbox.addEventListener("change", aplicarFiltroSaudeCnes);
  });
}

function iniciarFiltroSaude() {
  todosOsMarcadoresSaudeCnes = window.App.layers.saudeCnes.getLayers();
  montarCheckboxesTipoSaude();

  document.getElementById("checkbox-saude-osm").addEventListener("change", (evento) => {
    const camadaSaudeOsm = window.App.layers.saudeOsm;
    if (evento.target.checked) {
      window.App.map.addLayer(camadaSaudeOsm);
    } else {
      window.App.map.removeLayer(camadaSaudeOsm);
    }
  });
}

window.addEventListener("climapampa:camadas-prontas", iniciarFiltroSaude);
