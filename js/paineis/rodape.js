/* Rodapé comum: realização (só em texto; nenhum logo até haver autorização, ver
   config/identidade.json), fontes montadas a partir dos metadados dos produtos
   carregados (campo fonte_rodape, ou "fonte" se faltar), sem repetir, e a data de
   atualização (manifesto). */
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
    const real = document.createElement('p');
    real.className = 'rodape-realizacao';
    real.innerHTML = '<b>Realização:</b> ClimaPampa — PET-Saúde: Clima · Unipampa e Secretaria Municipal de Saúde de Uruguaiana';
    host.appendChild(real);

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

    const atual = document.createElement('p');
    atual.className = 'rodape-atualizado';
    host.appendChild(atual);
    if(P.dataAtualizacao) P.dataAtualizacao().then(d => { if(d) atual.textContent = `Atualizado em ${d}`; });
  };
})();
