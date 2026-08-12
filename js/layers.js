/**
 * Carrega os GeoJSON de data/geoportal/, estiliza cada camada e monta o
 * controle de camadas (embutido na seção "Camadas" do painel, não na
 * caixa flutuante padrão do Leaflet — o container do L.control.layers é
 * reaproveitado dentro do nosso próprio painel).
 *
 * saudeCnes e saudeOsm não entram no L.control.layers: as duas ficam
 * sempre no mapa desde o carregamento, e a visibilidade (por tipo de
 * estabelecimento ou por fonte) é controlada só pela seção "Saúde"
 * (filtro-saude.js) — evita ter dois controles pra mesma camada.
 */

const DIR_DADOS = "data/geoportal";

// cores por categoria de estabelecimento de saúde — usadas tanto no estilo
// dos pontos aqui quanto na legenda/checkboxes de filtro-saude.js
const CORES_TIPO_SAUDE = {
  clinica_ambulatorio: "#2b8cbe",
  farmacia: "#31a354",
  ubs_esf: "#e6550d",
  laboratorio_apoio_diagnostico: "#756bb1",
  outro: "#969696",
  vigilancia_saude: "#c51b8a",
  hospital: "#de2d26",
};

const ROTULOS_TIPO_SAUDE = {
  clinica_ambulatorio: "Clínica / Ambulatório",
  farmacia: "Farmácia",
  ubs_esf: "UBS / ESF",
  laboratorio_apoio_diagnostico: "Laboratório / Apoio diagnóstico",
  outro: "Outro",
  vigilancia_saude: "Vigilância em saúde",
  hospital: "Hospital",
};

window.App.coresTipoSaude = CORES_TIPO_SAUDE;
window.App.rotulosTipoSaude = ROTULOS_TIPO_SAUDE;

// cores/rótulos por dependência administrativa (escolas INEP) — mesmo
// padrão de CORES_TIPO_SAUDE/ROTULOS_TIPO_SAUDE, consumido por
// filtro-educacao.js (mesma estrutura de filtro-saude.js)
const CORES_DEPENDENCIA_ESCOLA = {
  Municipal: "#2b8cbe",
  Estadual: "#31a354",
  Privada: "#e6550d",
  Federal: "#756bb1",
};

const ROTULOS_DEPENDENCIA_ESCOLA = {
  Municipal: "Municipal",
  Estadual: "Estadual",
  Privada: "Privada",
  Federal: "Federal",
};

window.App.coresDependenciaEscola = CORES_DEPENDENCIA_ESCOLA;
window.App.rotulosDependenciaEscola = ROTULOS_DEPENDENCIA_ESCOLA;

// cores por classificação de pavimento (DNIT/SNV) — mesmos valores hex do
// mini-legenda estática em index.html (grupo "Malha viária"), mantidos em
// sincronia manualmente (só 2 categorias fixas, sem geração dinâmica)
const CORES_PAVIMENTO_DNIT = {
  PAV: "#1d4ed8",
  PLA: "#f59e0b",
};

// cores por status_imovel (CAR/SICAR) — mesmos valores hex da mini-legenda
// estática em index.html (grupo "Estrutura fundiária"), sincronia manual
const CORES_STATUS_CAR = {
  AT: "#16a34a",
  PE: "#f59e0b",
  CA: "#6b7280",
};

