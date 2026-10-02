/**
 * Slider de cota de inundação. Espera o evento "climapampa:camadas-prontas"
 * (disparado por layers.js ao fim do carregamento assíncrono dos GeoJSON)
 * antes de tocar em App.layers.cotasInundacao.
 *
 * Em vez de recolorir todas as cotas ao mesmo tempo, mostra só a mancha de
 * inundação da cota selecionada — as demais ficam com opacidade 0.
 *
 * A população exposta por cota (estimativa preliminar por área do setor) foi
 * retirada do portal em 2026-10-02: substituída pelo cálculo por endereços
 * (scripts/processamento/exposicao_inundacao_enderecos.py), ainda pendente de
 * conferência e por isso não publicado aqui.
 */

let cotasDisponiveis = [];
let estatisticasPorCota = {};

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

  // unidades de saúde (ESF/UBS) dentro da mancha — definição cumulativa (união das manchas de cota <= X)
  document.getElementById("stat-unidades-saude").textContent = stats
    ? formatarNumero(stats.unidades_saude.n_na_mancha)
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
