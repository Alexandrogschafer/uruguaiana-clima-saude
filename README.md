# uruguaiana-clima-saude

🌐 **Acesse o geoportal:** [alexandrogschafer.github.io/uruguaiana-clima-saude](https://alexandrogschafer.github.io/uruguaiana-clima-saude/)

Geoportal e pipeline de dados espaciais do projeto **ClimaPampa**
(PET-Saúde: Clima — UNIPAMPA + Secretaria Municipal de Saúde de
Uruguaiana), subsidiando o Eixo III (Comunicação e Inovação), objetivo
específico 1: plataforma digital geoespacial para vigilância em saúde e
tomada de decisão territorial.

O repositório reúne dois blocos: os **scripts** que baixam, padronizam e
cruzam dados espaciais (vetoriais e matriciais) sobre clima, território,
vulnerabilidade socioambiental e saúde; e o **geoportal** (`index.html` +
`css/` + `js/`), um mapa interativo que publica essas camadas para
consulta por gestores, equipes de saúde e pesquisadores.

## Camadas disponíveis

O painel lateral do geoportal organiza as camadas nos mesmos grupos
colapsáveis abaixo — todas já publicadas (versão preliminar, ainda em
revisão visual):

- **Mapa base:** alternância entre mapa (OpenStreetMap) e imagem de
  satélite.
- **Saúde:** estabelecimentos de saúde do município (UBS/ESF, hospitais,
  clínicas, farmácias, laboratórios, vigilância em saúde), com filtro por
  tipo de unidade e fontes CNES/OpenStreetMap.
- **Demografia:** densidade populacional por setor censitário, com
  seletor de ano (2022/2010/2000 — 1 ano visível por vez, malhas de
  setores diferentes entre si e não comparáveis geometricamente);
  concentração de crianças (0-4 anos) e de idosos (60+ anos) por setor,
  só para 2022 (a fonte de 2000/2010 não traz distribuição etária por
  setor).
- **Inundação:** cotas históricas de inundação registradas pelo Serviço
  Geológico do Brasil, com área e população/estabelecimentos de saúde
  estimados expostos em cada cenário.
- **Uso do solo:** série histórica MapBiomas (1985-2024), com linha do
  tempo por ano.
- **Hidrografia e terreno:** bacias hidrográficas por nível Otto
  Pfafstetter (da bacia regional às microbacias locais), rede
  hidrográfica, hipsometria e relevo sombreado (MDT ANADEM).
- **Meio físico** *(novo, versão preliminar):* geologia, geomorfologia,
  pedologia e vegetação nativa (BDiA/IBGE — escala 1:250.000, leitura
  regional/contextual, não decisão em nível de microárea), poços de água
  subterrânea (SIAGAS/CPRM) e APP hídrica calculada (aproximação
  metodológica a partir da rede hidrográfica, com indicador de % de
  ocupação antrópica por MapBiomas).
- **Malha viária:** camada de contexto (OpenStreetMap).

## Dados disponíveis para análise (ainda fora do geoportal)

Além das camadas espaciais acima, o pipeline já processou séries
tabulares de saúde e clima que ainda não estão publicadas como camada de
mapa (não são espacializáveis por bairro/setor na fonte, ou ainda faltam
integrar):

- **Mortalidade (SIM/DATASUS):** óbitos por causa (CID-10), agrupados em
  respiratórias/calor extremo/afogamento — 2010 até o último ano fechado.
- **Arboviroses (SINAN/DATASUS):** notificações mensais de dengue,
  chikungunya e zika — 2010 até o último ano fechado, com o ano corrente
  à parte (`*_preliminar.csv`) e explicitamente marcado como **dado
  sujeito a revisão** (atraso de notificação, típico de vigilância
  epidemiológica — meses ainda não decorridos aparecem como sem dado, não
  como zero).
- **Internações respiratórias (SIH/DATASUS):** internações mensais com
  diagnóstico respiratório — 2010 até o último ano fechado.
- **Precipitação mensal:** série 2010 até o mês mais recente disponível,
  via MERGE/INPE-CPTEC (fonte primária tentada é o CHIRPS/UCSB, com troca
  automática para MERGE quando o CHIRPS bloqueia por IP — documentado por
  mês/período no metadado `.json` irmão).
- **Focos de queimada (INPE/BDQueimadas):** registro pontual (lat/lon) de
  cada foco de calor por satélite — 2010 até o ano mais recente.
- **Nível do Rio Uruguai (ANA HidroWeb):** viabilidade confirmada (estação
  telemétrica no próprio município) — hoje só uma consulta de exemplo,
  **ingestão contínua ainda não implementada**.
- **Risco geológico (CPRM):** Carta de Suscetibilidade a Movimentos
  Gravitacionais de Massa e Inundações — já processada, ainda não
  publicada como camada do geoportal.

Todos os arquivos estão em `data/raw/` (ou `data/processed/`, no caso de
produtos derivados), cada um com metadado `.json` irmão descrevendo fonte,
método e período coberto.

### Investigado e indisponível (não por falta de tentativa)

- **Saneamento (SNIS):** nenhuma rota de download programático encontrada
  sem exigir login gov.br — três alternativas testadas e documentadas em
  `data/raw/saneamento_snis_indisponivel.json`.

**Para o inventário técnico completo** (toda fonte, com licença, resolução
espacial/temporal, URL/API, script responsável e observações detalhadas
de cada coleta) — inclusive as camadas já publicadas no geoportal — ver
`data/catalogo_fontes.csv`, mantido vivo a cada nova fonte processada.

## Padrões técnicos

- **Linguagem:** Python. Bibliotecas: `geopandas`, `rasterio`, `xarray`,
  `rasterstats`, `pyproj`, `requests`/`httpx`.
- **CRS padrão:** SIRGAS 2000 / UTM 21S — `EPSG:31981`. Dados brutos podem
  vir em outro CRS; todo processamento intermediário reprojeta para o
  padrão.
- **Área de estudo:** referenciada sempre a partir de um único arquivo:
  `config/area_estudo.geojson`.
- **Scripts de download:** idempotentes (checam se o arquivo já existe
  antes de baixar de novo) e logam fonte/data/tamanho.
- **Catálogo de fontes:** toda fonte nova é registrada em
  `data/catalogo_fontes.csv`.
- **Nomenclatura de arquivos:**
  `{tema}_{fonte}_{ano-ou-periodo}_{resolucao}.{ext}`
  ex.: `uso-solo_mapbiomas_2023_30m.tif`
- **Metadados:** todo output processado tem um arquivo irmão `.json`
  descrevendo fonte, data de processamento e transformação aplicada.
- **Replicabilidade:** a metodologia deve ser adaptável a outros
  municípios (especialmente de fronteira, ribeirinhos ou expostos a
  enchentes/estiagens). Parametrizar por código IBGE, evitar hardcode.

## Estrutura de pastas

```
uruguaiana-clima-saude/
├── README.md
├── requirements.txt
├── config/
│   └── area_estudo.geojson        # limite oficial do município (referência única)
├── data/
│   ├── raw/
│   │   ├── vetor/
│   │   └── raster/
│   ├── processed/
│   └── catalogo_fontes.csv
├── scripts/
│   ├── download/
│   ├── processamento/
│   └── utils/
├── notebooks/                     # exploração, não produção
├── dashboards/
└── docs/
```

## Município de referência (default)

- Código IBGE: `4322400` (Uruguaiana, RS)
- Todos os scripts aceitam o código IBGE como parâmetro/argumento —
  o default é Uruguaiana, mas nada deve ficar hardcoded a ponto de
  impedir reuso em outro município.

## Como começar

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium  # necessário só para scripts/download/desastres_s2id.py
python scripts/download/vetor_ibge.py --codigo-ibge 4322400
```

Isso gera `config/area_estudo.geojson` (EPSG:31981), que passa a ser a
referência única de recorte para todos os demais scripts.

## Geoportal (front-end estático)

`index.html` + `css/` + `js/` compõem o geoportal ClimaPampa — mapa
Leaflet que consome as camadas geradas em `data/geoportal/` (exportadas
pelos scripts de `scripts/geoportal/`).

### Testar localmente

O geoportal usa `fetch()` para carregar as camadas GeoJSON, então precisa
ser servido por HTTP (não abrir `index.html` direto via `file://`):

```bash
python -m http.server 8000
# abrir http://localhost:8000
```

### Teste automatizado (Playwright headless)

```bash
npm install
npx playwright install chromium   # baixa o browser headless, uma vez
npm run test:geoportal
```

O script `scripts/geoportal/test_headless.js` sobe um servidor HTTP local,
abre o geoportal em Chromium headless e valida:

- inicialização do mapa Leaflet;
- carregamento das camadas (painel de controle, sliders de cota e uso do
  solo, filtro de saúde);
- ausência de erros de JS/console.

Gera um screenshot em `scripts/geoportal/geoportal-headless.png` (não
versionado) para inspeção visual rápida.
