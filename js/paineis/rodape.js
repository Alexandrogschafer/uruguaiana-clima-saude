/* Rodapé comum: fontes montadas a partir dos metadados dos produtos
   carregados (campo fonte_rodape, ou "fonte" se faltar), sem repetir. */
(function(){
  const P = window.Paineis = window.Paineis || {};

  P.montarRodape = function(){
    const host = document.getElementById('rodape-comum');
    if(!host) return;
    const vistas = [];
    (P.produtosCarregados || []).forEach(m => {
      // fonte_rodape vazio = produto que só junta fontes já citadas (ex.: tabela)
      const t = ('fonte_rodape' in m ? m.fonte_rodape : (m.fonte || '')).trim();
      if(t && !vistas.includes(t)) vistas.push(t);
    });
    host.innerHTML = '';
    const p = document.createElement('p');
    const b = document.createElement('b');
    b.textContent = 'Fontes:';
    p.appendChild(b);
    p.appendChild(document.createTextNode(' ' + vistas.map(t => t.replace(/\.$/, '')).join('. ') + '. Ver '));
    const code = document.createElement('code');
    code.textContent = 'data/catalogo_fontes.csv';
    p.appendChild(code);
    p.appendChild(document.createTextNode(' no repositório para licença, método e limitações de cada fonte.'));
    host.appendChild(p);

    const p2 = document.createElement('p');
    p2.innerHTML = '<b>Outros painéis:</b> no menu do topo (Explorar) e no <a href="' + (P.raiz ? P.raiz() : '..') + '/index.html">mapa</a>, que traz a visão espacial.';
    host.appendChild(p2);
  };
})();
