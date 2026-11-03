/* Carregamento dos produtos de data/paineis/ (esquema climapampa-painel/1).
   A raiz do site vem de <html data-raiz="..">; o padrão é "..", porque as
   telas ficam em dashboards/. */
(function(){
  const P = window.Paineis = window.Paineis || {};
  const ESQUEMA = 'climapampa-painel/1';
  P.produtosCarregados = [];

  P.raiz = () => document.documentElement.dataset.raiz || '..';
  P.urlProduto = id => `${P.raiz()}/data/paineis/${id}.json`;

  // Manifesto (data/paineis/manifesto.json), lido uma vez por página: diz quais ids estão publicados e, pelo
  // campo "substitui", qual id novo responde por um id antigo (ids com período no nome, até a rodada 119).
  let manifestoPromessa = null;
  P.manifesto = function(){
    if(!manifestoPromessa){
      manifestoPromessa = fetch(`${P.raiz()}/data/paineis/manifesto.json`)
        .then(r => { if(!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .catch(e => { console.warn(`manifesto indisponível: ${e.message}`); return null; });
    }
    return manifestoPromessa;
  };

  // id pedido → id publicado: ele mesmo, se está no manifesto; o produto que o "substitui", se é id antigo.
  // Sem manifesto (falha de rede), segue com o id pedido. Id que não existe → erro (vai para o bloco).
  P.resolverId = async function(id){
    const m = await P.manifesto();
    if(!m || !Array.isArray(m.produtos)) return id;
    if(m.produtos.some(p => p.id === id)) return id;
    const novo = m.produtos.find(p => p.substitui === id);
    if(novo) return novo.id;
    throw new Error('produto não está no manifesto');
  };

  // Busca o produto e confere o esquema. Em caso de falha, mostra o erro no
  // bloco (se dado) e devolve null, em vez de lançar e parar a página.
  P.carregar = async function(id, bloco){
    try{
      const publicado = await P.resolverId(id);
      const resp = await fetch(P.urlProduto(publicado));
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
