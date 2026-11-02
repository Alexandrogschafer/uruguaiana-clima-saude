"""Testes sem rede de precipitacao_merge.py: escolha de fonte, descoberta do último ano,
retomada e nome do arquivo final. O acesso à rede fica bloqueado e os arquivos vão para uma
pasta temporária.

Uso:
    python scripts/download/test_precipitacao_merge.py
"""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import precipitacao_merge as pm  # noqa: E402

ANO_RECENTE = datetime.now(timezone.utc).year - 1
STATS = {"mean": 10.0, "min": 1.0, "max": 20.0, "count": 118}
STATS_MERGE = {**STATS, "n_dias_com_dado": 30, "n_dias_no_mes": 30}


def respostas_head(status_por_ano):
    """Simula o HEAD: cada ano devolve um status, ou levanta a exceção informada."""
    urls = []

    def head(url, **_):
        urls.append(url)
        ano = next(a for a in status_por_ano if str(a) in url)
        resultado = status_por_ano[ano]
        if isinstance(resultado, Exception):
            raise resultado
        return mock.Mock(status_code=resultado)

    return head, urls


class SemRede(unittest.TestCase):
    def setUp(self):
        for alvo, valor in (("socket.socket.connect", mock.Mock(side_effect=AssertionError("tentou usar a rede"))),
                            ("time.sleep", mock.Mock())):
            p = mock.patch(alvo, valor)
            p.start()
            self.addCleanup(p.stop)


class Descoberta(SemRede):
    def descobrir(self, fonte, status):
        head, urls = respostas_head(status)
        with mock.patch.object(pm.requests, "head", head):
            return pm.descobrir_ultimo_ano_completo(fonte, 2010), urls

    def test_merge_nao_consulta_chirps(self):
        ano, urls = self.descobrir("merge", {ANO_RECENTE: 200})
        self.assertEqual(ano, ANO_RECENTE)
        self.assertTrue(urls and all("cptec.inpe.br" in u for u in urls))
        self.assertIn("1231.grib2", urls[0])

    def test_chirps_e_auto_consultam_chirps(self):
        for fonte in ("chirps", "auto"):
            _, urls = self.descobrir(fonte, {ANO_RECENTE: 200})
            self.assertTrue(all("chc.ucsb.edu" in u for u in urls))

    def test_404_recua_um_ano(self):
        ano, _ = self.descobrir("merge", {ANO_RECENTE: 404, ANO_RECENTE - 1: 200})
        self.assertEqual(ano, ANO_RECENTE - 1)

    def test_403_do_chirps_pede_ano_fim(self):
        with self.assertRaisesRegex(pm.DescobertaAnoFimError, r"HTTP 403.*--ano-fim"):
            self.descobrir("chirps", {ANO_RECENTE: 403})

    def test_403_no_auto_nao_encurta_a_serie(self):
        with self.assertRaises(pm.DescobertaAnoFimError):
            self.descobrir("auto", {ANO_RECENTE: 403, ANO_RECENTE - 1: 200})

    def test_500_e_erro_de_rede_pedem_ano_fim(self):
        for falha in (500, requests.ConnectionError("sem conexão")):
            with self.assertRaisesRegex(pm.DescobertaAnoFimError, "--ano-fim"):
                self.descobrir("merge", {ANO_RECENTE: falha})

    def test_403_seguido_de_200_na_nova_tentativa(self):
        codigos = iter([403, 200])
        with mock.patch.object(pm.requests, "head", lambda *_, **__: mock.Mock(status_code=next(codigos))):
            self.assertEqual(pm.descobrir_ultimo_ano_completo("chirps", 2010), ANO_RECENTE)

    def test_nenhum_ano_encontrado(self):
        with mock.patch.object(pm.requests, "head", lambda *_, **__: mock.Mock(status_code=404)):
            with self.assertRaisesRegex(pm.DescobertaAnoFimError, "--ano-fim"):
                pm.descobrir_ultimo_ano_completo("merge", ANO_RECENTE - 1)


class EscolhaDeFonte(SemRede):
    def mes(self, fonte, estado, chirps=None, merge=None):
        chirps = chirps or mock.Mock(return_value=dict(STATS))
        merge = merge or mock.Mock(return_value=dict(STATS_MERGE))
        with mock.patch.object(pm, "extrair_precipitacao_mes_chirps", chirps), mock.patch.object(pm, "extrair_precipitacao_mes_merge", merge):
            return pm.obter_stats_do_mes(2020, 1, fonte, estado, None, {}), chirps, merge

    def test_merge_nao_chama_chirps(self):
        (_, fonte, gravar), chirps, _ = self.mes("merge", {"usando_merge": True, "falhas_chirps": 0})
        self.assertEqual((fonte, gravar), (pm.ROTULO_MERGE, True))
        chirps.assert_not_called()

    def test_chirps_nao_chama_merge_e_para_na_terceira_falha(self):
        falha = mock.Mock(side_effect=RuntimeError("HTTP 403"))
        estado = {"usando_merge": False, "falhas_chirps": 0}
        for _ in range(2):
            (stats, _, gravar), _, merge = self.mes("chirps", estado, chirps=falha)
            self.assertEqual((stats, gravar), (None, False))
            merge.assert_not_called()
        with self.assertRaises(pm.ChirpsIndisponivelError):
            self.mes("chirps", estado, chirps=falha)

    def test_auto_troca_na_terceira_falha(self):
        falha = mock.Mock(side_effect=RuntimeError("HTTP 403"))
        estado = {"usando_merge": False, "falhas_chirps": 0}
        for _ in range(2):
            (stats, _, gravar), _, _ = self.mes("auto", estado, chirps=falha)
            self.assertEqual((stats, gravar), (None, True))
        (_, fonte, _), _, _ = self.mes("auto", estado, chirps=falha)
        self.assertEqual(fonte, pm.ROTULO_MERGE)
        self.assertTrue(estado["usando_merge"])

    def test_auto_sucesso_zera_falhas(self):
        estado = {"usando_merge": False, "falhas_chirps": 2}
        (_, fonte, _), _, _ = self.mes("auto", estado)
        self.assertEqual((fonte, estado["falhas_chirps"]), ("chirps", 0))


