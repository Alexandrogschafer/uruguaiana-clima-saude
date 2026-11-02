/* Cabeçalho comum: navegação entre as telas e avisos de filtro.
   Telas que ainda não existem aparecem desativadas, com "em breve".
   Os painéis antigos de saúde, demografia e território seguem no menu
   "Explorar" até serem migrados. A tela atual vem de <html data-tela="...">. */
(function(){
  const P = window.Paineis = window.Paineis || {};

  const PRINCIPAIS = [
    {tela: 'sintese', rotulo: 'Síntese', href: null},
    {tela: 'cheia-saude', rotulo: 'Cheia e saúde', href: null},
  ];
  const EXPLORAR = [
    {tela: 'clima', rotulo: 'Clima e ambiente', href: 'clima.html'},
    {tela: 'saude', rotulo: 'Saúde', href: 'painel-saude.html'},
    {tela: 'demografia', rotulo: 'Demografia', href: 'painel-demografia.html'},
    {tela: 'territorio', rotulo: 'Território e vulnerabilidade', href: 'painel-territorio.html'},
  ];

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

    const marca = document.createElement('span');
    marca.className = 'cabecalho-marca';
    marca.textContent = 'ClimaPampa · Uruguaiana';
    host.appendChild(marca);

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
