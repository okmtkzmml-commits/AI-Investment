"""オフラインテスト（ネットに出ず、Yahoo の応答をダミーで置き換える）。
実行: python -m unittest analyst/test_analyst_data.py
"""
import contextlib
import io
import json
import math
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyst_data as ad  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def fake_chart(trend: float, vol_boost: float, n: int = 250):
    t0 = int(time.time()) - n * 86400
    closes = [1000 + trend * i + 30 * math.sin(i / 3) for i in range(n)]
    vols = [100000.0] * (n - 5) + [100000.0 * vol_boost] * 5
    return {"timestamp": [t0 + i * 86400 for i in range(n)],
            "indicators": {"quote": [{"close": closes, "volume": vols}]}}


class FakeYahoo:
    """コード別に財務・株価を返すダミー。"""
    UNIVERSE = {
        "7974": ("任天堂", "Electronic Gaming & Multimedia"),
        "9746": ("TKC", "Information Technology Services"),
        "6929": ("日本セラミック", "Electronic Components"),
        "7269": ("スズキ", "Auto Manufacturers"),
        "8306": ("三菱UFJ", "Banks - Diversified"),  # 対象外セクター
        "1111": ("赤字テスト", "Software - Application"),
        "3197": ("すかいらーく", "Restaurants"),
    }

    def __init__(self, *a, **k):
        pass

    def quote_summary(self, code, modules):
        if code not in self.UNIVERSE:
            return None
        name, ind = self.UNIVERSE[code]
        r = lambda v: {"raw": v}
        return {
            "price": {"shortName": name},
            "assetProfile": {"sector": "x", "industry": ind},
            "summaryDetail": {"marketCap": r(1e12), "trailingPE": r(15.0), "dividendYield": r(0.025),
                              "payoutRatio": r(0.4), "beta": r(0.8), "averageVolume": r(500000)},
            "defaultKeyStatistics": {"priceToBook": r(2.0)},
            "financialData": {"currentPrice": r(5000), "returnOnEquity": r(0.12), "operatingMargins": r(0.2),
                              "earningsGrowth": r(0.1)},
            "calendarEvents": {"earnings": {"earningsDate": [{"fmt": "2026-11-05"}]}},
        }

    def timeseries(self, code, types):
        loss = code == "1111"
        return {
            "annualTotalRevenue": [{"d": "2025-03-31", "v": 100.0}, {"d": "2026-03-31", "v": 112.0}],
            "annualOperatingIncome": [{"d": "2025-03-31", "v": 10.0}, {"d": "2026-03-31", "v": -1.0 if loss else 12.0}],
            "annualTotalAssets": [{"d": "2026-03-31", "v": 100.0}],
            "annualStockholdersEquity": [{"d": "2026-03-31", "v": 70.0}],
        }

    def chart(self, code, rng="1y", interval="1d", events=""):
        if events == "div":
            return {"events": {"dividends": {"a": {"date": 1700000000, "amount": 50}}}}
        return fake_chart(trend=2.0 if code != "6929" else -2.0, vol_boost=1.5)


