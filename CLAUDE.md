# CLAUDE.md — Contexto do projeto para o Claude Code

Este arquivo é lido pelo Claude Code no início de cada sessão. Mantenha-o
atualizado conforme o projeto evolui.

## Projeto guarda-chuva

ClimaPampa — Produção do Cuidado Integral no Território e Inovação em
Saúde (edital PET-Saúde: Clima). Proponentes: Secretaria Municipal de
Saúde de Uruguaiana + UNIPAMPA. Coordenador: Prof. Samuel Salvi Romero.

Este repositório apoia o Eixo III (Comunicação e Inovação), objetivo
específico 1: plataforma digital geoespacial para vigilância em saúde e
tomada de decisão territorial.

## Município de referência

- Uruguaiana, RS — código IBGE `4322400`
- Fronteira Oeste do RS, divisa com a Argentina (Paso de los Libres)
- Bioma Pampa, clima subtropical úmido (Cfa), histórico de cheias do rio
  Uruguai e eventos extremos de calor/seca
- Populações prioritárias: comunidades ribeirinhas, áreas rurais de
  difícil acesso, mulheres, idosos, crianças, pessoas com doenças crônicas

## Padrões técnicos (seguir sempre)

- **Linguagem:** Python. Bibliotecas preferidas: `geopandas`, `rasterio`,
  `xarray`, `rasterstats`, `pyproj`, `requests`/`httpx`.
- **CRS padrão de trabalho:** SIRGAS 2000 / UTM 21S — `EPSG:31981`. Dados
  brutos podem vir em outro CRS; todo processamento intermediário
  reprojeta para o padrão.
- **Área de estudo:** sempre referenciada a partir de
  `config/area_estudo.geojson` (arquivo único, gerado por
  `scripts/download/vetor_ibge.py`). Não recriar o polígono em outros
  scripts — importar de `scripts/utils/recorte_municipio.py`.
- **Scripts de download:** idempotentes (checam se o arquivo já existe
  antes de baixar de novo) e logam fonte/data/tamanho.
- **Catálogo de fontes:** toda fonte nova entra em
  `data/catalogo_fontes.csv` (fonte, licença, resolução espacial/temporal,
  URL/API, data de acesso).
- **Nomenclatura de arquivos:**
  `{tema}_{fonte}_{ano-ou-periodo}_{resolucao}.{ext}`
  ex.: `uso-solo_mapbiomas_2023_30m.tif`
- **Metadados:** todo output processado tem um `.json` irmão descrevendo
  fonte, data de processamento e transformação aplicada.
- **Preferir API/Python** a download manual; documentar quando não for
  possível.
- Ao gerar código, incluir tratamento de erro básico e comentários curtos
  explicando decisões de CRS, recorte ou resolução.

## Replicabilidade (importante)

A metodologia deve ser adaptável a outros municípios (fronteira,
ribeirinhos, expostos a enchentes/estiagens). **Parametrizar por código
IBGE — evitar hardcode** de nomes/códigos específicos de Uruguaiana sem
necessidade.

## Variáveis prioritárias do projeto

Clima, território, vulnerabilidade socioambiental e agravos à saúde.
Camadas temáticas devem ser pensadas para compor uma plataforma futura —
priorizar granularidade por microárea/território quando possível.

Agravos citados no edital (referência para pensar indicadores espaciais):
doenças respiratórias, arboviroses, doenças de veiculação hídrica
(pós-enchente), insegurança nutricional, impactos psicossociais/saúde
mental em eventos extremos, violência de gênero em deslocamentos/
abrigamentos.

## Estrutura de pastas

```
config/                  # area_estudo.geojson — referência única de recorte
data/raw/{vetor,raster}  # dados brutos, como baixados
data/processed/          # dados já recortados/reprojetados/padronizados
data/catalogo_fontes.csv # catálogo vivo de fontes
scripts/download/        # um script por fonte de dado
scripts/processamento/   # limpeza, recorte, reprojeção, cruzamento
scripts/utils/           # funções reutilizáveis (ex.: recorte_municipio.py)
notebooks/                # exploração, não produção
dashboards/
docs/
```

## Comandos úteis

```bash
pip install -r requirements.txt
python scripts/download/vetor_ibge.py --codigo-ibge 4322400
```

## O que NÃO fazer

- Não hardcodear o polígono do município direto em scripts de
  processamento — sempre importar de `scripts/utils/recorte_municipio.py`.
- Não commitar dados brutos pesados (raster grandes) sem avaliar
  `.gitignore` / Git LFS.
- Não misturar CRS sem reprojetar explicitamente antes de qualquer
  operação espacial (join, clip, buffer).
