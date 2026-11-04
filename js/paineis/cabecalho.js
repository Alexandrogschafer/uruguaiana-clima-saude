/* Cabeçalho comum em dois níveis (rodada A8) e avisos de filtro.
   Nível 1: marca em texto ("Clima" em --marca-1, "Pampa" em --marca-2, linha "Painéis · Uruguaiana/RS"; nenhum
   logo até haver autorização, ver config/identidade.json) à esquerda, levando à página inicial; à direita
   Início · Mapa · Dados · ClimaPampa ↗ (site do projeto, nova aba).
   Nível 2: faixa de abas com as seis dimensões e, depois de um separador, Cheia e saúde.
   A tela atual (de <html data-tela="...">) fica com aria-current="page": no nível 1 para início, mapa e dados;
   no nível 2 para as dimensões e a cheia. No celular (até 720 px) o nível 1 é marca + ☰, e o ☰ abre a lista
   completa (nível 1 e abas, nessa ordem). Os caminhos são da raiz do site (data-raiz), para valer no início
   (index.html) e nas telas (dashboards/).
   Os nomes das abas são os "nome" de config/textos_inicio.json (iguais aos h1 das telas); cabecalho.js não lê o
   arquivo (seria uma requisição a mais por tela) e test_regras_publicas.py confere a cópia.
   "Atualizado em" (data do manifesto) divide a linha com o h1 da tela: título à esquerda, data à direita; no
   celular, a data vai abaixo do título. */
