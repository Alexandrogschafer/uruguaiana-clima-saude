/**
 * Slider de cota de inundação. Espera o evento "climapampa:camadas-prontas"
 * (disparado por layers.js ao fim do carregamento assíncrono dos GeoJSON)
 * antes de tocar em App.layers.setoresInundacao / cotasInundacao.
 *
 * Em vez de recolorir todas as cotas ao mesmo tempo, mostra só os polígonos
 * (setor censitário e mancha de inundação) da cota selecionada — as demais
 * ficam com opacidade 0, já que setores-inundacao.geojson tem uma feição
 * por combinação setor×cota (múltiplas cotas se sobrepõem no mesmo setor).
 */

let cotasDisponiveis = [];
let estatisticasPorCota = {};

const ESTILO_SETOR_VISIVEL = { color: "#c2410c", weight: 1, fillColor: "#fb8500", fillOpacity: 0.45 };
const ESTILO_SETOR_OCULTO = { color: "#c2410c", weight: 0, fillColor: "#fb8500", fillOpacity: 0, opacity: 0 };
const ESTILO_MANCHA_VISIVEL = { color: "#1d4ed8", weight: 1.5, fillColor: "#2563eb", fillOpacity: 0.35 };
const ESTILO_MANCHA_OCULTA = { color: "#1d4ed8", weight: 0, fillColor: "#2563eb", fillOpacity: 0, opacity: 0 };

function formatarNumero(valor, casas = 0) {
  if (valor === null || valor === undefined || Number.isNaN(valor)) return "—";
  return Number(valor).toLocaleString("pt-BR", { maximumFractionDigits: casas });
}

function atualizarCota(indice) {
  const cota = cotasDisponiveis[indice];
  if (cota === undefined) return;

  const { layers } = window.App;

  layers.setoresInundacao.eachLayer((camada) => {
    const visivel = camada.feature.properties.cota_cm === cota;
    camada.setStyle(visivel ? ESTILO_SETOR_VISIVEL : ESTILO_SETOR_OCULTO);
    camada.options.interactive = visivel;
  });

  layers.cotasInundacao.eachLayer((camada) => {
    const visivel = camada.feature.properties.cota_cm === cota;
    camada.setStyle(visivel ? ESTILO_MANCHA_VISIVEL : ESTILO_MANCHA_OCULTA);
    camada.options.interactive = visivel;
  });

  const stats = estatisticasPorCota.por_cota ? estatisticasPorCota.por_cota[String(cota)] : null;

  document.getElementById("rotulo-cota").textContent = `${cota} cm`;
  document.getElementById("rotulo-tr").textContent = stats
    ? `período de retorno: ${formatarNumero(stats.tr_anos, 1)} anos`
    : "período de retorno: —";

  document.getElementById("stat-setores-afetados").textContent = stats
    ? formatarNumero(stats.populacao.n_setores_afetados)
    : "—";
  document.getElementById("stat-populacao-ponderada").textContent = stats
    ? `${formatarNumero(stats.populacao["populacao_estimada_ponderada_uso-solo"], 1)} hab.`
    : "—";
  document.getElementById("stat-pct-populacao").textContent = stats
    ? `${formatarNumero(stats.populacao["pct_populacao_municipio_exposta_ponderada_uso-solo"], 2)}%`
    : "—";
  document.getElementById("stat-estabelecimentos").textContent = stats
    ? formatarNumero(stats.saude.n_estabelecimentos_total)
    : "—";
}

async function iniciarSliderInundacao() {
  try {
    const resposta = await fetch("data/geoportal/estatisticas-por-cota.json");
    if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
    estatisticasPorCota = await resposta.json();
    cotasDisponiveis = estatisticasPorCota.cotas_disponiveis_cm || [];
  } catch (erro) {
    console.error("Erro ao carregar estatísticas por cota:", erro);
    return;
  }

  const slider = document.getElementById("slider-cota");
  slider.max = String(Math.max(cotasDisponiveis.length - 1, 0));
  slider.disabled = cotasDisponiveis.length === 0;
  slider.value = "0";

  slider.addEventListener("input", (evento) => atualizarCota(Number(evento.target.value)));

  atualizarCota(0);
}

window.addEventListener("climapampa:camadas-prontas", iniciarSliderInundacao);
