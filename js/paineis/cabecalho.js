/* Cabeçalho comum: navegação entre as telas e avisos de filtro.
   Menu (rodada A2): Início · Mapa · Cheia e saúde · Clima · Saúde · Mais temas ▾ (População, Território,
   Economia, Riscos) · Dados · ClimaPampa ↗ (site do projeto, nova aba). No celular (até 720 px) o botão ☰
   abre a lista inteira, com os "Mais temas" abertos. Os caminhos são da raiz do site (data-raiz), para
   valer no início (index.html) e nas telas (dashboards/). A marca em texto ("Clima" em --marca-1, "Pampa"
   em --marca-2; nenhum logo até haver autorização, ver config/identidade.json) leva à página inicial.
   "Atualizado em" (data do manifesto) fica na linha da tela, acima do título. A tela atual vem de
   <html data-tela="...">. */
(function(){
  const P = window.Paineis = window.Paineis || {};

  // caminho a partir da raiz do site; `mapa` = o link leva os filtros da tela ao mapa
  const PRINCIPAIS = [
    {tela: 'inicio', rotulo: 'Início', caminho: 'index.html'},
    {tela: 'mapa', rotulo: 'Mapa', caminho: 'mapa.html', mapa: true},
    {tela: 'cheia-saude', rotulo: 'Cheia e saúde', caminho: 'dashboards/cheia-saude.html'},
    {tela: 'clima', rotulo: 'Clima', caminho: 'dashboards/clima.html'},
    {tela: 'saude', rotulo: 'Saúde', caminho: 'dashboards/saude.html'},
  ];
  const MAIS_TEMAS = [
    {tela: 'populacao', rotulo: 'População', caminho: 'dashboards/populacao.html'},
    {tela: 'territorio', rotulo: 'Território', caminho: 'dashboards/territorio.html'},
    {tela: 'economia', rotulo: 'Economia', caminho: 'dashboards/economia.html'},
    {tela: 'riscos', rotulo: 'Riscos', caminho: 'dashboards/riscos.html'},
  ];
  const FINAIS = [
    {tela: 'catalogo', rotulo: 'Dados', caminho: 'dashboards/catalogo.html'},
  ];
  // site do projeto: lido de config/textos_inicio.json seria uma requisição a mais em toda tela; fica aqui
  const SITE_PROJETO = {rotulo: 'ClimaPampa ↗', href: 'https://pet-saude-climapampa.github.io/'};

  // linha pequena da marca (o município entra aqui; o resto da marca não muda entre municípios)
  const LINHA_MARCA = 'Geoportal · Uruguaiana/RS';

  // data de geração do manifesto de data/paineis/ como DD/MM/AAAA (ou '' se faltar); usada também no rodapé
  P.dataAtualizacao = function(){
    const m = P.manifesto ? P.manifesto() : Promise.resolve(null);
    return m.then(x => {
      const d = /^(\d{4})-(\d{2})-(\d{2})/.exec((x && x.gerado_em) || '');
      return d ? `${d[3]}/${d[2]}/${d[1]}` : '';
    });
  };

  function item(def, atual){
    if(!def.href && !def.caminho){
      const s = document.createElement('span');
      s.className = 'nav-item nav-desativado';
      s.setAttribute('aria-disabled', 'true');
      s.innerHTML = `${def.rotulo} <small>em breve</small>`;
      return s;
    }
    const a = document.createElement('a');
    a.className = 'nav-item';
    const raiz = P.raiz ? P.raiz() : '..';
    a.href = def.href || (def.mapa && P.urlCom ? P.urlCom(`${raiz}/${def.caminho}`, ['bairro', 'unidade', 'cota', 'camada']) : `${raiz}/${def.caminho}`);
    a.textContent = def.rotulo;
    if(def.tela === atual) a.setAttribute('aria-current', 'page');
    return a;
  }

  P.montarCabecalho = function(){
    const host = document.getElementById('cabecalho-comum');
    if(!host) return;
    const atual = document.documentElement.dataset.tela;
    host.innerHTML = '';
    host.className = 'cabecalho-comum';

    // marca em texto: leva à página inicial (index.html)
    const marca = document.createElement('a');
    marca.href = `${P.raiz ? P.raiz() : '..'}/index.html`;
    marca.className = 'cabecalho-marca';
    marca.setAttribute('aria-label', `ClimaPampa — ${LINHA_MARCA}`);
    marca.innerHTML = '<span class="marca-texto" aria-hidden="true"><span class="marca-clima">Clima</span><span class="marca-pampa">Pampa</span></span>' +
      '<span class="marca-linha" aria-hidden="true"></span>';
    marca.querySelector('.marca-linha').textContent = LINHA_MARCA;
    host.appendChild(marca);

    // "Atualizado em" na linha da tela (ao lado do eyebrow, acima do título); sem masthead, fica no cabeçalho
    const atualizado = document.createElement('span');
    atualizado.className = 'atualizado-tela';
    const eyebrow = document.querySelector('.masthead .eyebrow');
    if(eyebrow){
      const linha = document.createElement('div');
      linha.className = 'linha-tela';
      eyebrow.parentNode.insertBefore(linha, eyebrow);
      linha.appendChild(eyebrow);
      linha.appendChild(atualizado);
    }
    P.dataAtualizacao().then(d => { if(d) atualizado.textContent = `Atualizado em ${d}`; });

    const nav = document.createElement('nav');
    nav.className = 'cabecalho-nav';
    nav.id = 'cabecalho-nav';
    nav.setAttribute('aria-label', 'Telas do geoportal');
    PRINCIPAIS.forEach(d => nav.appendChild(item(d, atual)));

    const menu = document.createElement('details');
    menu.className = 'menu-explorar';
    const sum = document.createElement('summary');
    sum.textContent = 'Mais temas';
    if(MAIS_TEMAS.some(d => d.tela === atual)) sum.classList.add('nav-atual');
    menu.appendChild(sum);
    const lista = document.createElement('div');
    lista.className = 'menu-explorar-lista';
    MAIS_TEMAS.forEach(d => lista.appendChild(item(d, atual)));
    menu.appendChild(lista);
    nav.appendChild(menu);
    FINAIS.forEach(d => nav.appendChild(item(d, atual)));
    const site = item(SITE_PROJETO, atual);
    site.classList.add('nav-site');
    site.target = '_blank';
    site.rel = 'noopener';
    nav.appendChild(site);

    // celular: ☰ abre e fecha a lista inteira (com "Mais temas" aberto)
    const botao = document.createElement('button');
    botao.type = 'button';
    botao.className = 'menu-celular';
    botao.setAttribute('aria-controls', 'cabecalho-nav');
    botao.setAttribute('aria-expanded', 'false');
    botao.innerHTML = '<span aria-hidden="true">☰</span> Menu';
    botao.addEventListener('click', () => {
      const aberto = host.classList.toggle('menu-aberto');
      botao.setAttribute('aria-expanded', String(aberto));
      menu.open = aberto;
    });
    host.appendChild(botao);
    host.appendChild(nav);
    if(!eyebrow) host.appendChild(atualizado);

    // fecha o "Mais temas" ao tocar fora dele (com o ☰ aberto, ele fica aberto dentro da lista)
    document.addEventListener('pointerdown', e => { if(!menu.contains(e.target) && !host.classList.contains('menu-aberto')) menu.open = false; });

    const avisos = (P.filtros && P.filtros.avisos) || [];
    if(avisos.length){
      const box = document.createElement('div');
      box.className = 'cabecalho-avisos';
      avisos.forEach(t => P.aviso(box, [{tipo: 'metodo', texto: t}]));
      host.appendChild(box);
    }
  };
})();
