# Ficha de métodos — exposição à inundação por endereços

Documento gerado por `scripts/processamento/exposicao_inundacao_robustez.py` (auditoria de robustez das estimativas de exposição). Descreve o que o código faz; cada item cita o arquivo e a função. Município: código IBGE 4322400. CRS de trabalho: EPSG:31981.

## 1. Estimadores

### 1.1 Estimativa por endereços

    E(H) = Σ_s P_s · n_s(H) / N_s

- **P_s**: população total do setor censitário s de 2022 (variável V0001 dos agregados por setor, coluna `pop` da camada de setores; `dinamica_populacional_2022.py`, função `setores_2022`).
- **N_s**: número de endereços de domicílio particular (CNEFE 2022, espécie 1) atribuídos ao setor s (`exposicao_inundacao_enderecos.py`, função `main`: `pts.groupby("setor_2022").size()`).
- **n_s(H)**: desses endereços, os que têm o ponto dentro do polígono H (`exposicao_inundacao_cenarios.py`, função `expor`).
- No código, cada endereço recebe `pop_est_setor = P_s / N_s` e a estimativa é a soma nos endereços dentro de H.
- **De onde vem o setor do endereço**: do código de setor que o CNEFE traz (os 15 primeiros dígitos de `COD_SETOR`, setor preliminar), ligado ao setor de divulgação pelo histórico de formação dos setores do IBGE (`dinamica_populacional_2022.py`, função `cnefe_2022`). Só quando o setor preliminar foi dividido em mais de um setor de divulgação é que entra a posição do ponto: fica a parte que contém o ponto ou, se nenhuma contém, a mais próxima. Contagem no município: codigo_oficial = 42.225; divisao_ponto_no_poligono = 4.876; divisao_parte_mais_proxima = 92.
- A soma das estimativas por endereço é 116.784; a população do município é 117.210. A diferença é a população dos setores sem endereço de domicílio particular.

### 1.2 Método por área do setor

    A(H) = Σ_s P_s · área(s ∩ H) / área(s)

- `vulnerabilidade_inundacao.py`, função `calcular_exposicao_por_setor` (interseção setor × polígono; a fração é limitada a 1), chamada por `exposicao_inundacao_estimativas_oficiais.py`, função `populacao_por_area_e_uso_do_solo`. Área do setor calculada da geometria, no CRS de trabalho.

### 1.3 Método por uso do solo

    U(H) = Σ_s P_s · urb(s ∩ H) / urb(s)      se urb(s) / área(s) ≥ limiar
           Σ_s P_s · área(s ∩ H) / área(s)    caso contrário (recaída no método por área)

- **urb(·)**: área dos pixels da classe 24 (área urbanizada) do raster `data/raw/raster/uso-solo_mapbiomas_2024_30m.tif` dentro da geometria, contados por estatística zonal (`calcular_area_urbanizada_km2`); pixel de 746,1 m².
- **Limiar**: constante `LIMIAR_PCT_AREA_URBANIZADA_SETOR` = 0.05 (5 % da área do setor), `scripts/processamento/vulnerabilidade_inundacao.py`, linha 155; aplicada na função `calcular_exposicao_por_setor`, linha 444. Setor com área urbanizada abaixo do limiar é tratado como rural disperso e usa o método por área.
- Setor acima do limiar e sem pixel urbanizado dentro de H contribui com zero. Com limiar 0 todo setor usa o uso do solo; em setor sem pixel de área urbanizada a fração fica indefinida (0/0) e o código a troca por 0 (np.where(area_urbanizada_setor > 0, …, nan) seguido de fillna(0)): o setor contribui com 0 pessoas, sem erro de divisão.
- Raster, segundo o metadado: fonte: MapBiomas Brasil — Coleção 10 (Land Use and Land Cover, produto 'coverage'); colecao: collection_10 (chamada de 'Coleção 10.1' no site oficial; série 1985-2024); ano: 2024; resolucao_espacial_nativa: 30m (raster original em WGS84 geográfico, EPSG:4326); crs_processado: EPSG:31981.

### 1.4 Versão pela grade estatística

    G(H) = Σ_c P_c · n_c(H) / N_c

