#!/bin/sh
# Instala a trava pré-commit neste clone (idempotente: só copia se mudou).
# Também cria, se faltar, o arquivo LOCAL de padrões proibidos, vazio — os
# termos são preenchidos à mão e nunca entram em arquivo versionado.
set -eu
raiz=$(git rev-parse --show-toplevel)
origem="$raiz/scripts/git_hooks/pre-commit"
destino=$(git rev-parse --git-path hooks/pre-commit)
padroes=$(git rev-parse --git-path info/padroes_proibidos.txt)

if [ -f "$destino" ] && cmp -s "$origem" "$destino"; then
  echo "hook já instalado e atualizado: $destino"
else
  if [ -f "$destino" ] && ! grep -q "Trava pré-commit" "$destino"; then
    cp "$destino" "$destino.anterior"
    echo "hook anterior guardado em $destino.anterior"
  fi
  cp "$origem" "$destino"
  chmod +x "$destino"
  echo "hook instalado: $destino"
fi

if [ ! -f "$padroes" ]; then
  mkdir -p "$(dirname "$padroes")"
  printf '%s\n' "# um regex por linha, insensível a maiúsculas; linhas com # são ignoradas" > "$padroes"
  echo "criado $padroes (vazio): preencha os termos à mão"
fi