(function(){
  const P = window.Paineis = window.Paineis || {};

  // nível 1; `mapa` = o link leva os filtros da tela ao mapa
  const NIVEL1 = [
    {tela: 'inicio', rotulo: 'Início', caminho: 'index.html'},
    {tela: 'mapa', rotulo: 'Mapa', caminho: 'mapa.html', mapa: true},
    {tela: 'catalogo', rotulo: 'Dados', caminho: 'dashboards/catalogo.html'},
  ];
  // site do projeto: lido de config/textos_inicio.json seria uma requisição a mais em toda tela; fica aqui
  const SITE_PROJETO = {rotulo: 'ClimaPampa ↗', href: 'https://pet-saude-climapampa.github.io/'};
  // nível 2: as seis dimensões e, depois do separador, o cruzamento (nomes = "nome" de config/textos_inicio.json)
  const DIMENSOES = [
    {tela: 'clima', rotulo: 'Clima', caminho: 'dashboards/clima.html'},
    {tela: 'saude', rotulo: 'Saúde', caminho: 'dashboards/saude.html'},
    {tela: 'populacao', rotulo: 'População', caminho: 'dashboards/populacao.html'},
    {tela: 'territorio', rotulo: 'Território', caminho: 'dashboards/territorio.html'},
    {tela: 'economia', rotulo: 'Economia', caminho: 'dashboards/economia.html'},
    {tela: 'riscos', rotulo: 'Riscos', caminho: 'dashboards/riscos.html'},
  ];
  const CRUZAMENTOS = [
    {tela: 'cheia-saude', rotulo: 'Cheia e saúde', caminho: 'dashboards/cheia-saude.html'},
  ];

  // nome do sistema e linha pequena da marca: cópia de config/identidade.json (nome_sistema.nome e
  // marca_texto.linha_pequena; test_regras_publicas.py confere). O município entra na linha pequena.
  const NOME_SISTEMA = 'Painéis ClimaPampa';
  const LINHA_MARCA = 'Painéis · Uruguaiana/RS';

  // data de geração do manifesto de data/paineis/ como DD/MM/AAAA (ou '' se faltar); usada também no rodapé
  P.dataAtualizacao = function(){
    const m = P.manifesto ? P.manifesto() : Promise.resolve(null);
    return m.then(x => {
      const d = /^(\d{4})-(\d{2})-(\d{2})/.exec((x && x.gerado_em) || '');
      return d ? `${d[3]}/${d[2]}/${d[1]}` : '';
    });
  };

  function item(def, atual, classe){
    const a = document.createElement('a');
    a.className = classe || 'nav-item';
    const raiz = P.raiz ? P.raiz() : '..';
    a.href = def.href || (def.mapa && P.urlCom ? P.urlCom(`${raiz}/${def.caminho}`, ['bairro', 'unidade', 'cota', 'camada']) : `${raiz}/${def.caminho}`);
    a.textContent = def.rotulo;
    if(def.tela && def.tela === atual) a.setAttribute('aria-current', 'page');
    return a;
  }

  P.montarCabecalho = function(){
    const host = document.getElementById('cabecalho-comum');
    if(!host) return;
    const atual = document.documentElement.dataset.tela;
    host.innerHTML = '';
    host.className = 'cabecalho-comum';

    // ---- nível 1: marca, ☰ (só no celular) e Início · Mapa · Dados · ClimaPampa ↗
    const n1 = document.createElement('div');
    n1.className = 'cabecalho-n1';
    const marca = document.createElement('a');
    marca.href = `${P.raiz ? P.raiz() : '..'}/index.html`;
    marca.className = 'cabecalho-marca';
    marca.setAttribute('aria-label', `${NOME_SISTEMA} — página inicial`);
    marca.innerHTML = '<span class="marca-texto" aria-hidden="true"><span class="marca-clima">Clima</span><span class="marca-pampa">Pampa</span></span>' +
      '<span class="marca-linha" aria-hidden="true"></span>';
    marca.querySelector('.marca-linha').textContent = LINHA_MARCA;
    n1.appendChild(marca);

    // o botão vem antes das listas: no celular a ordem do Tab é marca → ☰ → itens
    const botao = document.createElement('button');
    botao.type = 'button';
    botao.className = 'menu-celular';
    botao.setAttribute('aria-controls', 'cabecalho-nav cabecalho-abas');
    botao.setAttribute('aria-expanded', 'false');
    botao.innerHTML = '<span aria-hidden="true">☰</span> Menu';
    n1.appendChild(botao);

    const nav = document.createElement('nav');
    nav.className = 'cabecalho-nav';
    nav.id = 'cabecalho-nav';
    nav.setAttribute('aria-label', 'Navegação principal');
    NIVEL1.forEach(d => nav.appendChild(item(d, atual)));
    const site = item(SITE_PROJETO, atual);
    site.classList.add('nav-site');
    site.target = '_blank';
    site.rel = 'noopener';
    nav.appendChild(site);
    n1.appendChild(nav);
    host.appendChild(n1);

    // ---- nível 2: faixa de abas
    const abas = document.createElement('nav');
    abas.className = 'cabecalho-abas';
    abas.id = 'cabecalho-abas';
    abas.setAttribute('aria-label', `Telas dos ${NOME_SISTEMA}`);
    DIMENSOES.forEach(d => abas.appendChild(item(d, atual, 'aba')));
    const sep = document.createElement('span');
    sep.className = 'abas-sep';
    sep.setAttribute('aria-hidden', 'true');
    abas.appendChild(sep);
    CRUZAMENTOS.forEach(d => abas.appendChild(item(d, atual, 'aba')));
    host.appendChild(abas);

    botao.addEventListener('click', () => {
      const aberto = host.classList.toggle('menu-aberto');
      botao.setAttribute('aria-expanded', String(aberto));
    });

    // ---- "Atualizado em" na linha do título da tela (h1 à esquerda, data à direita)
    const atualizado = document.createElement('span');
    atualizado.className = 'atualizado-tela';
    const h1 = document.querySelector('.masthead h1');
    if(h1){
      const linha = document.createElement('div');
      linha.className = 'linha-titulo';
      h1.parentNode.insertBefore(linha, h1);
      linha.appendChild(h1);
      linha.appendChild(atualizado);
    } else {
      host.appendChild(atualizado);
    }
    P.dataAtualizacao().then(d => { if(d) atualizado.textContent = `Atualizado em ${d}`; });

    const avisos = (P.filtros && P.filtros.avisos) || [];
    if(avisos.length){
      const box = document.createElement('div');
      box.className = 'cabecalho-avisos';
      avisos.forEach(t => P.aviso(box, [{tipo: 'metodo', texto: t}]));
      host.appendChild(box);
    }
  };
})();
