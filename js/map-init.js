/**
 * Inicialização do mapa e da interface do painel lateral.
 *
 * Namespace global `App` compartilhado pelos outros scripts (layers.js,
 * inundacao-slider.js, uso-solo-timeline.js, filtro-saude.js) — sem build
 * step, então os scripts se comunicam via este objeto único em vez de
 * módulos ES.
 */
window.App = {
  map: null,
  layers: {},
};

(function () {
  // Centro aproximado do município de Uruguaiana/RS (centroide da área de
  // estudo); a vista real é ajustada por fitBounds quando o limite
  // municipal termina de carregar em layers.js.
  const CENTRO_INICIAL = [-29.8406, -56.7294];
  const ZOOM_INICIAL = 11;

  const mapa = L.map("mapa", {
    center: CENTRO_INICIAL,
    zoom: ZOOM_INICIAL,
    zoomControl: true,
  });

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(mapa);

  L.control.scale({ imperial: false }).addTo(mapa);

  window.App.map = mapa;

  // ---------- painel / gaveta mobile ----------

  const painel = document.getElementById("painel");
  const botaoAbrir = document.getElementById("botao-abrir-painel");
  const botaoFechar = document.getElementById("botao-fechar-painel");

  botaoAbrir.addEventListener("click", () => {
    painel.classList.add("aberto");
    botaoAbrir.setAttribute("aria-expanded", "true");
  });

  botaoFechar.addEventListener("click", () => {
    painel.classList.remove("aberto");
    botaoAbrir.setAttribute("aria-expanded", "false");
  });

  // ---------- seções colapsáveis ----------

  document.querySelectorAll(".secao-titulo").forEach((botao) => {
    botao.addEventListener("click", () => {
      const secao = botao.closest(".secao");
      const fechada = secao.classList.toggle("fechada");
      botao.setAttribute("aria-expanded", String(!fechada));
    });
  });
})();