// nomes de campo legíveis, por camada — usados na montagem dos popups
const CAMPOS_LEGIVEIS = {
  densidadePopulacional: {
    CD_SETOR: "Código do setor",
    NM_BAIRRO: "Bairro",
    NM_DIST: "Distrito",
    populacao_total: "População total",
    AREA_KM2: "Área (km²)",
    densidade_hab_km2: "Densidade populacional (hab/km²)",
  },
  densidadePopulacional2010: {
    cd_setor: "Código do setor",
    situacao: "Situação (urbana/rural)",
    populacao_total: "População total",
    domicilios_total: "Domicílios totais",
    area_km2: "Área (km²)",
    densidade_demografica_hab_km2: "Densidade populacional (hab/km²)",
  },
  densidadePopulacional2000: {
    cd_setor: "Código do setor",
    situacao: "Situação (urbana/rural)",
    populacao_total: "População total",
    domicilios_total: "Domicílios totais",
    area_km2: "Área (km²)",
    densidade_demografica_hab_km2: "Densidade populacional (hab/km²)",
  },
  criancas0a4: {
    CD_SETOR: "Código do setor",
    NM_BAIRRO: "Bairro",
    NM_DIST: "Distrito",
    populacao_total: "População total do setor",
    pct_populacao_0_a_4_anos: "% população 0-4 anos (Censo)",
    estimativa_criancas_0_a_4: "Crianças 0-4 anos (estimativa)",
  },
  idosos60Mais: {
    CD_SETOR: "Código do setor",
    NM_BAIRRO: "Bairro",
    NM_DIST: "Distrito",
    populacao_total: "População total do setor",
    pct_populacao_60_anos_ou_mais: "% população 60+ anos (Censo)",
    estimativa_idosos_60_mais: "Idosos 60+ anos (estimativa)",
  },
  setoresInundacao: {
    CD_SETOR: "Código do setor",
    cota_cm: "Cota de inundação (cm)",
    tr_anos: "Período de retorno (anos)",
    area_setor_km2: "Área do setor (km²)",
    area_intersecao_km2: "Área inundada no setor (km²)",
    pct_area_coberta: "% da área do setor coberta",
    metodo_estimativa_uso_solo: "Método de estimativa",
    populacao_total: "População total do setor",
    "populacao_estimada_area-proporcional": "População estimada (área-proporcional)",
    "populacao_estimada_ponderada_uso-solo": "População estimada (ponderada por uso do solo)",
    pct_populacao_0_a_4_anos: "% população 0-4 anos",
    pct_populacao_60_anos_ou_mais: "% população 60+ anos",
  },
  cotasInundacao: {
    cota_cm: "Cota de inundação (cm)",
    tr_anos: "Período de retorno (anos)",
    bacia: "Bacia",
    municipio: "Município",
    estado: "Estado",
  },
  saudeCnes: {
    nome_fantasia: "Nome",
    nome_empresarial: "Razão social",
    tipo_unidade_categoria: "Categoria",
    tipo_unidade: "Tipo de unidade (CNES)",
    endereco: "Endereço",
    cep: "CEP",
    telefone: "Telefone",
    atende_sus: "Atende SUS",
    gestao: "Gestão",
    cnes: "Código CNES",
  },
  saudeOsm: {
    name: "Nome",
    amenity: "Categoria (OSM)",
    healthcare: "Tipo de assistência",
    "healthcare:speciality": "Especialidade",
    "addr:street": "Rua",
    "addr:housenumber": "Número",
    "addr:suburb": "Bairro",
    "addr:postcode": "CEP",
    dispensing: "Dispensa medicamentos",
    website: "Site",
    "ref:CNES": "Código CNES (referência)",
  },
  estacoesClima: {
    nome_estacao: "Nome da estação",
    codigo_estacao: "Código da estação",
    situacao: "Situação",
    tipo_estacao: "Tipo de estação",
    altitude_m: "Altitude (m)",
    data_inicio_operacao: "Início de operação",
    distancia_centroide_km: "Distância ao centro do município (km)",
  },
  malhaViaria: {
    name: "Nome da via",
    highway: "Tipo de via",
  },
  malhaViariaDnit: {
    rodovia: "Rodovia",
    ds_local_i: "Trecho — início",
    ds_local_f: "Trecho — fim",
    ds_legenda: "Pavimentação",
    ds_jurisdi: "Jurisdição",
    km_dentro_area_estudo: "Extensão dentro do município (km)",
  },
  estruturaFundiaria: {
    codigo_imovel_car: "Código CAR",
    status_imovel_legenda: "Status",
    tipo_imovel_legenda: "Tipo de imóvel",
    condicao_analise: "Condição da análise",
    area_declarada_ha: "Área declarada (ha)",
    modulos_fiscais: "Módulos fiscais",
  },
  coberturaMovel: {
    NM_BAIRRO: "Bairro",
    NM_DIST: "Distrito",
    cobertura_pct_202412: "Cobertura 12/2024 (%)",
    cobertura_pct_202503: "Cobertura 03/2025 (%)",
    cobertura_pct_202506: "Cobertura 06/2025 (%)",
    cobertura_pct_202509: "Cobertura 09/2025 (%)",
    cobertura_pct_202512: "Cobertura 12/2025 (%)",
    cobertura_pct_202603: "Cobertura 03/2026 (%)",
    cobertura_pct_202606: "Cobertura 06/2026 (%)",
  },
  escolasInep: {
    codigo_inep: "Código INEP",
    dependencia_administrativa: "Dependência administrativa",
    localizacao: "Localização",
    endereco: "Endereço",
    telefone: "Telefone",
    etapas_ensino: "Etapas de ensino",
    porte_escola: "Porte da escola",
    matriculas: "Matrículas",
    docentes: "Docentes",
    salas_utilizadas: "Salas utilizadas",
    abastecimento_agua: "Abastecimento de água",
    fornecimento_energia: "Fornecimento de energia",
    esgotamento_sanitario: "Esgotamento sanitário",
    internet: "Internet",
  },
  redeHidrografica: {
    cocursodag: "Código do curso d'água (BHO)",
    nuordemcda: "Ordem do curso d'água",
  },
  bacia: {
    codigo_otto: "Código Otto Pfafstetter",
    nivel_otto: "Nível Otto Pfafstetter",
  },
  geologia: {
    nm_unidade: "Unidade geológica",
    letra_simb: "Símbolo",
    nm_lito1: "Litologia principal",
    nm_lito2: "Litologia secundária",
    nm_tempo_g: "Idade geológica",
    nm_provincia: "Província estrutural",
    ar_poli_km: "Área do polígono na fonte original (km², antes do recorte municipal)",
  },
  geomorfologia: {
    legenda: "Unidade geomorfológica",
    categoria: "Categoria",
    natureza: "Natureza",
    forma: "Forma do relevo",
    dens_dren: "Densidade de drenagem",
    niv_alt: "Nível altimétrico",
    compartimento: "Compartimento",
    ar_poli_km: "Área do polígono na fonte original (km², antes do recorte municipal)",
  },
  pedologia: {
    legenda: "Classe de solo",
    nom_unidad: "Unidade pedológica",
    cod_simbol: "Símbolo",
    textura: "Textura",
    relevo: "Relevo associado",
    erosao: "Suscetibilidade à erosão",
    ar_poli_km: "Área do polígono na fonte original (km², antes do recorte municipal)",
  },
  vegetacao: {
    legenda: "Fitofisionomia / uso",
    clas_domi: "Classe dominante",
    leg_sup: "Legenda de superfície",
    ar_poli_km: "Área do polígono na fonte original (km², antes do recorte municipal)",
  },
  pocosSiagas: {
    codigo_poco: "Código do poço (SIAGAS)",
    nome_poco: "Nome/identificação",
    local_poco: "Local",
    profundidade_m: "Profundidade (m)",
    nivel_estatico_m: "Nível estático (m)",
    nivel_dinamico_m: "Nível dinâmico (m)",
    vazao_especifica_m3h_m: "Vazão específica (m³/h por m)",
    aquifero: "Aquífero captado",
    situacao: "Situação",
    uso_agua: "Uso da água",
  },
  appHidrica: {
    curso_dagua: "Curso d'água",
    classe_largura_codigo_florestal: "Classe de largura (Código Florestal)",
    faixa_app_m: "Faixa de APP (m)",
    area_km2: "Área (km²)",
  },
};

