"""
Testes da trava do evento em curso e da última leitura (dados_rio.py), com leituras simuladas:
(i) rio abaixo de 800 cm e leitura recente; (ii) acima de 800 cm a partir de 01/10/2026;
(iii) leitura com mais de 3 dias. Com o evento começando em outubro de 2026, a série diária termina em
30/09/2026 (MES_DO_EVENTO_COM_CORTE); o produto publicado não tem nenhum valor de outubro. Também confere que as máximas anuais excluem o ano do evento e que ano
sem valor fica null (nunca 0).

Rodada 118: a trava vale só para os produtos consolidados. A telemetria recente (rio_ana-<estacao>_telemetria-
recente_diario) é publicada sem corte, com "provisorio": true: aqui se confere que ela inclui outubro e novembro
de 2026, que o consolidado continua terminando em 30/09/2026, que nenhuma máxima anual muda, que o rótulo
provisório está no produto e que o JSON tem menos de 100 kB.

Uso: python scripts/paineis/test_rio.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dados_rio as R  # noqa: E402

CAMPOS_PROIBIDOS = ("pico", "duracao", "posicao")


def telemetria(niveis_por_dia: dict[str, float]) -> pd.DataFrame:
    """Quatro leituras de 15 min por dia com o nível dado (vazão proporcional, só para ter número)."""
    linhas = []
    for dia, nivel in niveis_por_dia.items():
        for h in ("00:00", "06:00", "12:00", "18:00"):
            linhas.append({"codigo_estacao": R.ESTACAO, "data_hora": f"{dia} {h}:00", "nivel_cm": nivel, "vazao_m3s": nivel * 10.0, "chuva_mm": 0.0})
    t = pd.DataFrame(linhas)
    t["dia"] = t["data_hora"].str[:10]
    return t


def dias(inicio: str, fim: str, nivel) -> dict[str, float]:
    return {d.strftime("%Y-%m-%d"): (nivel(d) if callable(nivel) else nivel) for d in pd.date_range(inicio, fim)}


class Trava(unittest.TestCase):
    def test_i_abaixo_do_limiar_leitura_recente(self):
        t = telemetria(dias("2025-09-01", "2026-10-20", 500.0))
        ev = R.evento(t)
        self.assertFalse(ev["em_curso"])
        linha, avisos = R.ultima_leitura(t, ev, date(2026, 10, 21))
        self.assertEqual(linha["estado"], "normal")
        self.assertEqual((linha["nivel_cm"], linha["vazao_m3s"], linha["idade_dias"]), (500.0, 5000.0, 1))
        self.assertEqual(linha["data_hora"], "2026-10-20 18:00:00")
        textos = [a["texto"] for a in avisos]
        self.assertIn("não é dado em tempo real; atualizado em 20/10", textos)
        self.assertNotIn("leitura antiga", textos)
        d = R.diario(t, None)
        self.assertEqual((len(d), d[-1]["dia"]), (365, "2026-10-20"))

    def test_ii_acima_do_limiar_desde_outubro(self):
        # sobe a 850 cm em 04/10/2026 e segue acima; antes disso, 500 cm
        t = telemetria(dias("2025-09-01", "2026-10-18", lambda d: 850.0 if d >= pd.Timestamp("2026-10-04") else 500.0))
        ev = R.evento(t)
        self.assertEqual(ev, {"em_curso": True, "inicio": "2026-10-04"})
        linha, avisos = R.ultima_leitura(t, ev, date(2026, 10, 19))
        self.assertEqual(linha["estado"], "evento_em_curso")
        self.assertTrue(all(linha[k] is None for k in ("data_hora", "nivel_cm", "vazao_m3s", "idade_dias")))
        self.assertEqual(avisos[0], {"tipo": "evento_em_curso", "texto": R.AVISO_PREPARACAO})
        # evento que começa em outubro de 2026: a série termina em 30/09/2026, sem nenhum dia de outubro
        self.assertEqual(R.fim_da_serie(ev), "2026-10-01")
        d = R.diario(t, R.fim_da_serie(ev))
        self.assertEqual(d[-1]["dia"], "2026-09-30")
        self.assertFalse(any(r["dia"] >= "2026-10-01" for r in d))
        self.assertTrue(all(r["nivel_medio_cm"] <= R.LIMIAR_CM for r in d))
        self.assertEqual(R.aviso_corte(R.fim_da_serie(ev)), "série até 30/09/2026; " + R.AVISO_PREPARACAO)
        texto = json.dumps([linha, avisos, d])
        self.assertFalse(any(c in texto for c in CAMPOS_PROIBIDOS))

    def test_ii_ultima_leitura_acima_sem_outubro(self):
        # só a última leitura acima do limiar (antes da janela de busca): também trava
        niv = dias("2025-08-01", "2026-09-20", 500.0)
        niv["2026-09-20"] = 820.0
        t = telemetria(niv)
        ev = R.evento(t)
        self.assertTrue(ev["em_curso"])
        self.assertEqual(ev["inicio"], "2026-09-20")
        # fora do mês do corte vale a regra geral: termina no dia anterior ao início do evento
        self.assertEqual(R.fim_da_serie(ev), "2026-09-20")
        self.assertEqual(R.diario(t, R.fim_da_serie(ev))[-1]["dia"], "2026-09-19")

    def test_ii_evento_que_comecou_antes_da_busca(self):
        # acima de 800 cm desde 28/09 sem interrupção: o evento começa no primeiro dia da sequência
        t = telemetria(dias("2025-09-01", "2026-10-10", lambda d: 900.0 if d >= pd.Timestamp("2026-09-28") else 500.0))
        self.assertEqual(R.evento(t), {"em_curso": True, "inicio": "2026-09-28"})

    def test_iii_leitura_com_mais_de_3_dias(self):
        t = telemetria(dias("2025-09-01", "2026-10-10", 500.0))
        ev = R.evento(t)
        linha, avisos = R.ultima_leitura(t, ev, date(2026, 10, 14))
        self.assertEqual(linha["idade_dias"], 4)
        self.assertIn({"tipo": "parcial", "texto": "leitura antiga"}, avisos)
        _, avisos3 = R.ultima_leitura(t, ev, date(2026, 10, 13))
        self.assertNotIn({"tipo": "parcial", "texto": "leitura antiga"}, avisos3)


class ProdutoPublicado(unittest.TestCase):
    def test_json_do_rio_sem_valor_de_outubro_de_2026(self):
        """Os produtos publicados não têm nenhum valor de 01/10/2026 em diante enquanto o evento estiver em curso."""
        pasta = R.RAIZ / "data" / "paineis"
        d = json.loads((pasta / f"{R.ids()['dia']}.json").read_text(encoding="utf-8"))
        if not d["evento_em_curso"]:
            self.skipTest("sem evento em curso nos dados")
        self.assertEqual(d["dados"][-1]["dia"], "2026-09-30")
        self.assertEqual(d["dados_ate"], "2026-09-30")
        self.assertFalse(any(r["dia"] >= "2026-10-01" for r in d["dados"]))
        self.assertIn("série até 30/09/2026; " + R.AVISO_PREPARACAO, [a["texto"] for a in d["avisos"]])
        u = json.loads((pasta / f"{R.ids()['ult']}.json").read_text(encoding="utf-8"))
        self.assertTrue(all(v is None for k, v in u["dados"][0].items() if k != "estado"))
        m = json.loads((pasta / f"{R.ids()['max']}.json").read_text(encoding="utf-8"))
        self.assertLess(m["dados"][-1]["ano"], 2026)


class EscolhaDaSerie(unittest.TestCase):
    def test_vale_a_coleta_mais_recente_da_serie_inteira(self):
        """Duas coletas da série inteira em arquivos próprios: o gerador usa a de data_acesso mais recente."""
        with tempfile.TemporaryDirectory() as tmp:
            antiga = Path(tmp) / f"nivel-rio_ana-serie-historica-{R.ESTACAO}_1940-2026_diario.csv"
            nova = Path(tmp) / f"nivel-rio_ana-serie-historica-{R.ESTACAO}_1940-01-01-a-2026-11-02_diario.csv"
            for arq, quando in ((antiga, "2026-10-06T19:33:55+00:00"), (nova, "2026-11-02T16:41:34+00:00")):
                arq.write_text("x\n", encoding="utf-8")
                arq.with_suffix(".json").write_text(json.dumps({"data_acesso": quando}), encoding="utf-8")
            # a ordem alfabética poria a antiga por último; a escolha é pela data da coleta
            self.assertEqual(sorted([antiga, nova])[-1], antiga)
            self.assertEqual(R.serie_mais_recente([antiga, nova]), nova)
            self.assertEqual(R.serie_mais_recente([nova, antiga]), nova)


class Maximas(unittest.TestCase):
    def test_ano_do_evento_fora_e_lacuna_null(self):
        linhas = []
        for d in pd.date_range("2023-01-01", "2026-03-31"):
            if d.year == 2024:
                continue  # ano inteiro sem valor
            linhas.append({"codigo_estacao": R.ESTACAO, "data": d.strftime("%Y-%m-%d"), "media_diaria": 1, "cota_cm": 300.0 + d.dayofyear})
            linhas.append({"codigo_estacao": R.ESTACAO, "data": d.strftime("%Y-%m-%d"), "media_diaria": 0, "cota_cm": 310.0 + d.dayofyear})
        # a série termina em 31/12/2026 (ano completo); com evento em 2026, as máximas param em 2025
        linhas.append({"codigo_estacao": R.ESTACAO, "data": "2026-12-31", "media_diaria": 1, "cota_cm": None})
        with tempfile.TemporaryDirectory() as tmp:
            arq = Path(tmp) / "serie.csv"
            pd.DataFrame(linhas).to_csv(arq, index=False)
            mx, ultimo = R.maximas(arq, 2026)
            mx_sem, ultimo_sem = R.maximas(arq, None)
        self.assertEqual(ultimo, 2025)
        self.assertEqual(ultimo_sem, 2026)
        por = {r["ano"]: r for r in mx}
        self.assertEqual(sorted(por), [2023, 2024, 2025])
        self.assertIsNone(por[2024]["maximo_cm"])
        self.assertTrue(por[2024]["lacuna"])
        self.assertEqual(por[2023]["maximo_cm"], 310.0 + 365)
        self.assertEqual(por[2023]["origem"], "leitura das 7h ou 17h")
        self.assertFalse(por[2025]["lacuna"])


class TelemetriaRecente(unittest.TestCase):
    """Produto de telemetria: sem a trava, provisório, com a máxima do período só como leitura."""

    def test_sem_corte_no_evento(self):
        # mesmo cenário do evento de outubro (850 cm desde 04/10/2026): o consolidado corta, a telemetria não
        t = telemetria(dias("2025-09-01", "2026-11-02", lambda d: 850.0 if pd.Timestamp("2026-10-04") <= d <= pd.Timestamp("2026-10-20") else 500.0))
        t.loc[t["data_hora"] == "2026-10-16 12:00:00", "nivel_cm"] = 1004.0
        ev = R.evento(t)
        self.assertTrue(ev["em_curso"])
        dd = R.diario(t, R.fim_da_serie(ev))
        self.assertEqual(dd[-1]["dia"], "2026-09-30")
        tel = R.telemetria_recente(t, dd[0]["dia"])
        self.assertEqual((tel[0]["dia"], tel[-1]["dia"]), ("2025-10-01", "2026-11-02"))
        self.assertTrue(any(r["dia"].startswith("2026-10") for r in tel) and any(r["dia"].startswith("2026-11") for r in tel))
        dia16 = next(r for r in tel if r["dia"] == "2026-10-16")
        self.assertEqual((dia16["nivel_max_cm"], dia16["nivel_ultimo_cm"], dia16["hora_ultima"], dia16["n_leituras"]), (1004.0, 850.0, "18:00", 4))
        mx = R.maxima_periodo(t, dd[0]["dia"])
        self.assertEqual((mx["nivel_cm"], mx["nivel_data_hora"]), (1004.0, "2026-10-16 12:00"))
        self.assertEqual(mx["vazao_m3s"], 8500.0)  # vazão simulada não acompanha o ajuste do nível às 12h
        texto = json.dumps([tel, mx])
        self.assertFalse(any(c in texto for c in CAMPOS_PROIBIDOS + ("retorno", "recorr")))

    def test_maximas_iguais_com_ou_sem_telemetria(self):
        """A máxima anual só lê a série histórica: a telemetria nova não muda nenhuma linha."""
        mx_a, _ = R.maximas(R.entradas()["serie"], 2026)
        pub = json.loads((R.RAIZ / "data" / "paineis" / f"{R.ids()['max']}.json").read_text(encoding="utf-8"))
        self.assertEqual(pub["dados"], json.loads(json.dumps(mx_a)))


class TelemetriaPublicada(unittest.TestCase):
    pasta = R.RAIZ / "data" / "paineis"

    def ler(self, chave):
        return json.loads((self.pasta / f"{R.ids()[chave]}.json").read_text(encoding="utf-8"))

    def test_telemetria_com_outubro_e_novembro_e_rotulo(self):
        d = self.ler("tel")
        self.assertIs(d["provisorio"], True)
        self.assertEqual(d["fonte_tipo"], "ANA telemetria")
        self.assertIn({"tipo": "preliminar", "texto": R.AVISO_PROVISORIO}, d["avisos"])
        self.assertEqual(R.AVISO_PROVISORIO, "dados de telemetria, não consolidados")
        self.assertRegex(d["ultima_leitura"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(d["dados"][-1]["dia"], d["ultima_leitura"][:10])
        self.assertEqual(d["dados"][0]["dia"], "2025-10-01")
        dias_pub = [r["dia"] for r in d["dados"]]
        self.assertTrue(any(x.startswith("2026-10") for x in dias_pub))
        if d["dados_ate"] >= "2026-11-01":
            self.assertTrue(any(x.startswith("2026-11") for x in dias_pub))
        mx = d["maxima_periodo"]
        self.assertEqual(mx["nivel_cm"], max(r["nivel_max_cm"] for r in d["dados"] if r["nivel_max_cm"] is not None))
        chaves = {c["nome"] for c in d["colunas"]} | set(d) | set(mx)
        self.assertFalse([k for k in chaves if any(t in k for t in CAMPOS_PROIBIDOS + ("retorno", "recorr"))])
        meta = json.loads((self.pasta / f"{R.ids()['tel']}.json.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["script"], R.SCRIPT)

    def test_consolidado_continua_cortado(self):
        d = self.ler("dia")
        if not d["evento_em_curso"]:
            self.skipTest("sem evento em curso nos dados")
        self.assertEqual(d["dados_ate"], "2026-09-30")
        self.assertFalse(any(r["dia"] >= "2026-10-01" for r in d["dados"]))
        self.assertNotIn("clima", d["telas"])  # a tela de clima lê a telemetria

    def test_nenhuma_maxima_anual_muda(self):
        """Máximas anuais publicadas iguais às do último commit (se houver git) e sem 2026."""
        m = self.ler("max")
        self.assertLess(m["dados"][-1]["ano"], 2026)
        rel = f"data/paineis/{R.ids()['max']}.json"
        try:
            antigo = subprocess.run(["git", "-C", str(R.RAIZ), "show", f"HEAD:{rel}"], capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("sem git para comparar")
        self.assertEqual(json.loads(antigo)["dados"], m["dados"])

    def test_tamanho_abaixo_de_100_kb(self):
        tam = (self.pasta / f"{R.ids()['tel']}.json").stat().st_size
        self.assertLess(tam, 100 * 1024)
        # teto da janela: com 450 dias, ~200 bytes por dia, ainda abaixo do limite
        self.assertLess(R.DIAS_MAX_TELEMETRIA * 205 + 6000, 100 * 1024)


if __name__ == "__main__":
    unittest.main(verbosity=1)
