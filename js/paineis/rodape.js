/* Rodapé comum: realização (só em texto; nenhum logo até haver autorização, ver
   config/identidade.json), fontes montadas a partir dos metadados dos produtos
   carregados (campo fonte_rodape, ou "fonte" se faltar), sem repetir, o bloco recolhível
   "Dados desta tela" (rodada A2) e a data de atualização (manifesto).

   "Dados desta tela": cada produto da tela (pelo campo "telas" do manifesto, mais os que a página
   carregou) com título, período, selo, "Baixar CSV", "Baixar JSON" e "Sobre este dado" (fonte,
   período, selo, licença, data de coleta e método; licença, coleta e método vêm do catálogo,
   data/paineis/catalogo_dados.json). */
(function(){
  const P = window.Paineis = window.Paineis || {};

  const raiz = () => (P.raiz ? P.raiz() : '..');
  const ROTULO_SELO = {conferido: 'conferido', em_confirmacao: 'em confirmação', pendente: 'pendente'};
  const periodoTxt = m => { const i = m && m.periodo ? String(m.periodo.inicio) : '', f = m && m.periodo ? String(m.periodo.fim) : '';
    return !i ? '' : (i === f ? i : `${i}–${f}`); };
  const criarEl = (tag, cls, txt) => { const e = document.createElement(tag); if(cls) e.className = cls; if(txt !== undefined) e.textContent = txt; return e; };

  async function montarDadosTela(bloco){
    const tela = document.documentElement.dataset.tela;
    const [man, cat] = await Promise.all([
      P.manifesto ? P.manifesto() : null,
      fetch(`${raiz()}/data/paineis/catalogo_dados.json`).then(r => r.ok ? r.json() : null).catch(() => null),
    ]);
    if(!man) throw new Error('manifesto indisponível');
    const carregados = Object.fromEntries((P.produtosCarregados || []).map(m => [m.id, m]));
    const itens = man.produtos.filter(i => (i.telas || []).includes(tela) || carregados[i.id]);
    if(!itens.length){ bloco.remove(); return; }
    const sobre = (cat && cat.sobre_produtos) || {};
    const sum = criarEl('summary', null, `Dados desta tela (${itens.length})`);
    bloco.appendChild(sum);
    const ul = criarEl('ul', 'lista-dados-tela');
    itens.forEach(i => {
      const m = carregados[i.id], s = sobre[i.id] || {};
      const li = criarEl('li');
      li.appendChild(criarEl('span', 'dado-titulo', i.titulo));
      const per = periodoTxt(m) || i.dados_ate;
      li.appendChild(criarEl('span', 'dado-periodo', ` · ${per} · `));
      if(P.selo) li.appendChild(P.selo(i.selo));
      const baixar = criarEl('span', 'dado-baixar');
      if(i.csv){ const a = criarEl('a', null, 'Baixar CSV'); a.href = `${raiz()}/data/paineis/${i.csv}`; a.setAttribute('download', ''); baixar.appendChild(a); }
      const j = criarEl('a', null, 'Baixar JSON'); j.href = `${raiz()}/data/paineis/${i.id}.json`; j.setAttribute('download', ''); baixar.appendChild(j);
      li.appendChild(baixar);
      const det = criarEl('details', 'sobre-dado');
      det.appendChild(criarEl('summary', null, 'Sobre este dado'));
      const dl = criarEl('dl');
      [['Fonte', m ? m.fonte : ''], ['Período', per], ['Selo', ROTULO_SELO[i.selo] || i.selo], ['Licença', s.licenca || 'ver fonte'],
       ['Data de coleta', s.data_coleta ? s.data_coleta.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$3/$2/$1') : '—'], ['Método', s.metodo || '—']].forEach(([k, v]) => {
        if(!v) return;
        dl.appendChild(criarEl('dt', null, k)); dl.appendChild(criarEl('dd', null, v));
      });
      det.appendChild(dl);
      li.appendChild(det);
      ul.appendChild(li);
    });
    bloco.appendChild(ul);
  }

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
    p.appendChild(document.createTextNode(' ' + vistas.map(t => t.replace(/\.$/, '')).join('. ') + '. Licença, período e método de cada fonte em '));
    const cat = document.createElement('a');
    cat.href = `${raiz()}/dashboards/catalogo.html`;
    cat.textContent = 'Dados';  // mesmo nome do menu e do h1 do catálogo
    p.appendChild(cat);
    p.appendChild(document.createTextNode('.'));
    host.appendChild(p);

    const bloco = document.createElement('details');
    bloco.className = 'dados-tela';
    bloco.id = 'dados-tela';
    host.appendChild(bloco);
    montarDadosTela(bloco).catch(e => { console.warn(`dados desta tela: ${e.message}`); bloco.remove(); });

    const atual = document.createElement('p');
    atual.className = 'rodape-atualizado';
    host.appendChild(atual);
    if(P.dataAtualizacao) P.dataAtualizacao().then(d => { if(d) atual.textContent = `Atualizado em ${d}`; });
  };
})();
