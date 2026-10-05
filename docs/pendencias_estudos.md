# Pendências dos estudos de população, inundação e acessibilidade

*Registro de 2026-10-02.* O que ficou em aberto e onde está o material.

- **Conferência em campo das travessias e pontes.** São 5 pontes do OpenStreetMap
  (P01–P05) e 33 travessias de via com curso d'água sem ponte marcada (T01–T33). Material:
  planilha, GeoJSON, roteiro e mapa em `docs/acessibilidade_inundacao/campo/`. Enquanto
  não houver conferência, os cenários pessimista e otimista da acessibilidade coincidem.
- **Rede de pedestres como sensibilidade da acessibilidade.** O cálculo atual usa só
  vias de veículos do OpenStreetMap. Material: `scripts/processamento/acessibilidade_inundacao.py`
  e `scripts/download/infraestrutura_osm.py`.
- **População informada por unidade de saúde.** Falta saber o conceito (cadastrada,
  adscrita ou atendida). Material: `scripts/download/saude_unidades_revisadas.py` e o `.json` da
  camada de unidades.
- **Manchas de inundação.** Régua, referência de nível e data das manchas não constam nos
  metadados da fonte (SGB). A maior mancha (1252 cm) tem tempo de retorno de 13,4 anos;
  cheias maiores não estão representadas. Material: `scripts/download/hidrologia_sgb.py`
  e `docs/exposicao_inundacao/`.
- **Altimetria.** A referência vertical do modelo de elevação de 30 m (ANADEM) não está
  registrada no repositório. Falta altimetria fina para estimar a profundidade na rua e a
  cota do tabuleiro das pontes. Material: `scripts/download/terreno_anadem.py` e
  `docs/acessibilidade_inundacao/acessibilidade_unidades_saude_inundacao_2022.md`.
- **Comparação 2000–2010 dentro da cidade.** Não há correspondência oficial de setores
  entre esses censos. Material: `docs/dinamica_populacional/` e
  `scripts/processamento/dinamica_populacional_verificacao_2000.py`.
- **Conferência e publicação.** Os mapas e tabelas ainda estão "pendente de conferência"
  nos `.json` e precisam da conferência do responsável. Os indicadores novos ainda não
  foram publicados no portal. Material: `docs/dinamica_populacional/`,
  `docs/exposicao_inundacao/` e `docs/acessibilidade_inundacao/`.
- **Limpeza.** A pasta `cache/` saiu do git em 2026-10-04 e é ignorada. Os scripts do cálculo
  preliminar de população exposta continuam ativos:
  `scripts/processamento/vulnerabilidade_inundacao.py` e
  `scripts/processamento/mapa_consolidado.py`.