class Main(SemRede):
    """Executa main() numa pasta temporária, com a extração e o IBGE simulados."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        (self.raiz / "data/raw").mkdir(parents=True)
        self.area = self.raiz / "area.geojson"
        self.area.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        self.merge = mock.Mock(side_effect=lambda *_: dict(STATS_MERGE))
        self.chirps = mock.Mock(side_effect=lambda *_: dict(STATS))
        area = mock.Mock(to_crs=mock.Mock(return_value=None))
        for nome, valor in (("RAIZ", self.raiz), ("obter_uf_sigla", lambda _: ("Teste", "RS")),
                            ("extrair_precipitacao_mes_merge", self.merge), ("extrair_precipitacao_mes_chirps", self.chirps)):
            p = mock.patch.object(pm, nome, valor)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(pm.gpd, "read_file", lambda _: area)
        p.start()
        self.addCleanup(p.stop)

    def rodar(self, *args):
        with mock.patch.object(sys, "argv", ["x", "--area-bacias", str(self.area), "--ano-inicio", "2020", *args]):
            pm.main()

    def csvs(self):
        return sorted(p.name for p in (self.raiz / "data/raw").glob("*.csv"))

    def test_padrao_e_merge_e_nomeia_pela_fonte(self):
        self.rodar("--ano-fim", "2020")
        self.assertEqual(self.csvs(), ["precipitacao_merge-inpe_2020-2020_mensal.csv"])
        self.assertEqual(self.merge.call_count, 12)
        self.chirps.assert_not_called()

    def test_segunda_execucao_nao_muda_nada(self):
        self.rodar("--ano-fim", "2020")
        csv = self.raiz / "data/raw/precipitacao_merge-inpe_2020-2020_mensal.csv"
        antes = (csv.read_bytes(), csv.with_suffix(".json").read_bytes())
        self.merge.reset_mock()
        self.rodar("--ano-fim", "2020")
        self.merge.assert_not_called()
        self.assertEqual(self.csvs(), [csv.name])
        self.assertEqual((csv.read_bytes(), csv.with_suffix(".json").read_bytes()), antes)

    def test_retomada_conta_o_arquivo_inteiro(self):
        csv = self.raiz / "data/raw/precipitacao_merge-inpe_2020-2020_mensal.csv"
        linhas = [{"ano": 2020, "mes": m, "codigo_ibge": "1", "nome_municipio": "T", "fonte_dado": pm.ROTULO_MERGE,
                   "precipitacao_media_mm": 1, "precipitacao_min_mm": 1, "precipitacao_max_mm": 1, "n_pixels": 1} for m in range(1, 12)]
        pd.DataFrame(linhas).to_csv(csv, index=False)
        self.rodar("--ano-fim", "2020")
        self.assertEqual(self.merge.call_count, 1)
        self.assertEqual(self.csvs(), [csv.name])
        self.assertEqual(len(pd.read_csv(csv)), 12)

    def test_forcar_refaz(self):
        self.rodar("--ano-fim", "2020")
        self.merge.reset_mock()
        self.rodar("--ano-fim", "2020", "--forcar")
        self.assertEqual(self.merge.call_count, 12)

    def test_auto_com_maioria_chirps_nomeia_chirps(self):
        self.rodar("--ano-fim", "2020", "--fonte", "auto")
        self.assertEqual(self.csvs(), ["precipitacao_chirps_2020-2020_mensal.csv"])
        self.merge.assert_not_called()

    def test_fonte_chirps_para_com_progresso_salvo(self):
        self.chirps.side_effect = [dict(STATS)] + [RuntimeError("HTTP 403")] * 3
        with self.assertRaises(SystemExit) as saida:
            self.rodar("--ano-fim", "2020", "--fonte", "chirps")
        self.assertEqual(saida.exception.code, 1)
        csv = self.raiz / "data/raw/precipitacao_pendente_2020-2020_mensal.csv"
        self.assertEqual(len(pd.read_csv(csv)), 1)
        self.merge.assert_not_called()

    def test_descoberta_com_403_pede_ano_fim(self):
        head, _ = respostas_head({ANO_RECENTE: 403})
        with mock.patch.object(pm.requests, "head", head), mock.patch("sys.stderr") as erro:
            with self.assertRaises(SystemExit) as saida:
                self.rodar("--fonte", "chirps")
        self.assertEqual(saida.exception.code, 2)
        self.assertIn("--ano-fim", "".join(c.args[0] for c in erro.write.call_args_list))


if __name__ == "__main__":
    unittest.main(verbosity=2)
