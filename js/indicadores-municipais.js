/**
 * Cartão fixo "Indicadores municipais" — valores únicos para todo o
 * município (Censo 2022), sem variação por setor censitário, por isso
 * fora do mapa (ver scripts/geoportal/gerar_indicadores_municipais.py).
 * Independente do carregamento das camadas do mapa, não espera nenhum
 * evento de layers.js.
 */

async function iniciarIndicadoresMunicipais() {
  try {
    const resposta = await fetch("data/geoportal/indicadores-municipais.json");
    if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
    const dados = await resposta.json();
    const ind = dados.indicadores;

    document.getElementById("stat-renda-media").textContent =
      ind.rendimento_medio_domiciliar_per_capita_reais_municipio.toLocaleString("pt-BR", {
        style: "currency",
        currency: "BRL",
      });
    document.getElementById("stat-agua-inadequada").textContent =
      `${ind.pct_domicilios_agua_inadequada_municipio.toLocaleString("pt-BR")}%`;
    document.getElementById("stat-esgoto-inadequado").textContent =
      `${ind.pct_domicilios_esgoto_inadequado_municipio.toLocaleString("pt-BR")}%`;
  } catch (erro) {
    console.error("Erro ao carregar indicadores municipais:", erro);
  }
}

iniciarIndicadoresMunicipais();
