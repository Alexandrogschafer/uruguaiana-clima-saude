# Acervo de documentos de referência

Um documento da internet pode sair do ar de um dia para o outro. Por isso cada fonte citada nos estudos tem uma cópia local, conferida e registrada com endereço, data, tamanho e sha256.

## Onde ficam os arquivos

Os arquivos ficam **fora do git**, por direito autoral e por tamanho: documentos e páginas salvas em `data/raw/documentos/`; anexos espaciais em `data/raw/vetor/`. Cada arquivo tem um `.json` irmão. No git ficam só:

- `lista_de_fontes.csv` — a lista de entrada (referência, endereço, nome do arquivo, termos de conferência);
- `manifesto_documentos.csv` — o resultado, uma linha por arquivo;
- o script `scripts/download/documentos_referencia.py`.

## Como rodar

```bash
python scripts/download/documentos_referencia.py                        # só o que falta
python scripts/download/documentos_referencia.py --copias-locais PASTA  # copia o que o responsável já tem, achando pelo sha256
python scripts/download/documentos_referencia.py --so D013 --forcar     # pede de novo um documento
```

Na lista, a coluna `url_arquivo` (opcional) dá o endereço direto do arquivo, pedido sem passar pela página do registro; vários endereços, separados por espaço, são partes (`_parte-N`). O modo `partes` baixa todos os PDF de um registro, na ordem dele, com o mesmo sufixo.

Arquivo igual ao que já existe não é regravado; arquivo diferente não é sobrescrito: o novo ganha o sufixo `_baixado-AAAA-MM-DD`.

## Situações do manifesto

| Situação | Significado |
|---|---|
| baixado | baixado e conferido nesta ou em outra rodada |
| já tínhamos | cópia local do responsável, ou arquivo idêntico a outro já guardado |
| recusado pelo site | o site negou o pedido (403, captcha, verificação de navegador, login) |
| sem arquivo aberto | a página existe, mas não oferece o arquivo |
| pago | só à venda |
| não localizado | sem endereço, endereço inexistente ou servidor sem resposta |

A coluna `titulo_confere` diz se o título ou os autores aparecem nas duas primeiras páginas do PDF ("não verificado": página em imagem ou sem termos de conferência).

## Bloqueios

Nada de contornar bloqueio. Site que recusa é registrado como "recusado pelo site" e o download segue. Sem espelhos, sem caches, sem sites de cópia não autorizada: esses documentos são baixados à mão pelo responsável e entram depois por `--copias-locais`.
