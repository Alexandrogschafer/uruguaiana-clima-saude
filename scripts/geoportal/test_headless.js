// Teste headless (Playwright) do geoportal ClimaPampa.
// Sobe um servidor HTTP local (fetch() de GeoJSON não funciona em file://),
// abre mapa.html (o mapa saiu de index.html na rodada A2) em Chromium headless e valida:
//   - mapa Leaflet inicializado
//   - camadas/painéis carregados a partir dos JSON em data/geoportal/
//   - ausência de erros de JS/console
//
//   - parâmetros da URL (cota, unidade, camada; inválidos geram aviso e saem da URL)
//   - popup das unidades (2017 e 2023, sem 2026 nem total; unidade sem classe sem população)
//   - links "Início", "Ver na saúde" e "Ver na cheia e saúde" respondendo 200
//   - endereço antigo: index.html com parâmetro de mapa redireciona para mapa.html (busca e # mantidos);
//     index.html sem parâmetro de mapa fica na página inicial
//
// Uso: npm run test:geoportal
// Requer Chromium do Playwright instalado (npx playwright install chromium).
// O screenshot vai para uma pasta temporária do sistema (ou SCREENSHOT_DIR), fora do repositório.

const path = require("path");
const http = require("http");
const fs = require("fs");
const os = require("os");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..", "..");
const SCREENSHOT_DIR = process.env.SCREENSHOT_DIR || fs.mkdtempSync(path.join(os.tmpdir(), "geoportal-headless-"));
const SCREENSHOT_PATH = path.join(SCREENSHOT_DIR, "geoportal-headless.png");
// <title> do mapa montado a partir de config/identidade.json (nome_sistema, rodada A4)
const NOME_SISTEMA = JSON.parse(fs.readFileSync(path.join(ROOT, "config", "identidade.json"), "utf8")).nome_sistema;
const TITULO_MAPA = NOME_SISTEMA.titulos.mapa.replace("{nome_mapa}", NOME_SISTEMA.nome_mapa).replace("{nome}", NOME_SISTEMA.nome);

const MIME = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".geojson": "application/json",
  ".woff2": "font/woff2",
};

function serve() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      const urlPath = decodeURIComponent(req.url.split("?")[0]);
      const filePath = path.join(ROOT, urlPath === "/" ? "index.html" : urlPath);
      fs.readFile(filePath, (err, data) => {
        if (err) {
          res.writeHead(404);
          res.end(`not found: ${filePath}`);
          return;
        }
        const ext = path.extname(filePath);
        res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
        res.end(data);
      });
    });
    server.listen(0, "127.0.0.1", () => resolve(server));
  });
}

