# uruguaiana-clima-saude

Aquisição, padronização e processamento de dados espaciais (vetoriais e
matriciais) para apoiar o projeto **ClimaPampa** (PET-Saúde: Clima —
UNIPAMPA + Secretaria Municipal de Saúde de Uruguaiana), subsidiando o
Eixo III (Comunicação e Inovação), objetivo específico 1: plataforma
digital geoespacial para vigilância em saúde e tomada de decisão
territorial.

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
