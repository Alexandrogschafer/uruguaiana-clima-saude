# uruguaiana-clima-saude

🌐 **Início:** [alexandrogschafer.github.io/uruguaiana-clima-saude](https://alexandrogschafer.github.io/uruguaiana-clima-saude/)
(índice por dimensão; leva às telas e ao mapa — lista completa em [Painéis](#painéis-dashboards))

🗺️ **Mapa:** [alexandrogschafer.github.io/uruguaiana-clima-saude/mapa.html](https://alexandrogschafer.github.io/uruguaiana-clima-saude/mapa.html)

**Painéis ClimaPampa** — painéis de dados de Uruguaiana — e pipeline de
dados espaciais do projeto **ClimaPampa**
(PET-Saúde: Clima — UNIPAMPA + Secretaria Municipal de Saúde de
Uruguaiana), subsidiando o Eixo III (Comunicação e Inovação), objetivo
específico 1: plataforma digital geoespacial para vigilância em saúde e
tomada de decisão territorial.

O repositório reúne três blocos: os **scripts** que baixam, padronizam e
cruzam dados espaciais (vetoriais e matriciais) sobre clima, território,
vulnerabilidade socioambiental e saúde; o **mapa** (`mapa.html` +
`css/` + `js/`), um mapa interativo que publica essas camadas para
consulta por gestores, equipes de saúde e pesquisadores; e os **painéis**
(`dashboards/`), telas com séries e tabelas lidas de `data/paineis/`. O
mapa e os painéis têm links nos dois sentidos.

**Endereços (desde a rodada A2):** `index.html` é a página inicial; o mapa
fica em `mapa.html`. Um endereço antigo do mapa com parâmetro de mapa
(`cota`, `unidade`, `camada`, `periodo`, `bairro`, `cenario`) vai sozinho
para `mapa.html`, com a busca e o `#`; sem parâmetro de mapa, abre a página
inicial. `dashboards/sintese.html` foi incorporada ao início: com `?cota=`
de uma mancha publicada leva a `dashboards/cheia-saude.html?cota=…`, sem
cota leva ao início. `dashboards/index.html` também leva ao início.

## Camadas disponíveis

O painel lateral do mapa organiza as camadas em 12 grupos
colapsáveis (contados em `mapa.html`), na mesma ordem abaixo — todas já
publicadas (versão preliminar, ainda em revisão visual). O cabeçalho do
painel tem o link **Início**, para a página inicial.

- **Mapa base:** alternância entre mapa (OpenStreetMap) e imagem de
  satélite.
- **Saúde:** as 22 unidades de saúde da atenção primária (ESF e UBS), com
  filtro por classe; cadastro do CNES (Ministério da Saúde) revisado e
  corrigido pela equipe do projeto com informações dos profissionais de
  saúde do município (versão 4, 2026). O popup mostra a população
  informada pelos profissionais de saúde em 2017 e 2023 (conceito em
  confirmação; não somar nem comparar com o Censo) e o link **Ver na
  saúde**, para a unidade na tela de saúde. A Equipe de Saúde Prisional
  aparece com borda tracejada, no filtro "Sem classe", sem população e
  fora das comparações.
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
  dentro dela em cada cota, e o link **Ver na cheia e saúde**, para a tela
  dos painéis na cota escolhida.
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
- **Base territorial:** densidade de edificações em grade de 250 m
  (Overture Maps Foundation), desligada por padrão; produto provisório,
  ainda não conferido, sem células com poucas edificações.
- **Educação:** escolas do INEP (Catálogo de Escolas + Censo Escolar
  2024), com filtro por dependência administrativa; só as escolas com
  coordenada geocodificada na fonte aparecem no mapa.
- **Cobertura móvel (ANATEL):** % de cobertura de rede móvel por setor
  censitário (todas as tecnologias e operadoras), com seletor de período —
  pensada para localizar onde não há cobertura hoje.

### Endereço do mapa com parâmetros

O mapa lê da URL os mesmos parâmetros dos painéis
(`js/url-estado.js`); valor que não existe no mapa é ignorado, sai da URL
e aparece num aviso que pode ser fechado:

| Parâmetro | Valores | O que faz |
|---|---|---|
| `cota` | uma das cotas de `data/geoportal/estatisticas-por-cota.json` (833, 952, 1205, 1252) | liga a mancha e põe o controle na cota |
| `unidade` | CNES de 7 dígitos de uma unidade da camada de saúde | centraliza a unidade e abre o popup |
| `camada` | chaves separadas por vírgula: `densidadePopulacional`, `densidadePopulacional2010`, `densidadePopulacional2000`, `criancas0a4`, `idosos60Mais`, `bairros`, `cotasInundacao`, `unidadesSaude`, `estacoesClima`, `redeHidrografica`, `malhaViaria`, `malhaViariaDnit`, `estruturaFundiaria`, `escolasInep`, `geologia`, `geomorfologia`, `pedologia`, `vegetacao`, `pocosSiagas`, `appHidrica`, `usoSolo`, `hipsometria`, `hillshade`, `coberturaMovel`, `densidadeEdificacoes` | liga cada camada; chave desconhecida sai da lista |
| `periodo` | `AAAA`, `AAAA-AAAA` ou `AAAA-MM` | só o uso do solo tem controle de ano: liga a camada no ano pedido (numa faixa, o último ano disponível) |
| `bairro` | `CD_BAIRRO` do IBGE (10 dígitos) de `data/geoportal/bairros.geojson` | liga os bairros, aproxima e destaca o bairro |

`cenario` é ignorado. Quando a cota, as camadas, o ano do uso do solo ou a
unidade aberta mudam, o estado volta para a URL (sem criar entradas no
histórico do navegador); em `camada` vão só as camadas ligadas além das que
já abrem ligadas. Exemplos: `mapa.html?camada=cotasInundacao,unidadesSaude&cota=952`,
`mapa.html?unidade=2247275`.

## Painéis (dashboards/)

Telas estáticas que leem só os JSON de `data/paineis/`; nenhum número fica
escrito no HTML. Endereço de cada uma (no site publicado, depois de
`.../uruguaiana-clima-saude/`):

| Tela | Endereço | Conteúdo |
|---|---|---|
| Início | `index.html` | índice por dimensão: rio agora, cartões das seis dimensões com números-chave e data, cheia e mapa, dados e o projeto |
| Cheia e acesso à saúde | `dashboards/cheia-saude.html` | máximas anuais do rio e o último ano, manchas do SGB por cota, unidades em relação às manchas e o que falta conferir |
| Clima | `dashboards/clima.html` | temperatura e dias de calor (INMET, estação A809), precipitação (MERGE/INPE), rio e queimadas |
| Saúde | `dashboards/saude.html` | óbitos, internações, arboviroses e as unidades ESF/UBS |
| População | `dashboards/populacao.html` | população, indicadores do Censo e pirâmides etárias |
| Território | `dashboards/territorio.html` | uso do solo e meio físico |
| Economia | `dashboards/economia.html` | PIB, agropecuária, emprego, vulnerabilidade e despesas municipais |
| Riscos | `dashboards/riscos.html` | desastres reconhecidos, suscetibilidade, setorização de risco e energia |
| Catálogo de dados | `dashboards/catalogo.html` | fontes dos produtos e das camadas, licença, resolução, acesso, selo e downloads; "Como ler" os selos e avisos |

`dashboards/index.html` e `dashboards/sintese.html` levam ao início; os
endereços antigos (`painel-*.html`) levam às telas novas. Os filtros também vão na URL (`periodo`, `unidade`, `cota`);
os links "ver no mapa" abrem o mapa com os parâmetros acima.

**Dados dos painéis (`data/paineis/`):** cada produto é um JSON pequeno no
esquema `climapampa-painel/1` (fonte, período, selo, avisos, supressão e
os dados), com o metadado irmão `.json.json` (entradas com sha256, script
e transformação). São gerados por `scripts/paineis/gerar_todos.py`, que
roda os geradores `scripts/paineis/dados_*.py` e grava também
`manifesto.json` e `paineis_espera.json`. Só entram ativos marcados como
livres em `config/paineis_ativos.csv`. Os geradores leem de `data/raw/` e
`data/processed/`; sem essas pastas não é possível regerar, mas a
publicação continua funcionando com os JSON versionados.

**Início, catálogo e downloads (desde a rodada A2):** três geradores rodam no
fim do `gerar_todos.py`, lendo só o que já está publicado:

- `dados_catalogo.py` → `catalogo_dados.json`, lido por
  `dashboards/catalogo.html` (tabela filtrável por dimensão e "Como ler").
  Entram só as linhas de `data/catalogo_fontes.csv` ligadas, em
  `config/catalogo_ligacao.csv`, a um produto de `data/paineis/` ou a uma
  camada de `data/geoportal/`; a ligação é conferida contra o que está
  publicado. Não vão a público a coluna `observacao`, a url de acesso nem o
  script. Licença não identificada aparece como "ver fonte". Para listar as
  linhas sem ligação: `python scripts/paineis/dados_catalogo.py --conferencia`.
- `dados_inicio.py` → `inicio_resumo.json`: os indicadores do topo de cada
  tela, as cotas da cheia e as contagens da página inicial. Os textos do
  início ficam em `config/textos_inicio.json`. Nenhum número do rio entra no
  resumo: a faixa do rio lê a telemetria recente no navegador.
- `exportar_csv.py` → `data/paineis/csv/<id>.csv` para todo produto com
  `dados` tabular, com o metadado irmão `<id>.csv.json` (sha256 do JSON de
  origem). O CSV é derivado do JSON publicado, com a supressão já aplicada:
  UTF-8, separador vírgula, ponto decimal, cabeçalho com os nomes das colunas
  do produto, célula vazia para valor ausente ou suprimido e lógico como
  `sim`/`não`. O manifesto aponta o CSV de cada produto (campo `csv`).

Cada tela (e o início) tem no rodapé o bloco recolhível **Dados desta tela**:
título, período e selo de cada produto, com "Baixar CSV", "Baixar JSON" e
"Sobre este dado" (fonte, período, selo, licença, data de coleta e método).

Para regerar tudo: `python scripts/paineis/gerar_todos.py` (idempotente; uma
segunda execução não grava nada). `--forcar` regrava; `--so <id>` gera um
produto.

**Atualização das fontes e rótulos (rodada 121):**

- Rótulos:
  - **provisório:** dado que a fonte ainda revisa ou não consolida, como a
    telemetria do rio ou os meses de outubro de 2026 em diante;
  - **parcial:** ano incompleto;
  - **preliminar:** arquivo preliminar da fonte, como o SIM e o SINAN do
    PRELIM.
- O ano preliminar ou parcial aparece com `*` e trecho tracejado nos
  gráficos e nas tabelas. Os indicadores do topo e do início usam o último
  ano fechado, e o preliminar ou parcial vai como aviso.
- **Decisão C1.** Dado de `comum.PROVISORIO_DESDE` (2026-10) em diante só é
  publicado marcado: `provisorio: true`, `provisorio_desde` ou aviso
  `parcial` do ano. A regra `n` de `test_regras_publicas.py` confere, e o
  `test_tela.js` confere o rótulo na tela.
- **Bruto mais recente.** Cada coletor grava `data_acesso` no `.json` irmão
  do bruto. O gerador escolhe o bruto pelo `data_acesso`
  (`comum.bruto_mais_recente`); numa atualização, o anterior vai para
  `data/raw/_substituidos/` (fora do git).
- Opções novas dos coletores:
  - `precipitacao_merge.py --ano-fim 2026 --mes-fim 2026-10 --continuar-de <csv anterior>`:
    corta no último mês completo e baixa só os meses novos;
  - `saude_sim_obitos.py --prelim-ate 2025`: os anos ainda não
    consolidados vêm do PRELIM;
  - `saude_sih_internacoes.py --ano-fim 2026`: ano parcial. No gerador, o
    mês com total menor que 50% da média dos meses anteriores do ano fica
    de fora;
  - `queimadas_inpe_mensal.py --inicio AAAA-MM --mes-fim AAAA-MM`: CSV
    mensal do INPE, lido por partes, sem guardar o arquivo nacional.
    Usado só para conferir; 2026 não foi publicado (ver o catálogo);
  - agropecuária, PIB e tabela 6579 leem o último ano em `/periodos`.

**Ids estáveis (desde a rodada 120):** o id do produto (nome do JSON) não
carrega o período. Convenção `{tema}_{fonte}_{serie|atual}_{resolucao}`:
`serie` para série temporal que cresce a cada coleta (ex.:
`saude_sih_serie_anual`), `atual` para retrato que é substituído (ex.:
`emprego_rais_atual_secao`). Período e `dados_ate` ficam dentro do JSON.
Edições fixas da fonte mantêm o ano, que é a edição e não o período
(`censo_ibge_2022_situacao-setor`, `areas-de-risco_ibge_2018_bater`,
`risco-geologico_cprm_2021_classe`, `setorizacao-risco_sgb_2014_setor`,
`areas-urbanizadas_ibge_2019_classe`), assim como os produtos do rio. Produto
que mudou de id leva o campo `substitui` com o id antigo, também no
manifesto; as telas pedem o produto ao manifesto (`js/paineis/dados.js`), que
resolve id antigo pelo `substitui`. O manifesto lista só os ids declarados
em `PRODUTOS` pelos geradores; JSON sem gerador é órfão.

Quando mais de um bruto casa com o padrão de entrada de um gerador, vale o de
`data_acesso` mais recente no `.json` irmão (sem `data_acesso`, o de
`data_processamento`); o bruto antigo nunca é apagado.

```bash
python scripts/paineis/gerar_todos.py                 # só regrava o que mudou
python scripts/paineis/gerar_todos.py --forcar        # regrava tudo
python scripts/paineis/gerar_todos.py --so <id>       # só um produto (id = nome do JSON sem extensão)
python scripts/paineis/gerar_todos.py --limpar        # move JSON órfão para data/paineis/_orfaos/ (fora do site)
```

## Séries tabulares (nos painéis, fora do mapa)

Além das camadas espaciais acima, o pipeline processa séries tabulares de
saúde e clima que não são camada de mapa (não são espacializáveis por
bairro/setor na fonte) e aparecem nos painéis:

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
- **Precipitação mensal:** série 2010–2025 via MERGE/INPE-CPTEC, fonte da
  precipitação do painel de clima (média de uma área com margem de 18 km em
  torno do município). O script de coleta
  (`scripts/download/precipitacao_merge.py`, antes chamado
  `precipitacao_chirps.py`) usa o MERGE por padrão; com
  `--fonte chirps` usa só o CHIRPS/UCSB, e com `--fonte auto` tenta o CHIRPS
  e troca para o MERGE depois de 3 falhas seguidas. O registro por mês fica
  no metadado `.json` irmão. O painel recebe os dados de
  `scripts/processamento/precipitacao_painel_clima.py`.
- **Temperatura (INMET A809):** série horária da estação no município, usada
  para temperatura, dias de calor e geada no painel de clima. A precipitação
  da estação aparece só na comparação anual com o MERGE.
- **Focos de queimada (INPE/BDQueimadas):** registro pontual (lat/lon) de
  cada foco de calor por satélite — 2010 até o ano mais recente.
- **Nível do Rio Uruguai (ANA):** ingestão implementada para a estação
  77150000, no próprio município (`scripts/download/nivel_rio_ana.py`):
  série histórica diária da régua (desde 1940) e telemetria de 15 minutos
  por período, com idempotência e metadado `.json` irmão. Não há coleta
  agendada: cada atualização é uma execução do script. Os painéis mostram
  as máximas anuais e a média diária do último ano; enquanto uma cheia
  estiver em curso, os números dela ficam em preparação e a série
  publicada para antes do evento.
- **Risco geológico (CPRM):** Carta de Suscetibilidade a Movimentos
  Gravitacionais de Massa e Inundações — área por classe na tela de
  riscos; ainda não publicada como camada do mapa.

Todos os arquivos estão em `data/raw/` (ou `data/processed/`, no caso de
produtos derivados), cada um com metadado `.json` irmão descrevendo fonte,
método e período coberto.

### Investigado e indisponível (não por falta de tentativa)

- **Saneamento (SNIS):** nenhuma rota de download programático encontrada
  sem exigir login gov.br — três alternativas testadas e documentadas em
  `data/raw/saneamento_snis_indisponivel.json`.

**Para o inventário técnico completo** (toda fonte, com licença, resolução
espacial/temporal, URL/API, script responsável e observações detalhadas
de cada coleta) — inclusive as camadas já publicadas no mapa — ver
`data/catalogo_fontes.csv`, mantido vivo a cada nova fonte processada.

## Estudos (produtos de trabalho, fora dos Painéis ClimaPampa)

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
  ex.: `uso-solo_mapbiomas_2023_30m.tif`. Produtos dos painéis
  (`data/paineis/`) seguem `{tema}_{fonte}_{serie|atual}_{resolucao}`, sem
  período no id (ex.: `saude_sih_serie_anual`); o período fica dentro do
  JSON.
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
├── index.html                     # página inicial (índice por dimensão)
├── mapa.html, css/, js/           # mapa (js/paineis/: base comum dos painéis)
├── config/
│   ├── area_estudo.geojson        # limite oficial do município (referência única)
│   └── paineis_ativos.csv         # autorização de cada ativo nos painéis
├── data/
│   ├── raw/                       # fora do git
│   │   ├── vetor/
│   │   └── raster/
│   ├── processed/                 # fora do git
│   ├── geoportal/                 # camadas publicadas no mapa
│   ├── paineis/                   # JSON publicados nos painéis
│   └── catalogo_fontes.csv
├── scripts/
│   ├── download/
│   ├── processamento/
│   ├── geoportal/                 # exportação das camadas e teste do mapa
│   ├── paineis/                   # geradores e testes dos painéis
│   └── utils/
├── notebooks/                     # exploração, não produção
├── dashboards/                    # telas dos painéis
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

## Mapa (front-end estático)

`mapa.html` + `css/` + `js/` compõem o mapa dos Painéis ClimaPampa — mapa
Leaflet que consome as camadas geradas em `data/geoportal/` (exportadas
pelos scripts de `scripts/geoportal/`).

### Testar localmente

O mapa usa `fetch()` para carregar as camadas GeoJSON, então precisa
ser servido por HTTP (não abrir `mapa.html` direto via `file://`):

```bash
python -m http.server 8000
# abrir http://localhost:8000 (início) e http://localhost:8000/mapa.html (mapa)
```

O mesmo servidor serve os painéis: `http://localhost:8000/dashboards/cheia-saude.html`.

## Testes

```bash
npm install
npx playwright install chromium        # baixa o browser headless, uma vez

npm run test:geoportal                 # node scripts/geoportal/test_headless.js
npm run test:paineis                   # node scripts/paineis/test_tela.js (todas as telas)
node scripts/paineis/test_tela.js cheia-saude --saida /tmp/telas   # uma tela; screenshots fora do repositório
python scripts/paineis/test_regras_publicas.py
python -m unittest scripts/paineis/test_comum.py
python scripts/paineis/test_rio.py
python scripts/paineis/test_contraste.py   # contraste dos tokens de texto (claro e escuro)
```

- `scripts/geoportal/test_headless.js`: sobe um servidor HTTP local, abre o
  mapa em Chromium headless e confere o mapa, os grupos de camadas,
  os controles, os parâmetros da URL, o popup das unidades, os links para
  os painéis e a ausência de erros de JS e de console. O mapa precisa do
  Leaflet e dos tiles pela internet. O screenshot vai para uma pasta
  temporária do sistema (ou para a pasta da variável `SCREENSHOT_DIR`),
  fora do repositório.
- `scripts/paineis/test_tela.js`: cada tela em 1280 e 390 px, claro e
  escuro; valores, dicas e tabelas iguais aos JSON, filtros na URL, links
  e erros.
- `scripts/paineis/test_regras_publicas.py`: regras do repositório público
  sobre os arquivos publicados (termos barrados lidos de uma lista local
  fora do git, caminhos locais, pontos de endereço, autorização, supressão,
  tamanho, metadado, unidades sem 2026 nem soma, nenhum dado da cheia em
  curso, links dos painéis com chaves de camada que existem).
- `scripts/paineis/test_comum.py`: funções comuns dos geradores.
- `scripts/paineis/test_rio.py`: trava da cheia em curso nos produtos do
  rio, com leituras simuladas.
- `scripts/paineis/test_contraste.py`: contraste WCAG dos tokens de texto
  e de destaque de `css/paineis.css` contra cartão e página, claro e
  escuro (texto ≥ 4,5; marca em texto e elemento gráfico ≥ 3).

## Créditos

- **Fonte tipográfica:** Plus Jakarta Sans — Copyright 2020 The Plus Jakarta
  Sans Project Authors (https://github.com/tokotype/PlusJakartaSans), sob a
  SIL Open Font License 1.1. Os arquivos e a licença estão em
  `fonts/plus-jakarta-sans/` (`OFL.txt`) e são servidos do próprio
  repositório, sem chamada a serviços externos de fontes.
