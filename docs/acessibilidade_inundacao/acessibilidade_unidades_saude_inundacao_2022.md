# Acesso às unidades de saúde com vias alagadas, por cota de inundação

*Produto de trabalho — pendente de conferência. Não integra o portal.*

## A pergunta

Quando o rio sobe a cada uma das quatro cotas mapeadas pelo SGB (833, 952, 1205 e
1252 cm), quantas pessoas deixam de ter caminho pela rede viária até uma unidade de
saúde da atenção primária (ESF ou UBS), ou passam a ter de dar a volta? E quais ruas e
pontes decidem isso?

## O método, em linguagem simples

- **Destinos:** as 21 unidades de classe ESF e UBS do cadastro revisado (versão 4,
  2026). A Unidade Dispensadora de Medicação (UDM, "a confirmar") e a Equipe de Saúde
  Prisional ("sem classe") aparecem nos mapas, mas não são destino.
- **Origens:** os 47.193 endereços de domicílio particular do CNEFE 2022. A população
  de cada setor censitário é repartida igualmente entre os endereços do setor — é uma
  estimativa, como nas rodadas anteriores.
- **Rede:** malha viária do OpenStreetMap (vias de veículos), baixada de novo com as
  pontes como trechos próprios (na cópia antiga, a ponte vinha fundida às ruas de
  chegada). A rede é tratada como de mão dupla, porque o deslocamento até a unidade é
  curto e muitas vezes a pé. A medida é a distância pela rede, em metros; não foi
  convertida em tempo.
- **Via "alagada":** um trecho de rua entre dois cruzamentos é considerado interrompido
  quando pelo menos 5 m dele ficam dentro da mancha cumulativa da cota (união das
  manchas até aquela cota). Dentro do trecho interrompido, sai da rede só a parte que
  toca a mancha; quem mora na parte seca ainda pode sair pela outra ponta.
- **Dois cenários para as pontes** (a resposta real fica entre os dois):
  - *pessimista*: toda via com parte dentro da mancha é interrompida, pontes incluídas;
  - *otimista*: os trechos marcados como ponte ou viaduto no OpenStreetMap continuam
    passáveis; se a rua de chegada à ponte estiver na mancha, o acesso cai do mesmo jeito.
- **Classes do endereço, em ordem:** *exposto* (o próprio endereço está na mancha);
  *isolado* (fora da mancha, mas sem nenhum caminho até uma unidade); *com desvio* (o
  caminho até a unidade mais próxima fica mais longo); *sem alteração*.
- **Conferências:** sem nenhum trecho retirado, o resultado é idêntico ao da situação
  de base; o cenário otimista nunca ficou pior que o pessimista; expostos e isolados não
  diminuem quando a cota sobe. As três passaram.

## A situação de base (sem inundação)

Na área urbana da sede, a distância mediana pela rede até a unidade ESF/UBS mais próxima
é de 641 m (90 % dos endereços a até 1.138 m). Cerca de 35 % da população estimada mora a
até 500 m, 48 % entre 500 m e 1 km, 16 % entre 1 e 2 km e 1 % a mais de 2 km. No
interior, a mediana é de 11,7 km, e 74 % da população estimada está a mais de 2 km.

"Unidade mais próxima pela rede" **não** é o território oficial de cada equipe: é só a
unidade que fica mais perto pelo caminho das ruas.

## Resultados por cota

