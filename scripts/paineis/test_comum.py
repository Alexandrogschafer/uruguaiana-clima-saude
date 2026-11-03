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
    "E1": {"id": "E1", "autorizacao": "depende de conferência no QGIS", "excecao_premissa": "P6: aviso", "arquivo_origem": "c.csv",
           "selo": "em_confirmacao"},
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


class Selo(unittest.TestCase):
    def test_selo_mais_forte_que_o_ativo_recusa(self):
        with self.assertRaises(comum.ErroProduto):
            produto([{"ano": 2020}], ativos=("E1",), selo="conferido")

    def test_selo_igual_ou_mais_fraco_passa(self):
        self.assertEqual(produto([{"ano": 2020}], ativos=("E1",), selo="em_confirmacao")["selo"], "em_confirmacao")
        self.assertEqual(produto([{"ano": 2020}], ativos=("E1",), selo="pendente")["selo"], "pendente")


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


class Substitui(unittest.TestCase):
    """Rodada 120: id estável com o id antigo no campo "substitui", também no manifesto."""

    def test_produto_renomeado_leva_substitui(self):
        with mock.patch.dict(comum.IDS_SUBSTITUIDOS, {"teste_x_serie_anual": "teste_x_2020-2021_anual"}):
            p = produto([{"n": 10}], id="teste_x_serie_anual")
        self.assertEqual(p["substitui"], "teste_x_2020-2021_anual")
        self.assertEqual(list(p)[:3], ["esquema", "id", "substitui"])

    def test_produto_sem_troca_nao_leva_substitui(self):
        self.assertNotIn("substitui", produto([{"n": 10}], id="teste_y_atual_classe"))

    def test_manifesto_leva_substitui_e_deixa_orfao_de_fora(self):
        import manifesto
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(comum.IDS_SUBSTITUIDOS, {"teste_x_serie_anual": "teste_x_2020-2021_anual"}), \
                mock.patch.object(comum, "conferir_autorizacao", return_value="livre"):
            pasta = Path(tmp)
            novo = produto([{"n": 10}], id="teste_x_serie_anual")
            antigo = produto([{"n": 10}], id="teste_x_2020-2021_anual")
            for prod in (novo, antigo):
                (pasta / f"{prod['id']}.json").write_text(json.dumps(prod), encoding="utf-8")
            with mock.patch.object(comum, "ARQ_ATIVOS", pasta / "ativos.csv"), mock.patch.object(comum, "ler_autorizacoes", return_value={}):
                (pasta / "ativos.csv").write_text("id\n", encoding="utf-8")
                manifesto.gerar("0000000", pasta, declarados=["teste_x_serie_anual"])
            m = json.loads((pasta / "manifesto.json").read_text(encoding="utf-8"))
            self.assertEqual([(i["id"], i.get("substitui")) for i in m["produtos"]], [("teste_x_serie_anual", "teste_x_2020-2021_anual")])
            self.assertEqual([o.name for o in manifesto.orfaos(pasta, ["teste_x_serie_anual"])], ["teste_x_2020-2021_anual.json"])

    def test_tabela_de_ids_coerente(self):
        """Nenhum id novo com ano, nenhum id antigo repetido, nenhuma edição fixa renomeada."""
        import re
        antigos = list(comum.IDS_SUBSTITUIDOS.values())
        self.assertEqual(len(antigos), len(set(antigos)))
        for novo in comum.IDS_SUBSTITUIDOS:
            self.assertIsNone(re.search(r"_(19|20)\d{2}", novo), novo)
            self.assertRegex(novo, r"_(serie|atual|censos)(_|$)")
        self.assertFalse(set(comum.IDS_SUBSTITUIDOS) & comum.EDICOES_FIXAS)


class BrutoMaisRecente(unittest.TestCase):
    """Rodada 120: com mais de um bruto que casa com o padrão, vale o de data_acesso mais recente."""

    def gravar(self, pasta, nome, **meta):
        (pasta / nome).write_text("x\n", encoding="utf-8")
        (pasta / nome).with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")

    def test_um_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            (pasta / "a_2010-2025_m.csv").write_text("x\n", encoding="utf-8")  # sem metadado: com um só, não precisa
            self.assertEqual(comum.bruto_mais_recente(pasta, "a_*_m.csv").name, "a_2010-2025_m.csv")

    def test_data_acesso_mais_recente_vence_a_ordem_alfabetica(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            self.gravar(pasta, "a_2020-202606_m.csv", data_acesso="2026-07-30")
            self.gravar(pasta, "a_2020-202512_m.csv", data_acesso="2026-11-03T10:00:00+00:00")
            self.assertEqual(comum.bruto_mais_recente(pasta, "a_*_m.csv").name, "a_2020-202512_m.csv")

    def test_sem_data_acesso_usa_data_processamento(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            self.gravar(pasta, "a_1_m.csv", data_processamento="2026-08-09T21:12:21+00:00")
            self.gravar(pasta, "a_2_m.csv", data_processamento="2026-08-01T00:00:00+00:00")
            self.assertEqual(comum.bruto_mais_recente(pasta, "a_*_m.csv").name, "a_1_m.csv")

    def test_sem_data_nenhuma_falha_e_nada_some(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            self.gravar(pasta, "a_1_m.csv")
            self.gravar(pasta, "a_2_m.csv", data_acesso="2026-01-01")
            with self.assertRaises(ValueError):
                comum.bruto_mais_recente(pasta, "a_*_m.csv")
            self.assertEqual(len(list(pasta.glob("a_*_m.csv"))), 2)  # o bruto antigo nunca é apagado
            with self.assertRaises(ValueError):
                comum.bruto_mais_recente(pasta, "b_*_m.csv")

    def test_metadado_do_json_e_o_json_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            for nome, quando in (("p_2010-2025_x.json", "2026-01-01"), ("p_2010-2026_x.json", "2026-11-01")):
                (pasta / nome).write_text("{}", encoding="utf-8")
                (pasta / (nome + ".json")).write_text(json.dumps({"data_processamento": quando}), encoding="utf-8")
            self.assertEqual(comum.bruto_mais_recente(pasta, "p_*_x.json").name, "p_2010-2026_x.json")


if __name__ == "__main__":
    unittest.main()
