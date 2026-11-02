/* Filtros compartilhados entre o portal e os painéis, guardados na URL.
   Parâmetros: bairro (CD_BAIRRO do IBGE, 10 dígitos, ou "municipio"),
   unidade (CNES, 7 dígitos), periodo (AAAA, AAAA-AAAA ou AAAA-MM),
   cota (cm), cenario (unico; pessimista/otimista viram unico enquanto os
   cenários coincidirem) e camada (chaves do portal separadas por vírgula).
   Valor inválido é ignorado, sai da URL e gera um aviso. Nenhum código de
   município fica fixo aqui: listas de valores válidos vêm da tela. */
(function(){
  const P = window.Paineis = window.Paineis || {};
  const NOMES = ['bairro', 'unidade', 'periodo', 'cota', 'cenario', 'camada'];

  function periodoValido(v){
    let m = /^(\d{4})$/.exec(v);
    if(m) return true;
    m = /^(\d{4})-(\d{4})$/.exec(v);
    if(m) return +m[1] <= +m[2];
    m = /^(\d{4})-(\d{2})$/.exec(v);
    return !!m && +m[2] >= 1 && +m[2] <= 12;
  }
  const FORMATO = {
    bairro:  v => /^\d{10}$/.test(v) || v === 'municipio',
    unidade: v => /^\d{7}$/.test(v),
    periodo: periodoValido,
    cota:    v => /^\d{3,4}$/.test(v),
    cenario: v => ['unico', 'pessimista', 'otimista'].includes(v),
    camada:  v => /^[a-z0-9-]+(,[a-z0-9-]+)*$/.test(v),
  };

  P.filtros = {estado: {}, avisos: [], listas: {}, cenariosDistintos: false};

  // anos de um período (AAAA, AAAA-AAAA ou AAAA-MM) → [início, fim]
  P.anosDoPeriodo = function(v){
    const m = /^(\d{4})(?:-(\d{4}))?/.exec(v || '');
    if(!m) return null;
    return [+m[1], m[2] && v.length === 9 ? +m[2] : +m[1]];
  };

  function validar(nome, valor){
    if(!FORMATO[nome](valor)) return false;
    const lista = P.filtros.listas[nome];
    // período: a tela passa {inicio, fim} em anos e o período precisa caber nele
    if(nome === 'periodo' && lista && !Array.isArray(lista)){
      const [a, b] = P.anosDoPeriodo(valor);
      return a >= lista.inicio && b <= lista.fim;
    }
    if(lista && nome !== 'camada' && valor !== 'municipio' && !lista.map(String).includes(valor)) return false;
    if(lista && nome === 'camada') return valor.split(',').every(c => lista.includes(c));
    return true;
  }

  function gravarNaUrl(){
    const params = new URLSearchParams(location.search);
    NOMES.forEach(n => params.delete(n));
    NOMES.forEach(n => { if(P.filtros.estado[n] !== undefined) params.set(n, P.filtros.estado[n]); });
    const busca = params.toString();
    history.replaceState(null, '', location.pathname + (busca ? '?' + busca : '') + location.hash);
  }

  // Lê a URL. opcoes.listas: {cota: [...], bairro: [...], ...} com valores aceitos pela tela.
  P.iniciarFiltros = function(opcoes){
    opcoes = opcoes || {};
    P.filtros.listas = opcoes.listas || {};
    P.filtros.cenariosDistintos = !!opcoes.cenariosDistintos;
    P.filtros.estado = {};
    P.filtros.avisos = [];
    const params = new URLSearchParams(location.search);
    NOMES.forEach(nome => {
      if(!params.has(nome)) return;
      let valor = params.get(nome).trim();
      if(!validar(nome, valor)){
        P.filtros.avisos.push(`Parâmetro ignorado: ${nome}=${valor}`);
        return;
      }
      if(nome === 'cenario' && valor !== 'unico' && !P.filtros.cenariosDistintos){
        P.filtros.avisos.push('Os cenários de pontes coincidem; mostrando o cenário único.');
        valor = 'unico';
      }
      P.filtros.estado[nome] = valor;
    });
    gravarNaUrl();
    P.filtros.avisos.forEach(a => console.warn(a));
    return P.filtros.estado;
  };

  // Muda um filtro (valor undefined remove) e avisa a tela com o evento filtros:mudou.
  P.definirFiltro = function(nome, valor){
    if(!NOMES.includes(nome)) throw new Error(`filtro desconhecido: ${nome}`);
    if(valor === undefined || valor === null || valor === '') delete P.filtros.estado[nome];
    else if(validar(nome, String(valor))) P.filtros.estado[nome] = String(valor);
    else { console.warn(`Parâmetro ignorado: ${nome}=${valor}`); return false; }
    gravarNaUrl();
    document.dispatchEvent(new CustomEvent('filtros:mudou', {detail: {...P.filtros.estado}}));
    return true;
  };

  // Link para outra tela levando só os parâmetros que o destino entende.
  P.urlCom = function(destino, nomesAceitos){
    const params = new URLSearchParams();
    (nomesAceitos || NOMES).forEach(n => { if(P.filtros.estado[n] !== undefined) params.set(n, P.filtros.estado[n]); });
    const busca = params.toString();
    return destino + (busca ? '?' + busca : '');
  };
})();