// camadas em que campo vazio deve aparecer no popup como "sem dado" em vez
// de ser ocultado — usado nos poços SIAGAS pra deixar claro que a ausência
// de um atributo (ex. vazão específica, ~58% de dado faltante na fonte) é
// informação relevante, não um poço com popup "incompleto" por acidente
const CAMPOS_MOSTRAR_SEM_DADO = new Set(["pocosSiagas"]);

// ---------- utilitários de texto compartilhados (também usados por meio-fisico.js) ----------

function normalizarTexto(texto) {
  return String(texto ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function ehClasseAgua(rotulo) {
  return normalizarTexto(rotulo).includes("agua");
}

// alguns campos "legenda" da fonte BDiA vêm com um dígito de ordenação
// grudado no início (ex. "5Corpo d'água continental", "2Planalto da
// Campanha") — removido só para exibição, o valor original é preservado
// no popup (campo bruto, sem essa limpeza)
function limparRotuloClasse(rotulo) {
  return String(rotulo ?? "").replace(/^\d+/, "").trim() || "Sem classificação";
}

// paleta categórica (ColorBrewer Set3, 12 cores de bom contraste entre si)
// usada nas 4 camadas do BDiA (geologia/geomorfologia/pedologia/vegetação);
// classes de água (qualquer rótulo contendo "água") sempre recebem a mesma
// cor fixa em vez de uma cor da paleta, pra ficar visualmente consistente
// entre as 4 camadas (todas têm uma classe "Corpo d'água continental")
const PALETA_CATEGORICA_SET3 = [
  "#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3", "#fdb462",
  "#b3de69", "#fccde5", "#d9d9d9", "#bc80bd", "#ccebc5", "#ffed6f",
];
const COR_AGUA_CATEGORICA = "#3987e5";

const CAMPO_TITULO = {
  densidadePopulacional: (p) => `Setor ${p.CD_SETOR ?? ""}`.trim(),
  densidadePopulacional2010: (p) => `Setor ${p.cd_setor ?? ""}`.trim(),
  densidadePopulacional2000: (p) => `Setor ${p.cd_setor ?? ""}`.trim(),
  criancas0a4: (p) => `Setor ${p.CD_SETOR ?? ""}`.trim(),
  idosos60Mais: (p) => `Setor ${p.CD_SETOR ?? ""}`.trim(),
  setoresInundacao: (p) => `Setor ${p.CD_SETOR ?? ""} — cota ${p.cota_cm ?? "?"} cm`,
  cotasInundacao: (p) => `Mancha de inundação — cota ${p.cota_cm ?? "?"} cm`,
  saudeCnes: (p) => p.nome_fantasia || p.nome_empresarial || "Estabelecimento de saúde",
  saudeOsm: (p) => p.name || "Estabelecimento de saúde (OSM)",
  estacoesClima: (p) => p.nome_estacao || "Estação climatológica",
  malhaViaria: (p) => p.name || "Via sem nome",
  malhaViariaDnit: (p) => (p.rodovia ? `${p.rodovia} — trecho oficial (DNIT)` : "Trecho rodoviário federal"),
  estruturaFundiaria: (p) => `${p.tipo_imovel_legenda || "Imóvel rural"} — ${p.status_imovel_legenda || "?"}`,
  escolasInep: (p) => p.escola || "Escola",
  coberturaMovel: (p) => `Setor ${p.CD_SETOR ?? ""}`.trim(),
  redeHidrografica: (p) => `Curso d'água ${p.cocursodag ?? ""}`.trim(),
  bacia: (p) => `Bacia ${p.codigo_otto ?? ""}`.trim(),
  geologia: (p) => limparRotuloClasse(p.nm_unidade),
  geomorfologia: (p) => limparRotuloClasse(p.legenda),
  pedologia: (p) => limparRotuloClasse(p.legenda),
  vegetacao: (p) => limparRotuloClasse(p.legenda),
  pocosSiagas: (p) => p.nome_poco || `Poço ${p.codigo_poco ?? ""}`.trim() || "Poço SIAGAS",
  appHidrica: (p) => (p.curso_dagua === "rio_uruguai" ? "APP — Rio Uruguai" : "APP — rede de drenagem local"),
};

// notas fixas exibidas no fim do popup de algumas camadas — usadas aqui pra
// deixar explícito que o valor absoluto de crianças/idosos é uma estimativa
// derivada do percentual do Censo (não contagem direta), e pra avisar
// quando o setor não tem dado por sigilo censitário (ver
// scripts/geoportal/converter_setores_demografia.py)
const AVISO_MALHA_HISTORICA =
  "Malha do Censo histórico — NÃO comparar/sobrepor geometricamente com a de outro ano " +
  "(setores mudam de configuração a cada Censo; ver metodologia em data/raw/vetor/).";

const NOTAS_POPUP = {
  densidadePopulacional2010: (p) =>
    p.sem_dado ? "Sem dado atributivo na fonte para este setor. " + AVISO_MALHA_HISTORICA : AVISO_MALHA_HISTORICA,
  densidadePopulacional2000: (p) =>
    p.sem_dado ? "Sem dado atributivo na fonte para este setor. " + AVISO_MALHA_HISTORICA : AVISO_MALHA_HISTORICA,
  criancas0a4: (p) =>
    p.sem_dado
      ? "Sem dado disponível para este setor (sigilo censitário)."
      : "Valor absoluto estimado a partir do percentual do Censo (não é contagem direta).",
  idosos60Mais: (p) =>
    p.sem_dado
      ? "Sem dado disponível para este setor (sigilo censitário)."
      : "Valor absoluto estimado a partir do percentual do Censo (não é contagem direta).",
  // só o trecho BR-377 coincidente com a BR-290 recebe nota — os demais 17
  // trechos ficam sem nota (ver ajuste em construirPopup pra função que
  // retorna null/vazio não sair como "<p>null</p>" no popup)
  malhaViariaDnit: (p) =>
    p.divergencia_osm
      ? "⚠ Trecho co-sinalizado com a BR-290 (mesmo traçado físico registrado sob as duas rodovias no SNV). " +
        "No OpenStreetMap está mapeado como BR-290/RSC-377, não como BR-377 — quem buscar só por \"BR-377\" no " +
        "OSM não encontra este trecho, relevante para quem for usá-lo numa rota de evacuação."
      : null,
  coberturaMovel: (p) => (p.sem_dado ? "Sem dado ANATEL para este setor em nenhum dos 7 períodos (ano_censo=2022)." : null),
};

function formatarValor(chave, valor) {
  if (chave === "curso_dagua") {
    return valor === "rio_uruguai" ? "Rio Uruguai (faixa de 200m)" : "Rede de drenagem local (faixa de 30m)";
  }
  if (valor === null || valor === undefined || valor === "") return null;
  if (typeof valor === "number") {
    if (Number.isNaN(valor)) return null;
    const texto = valor.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
    return chave.startsWith("pct_") ? `${texto}%` : texto;
  }
  return String(valor);
}

function construirPopup(chaveCamada, propriedades) {
  const campos = CAMPOS_LEGIVEIS[chaveCamada] || {};
  const titulo = (CAMPO_TITULO[chaveCamada] || (() => "Feição"))(propriedades);
  const mostrarSemDado = CAMPOS_MOSTRAR_SEM_DADO.has(chaveCamada);

  const linhas = Object.entries(campos)
    .map(([chave, rotulo]) => {
      const valorFormatado = formatarValor(chave, propriedades[chave]);
      if (valorFormatado === null) {
        return mostrarSemDado ? [rotulo, "sem dado"] : null;
      }
      return [rotulo, valorFormatado];
    })
    .filter((linha) => linha !== null)
    .map(([rotulo, valor]) => `<tr><td>${rotulo}</td><td>${valor}</td></tr>`)
    .join("");

  // funcaoNota pode existir na camada mas não se aplicar a esta feição
  // específica (ex.: malhaViariaDnit só anota o trecho com divergência OSM)
  // — nesse caso retorna null/vazio, e o <p> nem é montado
  const funcaoNota = NOTAS_POPUP[chaveCamada];
  const textoNota = funcaoNota ? funcaoNota(propriedades) : null;
  const nota = textoNota ? `<p class="popup-nota">${textoNota}</p>` : "";

  return `<div class="popup-titulo">${titulo}</div><table class="popup-tabela">${linhas}</table>${nota}`;
}

function onEachFeatureComPopup(chaveCamada) {
  return (feature, layer) => {
    layer.bindPopup(() => construirPopup(chaveCamada, feature.properties || {}));
  };
}

// ---------- choropleth dos setores censitários (densidade/crianças/idosos) ----------
//
// As 3 camadas usam quebras por quantil (5 classes) calculadas a partir dos
// valores presentes no próprio GeoJSON — não há classificação por natural
// breaks (Jenks) pra evitar depender de biblioteca extra só pra isso.
// Setores com sem_dado=true (sigilo censitário, ver
// scripts/geoportal/converter_setores_demografia.py) ficam fora do cálculo
// das quebras e recebem hachura neutra em vez de cor da rampa.

// rampas sequenciais (1 matiz, claro->escuro) — hue base de cada uma é o
// mesmo das 3 primeiras cores categóricas do design system (azul/laranja/água)
const RAMPA_DENSIDADE = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"];
const RAMPA_CRIANCAS = ["#fce4d6", "#f7b892", "#eb6834", "#b84f27", "#7a341a"];
const RAMPA_IDOSOS = ["#d3f3e6", "#8fdcbd", "#1baf7a", "#12805a", "#0a4d36"];

const CAMPO_CLASSIFICACAO = {
  densidadePopulacional: {
    campo: "densidade_hab_km2",
    rotulo: "Densidade populacional (2022)",
    rampa: RAMPA_DENSIDADE,
    formatar: (v) => `${Math.round(v).toLocaleString("pt-BR")} hab/km²`,
  },
  densidadePopulacional2010: {
    campo: "densidade_demografica_hab_km2",
    rotulo: "Densidade populacional (2010)",
    rampa: RAMPA_DENSIDADE,
    formatar: (v) => `${Math.round(v).toLocaleString("pt-BR")} hab/km²`,
  },
  densidadePopulacional2000: {
    campo: "densidade_demografica_hab_km2",
    rotulo: "Densidade populacional (2000)",
    rampa: RAMPA_DENSIDADE,
    formatar: (v) => `${Math.round(v).toLocaleString("pt-BR")} hab/km²`,
  },
  criancas0a4: {
    campo: "pct_populacao_0_a_4_anos",
    rotulo: "Crianças (0-4 anos)",
    rampa: RAMPA_CRIANCAS,
    formatar: (v) => `${v.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`,
  },
  idosos60Mais: {
    campo: "pct_populacao_60_anos_ou_mais",
    rotulo: "Idosos (60+ anos)",
    rampa: RAMPA_IDOSOS,
    formatar: (v) => `${v.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`,
  },
};

const quebrasPorCamada = {};

function calcularQuebrasQuantil(valores, nClasses) {
  const ordenados = [...valores].sort((a, b) => a - b);
  const quebras = [];
  for (let i = 1; i < nClasses; i++) {
    const indice = Math.floor((i / nClasses) * (ordenados.length - 1));
    quebras.push(ordenados[indice]);
  }
  return quebras;
}

function classeParaValor(valor, quebras) {
  for (let i = 0; i < quebras.length; i++) {
    if (valor <= quebras[i]) return i;
  }
  return quebras.length;
}

// injeta, uma única vez, o padrão SVG de hachura usado nos setores sem_dado
// — tom sobre tom em cinza neutro (não usa cor de nenhuma rampa), pra não
// ser confundido com nenhuma classe de valor real
function garantirPatternSemDado(mapa) {
  const svg = mapa.getPane("overlayPane").querySelector("svg");
  if (!svg || svg.querySelector("#hachura-sem-dado")) return;

  let defs = svg.querySelector("defs");
  if (!defs) {
    defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    svg.insertBefore(defs, svg.firstChild);
  }

  const pattern = document.createElementNS("http://www.w3.org/2000/svg", "pattern");
  pattern.setAttribute("id", "hachura-sem-dado");
  pattern.setAttribute("width", "6");
  pattern.setAttribute("height", "6");
  pattern.setAttribute("patternUnits", "userSpaceOnUse");
  pattern.setAttribute("patternTransform", "rotate(45)");

  const fundo = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  fundo.setAttribute("width", "6");
  fundo.setAttribute("height", "6");
  fundo.setAttribute("fill", "#e4e2dc");

  const linha = document.createElementNS("http://www.w3.org/2000/svg", "line");
  linha.setAttribute("x1", "0");
  linha.setAttribute("y1", "0");
  linha.setAttribute("x2", "0");
  linha.setAttribute("y2", "6");
  linha.setAttribute("stroke", "#9a9890");
  linha.setAttribute("stroke-width", "3");

  pattern.appendChild(fundo);
  pattern.appendChild(linha);
  defs.appendChild(pattern);
}

function construirCamadaChoropleth(chaveCamada, geojson) {
  const { campo, rampa } = CAMPO_CLASSIFICACAO[chaveCamada];

  const valores = geojson.features
    .map((f) => f.properties[campo])
    .filter((v) => typeof v === "number" && !Number.isNaN(v));
  const quebras = calcularQuebrasQuantil(valores, rampa.length);
  quebrasPorCamada[chaveCamada] = quebras;

  const estiloFeature = (feature) => {
    const p = feature.properties;
    if (p.sem_dado) {
      return { color: "#6b6a64", weight: 1, fillColor: "url(#hachura-sem-dado)", fillOpacity: 1 };
    }
    const classe = classeParaValor(p[campo], quebras);
    return { color: "#4b5563", weight: 1, fillColor: rampa[classe], fillOpacity: 0.75 };
  };

  return L.geoJSON(geojson, {
    style: estiloFeature,
    onEachFeature: onEachFeatureComPopup(chaveCamada),
  });
}

function construirLegendaHtml(chaveCamada) {
  const cfg = CAMPO_CLASSIFICACAO[chaveCamada];
  const quebras = quebrasPorCamada[chaveCamada];
  if (!cfg || !quebras) return "";

  const linhas = cfg.rampa
    .map((cor, i) => {
      const min = i === 0 ? null : quebras[i - 1];
      const max = i === cfg.rampa.length - 1 ? null : quebras[i];
      let rotuloFaixa;
      if (min === null) rotuloFaixa = `até ${cfg.formatar(max)}`;
      else if (max === null) rotuloFaixa = `acima de ${cfg.formatar(min)}`;
      else rotuloFaixa = `${cfg.formatar(min)} – ${cfg.formatar(max)}`;
      return `<div class="legenda-linha"><span class="swatch" style="background:${cor}"></span>${rotuloFaixa}</div>`;
    })
    .join("");

  const linhaSemDado =
    '<div class="legenda-linha"><span class="swatch swatch-sem-dado"></span>Sem dado (sigilo censitário)</div>';

  return `<div class="legenda-bloco"><strong>${cfg.rotulo}</strong>${linhas}${linhaSemDado}</div>`;
}

function atualizarLegendaDemografia() {
  const mapa = window.App.map;
  const container = document.getElementById("legenda-demografia");
  if (!container) return;
  const ativos = Object.keys(CAMPO_CLASSIFICACAO).filter(
    (chave) => window.App.layers[chave] && mapa.hasLayer(window.App.layers[chave])
  );
  container.innerHTML = ativos.map(construirLegendaHtml).join("");
}

async function buscarGeoJSON(nomeArquivo) {
  const resposta = await fetch(`${DIR_DADOS}/${nomeArquivo}`);
  if (!resposta.ok) {
    throw new Error(`falha ao carregar ${nomeArquivo}: HTTP ${resposta.status}`);
  }
  return resposta.json();
}

/**
 * Controle "Densidade populacional" do grupo Demografia: 1 checkbox (liga/
 * desliga a camada) + 1 seletor de ano (radio 2022/2010/2000). Nunca mostra
 * mais de um ano ao mesmo tempo — são malhas de setores diferentes entre si
 * (ver AVISO_MALHA_HISTORICA), sobrepor os 3 coropletos não faria sentido
 * de leitura nem seria uma comparação válida.
 *
 * `camadasPorAno`: { 2022: layer, 2010: layer, 2000: layer }.
 */
function montarControleDensidadeHistorica(camadasPorAno) {
  const mapa = window.App.map;
  const checkbox = document.getElementById("checkbox-densidade-populacional");
  const radios = document.querySelectorAll('input[name="ano-densidade"]');
  if (!checkbox || !radios.length) return;

  let camadaAtiva = null;

  function aplicar() {
    if (camadaAtiva) mapa.removeLayer(camadaAtiva);
    camadaAtiva = null;

    if (checkbox.checked) {
      const anoSelecionado = document.querySelector('input[name="ano-densidade"]:checked')?.value || "2022";
      camadaAtiva = camadasPorAno[anoSelecionado];
      mapa.addLayer(camadaAtiva);
    }
    atualizarLegendaDemografia();
  }

  checkbox.addEventListener("change", aplicar);
  radios.forEach((radio) => radio.addEventListener("change", aplicar));

  aplicar(); // estado inicial: checkbox ligado (HTML), ano 2022 (HTML)
}

/**
 * Monta uma lista de checkboxes simples (não usa L.control.layers — cada
 * grupo de nível superior do painel tem seu próprio container de toggles,
 * então um checkbox HTML normal dá controle total sobre onde ele aparece,
 * ao contrário do widget único do Leaflet que só suporta uma lista plana).
 *
 * `definicoes`: array de { layer, rotulo, ligado, aoAlternar? } — aplica o
 * estado inicial (`ligado`) no mapa imediatamente, e chama `aoAlternar()`
 * (se houver) a cada mudança, além de add/removeLayer.
 */
function montarTogglesCamadas(idContainer, definicoes) {
  const mapa = window.App.map;
  const container = document.getElementById(idContainer);
  if (!container) return;

  container.innerHTML = definicoes
    .map((def, indice) => `
      <label class="checkbox-linha">
        <input type="checkbox" data-indice="${indice}" ${def.ligado ? "checked" : ""} />
        ${def.rotulo}
      </label>`)
    .join("");

  container.querySelectorAll("input[type=checkbox]").forEach((checkbox) => {
    const def = definicoes[Number(checkbox.dataset.indice)];
    if (def.ligado) mapa.addLayer(def.layer);
    else mapa.removeLayer(def.layer);

    checkbox.addEventListener("change", (evento) => {
      if (evento.target.checked) mapa.addLayer(def.layer);
      else mapa.removeLayer(def.layer);
      if (def.aoAlternar) def.aoAlternar();
    });
  });
}

async function iniciarCamadas() {
  const mapa = window.App.map;

  try {
    const [
      limiteMunicipalGeoJSON,
      densidadePopulacionalGeoJSON,
      densidadePopulacional2010GeoJSON,
      densidadePopulacional2000GeoJSON,
      criancas0a4GeoJSON,
      idosos60MaisGeoJSON,
      setoresInundacaoGeoJSON,
      cotasInundacaoGeoJSON,
      saudeCnesGeoJSON,
      saudeOsmGeoJSON,
      estacoesClimaGeoJSON,
      malhaViariaGeoJSON,
      malhaViariaDnitGeoJSON,
      redeHidrograficaGeoJSON,
      estruturaFundiariaGeoJSON,
      escolasInepGeoJSON,
    ] = await Promise.all([
      buscarGeoJSON("limite-municipal.geojson"),
      buscarGeoJSON("densidade-populacional.geojson"),
      buscarGeoJSON("densidade-populacional-2010.geojson"),
      buscarGeoJSON("densidade-populacional-2000.geojson"),
      buscarGeoJSON("criancas-0-4.geojson"),
      buscarGeoJSON("idosos-60-mais.geojson"),
      buscarGeoJSON("setores-inundacao.geojson"),
      buscarGeoJSON("cotas-inundacao.geojson"),
      buscarGeoJSON("saude-cnes.geojson"),
      buscarGeoJSON("saude-osm.geojson"),
      buscarGeoJSON("estacoes-clima.geojson"),
      buscarGeoJSON("malha-viaria.geojson"),
      buscarGeoJSON("malha-viaria-dnit.geojson"),
      buscarGeoJSON("rede-hidrografica.geojson"),
      buscarGeoJSON("estrutura-fundiaria.geojson"),
      buscarGeoJSON("escolas-inep.geojson"),
    ]);

    // limite municipal: contorno de referência, sempre visível, não interativo
    const limiteMunicipal = L.geoJSON(limiteMunicipalGeoJSON, {
      style: { color: "#123c26", weight: 2, dashArray: "4 4", fill: false },
      interactive: false,
    }).addTo(mapa);
    mapa.fitBounds(limiteMunicipal.getBounds(), { padding: [16, 16] });
    garantirPatternSemDado(mapa);

    // só "Densidade populacional" começa visível — as 3 camadas cobrem a
    // mesma geometria dos setores censitários, então manter as 3 ligadas
    // por padrão só sobreporia choropleths sem ganho de leitura
    // (visibilidade inicial real é aplicada por montarTogglesCamadas mais
    // abaixo — aqui as camadas só são construídas, sem addTo(mapa))
    const densidadePopulacional = construirCamadaChoropleth("densidadePopulacional", densidadePopulacionalGeoJSON);
    // malhas históricas (2000/2010) — ver AVISO_MALHA_HISTORICA: nunca mostradas junto com a de
    // outro ano, alternadas pelo seletor de ano em montarControleDensidadeHistorica()
    const densidadePopulacional2010 = construirCamadaChoropleth("densidadePopulacional2010", densidadePopulacional2010GeoJSON);
    const densidadePopulacional2000 = construirCamadaChoropleth("densidadePopulacional2000", densidadePopulacional2000GeoJSON);
    const criancas0a4 = construirCamadaChoropleth("criancas0a4", criancas0a4GeoJSON);
    const idosos60Mais = construirCamadaChoropleth("idosos60Mais", idosos60MaisGeoJSON);

    const setoresInundacao = L.geoJSON(setoresInundacaoGeoJSON, {
      style: { color: "#c2410c", weight: 1, fillColor: "#fb8500", fillOpacity: 0.45 },
      onEachFeature: onEachFeatureComPopup("setoresInundacao"),
    });

    const cotasInundacao = L.geoJSON(cotasInundacaoGeoJSON, {
      style: { color: "#1d4ed8", weight: 1, fillColor: "#2563eb", fillOpacity: 0.3 },
      onEachFeature: onEachFeatureComPopup("cotasInundacao"),
    });

    const saudeCnes = L.geoJSON(saudeCnesGeoJSON, {
      pointToLayer: (feature, latlng) => {
        const cor = CORES_TIPO_SAUDE[feature.properties.tipo_unidade_categoria] || "#374151";
        return L.circleMarker(latlng, {
          radius: 5,
          weight: 1,
          color: "#1f2933",
          fillColor: cor,
          fillOpacity: 0.9,
        });
      },
      onEachFeature: onEachFeatureComPopup("saudeCnes"),
    }).addTo(mapa);

    const saudeOsm = L.geoJSON(saudeOsmGeoJSON, {
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, { radius: 6, weight: 1, color: "#115e59", fillColor: "#0ea5a5", fillOpacity: 0.85 }),
      style: { color: "#115e59", weight: 1, fillColor: "#0ea5a5", fillOpacity: 0.35 },
      onEachFeature: onEachFeatureComPopup("saudeOsm"),
    }).addTo(mapa);

    const estacoesClima = L.geoJSON(estacoesClimaGeoJSON, {
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, { radius: 7, weight: 1, color: "#92400e", fillColor: "#f59e0b", fillOpacity: 0.95 }),
      onEachFeature: onEachFeatureComPopup("estacoesClima"),
    });

    // malha viária: camada de contexto, desligada por padrão (não é addTo(mapa) aqui)
    const malhaViaria = L.geoJSON(malhaViariaGeoJSON, {
      style: { color: "#6b7280", weight: 1.5, opacity: 0.7 },
      onEachFeature: onEachFeatureComPopup("malhaViaria"),
    });

    // malha viária federal oficial (DNIT/SNV) — segunda fonte do mesmo
    // grupo "Malha viária" (sem grupo novo); estilizada por pavimentação,
    // com o trecho co-sinalizado BR-377/BR-290 destacado (linha mais
    // grossa) além da nota condicional no popup (ver NOTAS_POPUP.malhaViariaDnit)
    const malhaViariaDnit = L.geoJSON(malhaViariaDnitGeoJSON, {
      style: (feature) => ({
        color: CORES_PAVIMENTO_DNIT[feature.properties.sg_legenda] || "#6b7280",
        weight: feature.properties.divergencia_osm ? 4.5 : 2.5,
        opacity: 0.9,
        dashArray: feature.properties.sg_legenda === "PLA" ? "6 4" : null,
      }),
      onEachFeature: onEachFeatureComPopup("malhaViariaDnit"),
    });

    // rede hidrográfica: linha azul fina, contexto, desligada por padrão
    const redeHidrografica = L.geoJSON(redeHidrograficaGeoJSON, {
      style: { color: "#2563eb", weight: 1, opacity: 0.85 },
      onEachFeature: onEachFeatureComPopup("redeHidrografica"),
    });

    // estrutura fundiária (CAR/SICAR): grupo próprio, toggle único,
    // desligada por padrão (1.672 imóveis); cor por status_imovel
    const estruturaFundiaria = L.geoJSON(estruturaFundiariaGeoJSON, {
      style: (feature) => {
        const cor = CORES_STATUS_CAR[feature.properties.status_imovel] || "#374151";
        return { color: cor, weight: 1, fillColor: cor, fillOpacity: 0.25 };
      },
      onEachFeature: onEachFeatureComPopup("estruturaFundiaria"),
    });

    // escolas INEP: mesmo padrão de saudeCnes — sempre montada no mapa
    // desde o carregamento, visibilidade por categoria controlada só por
    // filtro-educacao.js (não entra em montarTogglesCamadas); todas as
    // categorias começam desmarcadas (ver filtro-educacao.js), então o
    // resultado visual inicial é "camada desligada", igual às demais
    // camadas novas, sem precisar de um mecanismo de on/off separado
    const escolasInep = L.geoJSON(escolasInepGeoJSON, {
      pointToLayer: (feature, latlng) => {
        const cor = CORES_DEPENDENCIA_ESCOLA[feature.properties.dependencia_administrativa] || "#374151";
        return L.circleMarker(latlng, {
          radius: 6,
          weight: 1,
          color: "#1f2933",
          fillColor: cor,
          fillOpacity: 0.9,
        });
      },
      onEachFeature: onEachFeatureComPopup("escolasInep"),
    }).addTo(mapa);

    window.App.layers = {
      limiteMunicipal,
      densidadePopulacional,
      densidadePopulacional2010,
      densidadePopulacional2000,
      criancas0a4,
      idosos60Mais,
      setoresInundacao,
      cotasInundacao,
      saudeCnes,
      saudeOsm,
      estacoesClima,
      malhaViaria,
      malhaViariaDnit,
      redeHidrografica,
      estruturaFundiaria,
      escolasInep,
    };

    // grupo "Demografia" — densidade populacional tem seletor de ano próprio
    // (montarControleDensidadeHistorica, abaixo: nunca mostra mais de um ano
    // ao mesmo tempo, ver AVISO_MALHA_HISTORICA); crianças/idosos só existem
    // para 2022 (a fonte de 2000/2010 não traz distribuição etária por
    // setor — ver scripts/download/setores_censitarios_historico.py)
    montarTogglesCamadas("container-camadas-demografia", [
      { layer: criancas0a4, rotulo: "Crianças (0-4 anos) — 2022", ligado: false, aoAlternar: atualizarLegendaDemografia },
      { layer: idosos60Mais, rotulo: "Idosos (60+ anos) — 2022", ligado: false, aoAlternar: atualizarLegendaDemografia },
    ]);
    montarControleDensidadeHistorica({
      2022: densidadePopulacional,
      2010: densidadePopulacional2010,
      2000: densidadePopulacional2000,
    });
    atualizarLegendaDemografia();

    // grupo "Inundação" — mesmo estado inicial de antes (ambas ligadas)
    montarTogglesCamadas("container-camadas-inundacao", [
      { layer: setoresInundacao, rotulo: "Setores expostos à inundação", ligado: true },
      { layer: cotasInundacao, rotulo: "Mancha de inundação (contorno real)", ligado: true },
    ]);

    // grupo "Hidrografia e terreno" — subseção "Contexto"
    montarTogglesCamadas("container-camadas-hidro-contexto", [
      { layer: redeHidrografica, rotulo: "Rede hidrográfica", ligado: false },
      { layer: estacoesClima, rotulo: "Estações climatológicas (INMET)", ligado: true },
    ]);

    // grupo "Malha viária"
    montarTogglesCamadas("container-camadas-malha-viaria", [
      { layer: malhaViaria, rotulo: "OpenStreetMap (contexto)", ligado: false },
      { layer: malhaViariaDnit, rotulo: "DNIT — malha federal oficial", ligado: false },
    ]);

    // grupo "Estrutura fundiária" — toggle único (sem categorias)
    montarTogglesCamadas("container-camadas-estrutura-fundiaria", [
      { layer: estruturaFundiaria, rotulo: "Imóveis rurais (CAR)", ligado: false },
    ]);

    window.dispatchEvent(new CustomEvent("climapampa:camadas-prontas"));
  } catch (erro) {
    console.error("Erro ao carregar camadas do geoportal:", erro);
    document.getElementById("container-camadas-demografia").innerHTML =
      '<p class="secao-ajuda">Não foi possível carregar as camadas. Veja o console para detalhes.</p>';
  }
}

iniciarCamadas();