Os dois cenários deram **o mesmo resultado em todas as cotas** (ver "As pontes que
decidem"). Por isso, a tabela abaixo serve para os dois. Área urbana da sede, população
estimada (endereços entre parênteses):

| Cota (TR) | Exposto | Isolado | Com desvio | Desvio > 500 m |
|---|---|---|---|---|
| 833 cm (1,3 anos) | 11 (4) | 0 | 0 | 0 |
| 952 cm (1,9 anos) | 236 (85) | 126 (49) | 678 (274) | 44 (27) |
| 1205 cm (9 anos) | 2.539 (891) | 361 (141) | 2.311 (924) | 53 (31) |
| 1252 cm (13,4 anos) | 3.461 (1.245) | 486 (189) | 3.769 (1.468) | 44 (27) |

Na maior cota, cerca de 4.256 pessoas (estimativa) fora da mancha passam a ficar
isoladas ou com o caminho mais longo, além das 3.461 que moram dentro dela. Quase todo o
desvio é curto: na cota 1252, 87 % das pessoas com desvio dão uma volta de até 250 m. A
exceção é um grupo de 27 endereços (≈ 44 pessoas) no extremo leste da área urbana, em
setor sem bairro, cujo desvio passa de 2 km já a partir da cota 952 cm.

Os isolados da área urbana são, na maior parte, endereços num pedaço seco de rua entre
duas partes alagadas da mesma rua, ou na ponta de ruas sem saída que descem para o rio
(144 desses pedaços na cota 1252). Há poucas "ilhas" maiores, partes da rede com
cruzamento que perdem a ligação com o resto: 3 na cota 952, 2 na 1205 e 3 na 1252,
com até 11 endereços cada, em Bela Vista, Santana, Mascarenhas de Moraes e numa área rural. Nenhuma unidade de
saúde fica dentro de uma ilha ou fora da rede principal, e nenhuma das 23 fica dentro da
mancha.

Os bairros com mais gente isolada ou com desvio na cota 1252 são Santo Inácio (≈ 1.400
pessoas com desvio curto), Santana (≈ 1.150), Santo Antônio, Nova Esperança e Bela Vista.
O perfil dos isolados acompanha o da cidade (≈ 17 % de 60 anos ou mais e ≈ 21 % de 0 a
14 anos, pelos setores). As unidades que mais perdem população "mais próxima" na cota
1252 são a ESF 17 (≈ 1.400 pessoas), a 23 (≈ 720), a 05 (≈ 530), a 02 (≈ 420) e a 22
(≈ 410); as vias de acesso a até 200 m da ESF 02 já entram na mancha na cota 833 cm.

No interior, 166 endereços (≈ 277 pessoas) passam a ter desvio a partir da cota 952 cm,
quase todos de 1 a 2 km ou mais, porque uma estrada rural junto ao rio entra na mancha;
17 endereços ficam isolados na cota 1252. É indicativo: a malha rural do OpenStreetMap
é incompleta, e 1.986 endereços do interior ficam a mais de 100 m de qualquer via (foram
ligados ao nó mais próximo).

## As pontes que decidem

Só cinco pontes do OpenStreetMap ficam a até 300 m da maior mancha. Três delas entram na
mancha: na Rua Coronel Rodrigues Portugal (P01, 21 m) e numa via sem nome a leste da
cidade (P02, 54 m), a partir da cota 952; na Rua General Canabarro (P03, 12 m), a partir
da 1205. Em todas, as ruas de chegada também ficam dentro da mancha. Por isso, manter as
pontes passáveis não muda nada: **nenhuma ponte muda o resultado entre os cenários**.
A ponte P02 está entre os trechos críticos: por ela passava o caminho de cerca de 320
pessoas.

Junto às manchas há 35 lugares onde uma via cruza um curso d'água da base hidrográfica
da ANA; em 33 deles não há ponte marcada no OpenStreetMap. Pode ser bueiro, ponte não
mapeada ou desalinhamento da base hidrográfica. É a lista de conferência em campo mais
útil desta rodada.

## Trechos críticos

Os trechos interrompidos por onde passava o caminho de mais pessoas ficam no centro
antigo, junto à mancha que avança pelo norte e pelo leste. Na cota 1205, a lista é
encabeçada pelas ruas General Bento Martins (≈ 770 pessoas), Marechal Deodoro (≈ 640) e
Tiradentes (≈ 540). Na cota 1252 entra a Rua Miguel Barbará (≈ 1.450 pessoas).

## Limites

- **Sem profundidade:** a mancha diz onde alaga, não quanta água há; uma rua "na mancha"
  pode ter poucos centímetros de água.
- **Pontes sem cota do tabuleiro:** não se sabe se o tabuleiro fica acima da água.
- **Só quatro cotas;** a maior tem tempo de retorno de 13,4 anos. Cheias maiores não
  estão representadas.
- **Cheia do rio, não alagamento por chuva:** pontos de alagamento urbano por drenagem
  não entram.
- **Rede do OpenStreetMap incompleta** nos loteamentos novos e no interior; só vias de
  veículos (trilhas e passagens de pedestre ficam de fora).
- **"Unidade mais próxima" não é o território da equipe.**
- **População por endereço é estimativa** (setor repartido entre os endereços).

## O que resolveria os limites

- **Altimetria fina** (levantamento a laser ou topografia das ruas baixas) para saber a
  profundidade em cada trecho.
- **Referência de nível da régua** do rio, para ligar a cota da régua à altitude do
  terreno.
- **Cota do tabuleiro das pontes** P01 a P05.
- **Conferência em campo da lista de pontes** e dos 33 cruzamentos sem ponte marcada.

---
Tabelas: `docs/acessibilidade_inundacao/tabelas/`. Mapas: `docs/acessibilidade_inundacao/mapas/`.
Scripts: `scripts/processamento/acessibilidade_inundacao.py` e
`scripts/processamento/acessibilidade_inundacao_mapas.py`; rede:
`scripts/download/infraestrutura_osm.py --malha-pontes`. EPSG:31981.
