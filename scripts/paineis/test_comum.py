"""
Testes de scripts/paineis/comum.py, sem rede e sem tocar em data/.

Uso:
    python -m unittest scripts/paineis/test_comum.py -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comum  # noqa: E402

AUT = {
    "L1": {"id": "L1", "autorizacao": "livre", "excecao_premissa": "", "arquivo_origem": "a.csv"},
    "Q1": {"id": "Q1", "autorizacao": "depende de conferência no QGIS", "excecao_premissa": "", "arquivo_origem": "b.csv"},
    "B1": {"id": "B1", "autorizacao": "bloqueada", "excecao_premissa": "", "arquivo_origem": ""},
    "E1": {"id": "E1", "autorizacao": "depende de conferência no QGIS", "excecao_premissa": "P6: aviso", "arquivo_origem": "c.csv"},
}


def produto(dados, ativos=("L1",), **kw):
    base = dict(id="teste_x_2020-2021_anual", codigo_ibge="0000000", ativos=list(ativos), titulo="t", fonte_curta="F",
                fonte="Fonte", unidade_espacial="município", unidade_medida="n", periodo={"inicio": "2020", "fim": "2021"},
                dados_ate="2021-12-31", selo="conferido", avisos=[], script="scripts/paineis/x.py",
                colunas=[{"nome": k, "tipo": "int"} for k in dados[0]], dados=dados, autorizacoes=AUT)
    base.update(kw)
    return comum.montar_produto(**base)


class Supressao(unittest.TestCase):
    def test_primaria(self):
        dados = [{"b": "a", "n": 3}, {"b": "b", "n": 12}, {"b": "c", "n": 5}]
        out, sup, prim, sec = comum.suprimir(dados, ["n"])
        self.assertEqual([r["n"] for r in out], [None, 12, 5])
        self.assertEqual((prim, sec), (1, 0))
        self.assertEqual(sup, [{"linha": 0, "campo": "n", "motivo": "primaria"}])
        self.assertEqual(dados[0]["n"], 3, "a entrada não pode ser alterada")

    def test_zero_conforme_parametro(self):
        dados = [{"n": 0}, {"n": 9}]
        out, _, prim, _ = comum.suprimir(dados, ["n"])
        self.assertEqual(out[0]["n"], 0)
        self.assertEqual(prim, 0)
        out, _, prim, _ = comum.suprimir(dados, ["n"], suprimir_zero=True)
        self.assertIsNone(out[0]["n"])
        self.assertEqual(prim, 1)

    def test_ausente_nao_vira_zero_nem_suprimido(self):
        dados = [{"n": None}, {"n": 2}]
        out, sup, _, _ = comum.suprimir(dados, ["n"])
        self.assertIsNone(out[0]["n"])
        self.assertEqual([s["linha"] for s in sup], [1], "null ausente não entra na lista de suprimidos")

    def test_secundaria(self):
        # total publicado = 30; só "a" seria suprimido → "b" (menor restante) também sai
        dados = [{"n": 3}, {"n": 7}, {"n": 20}]
        out, sup, prim, sec = comum.suprimir(dados, ["n"], grupos=[[0, 1, 2]])
        self.assertEqual([r["n"] for r in out], [None, None, 20])
        self.assertEqual((prim, sec), (1, 1))
        self.assertEqual(sup[1]["motivo"], "secundaria")

    def test_secundaria_dispensada_com_duas_primarias(self):
        dados = [{"n": 3}, {"n": 2}, {"n": 20}]
        out, _, prim, sec = comum.suprimir(dados, ["n"], grupos=[[0, 1, 2]])
        self.assertEqual([r["n"] for r in out], [None, None, 20])
        self.assertEqual((prim, sec), (2, 0))

    def test_vinculo(self):
        dados = [{"end": 4, "pop": 11.5}, {"end": 40, "pop": 100.0}]
        out, _, _, _ = comum.suprimir(dados, ["end"], vinculos={"end": ["pop"]})
        self.assertIsNone(out[0]["pop"])
        self.assertEqual(out[1]["pop"], 100.0)

    def test_valor_verdadeiro_nao_vai_ao_json(self):
        p = produto([{"ano": 2020, "n": 3}, {"ano": 2021, "n": 4444}], campos_contagem=["n"])
        texto = json.dumps(p)
        self.assertNotIn('"n": 3', texto)
        self.assertEqual(p["suprimidos"], [{"linha": 0, "campo": "n", "motivo": "primaria"}])
        self.assertTrue(any(c.get("contagem") for c in p["colunas"]))


class NullNaoEZero(unittest.TestCase):
    def test_nan_vira_null(self):
        p = produto([{"ano": 2020, "v": float("nan")}, {"ano": 2021, "v": 1.5}])
        self.assertIsNone(p["dados"][0]["v"])
        self.assertNotEqual(p["dados"][0]["v"], 0)
        json.dumps(p, allow_nan=False)


class Autorizacao(unittest.TestCase):
    def test_livre(self):
        self.assertEqual(comum.conferir_autorizacao(["L1"], AUT), "livre")

    def test_recusa_conferencia_e_bloqueada(self):
        for a in ("Q1", "B1"):
            with self.assertRaises(comum.ErroAutorizacao):
                comum.conferir_autorizacao(["L1", a], AUT)

    def test_recusa_desconhecido_e_vazio(self):
        with self.assertRaises(comum.ErroAutorizacao):
            comum.conferir_autorizacao(["ZZ9"], AUT)
        with self.assertRaises(comum.ErroAutorizacao):
            comum.conferir_autorizacao([], AUT)

    def test_excecao_registrada(self):
        self.assertTrue(comum.conferir_autorizacao(["E1"], AUT).startswith("exceção por premissa"))

    def test_montar_recusa(self):
        with self.assertRaises(comum.ErroAutorizacao):
            produto([{"ano": 2020}], ativos=("B1",))


class Gravacao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        raiz = Path(self.tmp.name)
        (raiz / "config").mkdir()
        with open(raiz / "config" / "paineis_ativos.csv", "w", encoding="utf-8") as f:
            f.write("id,autorizacao,excecao_premissa,arquivo_origem\nL1,livre,,a.csv\n")
        self.entrada = raiz / "entrada.csv"
        self.entrada.write_text("ano,n\n2020,10\n", encoding="utf-8")
        self.pasta = raiz / "data" / "paineis"
        self.patches = [mock.patch.object(comum, "RAIZ", raiz),
                        mock.patch.object(comum, "ARQ_ATIVOS", raiz / "config" / "paineis_ativos.csv")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def gravar(self, forcar=False, valor=10):
        p = produto([{"ano": 2020, "n": valor}])
        return comum.gravar_produto(p, transformacao="t", entradas=[self.entrada], pasta=self.pasta, forcar=forcar)

    def test_idempotencia(self):
        self.assertTrue(self.gravar())
        arq, meta = comum.caminhos("teste_x_2020-2021_anual", self.pasta)
        antes = (arq.read_bytes(), meta.read_bytes())
        self.assertFalse(self.gravar(), "segunda gravação igual não deve regravar")
        self.assertEqual(antes, (arq.read_bytes(), meta.read_bytes()))
        self.assertTrue(comum.produto_em_dia("teste_x_2020-2021_anual", [self.entrada], self.pasta))

    def test_forcar_e_mudanca(self):
        self.gravar()
        self.assertTrue(self.gravar(forcar=True))
        self.assertTrue(self.gravar(valor=11), "conteúdo novo regrava")

    def test_entrada_mudou(self):
        self.gravar()
        self.entrada.write_text("ano,n\n2020,12\n", encoding="utf-8")
        self.assertFalse(comum.produto_em_dia("teste_x_2020-2021_anual", [self.entrada], self.pasta))
        self.assertTrue(self.gravar(), "metadado com sha novo regrava")

    def test_metadado(self):
        self.gravar()
        _, meta = comum.caminhos("teste_x_2020-2021_anual", self.pasta)
        m = json.loads(meta.read_text(encoding="utf-8"))
        for c in comum.CAMPOS_OBRIGATORIOS_METADADO:
            self.assertIn(c, m)
        self.assertEqual(m["entradas"][0]["arquivo"], "entrada.csv", "caminho relativo, sem pasta local")


if __name__ == "__main__":
    unittest.main()
