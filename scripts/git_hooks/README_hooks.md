# Trava pré-commit

`scripts/git_hooks/pre-commit` roda antes de cada commit e examina **só os
arquivos em stage**. Bloqueia o commit e diz o arquivo e a regra, sem mostrar o
trecho encontrado.

## Instalação (uma vez por clone)

```bash
sh scripts/git_hooks/instalar.sh
```

O instalador é idempotente. Ele copia o hook para `.git/hooks/` e, se faltar,
cria o arquivo local `.git/info/padroes_proibidos.txt` vazio. Quando o hook
versionado muda, rode o instalador de novo.

## Regras

| Regra | Bloqueia |
|---|---|
| a | qualquer caminho em `data/campo/` ou `data/auditoria_cnes/` |
| b | texto (.py .js .html .md .csv .json .yml .txt .geojson) com caminho local de usuário: pasta pessoal do Linux/WSL ou do Windows |
| c | texto com padrão de CPF; em CSV/GeoJSON, telefone com DDD 53 (formatado, ou só dígitos numa coluna de contato). Coordenadas e códigos numéricos não casam |
| d | PDF, .qgz, .gpkg, .shp, .tif/.tiff, .zip, salvo exceção por caminho exato (`EXCECOES_BINARIOS`) |
| e | arquivo maior que 5 MB, salvo exceção por caminho exato (`EXCECOES_GRANDES`) |
| f | qualquer regex de `.git/info/padroes_proibidos.txt`, no caminho e no conteúdo, sem distinguir maiúsculas |

As listas de exceção foram tiradas do `git ls-files` em 2026-10-21 e estão
vazias: o repositório não rastreia nenhum arquivo desses tipos nem maior que
5 MB.

## Padrões locais (regra f)

O arquivo `.git/info/padroes_proibidos.txt` fica **fora do git** e é de cada
clone. Ele tem um regex por linha, e as linhas que começam com `#` são
ignoradas. Os termos ficam só nesse arquivo: não os copie para nenhum arquivo
versionado, nem para este.

## Conferência sem commitar

```bash
python3 scripts/git_hooks/pre-commit --todos          # todos os arquivos rastreados no HEAD
python3 scripts/git_hooks/pre-commit --commit <sha>   # arquivos de um commit
```

## Contorno consciente

Quando o bloqueio for falso positivo conferido e não der para ajustar a regra:

```bash
git commit --no-verify
```

Use só depois de abrir o arquivo e confirmar que nada sensível vai junto. Se o
falso positivo se repetir, ajuste a regra no hook versionado e reinstale.
