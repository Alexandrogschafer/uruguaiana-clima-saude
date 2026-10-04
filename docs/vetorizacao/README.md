# Camadas vetorizadas a partir de figura

Parte da informação territorial só existe como figura em PDF (manchas de inundação de outros estudos, mapas do plano diretor). Aqui está como essas figuras viram camadas vetoriais e como podem ser usadas. O que sai daqui **não é dado oficial**.

## Tipos de figura

| Tipo | O que se espera |
|---|---|
| PDF vetorial | Os traços já são vetores: melhor caso; tratado por `extrair_camadas_de_pdf_vetorial.py` (ver a seção "PDF vetorial de CAD"). |
| Imagem com grade de coordenadas | Caso do script; erro da ordem de um pixel. |
| Imagem sem grade | Exige pontos de controle de outra base; erro maior. |
| Hachura | Separada do fundo pela diferença entre canais da imagem suavizada; contorno menos preciso que o das classes por cor. |
| Digitalização ruim | A cor não separa as classes; pede traçado manual. |

## Método

O script `scripts/vetorizacao/extrair_classes_de_figura.py`:

1. lê a figura: a imagem embutida na página, sem reamostrar, ou a página renderizada na resolução dos parâmetros; gira o mapa deitado;
2. ajusta uma transformação afim (mínimos quadrados) nas marcas da grade medidas na imagem; a marca fica no centro do pixel e o raster começa meio pixel antes;
3. classifica cada pixel pela cor de classe mais próxima (lida na legenda do mapa), dentro de uma tolerância, ou por regra de hachura; retângulos de exclusão tiram a caixa de legenda;
4. limpa: fecha traços finos, tapa buracos pequenos, remove ilhas;
5. simplifica e, no modo cumulativo, repõe o aninhamento (cada classe contém a anterior);
6. grava os polígonos em GeoPackage, com um `.json` irmão e uma imagem de conferência.

```bash
.venv/bin/python scripts/vetorizacao/extrair_classes_de_figura.py \
  --parametros scripts/vetorizacao/parametros/<mapa>.json \
  --pdf data/raw/documentos/<documento>.pdf \
  --saida-dir data/processed/vetorizado
```

Se a saída já existe, o script avisa e não regrava (use `--forcar`). A reprodução de uma camada se confere por tolerância, nunca por igualdade: área de cada classe a menos de 0,5 % da referência e diferença simétrica abaixo de 1 % (o resultado muda um pouco com a versão do renderizador).

A comparação entre fontes (`comparar_fontes_inundacao.py`, com as fontes e cenários em `fontes_inundacao.json`) é feita só dentro de um domínio comum, porque cada mapa cobre uma área diferente.

## Arquivo de parâmetros

Tudo o que é específico de um mapa fica em `scripts/vetorizacao/parametros/<mapa>.json`: documento (página, resolução, sha256, origem), moldura em pixels, retângulos de exclusão, marcas da grade, cores ou regras das classes, tolerância, limpeza, EPSG e textos de fonte e status. O script não tem nome de lugar nem de fonte.

## Situações e regra de publicação

| Situação | Significado | Uso permitido |
|---|---|---|
| extraído | gerado pelo script | nenhum além da conferência |
| conferido | o responsável conferiu no mapa | análise e relatório, com a fonte citada e a marca "extraído de figura, não oficial" |
| autorizado | a fonte autorizou ou a licença permite | pode ir para `data/geoportal/` e para o portal |

**Fora do git**: os PDFs de origem (`data/raw/documentos/`, direito autoral) e as camadas (`data/processed/vetorizado/`), enquanto não forem "autorizado". **No git**: script, parâmetros, esta documentação e o `inventario.csv` (uma linha por mapa).

## Como ler o metadado e a imagem de conferência

O `.json` irmão traz renderizador, pixel em metros, resíduo de cada marca da grade, quadro do mapa e área por classe. Resíduo bem maior que um pixel indica marca mal medida.

A imagem `_conferencia.png` mostra a figura de origem com três contornos extraídos por cima (primeira classe, a do meio e a última). Onde o contorno não acompanha a borda da cor, a camada está errada.

## Limites

- A precisão é a da figura, não a do dado original: nunca melhor que o pixel e a escala do mapa.
- Rótulos, símbolos e linhas desenhados sobre uma classe são fechados por interpolação; ali o contorno é aproximado.
- Fora do quadro do mapa não há dado: ausência de mancha não significa ausência de risco.
- No modo cumulativo o aninhamento se confere pela área da sobra (classe anterior menos a seguinte: menos de 1 m²), não por `contains`.
- A versão do script anterior à rodada 15 deslocava tudo um pixel; camadas daquela versão têm de ser regeradas.
- A classe mais frequente de um mapa pode incluir o leito do rio: conferir antes de comparar áreas.

## PDF vetorial de CAD