- Células da grade estatística do Censo 2022: 200 m na área urbana e 1 km na rural (1 km: 5.586 células; 200 m: 2.718 células); `dinamica_populacional_2022.py`, função `grade_2022`.
- **P_c** é a população da célula (campo TOTAL), repartida igualmente entre os endereços de domicílio particular dentro da célula (**N_c**).
- O endereço é associado à célula por junção espacial (ponto dentro da célula; `exposicao_inundacao_enderecos.py`, função `main`); endereço fora de toda célula fica com zero.

### 1.5 Manchas cumulativas

- Corrige a geometria com buffer(0), dissolve as feições pela cota e, em ordem crescente de cota, acumula a união: a geometria da cota X é a união das manchas de cota menor ou igual a X; a mancha da cota sozinha fica na coluna geom_propria (`scripts/processamento/exposicao_inundacao_cenarios.py`, função `carregar_cenarios`).

## 2. Contagem espacial

- **Predicado**: `within` (ponto dentro do polígono), em `exposicao_inundacao_cenarios.py`, função `expor`. Um ponto exatamente sobre a borda **não** é contado: `within` exige que o ponto esteja no interior.
- **Coordenada repetida**: todos os endereços são contados, um por registro. No município, 9.418 dos 47.193 endereços de domicílio particular dividem a coordenada (arredondada a 1 cm) com outro; o estudo por endereços só os conta à parte, não os funde.
- **Endereço sem coordenada**: 0 dos 56.323 registros do arquivo do CNEFE do município. O código não tem tratamento próprio para esse caso (converte latitude e longitude diretamente em ponto).
- **Nível de geocodificação** (variável NV_GEO_COORD): 1 = Endereço - coordenada original do Censo 2022; 2 = Endereço - coordenada modificada (apartamentos em um mesmo número no logradouro) ²; 3 = Endereço - coordenada estimada (endereços originalmente sem coordenadas ou coordenadas inválidas) ³; 4 = Face de quadra; 5 = Localidade; 6 = Setor censitário. "Coordenada precisa" nas tabelas = níveis 1 e 2.

## 3. Versões

### 3.1 Ambiente

| Componente | Versão |
|---|---|
| python | 3.14.4 |
| geopandas | 1.1.4 |
| shapely | 2.1.2 |
| pyproj | 3.7.2 |
| pandas | 3.0.5 |
| numpy | 2.5.1 |
| rasterio | 1.5.0 |
| rasterstats | 0.21.0 |
| matplotlib | 3.11.1 |
| pyogrio | 0.13.0 |
| networkx | 3.6.1 |
| openpyxl | 3.1.5 |
| xlrd | 2.0.2 |

### 3.2 Bases, como estão em `data/catalogo_fontes.csv`

