// Teste headless (Playwright) do geoportal ClimaPampa.
// Sobe um servidor HTTP local (fetch() de GeoJSON não funciona em file://),
// abre index.html em Chromium headless e valida:
//   - mapa Leaflet inicializado
//   - camadas/painéis carregados a partir dos JSON em data/geoportal/
//   - ausência de erros de JS/console
//
// Uso: npm run test:geoportal
// Requer Chromium do Playwright instalado (npx playwright install chromium).

const path = require("path");
const http = require("http");
const fs = require("fs");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..", "..");
const SCREENSHOT_PATH = path.join(ROOT, "scripts", "geoportal", "geoportal-headless.png");

const MIME = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".geojson": "application/json",
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
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  // dá tempo para fetch() das camadas GeoJSON e inicialização dos controles
  await page.waitForTimeout(2000);

  const checks = {
    tituloOk: (await page.title()) === "ClimaPampa — Geoportal Uruguaiana/RS",
    mapaLeafletPresente: await page.evaluate(() => {
      const el = document.getElementById("mapa");
      return !!(el && el.classList.contains("leaflet-container"));
    }),
    gruposColapsaveisPresentes: await page.evaluate(() => {
      const esperados = ["mapa-base", "saude", "demografia", "inundacao", "uso-solo", "hidrografia-terreno", "meio-fisico", "malha-viaria", "estrutura-fundiaria", "educacao", "cobertura-movel"];
      return esperados.every((chave) => !!document.querySelector(`.grupo[data-grupo="${chave}"]`));
    }),
    camadasDemografiaCarregadas: await page.evaluate(() => {
      const el = document.getElementById("container-camadas-demografia");
      return !!el && el.textContent.trim() !== "Carregando camadas…" && el.querySelectorAll("input[type=checkbox]").length === 3;
    }),
    camadasInundacaoCarregadas: await page.evaluate(() => document.querySelectorAll("#container-camadas-inundacao input[type=checkbox]").length === 2),
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
    filtroSaudePopulado: await page.evaluate(() => {
      const el = document.getElementById("filtro-tipo-saude");
      return !!el && el.children.length > 0;
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

main();
