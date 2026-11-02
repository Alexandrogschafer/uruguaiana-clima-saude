/* Carregamento dos produtos de data/paineis/ (esquema climapampa-painel/1).
   A raiz do site vem de <html data-raiz="..">; o padrão é "..", porque as
   telas ficam em dashboards/. */
(function(){
  const P = window.Paineis = window.Paineis || {};
  const ESQUEMA = 'climapampa-painel/1';
  P.produtosCarregados = [];

  P.raiz = () => document.documentElement.dataset.raiz || '..';
  P.urlProduto = id => `${P.raiz()}/data/paineis/${id}.json`;

  // Busca o produto e confere o esquema. Em caso de falha, mostra o erro no
  // bloco (se dado) e devolve null, em vez de lançar e parar a página.
  P.carregar = async function(id, bloco){
    try{
      const resp = await fetch(P.urlProduto(id));
      if(!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      if(json.esquema !== ESQUEMA) throw new Error(`esquema inesperado (${json.esquema})`);
      if(!Array.isArray(json.dados)) throw new Error('campo "dados" ausente');
      const {dados, ...meta} = json;
      P.produtosCarregados.push(meta);
      return {meta, dados};
    }catch(erro){
      console.error(`produto ${id}: ${erro.message}`);
      P.erroBloco(typeof bloco === 'string' ? document.getElementById(bloco) : bloco, erro.message);
      return null;
    }
  };

  // Carrega vários produtos em paralelo; devolve {id: {meta, dados} | null}.
  P.carregarVarios = async function(ids){
    const res = await Promise.all(ids.map(id => P.carregar(id)));
    return Object.fromEntries(ids.map((id, i) => [id, res[i]]));
  };
})();
