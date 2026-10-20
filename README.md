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

O painel lateral do geoportal organiza as camadas em 11 grupos
colapsáveis, na mesma ordem abaixo — todas já publicadas (versão
preliminar, ainda em revisão visual):

- **Mapa base:** alternância entre mapa (OpenStreetMap) e imagem de
  satélite.
- **Saúde:** as 22 unidades de saúde da atenção primária (ESF e UBS), com
  filtro por classe; cadastro do CNES (Ministério da Saúde) revisado e
  corrigido pela equipe do projeto com informações dos profissionais de
  saúde do município (versão 4, 2026).
- **Demografia:** densidade populacional por setor censitário, com
  seletor de ano (2022/2010/2000 — 1 ano visível por vez, malhas de
  setores diferentes entre si e não comparáveis geometricamente);
  concentração de crianças (0-4 anos) e de idosos (60+ anos) por setor,
  só para 2022 (a fonte de 2000/2010 não traz distribuição etária por
  setor); bairros do Censo 2022 (IBGE), só na sede, com população, área,
  densidade, setores e endereços por bairro; e um quadro com indicadores
  municipais do Censo 2022 (renda média domiciliar per capita, % de
  domicílios com água e com esgoto inadequados), que não variam por setor.
- **Inundação:** cotas históricas de inundação registradas pelo Serviço
  Geológico do Brasil, com a mancha e as unidades de saúde (ESF/UBS)
  dentro dela em cada cenário.
- **Uso do solo:** série histórica MapBiomas (1985-2024), com linha do
  tempo por ano.
- **Hidrografia e terreno:** bacias hidrográficas por nível Otto
  Pfafstetter (da bacia regional às microbacias locais), hipsometria e
  relevo sombreado (MDT ANADEM) e, como contexto, rede hidrográfica e
  estações climatológicas do INMET.
- **Meio físico** *(novo, versão preliminar):* geologia, geomorfologia,
  pedologia e vegetação nativa (BDiA/IBGE — escala 1:250.000, leitura
  regional/contextual, não decisão em nível de microárea), poços de água
  subterrânea (SIAGAS/CPRM) e APP hídrica calculada (aproximação
  metodológica a partir da rede hidrográfica, com indicador de % de
  ocupação antrópica por MapBiomas).
- **Malha viária:** camadas de contexto, desligadas por padrão:
  mapeamento colaborativo (OpenStreetMap) e malha federal oficial (DNIT),
  com classificação de pavimento.
- **Estrutura fundiária:** imóveis rurais do CAR (Cadastro Ambiental
  Rural), com status do imóvel (ativo, pendente, cancelado); só o polígono
  e atributos básicos.
- **Educação:** escolas do INEP (Catálogo de Escolas + Censo Escolar
  2024), com filtro por dependência administrativa; só as escolas com
  coordenada geocodificada na fonte aparecem no mapa.
- **Cobertura móvel (ANATEL):** % de cobertura de rede móvel por setor
  censitário (todas as tecnologias e operadoras), com seletor de período —
  pensada para localizar onde não há cobertura hoje.

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
- **Nível do Rio Uruguai (ANA):** ingestão implementada para a estação
  77150000, no próprio município (`scripts/download/nivel_rio_ana.py`):
  série histórica diária da régua (desde 1940) e telemetria de 15 minutos
  por período, com idempotência e metadado `.json` irmão. Não há coleta
  agendada: cada atualização é uma execução do script.
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

## Estudos (produtos de trabalho, fora do geoportal)

Notas técnicas com tabelas, mapas e figuras, cada arquivo com `.json`
irmão. Estão pendentes de conferência e nenhuma delas alimenta o portal.

- **População** — `docs/dinamica_populacional/`: caracterização de
  2000–2022 por setor censitário (estrutura etária, onde mora mais e menos
  gente, ganhos e perdas entre 2010 e 2022) e série longa municipal de
  1970–2022.
- **Exposição à inundação por endereços** — `docs/exposicao_inundacao/`:
  endereços residenciais e população dentro de cada mancha por cota do SGB
  (CNEFE e Censo 2022), com a ficha de métodos e a transcrição dos números
  oficiais publicados (ANA e IBGE) em `numeros_publicados/`.
- **Acesso às unidades de saúde com vias alagadas** —
  `docs/acessibilidade_inundacao/`: quantas pessoas perdem o caminho pela
  rede viária até uma unidade ESF/UBS em cada cota, e o roteiro de
  conferência em campo de pontes e travessias.
- **Manchas de inundação** — comparação entre fontes de mancha dentro de
  um domínio comum (`scripts/vetorizacao/comparar_fontes_inundacao.py`;
  método das camadas vetorizadas em `docs/vetorizacao/`). As camadas
  vetorizadas de figura não são dado oficial e não vão ao portal.
- **Água vista por satélite** — água observada em cenas Sentinel-1
  (radar) e Sentinel-2 (óptico) nas datas de cheia, curva nível do rio ×
  área alagada e comparação com as manchas do SGB
  (`scripts/processamento/agua_observada_sentinel*.py`). Os produtos ficam
  em `data/processed/` (fora do git); ainda não há nota em `docs/`.

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