async function main() {
  const server = await serve();
  const { port } = server.address();
  const baseUrl = `http://127.0.0.1:${port}/`;

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  const consoleErrors = [];
  const pageErrors = [];

  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => pageErrors.push(String(err)));

  console.log(`Abrindo ${baseUrl}`);
  await page.goto(`${baseUrl}mapa.html`, { waitUntil: "networkidle" });
  // dá tempo para fetch() das camadas GeoJSON e inicialização dos controles
  await page.waitForTimeout(2000);

  const checks = {
    tituloOk: (await page.title()) === TITULO_MAPA,
    // rodada A4: nenhum "geoportal" no texto visível, nas descrições nem nos aria-label/title (caminhos e URLs não contam)
    semGeoportalVisivel: await page.evaluate(() => [document.title, document.body.innerText,
      ...[...document.querySelectorAll('meta[name="description"], meta[property^="og:"]')].map((m) => m.getAttribute("content")),
      ...[...document.querySelectorAll("[aria-label], [title]")].flatMap((e) => [e.getAttribute("aria-label"), e.getAttribute("title")])]
      .filter(Boolean).every((s) => !/geoportal/i.test(s.split(/\s+/).filter((w) => !/[/\\]|\.(geo)?json\b/i.test(w)).join(" ")))),
    mapaLeafletPresente: await page.evaluate(() => {
      const el = document.getElementById("mapa");
      return !!(el && el.classList.contains("leaflet-container"));
    }),
    gruposColapsaveisPresentes: await page.evaluate(() => {
      // 12 grupos (o README dizia 11; "base-territorial" entrou depois)
      const esperados = ["mapa-base", "saude", "demografia", "inundacao", "uso-solo", "hidrografia-terreno", "meio-fisico", "malha-viaria", "estrutura-fundiaria", "base-territorial", "educacao", "cobertura-movel"];
      return esperados.every((chave) => !!document.querySelector(`.grupo[data-grupo="${chave}"]`)) && document.querySelectorAll(".grupo").length === esperados.length;
    }),
    // 3 caixas (crianças, idosos e bairros): a "Densidade populacional" virou o seletor de ano no commit 531eb53
    camadasDemografiaCarregadas: await page.evaluate(() => {
      const el = document.getElementById("container-camadas-demografia");
      return !!el && el.textContent.trim() !== "Carregando camadas…" && el.querySelectorAll("input[type=checkbox]").length === 3;
    }),
    camadasInundacaoCarregadas: await page.evaluate(() => document.querySelectorAll("#container-camadas-inundacao input[type=checkbox]").length === 1), // só a mancha (setores × manchas retirada em 2026-10-02)
    camadasHidroContextoCarregadas: await page.evaluate(() => document.querySelectorAll("#container-camadas-hidro-contexto input[type=checkbox]").length === 2),
    camadaMalhaViariaCarregada: await page.evaluate(() => document.querySelectorAll("#container-camadas-malha-viaria input[type=checkbox]").length === 2),
    camadaEstruturaFundiariaCarregada: await page.evaluate(() => document.querySelectorAll("#container-camadas-estrutura-fundiaria input[type=checkbox]").length === 1),
    sliderCotaHabilitado: await page.evaluate(() => {
      const el = document.getElementById("slider-cota");
      return !!el && !el.disabled && el.max !== "0";
    }),
    sliderAnoHabilitado: await page.evaluate(() => {
      const el = document.getElementById("slider-ano");
      return !!el && !el.disabled && el.max !== "0";
    }),
    // saúde: camada única de unidades ESF/UBS, filtro com as 3 classes (ESF, UBS, sem classe) — 2026-10-02
    filtroSaudePopulado: await page.evaluate(() => {
      const el = document.getElementById("filtro-tipo-saude");
      return !!el && el.querySelectorAll("input[type=checkbox]").length === 3;
    }),
    camadaUnidadesSaudeCarregada: await page.evaluate(() => {
      const camada = window.App && window.App.layers && window.App.layers.unidadesSaude;
      return !!camada && camada.getLayers().length === 22 && !window.App.layers.saudeCnes && !window.App.layers.saudeOsm;
    }),
    indicadorUnidadesNaMancha: await page.evaluate(() => {
      const dt = Array.from(document.querySelectorAll("#painel-estatisticas-cota dt")).map((el) => el.textContent.trim());
      const dd = document.getElementById("stat-unidades-saude");
      return dt.includes("Unidades de saúde (ESF/UBS) na mancha") && !!dd && dd.textContent.trim() !== "—";
    }),
    filtroEscolaPopulado: await page.evaluate(() => {
      const el = document.getElementById("filtro-tipo-escola");
      return !!el && el.children.length === 4;
    }),
    coberturaMovelCheckboxPresente: await page.evaluate(() => {
      // camada lazy (só busca o GeoJSON ao marcar o checkbox) — aqui só
      // confirma que o controle existe antes de qualquer interação
      return !!document.getElementById("checkbox-cobertura-movel") && !!document.getElementById("select-periodo-cobertura");
    }),
    meioFisicoPopulado: await page.evaluate(() => {
      const el = document.getElementById("container-camadas-meio-fisico");
      return !!el && el.querySelectorAll("input[type=checkbox]").length === 6;
    }),
  };

  // bairros (grupo "Demografia"): desligada ao abrir; ao ligar a caixa, a camada
  // entra no mapa com os 26 bairros; ao desligar, sai
  const caixaBairros = await page.evaluateHandle(() => {
    const rotulos = Array.from(document.querySelectorAll("#container-camadas-demografia label"));
    const rotulo = rotulos.find((el) => el.textContent.trim() === "Bairros (IBGE, Censo 2022)");
    return rotulo ? rotulo.querySelector("input[type=checkbox]") : null;
  });
  const bairrosNoMapa = () =>
    page.evaluate(() => {
      const camada = window.App && window.App.layers && window.App.layers.bairros;
      return !!camada && window.App.map.hasLayer(camada);
    });
  const elementoCaixaBairros = caixaBairros.asElement();
  checks.camadaBairrosDesligadaAoAbrir = !!elementoCaixaBairros && !(await elementoCaixaBairros.isChecked()) && !(await bairrosNoMapa());
  if (elementoCaixaBairros) await elementoCaixaBairros.click();
  checks.camadaBairrosLigaCom26 =
    (await bairrosNoMapa()) && (await page.evaluate(() => window.App.layers.bairros.getLayers().length === 26));
  // nomes dos bairros: no zoom 13 só os que cabem sem se sobrepor; no 14, todos; no 12, nenhum
  const vistaAntes = await page.evaluate(() => {
    const m = window.App.map;
    return { centro: [m.getCenter().lat, m.getCenter().lng], zoom: m.getZoom() };
  });
  const nomesDosBairros = async (zoom) => {
    await page.evaluate((z) => {
      const m = window.App.map;
      m.setView(window.App.layers.bairros.getBounds().getCenter(), z, { animate: false });
    }, zoom);
    await page.waitForTimeout(600);
    return page.evaluate(() => {
      const todos = Array.from(document.querySelectorAll(".leaflet-tooltip.rotulo-bairro"));
      const marcados = todos.filter((el) => !el.classList.contains("rotulo-bairro-oculto"));
      const caixas = marcados.filter((el) => getComputedStyle(el).visibility !== "hidden").map((el) => el.getBoundingClientRect());
      let sobrepostos = 0;
      for (let i = 0; i < caixas.length; i++) {
        for (let j = i + 1; j < caixas.length; j++) {
          const a = caixas[i];
          const b = caixas[j];
          if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) sobrepostos += 1;
        }
      }
      return { total: todos.length, marcadosVisiveis: marcados.length, desenhados: caixas.length, sobrepostos };
    });
  };
  const nomesZoom13 = await nomesDosBairros(13);
  const nomesZoom14 = await nomesDosBairros(14);
  const nomesZoom12 = await nomesDosBairros(12);
  checks.nomesBairrosZoom13SemSobrepor = nomesZoom13.desenhados >= 1 && nomesZoom13.sobrepostos === 0;
  checks.nomesBairrosZoom14Todos = nomesZoom14.total === 26 && nomesZoom14.marcadosVisiveis === 26;
  checks.nomesBairrosZoom12Nenhum = nomesZoom12.total === 26 && nomesZoom12.desenhados === 0;
  await page.evaluate((v) => window.App.map.setView(v.centro, v.zoom, { animate: false }), vistaAntes);
  await page.waitForTimeout(300);

  if (elementoCaixaBairros) await elementoCaixaBairros.click();
  checks.camadaBairrosDesliga = !!elementoCaixaBairros && !(await bairrosNoMapa());

  // densidade de edificações (grupo "Base territorial"): desligada ao abrir; ao ligar,
  // carrega a grade (só polígonos, centenas de células), com legenda de 6 classes, popup com os 3 valores e
  // a atribuição do Overture no mapa; o aviso não cria rolagem horizontal no painel;
  // ao desligar, a camada e a atribuição saem
  const densidadeEdificacoes = {};
  densidadeEdificacoes.desligadaAoAbrir = await page.evaluate(() => {
    const cb = document.getElementById("checkbox-densidade-edificacoes");
    return !!cb && !cb.checked && !window.App.densidadeEdificacoes;
  });
  await page.click("#checkbox-densidade-edificacoes");
  await page.waitForFunction(() => window.App.densidadeEdificacoes && window.App.map.hasLayer(window.App.densidadeEdificacoes), null, { timeout: 10000 });
  Object.assign(densidadeEdificacoes, await page.evaluate(() => {
    const camada = window.App.densidadeEdificacoes;
    const feicoes = camada.getLayers();
    const legenda = document.querySelectorAll("#legenda-densidade-edificacoes .legenda-linha").length;
    const tituloLegenda = document.querySelector("#legenda-densidade-edificacoes strong")?.textContent || "";
    feicoes[0].openPopup();
    const popup = document.querySelector(".leaflet-popup-content")?.textContent || "";
    const atribuicao = document.querySelector(".leaflet-control-attribution")?.textContent || "";
    const painel = document.getElementById("painel");
    const aviso = document.getElementById("aviso-densidade-edificacoes");
    return {
      ligaSoPoligonos: feicoes.length >= 500 && feicoes.every((l) => l.feature.geometry.type === "Polygon"),
      legendaSeisClasses: legenda === 6 && tituloLegenda.includes("por hectare"),
      popupComValores: ["Edificações na célula", "Área construída (m²)", "Densidade (edificações/ha)", "provisório"].every((t) => popup.includes(t)),
      atribuicaoNoMapa: atribuicao.includes("Overture Maps Foundation") && atribuicao.includes("Microsoft e Google"),
      avisoVisivel: !!aviso && aviso.offsetHeight > 0 && aviso.textContent.includes("ainda não conferido"),
      semRolagemHorizontal: painel.scrollWidth <= painel.clientWidth && aviso.scrollWidth <= aviso.clientWidth,
    };
  }));
  await page.evaluate(() => window.App.map.closePopup());
  await page.click("#checkbox-densidade-edificacoes");
  densidadeEdificacoes.desliga = await page.evaluate(
    () => !window.App.map.hasLayer(window.App.densidadeEdificacoes) &&
      !(document.querySelector(".leaflet-control-attribution")?.textContent || "").includes("Overture")
  );
  console.log("densidade de edificações:", JSON.stringify(densidadeEdificacoes));
  checks.camadaDensidadeEdificacoes = Object.values(densidadeEdificacoes).every(Boolean);

  // liga as 6 camadas novas do grupo "Meio físico" (desligadas por padrão) e
  // confirma que cada uma soma pelo menos 1 layer ativo no mapa Leaflet —
  // exercita o fetch()/parse real de cada GeoJSON novo, não só a presença
  // do checkbox
  await page.evaluate(() => {
    const secaoMeioFisico = document.querySelector('.grupo[data-grupo="meio-fisico"]');
    if (secaoMeioFisico) secaoMeioFisico.classList.remove("fechada");
  });
  const checkboxesMeioFisico = await page.$$("#container-camadas-meio-fisico input[type=checkbox]");
  for (const checkbox of checkboxesMeioFisico) {
    await checkbox.click();
  }
  await page.waitForTimeout(2000);

  checks.meioFisicoCamadasAtivasNoMapa = await page.evaluate(() => {
    let contagem = 0;
    window.App.map.eachLayer(() => {
      contagem += 1;
    });
    // limite municipal + base + 6 camadas de meio físico + o que mais já estava ligado —
    // só confirma que o número de layers cresceu de forma consistente com 6 camadas novas
    return contagem > 6;
  });
  checks.legendaMeioFisicoPopulada = await page.evaluate(() => {
    const el = document.getElementById("legenda-meio-fisico");
    return !!el && el.querySelectorAll(".legenda-bloco").length >= 5; // 4 categóricas + ocupação da APP
  });

  await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });

  // ---------- parâmetros da URL, popup das unidades e links para os painéis (rodada 115) ----------
  const extras = await checarUrlPopupLinks(browser, baseUrl, consoleErrors, pageErrors);
  console.log("URL, popup e links:", JSON.stringify(extras.detalhe));
  Object.assign(checks, extras.checks);

  await browser.close();
  server.close();

  console.log("\n=== Checagens funcionais ===");
  console.log(JSON.stringify(checks, null, 2));

  console.log("\n=== Erros de console ===");
  console.log(consoleErrors.length ? consoleErrors.join("\n") : "(nenhum)");

  console.log("\n=== Exceções JS de página ===");
  console.log(pageErrors.length ? pageErrors.join("\n") : "(nenhuma)");

  console.log(`\nScreenshot: ${SCREENSHOT_PATH}`);

  const ok = Object.values(checks).every(Boolean) && consoleErrors.length === 0 && pageErrors.length === 0;
  if (!ok) {
    console.error("\nFALHOU");
    process.exitCode = 1;
    return;
  }
  console.log("\nOK");
}