| Tema | Fonte | Resolução espacial | Referência temporal | Data de acesso |
|---|---|---|---|---|
| cnefe_2022 | IBGE — CNEFE (Cadastro Nacional de Endereços para Fins Estatísticos) do Censo 2022 | endereço (coordenada) | estático (Censo 2022) | 2026-10-01 |
| agregados_setores_2022_temas | IBGE — Censo 2022, Agregados por Setores Censitários (temas básico, demografia e características do domicílio 1) + dicionário de dados | setor censitário | estático (Censo 2022) | 2026-10-01 |
| setores_censitarios | IBGE (Malha de Setores Censitários — Divisões Intramunicipais) | setor censitário | estático (Censo 2022) | 2026-07-27 |
| vulnerabilidade_censo | IBGE (Censo 2022: Agregados por Setores Censitários + API de Agregados/SIDRA) | setor censitário (renda/água/esgoto aproximados por município) | estático (Censo 2022) | 2026-07-27 |
| setores_historico_formacao_2010_2022 | IBGE — Histórico de formação dos Setores Censitários 2010–2022 + leia-me de comparabilidade 2010–2022 | setor censitário | 2010, malhas intermediárias 2011–2021, 2022 (preliminar, intermediária e divulgação) | 2026-10-01 |
| grade_estatistica_2022 | IBGE — Grade Estatística do Censo 2022 | 200 m (urbano) e 1 km (rural) | estático (Censo 2022) | 2026-10-01 |
| uso_cobertura_solo | MapBiomas | 30m (nativa; ~27m após reprojeção UTM) | série temporal a cada 5 anos (1985-2020) + ano mais recente (2024) | 2026-07-28 |
| cotas_inundacao | SGB (Serviço Geológico do Brasil) | seção/estação Uruguaiana | estático | 2026-07-27 |
| manchas_inundacao_sgb_servicos | SGB — serviços de manchas de inundação da pasta hidrologia (MapServer) | mancha por cota | estático (snapshot no acesso) | 2026-10-03 |
| setorizacao_risco_sgb | SGB — setorização de risco (MapServer gestaoterritorial/risco, camada 0) | setor de risco (mapeamento de campo) | estático (data de cada setor no atributo data_setor) | 2026-10-03 |
| populacao_areas_de_risco_ibge | IBGE — População em áreas de risco no Brasil (2018), base territorial estatística de áreas de risco (BATER) | área de risco (BATER); população do Censo 2010 | estático (publicação de 2018) | 2026-10-03 |
| area_diretamente_atingida_maio_2024 | FEPAM-RS — arquivos geoespaciais dos eventos climáticos de maio de 2024: área diretamente atingida | por município (482 feições no estado) | estático (evento de maio de 2024; arquivo publicado em 2025) | 2026-10-03 |
| areas_urbanizadas_ibge | IBGE — Áreas Urbanizadas do Brasil 2019 | polígono de área urbanizada | estático (referência 2019) | 2026-10-06 |
| limite_municipal | IBGE (malhas territoriais) | municipal | estático (ano de referência) | não registrado |

### 3.3 Bases, como estão nos metadados dos arquivos

| Base | Metadado | O que registra |
|---|---|---|
| CNEFE 2022 (arquivo do município) | data/raw/cache_dinamica_populacional/cnefe_2022/4322400_URUGUAIANA.zip.json | data_acesso: 2026-10-02T01:45:43+00:00; last_modified_origem: não registrado; url: https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/Municipio/43_RS/4322400_URUGUAIANA.zip |
| agregados por setor 2022, básico | data/raw/cache_dinamica_populacional/agregados_2022/Agregados_por_setores_basico_BR_20260520.zip.json | data_acesso: 2026-10-02T01:45:41+00:00; last_modified_origem: Wed, 20 May 2026 13:37:23 GMT; url: https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/Agregados_por_Setor_csv/Agregados_por_setores_basico_BR_20260520.zip |
| manchas por cota (SGB) | data/raw/vetor/cotas-inundacao_sgb_atual_vetorial.json | data_processamento: 2026-07-27T16:25:32.723188+00:00 |
| uso do solo (MapBiomas) | data/raw/raster/uso-solo_mapbiomas_2024_30m.json | data_processamento: 2026-07-28T00:27:05.645068+00:00; colecao: collection_10 (chamada de 'Coleção 10.1' no site oficial; série 1985-2024); ano: 2024 |
| SGB — setorização de risco | data/raw/vetor/setorizacao-risco_sgb_atual_vetorial.json | data_acesso: 2026-10-03T21:49:29+00:00 |
| IBGE — áreas de risco (2018) | data/processed/conferencia_fontes_inundacao/populacao-areas-de-risco_ibge_2018_recorte-municipio.json | data_processamento: 2026-10-04T00:22:25+00:00 |
| FEPAM — área diretamente atingida (maio de 2024) | data/processed/conferencia_fontes_inundacao/area-diretamente-atingida_fepam_2024-05_recorte-municipio-e-vizinhos.json | data_processamento: 2026-10-04T00:22:25+00:00 |
| setores 2022 com população | data/processed/dinamica_populacional/populacao-setores_ibge-censo_2022_setor.json | data_processamento: 2026-10-02T01:57:26+00:00 |
| grade estatística 2022 | data/processed/dinamica_populacional/populacao-grade_ibge-censo_2022_200m-1km.json | data_processamento: 2026-10-02T01:57:27+00:00; url: https://geoftp.ibge.gov.br/recortes_para_fins_estatisticos/grade_estatistica/censo_2022/grade_estatistica/grade_id13.zip |

O que não aparece acima não está registrado no repositório.