class TestJudgement(unittest.TestCase):
    def test_verdict(self):
        self.assertEqual(ad.overall_verdict(["◎", "◎", "◎"]), "買いタイミング良好")
        self.assertEqual(ad.overall_verdict(["◎", "〇", "◎"]), "買いタイミング良好")
        self.assertEqual(ad.overall_verdict(["◎", "〇", "〇"]), "要観察")
        self.assertEqual(ad.overall_verdict(["×", "◎", "◎"]), "要観察")
        self.assertEqual(ad.overall_verdict(["×", "×", "◎"]), "見送り")

    def test_rsi_bounds(self):
        j = lambda rsi: ad.judge_technical(10, 9, 9, rsi, 1.2)["rsi_mark"]
        self.assertEqual([j(29.9), j(30), j(65), j(65.1), j(69.9), j(70)], ["×", "◎", "◎", "〇", "〇", "×"])

    def test_ma_and_volume(self):
        t = ad.judge_technical(10, 11, 9, 50, 1.0)
        self.assertEqual((t["ma"], t["vol"]), ("〇", "〇"))
        t = ad.judge_technical(8, 11, 9, 50, 0.9)
        self.assertEqual((t["ma"], t["vol"]), ("×", "×"))

    def test_last_close_filled_from_meta(self):
        ch = fake_chart(1.0, 1.0)
        ch["indicators"]["quote"][0]["close"][-1] = None
        ch["meta"] = {"regularMarketPrice": 9999.0, "regularMarketTime": ch["timestamp"][-1] + 3600}
        t = ad.technical_from_chart(ch)
        self.assertEqual(t["close"], 9999.0)
        del ch["meta"]
        self.assertNotEqual(ad.technical_from_chart(ch)["close"], 9999.0)

    def test_tech_markdown_hard_rules(self):
        rows = [{"code": "1", "name": "過熱", "verdict": "監視"}, {"code": "2", "name": "高額", "verdict": "最優先"}]
        base = {"close": 100.0, "change_pct": 1.0, "ma25": 90, "ma75": 80, "ma": "◎", "vratio": 1.2, "vol": "◎",
                "last_date": "2026-11-02"}
        tech = {"1": {**base, "rsi": 72.0, "verdict": "要観察"},
                "2": {**base, "close": 20000.0, "rsi": 50.0, "verdict": "買いタイミング良好"}}
        md = ad.build_tech_markdown("20261103", rows, tech)
        self.assertIn("警告：過熱が過熱圏です（RSI：72）", md)
        self.assertIn("注意：高額は最低単元で約200万円", md)

    def test_rsi_all_up(self):
        self.assertEqual(ad.rsi_wilder([float(i) for i in range(30)]), 100.0)


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in (ad.BRIEFING, "00_投資プロフィール"):
            shutil.copytree(REPO / d, self.tmp / d)
        self.orig = ad.Yahoo
        ad.Yahoo = FakeYahoo

    def tearDown(self):
        ad.Yahoo = self.orig
        shutil.rmtree(self.tmp)

    def run_cmd(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = ad.main(["--base", str(self.tmp), "--date", "20261101",
                          "--universe-file", str(self.tmp / "data" / "universe.json"), *args])
        return rc, out.getvalue()

    def test_prepare_and_verify(self):
        master = self.tmp / ad.BRIEFING / ad.MASTER
        before = master.read_bytes()
        rc, out = self.run_cmd("prepare", "--code-from", "1111",
                               "--code-to", "9999", "--sleep", "0")
        self.assertEqual(rc, 0)
        self.assertEqual(master.read_bytes(), before, "マスターを書き換えてはいけない")
        md = Path(out.strip()).read_text(encoding="utf-8")
        self.assertIn("| 7974 | 任天堂 | ゲーム", md)
        self.assertIn("| 3197 | すかいらーく | 外食", md)
        self.assertNotIn("三菱UFJ", md)          # 対象外セクター
        self.assertNotIn("| 1111 | 赤字テスト", md)  # 営業赤字
        self.assertIn("7974 任天堂: 1回連続で提案（20261029）", md)
        self.assertIn("営業赤字 1", md)

        # AI が書いた想定のファイルで verify
        b = self.tmp / ad.BRIEFING
        (b / ad.FUND_FILE).write_text(ad.FUND_HEADER + "\n6929 / 日本セラミック / 15.0 / 12.0 / 20.0 / 12.0 / 良好\n",
                                      encoding="utf-8")
        (b / ad.TECH_FILE).write_text(ad.TECH_HEADER + "\n6929 / 日本セラミック / × / 40 / ◎ / 要観察 / 反発待ち、出来高増。\n",
                                      encoding="utf-8")
        (b / "新規提案_20261101.txt").write_text(
            "銘柄コード：6929\n企業名：日本セラミック\nファンダ評価：1位\nタイミング評価：要観察\n"
            "提案理由：高財務の電子部品株。\n\n" + ad.MASTER_OK + "\n", encoding="utf-8")
        rc, out = self.run_cmd("verify")
        self.assertEqual(rc, 0, out)

        # 判定の矛盾・マスター変更を検出できること
        (b / ad.TECH_FILE).write_text(ad.TECH_HEADER + "\n6929 / 日本セラミック / × / 40 / ◎ / 買いタイミング良好 / a,b\n",
                                      encoding="utf-8")
        master.write_text("changed", encoding="utf-8")
        rc, out = self.run_cmd("verify")
        self.assertEqual(rc, 1)
        self.assertIn("総合判定", out)
        self.assertIn("カンマ", out)
        self.assertIn("変わっています", out)


class TestUniverse(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "universe.json"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def quiet(self, f, *a, **k):
        with contextlib.redirect_stderr(io.StringIO()):
            return f(*a, **k)

    def test_resume_and_reuse(self):
        # 途中経過（7000 まで済み）から再開すると、それ以前のコードは問い合わせない
        partial = self.tmp / "universe.partial.json"
        partial.write_text(json.dumps({"updated": "2026-11-01T00:00:00+09:00", "code_from": 6900, "code_to": 9999,
                                       "next": 7001, "rows": [{"code": "6929", "name": "x", "industry": "Electronic Components", "mcap": 1e12}],
                                       "failed": []}), encoding="utf-8")
        calls = []

        class Y(FakeYahoo):
            def quote_summary(s, code, modules):
                calls.append(code)
                return FakeYahoo.quote_summary(s, code, modules)

        rows = self.quiet(ad.load_universe, Y(), self.path, 7, 6900, 9999)
        self.assertEqual(min(int(c) for c in calls), 7001)
        self.assertEqual([r["code"] for r in rows], ["6929", "7269", "7974", "8306", "9746"])
        self.assertFalse(partial.exists())
        # 保存済みで新しければ問い合わせない
        calls.clear()
        self.quiet(ad.load_universe, Y(), self.path, 7, 6900, 9999)
        self.assertEqual(calls, [])
        # 古ければ作り直す
        d = json.loads(self.path.read_text(encoding="utf-8"))
        d["updated"] = "2026-01-01T00:00:00+09:00"
        self.path.write_text(json.dumps(d), encoding="utf-8")
        self.quiet(ad.load_universe, Y(), self.path, 7, 9700, 9800)
        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()
