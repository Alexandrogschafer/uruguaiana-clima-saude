/* Cabeçalho comum: navegação entre as telas e avisos de filtro.
   Telas que ainda não existirem aparecem desativadas, com "em breve" (hoje, nenhuma).
   O menu "Explorar" leva às seis telas temáticas. A marca em texto ("Clima" em --marca-1, "Pampa" em
   --marca-2; nenhum logo até haver autorização, ver config/identidade.json) leva ao mapa. "Atualizado em"
   (data do manifesto) fica na linha da tela, acima do título. A tela atual vem de <html data-tela="...">. */
(function(){
  const P = window.Paineis = window.Paineis || {};

  const PRINCIPAIS = [
    {tela: 'sintese', rotulo: 'Síntese', href: 'sintese.html'},
    {tela: 'cheia-saude', rotulo: 'Cheia e saúde', href: 'cheia-saude.html'},
  ];
  const EXPLORAR = [
    {tela: 'clima', rotulo: 'Clima e ambiente', href: 'clima.html'},
    {tela: 'saude', rotulo: 'Saúde', href: 'saude.html'},
    {tela: 'populacao', rotulo: 'População', href: 'populacao.html'},
    {tela: 'territorio', rotulo: 'Território e meio físico', href: 'territorio.html'},
    {tela: 'economia', rotulo: 'Economia e vulnerabilidade', href: 'economia.html'},
    {tela: 'riscos', rotulo: 'Riscos, desastres e infraestrutura', href: 'riscos.html'},
  ];

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
    if(!def.href){
      const s = document.createElement('span');
      s.className = 'nav-item nav-desativado';
      s.setAttribute('aria-disabled', 'true');
      s.innerHTML = `${def.rotulo} <small>em breve</small>`;
      return s;
    }
    const a = document.createElement('a');
    a.className = 'nav-item';
    a.href = def.href;
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

    // marca em texto: leva ao mapa (index.html)
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
    nav.setAttribute('aria-label', 'Painéis');
    PRINCIPAIS.forEach(d => nav.appendChild(item(d, atual)));

    const menu = document.createElement('details');
    menu.className = 'menu-explorar';
    const sum = document.createElement('summary');
    sum.textContent = 'Explorar';
    menu.appendChild(sum);
    const lista = document.createElement('div');
    lista.className = 'menu-explorar-lista';
    EXPLORAR.forEach(d => lista.appendChild(item(d, atual)));
    menu.appendChild(lista);
    nav.appendChild(menu);

    const mapa = document.createElement('a');
    mapa.className = 'nav-item nav-mapa';
    mapa.href = P.urlCom ? P.urlCom(`${P.raiz ? P.raiz() : '..'}/index.html`, ['bairro', 'unidade', 'cota', 'camada']) : '../index.html';
    mapa.textContent = 'Mapa';
    nav.appendChild(mapa);
    host.appendChild(nav);
    if(!eyebrow) host.appendChild(atualizado);

    // fecha o menu ao tocar fora dele
    document.addEventListener('pointerdown', e => { if(!menu.contains(e.target)) menu.open = false; });

    const avisos = (P.filtros && P.filtros.avisos) || [];
    if(avisos.length){
      const box = document.createElement('div');
      box.className = 'cabecalho-avisos';
      avisos.forEach(t => P.aviso(box, [{tipo: 'metodo', texto: t}]));
      host.appendChild(box);
    }
  };
})();