Aplica-se a planta exportada de CAD para PDF que guarda os traços como vetores, separados por camada (conteúdo opcional do PDF). Nada passa por imagem. O script `scripts/vetorizacao/extrair_camadas_de_pdf_vetorial.py`:

1. lê as polilinhas da página, com camada, cor e largura do traço;
2. leva os pontos do PDF para a projeção por uma semelhança de 4 parâmetros;
3. monta as camadas: linhas, faces fechadas pela rede de traços, polígonos ou hachura convertida em polígono;
4. atribui cota às curvas de nível e, se pedido, interpola o modelo de terreno;
5. grava GeoPackage ou GeoTIFF, com `.json` irmão e imagem `_conferencia.png`.

```bash
.venv/bin/python scripts/vetorizacao/extrair_camadas_de_pdf_vetorial.py \
  --parametros scripts/vetorizacao/parametros/<planta>.json \
  --pdf data/raw/documentos/<planta>.pdf --saida-dir data/processed/vetorizado
```

O arquivo de parâmetros traz as camadas e cores de cada produto, a transformação e os rótulos de cota lidos na planta (posição e valor).

**Posição.** Sem malha de coordenadas na planta, a transformação é ajustada ao centro das quadras formadas por uma malha de ruas. A opção `--conferir-georef <malha de ruas>` refaz a medida: quadras casadas, resíduo mediano, RMS e resíduo médio em E e N.

**Cotas.** O campo `origem_da_cota` diz como cada curva foi cotada:

| Valor | Significado |
|---|---|
| rótulo da planta | mestra com o rótulo lido mais próximo |
| contagem entre mestras | intermediária, pelo número de curvas entre duas mestras cotadas |
| legenda da planta | curva que a legenda nomeia com a cota |
| entre vizinhas já cotadas | média das duas vizinhas |
| sequência das vizinhas (suposição) | borda do desenho, topo ou fundo fechado: conferir antes de usar |
| sem cota | nenhuma regra decidiu; descartada do produto quando o parâmetro `descartar_curvas_sem_cota` está ligado |

O `.json` registra saltos maiores que a equidistância entre curvas vizinhas (esperado: nenhum).

**Limites.** A posição é da ordem de 3 m. A planta não informa a referência de nível, e as cotas não se comparam diretamente com as de outra fonte. O modelo de terreno é interpolação das curvas, não levantamento. A zona de risco vem de hachura, interrompida nos cursos d'água. Alguns pedaços de curva tocam ou cruzam a si mesmos (retrocesso de um passo do traçador ou laço de poucos metros): é artefato da plotagem do PDF, não cruzamento entre curvas, e a geometria fica como veio. Feixe de curvas muito próximas pode acompanhar talude de rodovia ou de trevo: é feição do levantamento, não erro de leitura. A reprodução se confere por tolerância.

**Outras formas de leitura.** Além de linhas, faces, polígonos e hachura de linhas, o script lê:

- texto do PDF (`texto`): nome gravado como texto vira ponto na origem do texto, com ângulo e altura; a grafia é a da planta;
- rótulo desenhado como contorno (`pontos_lidos`): não há texto para ler; o rótulo é lido na planta e gravado nos parâmetros, com a posição;
- união de preenchimentos (`uniao`): área desenhada em muitos pedaços pequenos vira um polígono por parte;
- símbolo reduzido a ponto (`simbolo`): um ponto no centro de cada grupo de traços próximos; indica o lote ou a quadra, não o prédio;
- hachura de pontos e cruzes (`hachura` com pontas redondas): o contorno acompanha o fim dos traços, com cantos arredondados; furos pequenos são tapados;
- várias entradas na mesma camada: entradas de mesmo nome formam uma camada só, cada uma com atributo próprio (sigla da zona, classe da via); um valor também pode ser dado ao polígono que contém um ponto lido na planta.

Amostras desenhadas na caixa de legenda são excluídas por retângulo. O `.json` resume feições, área ou comprimento por valor do campo, e a imagem de conferência usa uma cor por valor.

Os mapas urbanos de um mesmo plano têm o mesmo enquadramento e usam a mesma transformação, conferida de novo em cada mapa com `--conferir-georef`. Os mapas do município inteiro (3, 5, 7, 8 e 11) têm outro enquadramento e pedem outro ajuste.

**Duas opções e a hidrografia.** `sem_duplicadas` (camadas de linha) deixa uma só vez a linha que a planta desenha duas vezes e registra quantas saíram. `com_tracos_de` e `sem_tracos_de` (camadas de polígono, com `tracos_min` e `tracos_max`) mantêm o contorno fechado que contém, ou que não contém, traços de outra seleção: servem para separar, por exemplo, o banhado (contorno com pontilhado dentro) do corpo d'água desenhado só com contorno. Com elas sai o produto de hidrografia: linhas de água, corpos d'água por tipo e mata ciliar. Limites: a planta usa o mesmo traço para vala, arroio, margem e contorno, e não distingue vala de rio permanente; o rio principal só tem a linha da margem, sem polígono; a mata ciliar vem da hachura.