// abre o portal com uma busca e espera os parâmetros aplicados (url-estado.js marca data-url-pronta)
async function abrirComParametros(browser, baseUrl, busca, consoleErrors, pageErrors) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on("console", (msg) => { if (msg.type() === "error") consoleErrors.push(`${busca}: ${msg.text()}`); });
  page.on("pageerror", (err) => pageErrors.push(`${busca}: ${err}`));
  await page.goto(`${baseUrl}mapa.html${busca}`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.documentElement.dataset.urlPronta === "1", null, { timeout: 15000 });
  return page;
}

async function checarUrlPopupLinks(browser, baseUrl, consoleErrors, pageErrors) {
  const checks = {};
  const detalhe = {};
  // conteúdo de texto do popup de uma unidade, montado pelo próprio portal
  const textoPopup = (page, cnes) => page.evaluate((c) => {
    const m = window.App.layers.unidadesSaude.getLayers().find((l) => l.feature.properties.cnes === c);
    m.openPopup();
    const el = m.getPopup().getElement().querySelector(".leaflet-popup-content");
    return { texto: el.innerText, links: Array.from(el.querySelectorAll("a")).map((a) => a.getAttribute("href")) };
  }, cnes);

  // cota=952: liga a mancha, só a de 952 visível, controle na posição da cota
  let page = await abrirComParametros(browser, baseUrl, "?cota=952", consoleErrors, pageErrors);
  detalhe.cota952 = await page.evaluate(() => {
    const cam = window.App.layers.cotasInundacao;
    const visiveis = cam.getLayers().filter((l) => l.options.fillOpacity > 0).map((l) => l.feature.properties.cota_cm);
    return { naMapa: window.App.map.hasLayer(cam), visiveis: [...new Set(visiveis)], slider: document.getElementById("slider-cota").value,
      rotulo: document.getElementById("rotulo-cota").textContent, caixa: document.querySelector('input[data-camada="cotasInundacao"]').checked,
      linkCheia: document.getElementById("link-cheia-saude").getAttribute("href"), url: location.search };
  });
  const cotas = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "geoportal", "estatisticas-por-cota.json"), "utf8")).cotas_disponiveis_cm;
  checks.urlCota952LigaManchaEControle = detalhe.cota952.naMapa && detalhe.cota952.caixa && JSON.stringify(detalhe.cota952.visiveis) === "[952]" &&
    detalhe.cota952.slider === String(cotas.indexOf(952)) && detalhe.cota952.rotulo === "952 cm" && detalhe.cota952.url === "?cota=952";
  // mover o controle grava a cota na URL e atualiza o link da cheia e saúde
  detalhe.cotaMovida = await page.evaluate(() => {
    const s = document.getElementById("slider-cota");
    s.value = String(s.max);
    s.dispatchEvent(new Event("input"));
    return { url: location.search, link: document.getElementById("link-cheia-saude").getAttribute("href") };
  });
  checks.urlCotaMovidaVaiParaUrl = detalhe.cotaMovida.url === `?cota=${cotas[cotas.length - 1]}` && detalhe.cotaMovida.link === `dashboards/cheia-saude.html?cota=${cotas[cotas.length - 1]}`;
  // links do portal para os painéis: 200 no servidor local
  const links = await page.evaluate(() => ({ paineis: document.getElementById("link-paineis").getAttribute("href"), cheia: document.getElementById("link-cheia-saude").getAttribute("href") }));
  const status = async (href) => (await page.request.get(new URL(href, page.url()).toString())).status();
  detalhe.links = { ...links, paineisStatus: await status(links.paineis), cheiaStatus: await status(links.cheia) };
  await page.close();

  // unidade válida: popup aberto e mapa centralizado; popup sem 2026 e sem total; "Ver na saúde" com 200
  page = await abrirComParametros(browser, baseUrl, "?unidade=2247143", consoleErrors, pageErrors);
  detalhe.unidade = await page.evaluate(() => {
    const el = document.querySelector(".leaflet-popup-content");
    const m = window.App.layers.unidadesSaude.getLayers().find((l) => l.feature.properties.cnes === "2247143");
    const c = window.App.map.getCenter(), p = m.getLatLng();
    return { aberto: !!el && el.innerText.includes("2247143"), centro: Math.abs(c.lat - p.lat) < 0.01 && Math.abs(c.lng - p.lng) < 0.01, zoom: window.App.map.getZoom(), url: location.search };
  });
  checks.urlUnidadeAbrePopup = detalhe.unidade.aberto && detalhe.unidade.centro && detalhe.unidade.zoom >= 15 && detalhe.unidade.url === "?unidade=2247143";
  // todas as unidades: nenhum popup com 2026, total ou soma; 2017 e 2023 com o aviso; a sem classe sem número de população
  const geo = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "geoportal", "unidades-saude-esf-ubs.geojson"), "utf8"));
  const popups = [];
  for (const f of geo.features) popups.push({ cnes: f.properties.cnes, classe: f.properties.classe, ...(await textoPopup(page, f.properties.cnes)) });
  await page.evaluate(() => window.App.map.closePopup());
  const comClasse = popups.filter((x) => x.classe !== "sem classe"), semClasse = popups.filter((x) => x.classe === "sem classe");
  const aviso = "Número informado pelos profissionais de saúde; o conceito está em confirmação; não somar nem comparar com o Censo";
  detalhe.popups = { n: popups.length, semClasse: semClasse.map((x) => x.texto.replace(/\s+/g, " ")) };
  checks.popupUnidadesSem2026NemTotal = popups.length === 22 && popups.every((x) => !/\(2026\)|\b(total|soma)\b/i.test(x.texto)) &&
    comClasse.every((x) => x.texto.includes("População informada (2017)") && x.texto.includes("População informada (2023)") && x.texto.includes(aviso)) &&
    popups.every((x) => JSON.stringify(x.links) === JSON.stringify([`dashboards/saude.html?unidade=${x.cnes}`]));
  checks.popupPrisionalSemPopulacao = semClasse.length === 1 && semClasse[0].cnes === "4126947" &&
    semClasse[0].texto.includes("Equipe de Saúde Prisional · sem classe · não entra em comparações de cobertura") &&
    !/popula/i.test(semClasse[0].texto) && !/\d/.test(semClasse[0].texto.replace(/CNES|4126947|BR 472, 07, KM 07|versão 4, 2026/g, ""));
  detalhe.links.saudeStatus = await status(popups[0].links[0]);
  await page.close();
  checks.linksPortalParaPaineis200 = detalhe.links.paineis === "index.html" && detalhe.links.paineisStatus === 200 &&
    detalhe.links.cheiaStatus === 200 && detalhe.links.saudeStatus === 200;

  // inválidos: unidade e cota geram aviso e saem da URL; chave de camada inválida é ignorada, a válida fica
  const errosAntes = consoleErrors.length + pageErrors.length;
  page = await abrirComParametros(browser, baseUrl, "?unidade=1234567&cota=999&camada=bairros,naoexiste&origem=teste", consoleErrors, pageErrors);
  detalhe.invalidos = await page.evaluate(() => ({
    url: location.search, aviso: (document.getElementById("aviso-url") || {}).innerText || "",
    bairros: window.App.map.hasLayer(window.App.layers.bairros), popup: !!document.querySelector(".leaflet-popup-content"),
    cota: document.getElementById("rotulo-cota").textContent,
  }));
  await page.click("#aviso-url button");
  detalhe.invalidos.avisoFechado = !(await page.$("#aviso-url"));
  await page.close();
  checks.urlInvalidosGeramAvisoESaemDaUrl = detalhe.invalidos.url === "?camada=bairros&origem=teste" && /unidade=1234567/.test(detalhe.invalidos.aviso) &&
    /cota=999/.test(detalhe.invalidos.aviso) && !detalhe.invalidos.popup && detalhe.invalidos.cota === `${cotas[0]} cm` && detalhe.invalidos.avisoFechado &&
    consoleErrors.length + pageErrors.length === errosAntes;
  checks.urlCamadaInvalidaIgnorada = detalhe.invalidos.bairros && /camada=naoexiste/.test(detalhe.invalidos.aviso);

  // endereço antigo do mapa (rodada A2): index.html com parâmetro de mapa vai para mapa.html, com busca e #;
  // só ?origem= (parâmetro neutro) fica na página inicial
  page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(`${baseUrl}index.html?cota=952#x`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => document.documentElement.dataset.urlPronta === "1", null, { timeout: 15000 });
  const u1 = new URL(page.url());
  await page.goto(`${baseUrl}index.html?origem=t`, { waitUntil: "networkidle" });
  const u2 = new URL(page.url());
  await page.close();
  detalhe.redirecionamento = { comCota: u1.pathname + u1.search + u1.hash, semParametroDeMapa: u2.pathname + u2.search };
  checks.redirecionamentoIndexParaMapa = detalhe.redirecionamento.comCota === "/mapa.html?cota=952#x" && detalhe.redirecionamento.semParametroDeMapa === "/index.html?origem=t";
  return { checks, detalhe };
}

main();
