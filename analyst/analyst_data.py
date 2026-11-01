#!/usr/bin/env python3
"""アナリストチーム（prompts/analyst_team.md）用のデータ準備・検証スクリプト。

サブコマンド:
  prepare  株価・財務データを集め、AI に渡す入力ファイル（Markdown）を作る
  verify   AI が保存した 3 ファイルの形式と、統合スクリーニング結果.txt が
           変わっていないことを確認する

方針:
  - 数値で決まる作業（定量フィルター・テクニカル判定・監視リストの抽出・
    連続提案日数の数え上げ）はスクリプトで行う。
  - 定性判断（材料の有無・最終 10 銘柄の選定・コメント・提案理由）は AI が行う。
  - 統合スクリーニング結果.txt は読むだけ。書き込みは一切しない。

標準ライブラリだけで動く（Python 3.9 以上）。
データ取得元は Yahoo Finance の非公式 API（tools/README.md の注意を参照）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.cookiejar
import json
import os
import re
import shutil
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

JST = dt.timezone(dt.timedelta(hours=9))

BRIEFING = "01_朝のブリーフィング"
PROFILE = "00_投資プロフィール/私の投資プロフィール_v1.txt"
MASTER = "統合スクリーニング結果.txt"
FUND_FILE = "ファンダスクリーニング結果.txt"
TECH_FILE = "テクニカルスクリーニング結果.txt"
FUND_HEADER = "銘柄コード / 企業名 / PER / ROE(%) / 営業利益率(%) / 売上高成長率(%) / 評価コメント"
TECH_HEADER = "銘柄コード / 企業名 / 移動平均 / RSI / 出来高 / 総合判定 / コメント"
NO_PROPOSAL = "本日の新規提案はありません"
MASTER_OK = "マスター確認：変更なし"

# Yahoo の業種(industry) → プロフィールの対象セクター
# （tools/2_make_candidates.ps1 の5セクターに「外食」を追加）
SECTOR_MAP = {
    "Semiconductors": "半導体・電子部品",
    "Semiconductor Equipment & Materials": "半導体・電子部品",
    "Electronic Components": "半導体・電子部品",
    "Electronic Gaming & Multimedia": "ゲーム",
    "Information Technology Services": "IT",
    "Software - Application": "IT",
    "Software - Infrastructure": "IT",
    "Apparel Retail": "消費財・小売",
    "Department Stores": "消費財・小売",
    "Footwear & Accessories": "消費財・小売",
    "Home Improvement Retail": "消費財・小売",
    "Internet Retail": "消費財・小売",
    "Specialty Retail": "消費財・小売",
    "Discount Stores": "消費財・小売",
    "Grocery Stores": "消費財・小売",
    "Household & Personal Products": "消費財・小売",
    "Packaged Foods": "消費財・小売",
    "Beverages - Brewers": "消費財・小売",
    "Beverages - Non-Alcoholic": "消費財・小売",
    "Confectioners": "消費財・小売",
    "Apparel Manufacturing": "消費財・小売",
    "Furnishings, Fixtures & Appliances": "消費財・小売",
    "Tobacco": "消費財・小売",
    "Consumer Electronics": "消費財・小売",
    "Restaurants": "外食",
    "Auto Manufacturers": "自動車",
    "Auto Parts": "自動車",
}

# 定量フィルター（プロフィールの「推奨スクリーニング基準」を数値にしたもの。
# tools/4_analyze.ps1 と同じ解釈。変える場合はここだけ直す）
CRITERIA = {
    "min_mcap": 50_000_000_000,   # 時価総額 500億円以上
    "min_equity_ratio": 40.0,     # 自己資本比率 40%以上
    "min_turnover_oku": 3.0,      # 1日平均売買代金 3億円以上（「数億円以上」の解釈）
    "min_rev_growth": 5.0,        # 売上高成長率 +5%以上（直近通期の前年比）
    "min_yield": 1.5,             # 配当利回り 1.5%以上
    "yield_exception_growth": 15.0,  # 売上・営業益ともに+15%以上なら利回り基準の例外
    "max_payout": 70.0,           # 配当性向 70%以下
    "per_vs_median": 1.3,         # PER ≤ セクター中央値×1.3
    "pbr_vs_median": 2.0,         # PBR ≤ セクター中央値×2
    "high_beta": 1.5,             # ベータ1.5超は減点
}
# 点数（プロフィールの優先順位: 財務健全性 > 成長性 > 配当 > 割安度）
WEIGHTS = {"eq": 0.4, "rg": 0.3, "yld": 0.2, "pe": 0.1}
HIGH_BETA_PENALTY = 0.1

# テクニカル（prompts/analyst_team.md の基準）
VOL_FLAT_LOW, VOL_FLAT_HIGH = 0.95, 1.05  # 5日/25日 がこの範囲なら「ほぼ同水準」

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- Yahoo client
class Yahoo:
    def __init__(self, sleep: float = 0.25, timeout: int = 20):
        self.sleep = sleep
        self.timeout = timeout
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.op.addheaders = [("User-Agent", UA)]
        self.crumb: str | None = None

    def _get(self, url: str) -> bytes:
        last = None
        for attempt in range(4):
            try:
                with self.op.open(url, timeout=self.timeout) as r:
                    data = r.read()
                time.sleep(self.sleep)
                return data
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (401, 429) or e.code >= 500:
                    if e.code == 401:
                        self.crumb = None
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"取得失敗: {url} ({last})")

    def get_json(self, url: str) -> dict:
        return json.loads(self._get(url).decode("utf-8"))

    def ensure_crumb(self) -> str:
        if self.crumb:
            return self.crumb
        try:
            self.op.open("https://fc.yahoo.com", timeout=self.timeout).read()
        except Exception:
            pass  # 404 でもクッキーは付く
        self.crumb = self._get("https://query1.finance.yahoo.com/v1/test/getcrumb").decode().strip()
        return self.crumb

    def quote_summary(self, code: str, modules: str) -> dict | None:
        crumb = self.ensure_crumb()
        url = (f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{code}.T"
               f"?modules={modules}&crumb={crumb}")
        try:
            r = self.get_json(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        res = (r.get("quoteSummary") or {}).get("result") or []
        return res[0] if res else None

    def timeseries(self, code: str, types: list[str]) -> dict:
        now = int(time.time())
        url = (f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{code}.T"
               f"?type={','.join(types)}&merge=false&period1=1600000000&period2={now + 86400 * 400}")
        r = self.get_json(url)
        out: dict[str, list] = {}
        for res in (r.get("timeseries") or {}).get("result") or []:
            k = (res.get("meta") or {}).get("type", [None])[0]
            vals = res.get(k) if k else None
            if vals:
                out[k] = sorted(
                    [{"d": v.get("asOfDate"), "v": (v.get("reportedValue") or {}).get("raw")}
                     for v in vals if v], key=lambda x: x["d"] or "")
        return out

    def chart(self, code: str, rng: str = "1y", interval: str = "1d", events: str = "") -> dict | None:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.T?range={rng}&interval={interval}"
        if events:
            url += f"&events={events}"
        r = self.get_json(url)
        res = (r.get("chart") or {}).get("result") or []
        return res[0] if res else None


def raw(d: dict | None, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    if isinstance(d, dict):
        return d.get("raw")
    return d


# ---------------------------------------------------------------- step 1: universe
def load_universe(yh: Yahoo, cache: Path, max_age_days: float, start: int, end: int) -> list[dict]:
    path = cache / "universe.json"
    if path.exists():
        age = (time.time() - path.stat().st_mtime) / 86400
        if age <= max_age_days:
            log(f"[universe] キャッシュを使用（{age:.1f}日前）: {path}")
            return json.loads(path.read_text(encoding="utf-8"))
    log(f"[universe] 銘柄コード {start}〜{end} を全探索します（初回・週1回、15分以上かかります）")
    rows = []
    for i, c in enumerate(range(start, end + 1)):
        code = str(c)
        try:
            x = yh.quote_summary(code, "assetProfile,summaryDetail,price")
        except Exception as e:
            log(f"  {code}: {e}")
            continue
        if not x:
            continue
        mcap = raw(x, "summaryDetail", "marketCap") or raw(x, "price", "marketCap")
        if mcap and mcap >= CRITERIA["min_mcap"]:
            rows.append({
                "code": code,
                "name": raw(x, "price", "shortName") or raw(x, "price", "longName") or "",
                "yahoo_sector": raw(x, "assetProfile", "sector") or "",
                "industry": raw(x, "assetProfile", "industry") or "",
                "mcap": mcap,
            })
        if i % 500 == 0:
            log(f"  ...{code}（{len(rows)}件）")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return rows


def make_candidates(universe: list[dict]) -> list[dict]:
    out = []
    for u in universe:
        sec = SECTOR_MAP.get(u.get("industry", ""))
        if sec:
            out.append({**u, "sector": sec})
    return out


# ---------------------------------------------------------------- step 2: fundamentals
TS_TYPES = ["annualStockholdersEquity", "annualTotalAssets", "annualOperatingIncome", "annualTotalRevenue",
            "quarterlyStockholdersEquity", "quarterlyTotalAssets"]


def crawl_fundamentals(yh: Yahoo, cands: list[dict]) -> tuple[list[dict], list[str]]:
    recs, errs = [], []
    log(f"[fundamentals] {len(cands)} 銘柄の財務データを取得します（10分程度）")
    for i, c in enumerate(cands):
        code = c["code"]
        try:
            x = yh.quote_summary(code, "financialData,summaryDetail,defaultKeyStatistics,calendarEvents") or {}
            ts = yh.timeseries(code, TS_TYPES)
        except Exception as e:
            errs.append(f"{code} {c['name']}: {e}")
            continue
        earn = None
        try:
            earn = x["calendarEvents"]["earnings"]["earningsDate"][0]["fmt"]
        except Exception:
            pass
        recs.append({
            **c,
            "pe": raw(x, "summaryDetail", "trailingPE"),
            "fpe": raw(x, "summaryDetail", "forwardPE"),
            "pb": raw(x, "defaultKeyStatistics", "priceToBook"),
            "yld": raw(x, "summaryDetail", "dividendYield"),
            "payout": raw(x, "summaryDetail", "payoutRatio"),
            "beta": raw(x, "summaryDetail", "beta"),
            "avgvol": raw(x, "summaryDetail", "averageVolume"),
            "price": raw(x, "financialData", "currentPrice"),
            "roe": raw(x, "financialData", "returnOnEquity"),
            "opm": raw(x, "financialData", "operatingMargins"),
            "revg_q": raw(x, "financialData", "revenueGrowth"),
            "epsg": raw(x, "financialData", "earningsGrowth"),
            "earn": earn,
            "ts": ts,
        })
        if i % 50 == 0:
            log(f"  ...{i}/{len(cands)}")
    return recs, errs


def _pct(v):
    return None if v is None else v * 100


def derive(r: dict) -> dict:
    """生データから判定に使う指標を作る（tools/4_analyze.ps1 と同じ計算）。"""
    ts = r.get("ts") or {}
    qa, qe = ts.get("quarterlyTotalAssets", []), ts.get("quarterlyStockholdersEquity", [])
    aa, ae = ts.get("annualTotalAssets", []), ts.get("annualStockholdersEquity", [])
    eq = None
    if qa and qe and qa[-1]["d"] == qe[-1]["d"] and qa[-1]["v"] and qe[-1]["v"] is not None:
        eq = qe[-1]["v"] / qa[-1]["v"] * 100
    elif aa and ae and aa[-1]["v"] and ae[-1]["v"] is not None:
        eq = ae[-1]["v"] / aa[-1]["v"] * 100
    rev, op = ts.get("annualTotalRevenue", []), ts.get("annualOperatingIncome", [])
    rg = og = opl = None
    fy_end = rev[-1]["d"] if rev else None
    if len(rev) >= 2 and rev[-2]["v"] and rev[-1]["v"] is not None:
        rg = (rev[-1]["v"] / rev[-2]["v"] - 1) * 100
    if len(op) >= 2 and op[-2]["v"] and op[-2]["v"] > 0 and op[-1]["v"] is not None:
        og = (op[-1]["v"] / op[-2]["v"] - 1) * 100
    if op:
        opl = op[-1]["v"]
    tv = r["avgvol"] * r["price"] / 1e8 if r.get("avgvol") and r.get("price") else None
    return {
        "code": r["code"], "name": r["name"], "sector": r["sector"], "industry": r.get("industry"),
        "mcap_oku": r["mcap"] / 1e8 if r.get("mcap") else None,
        "pe": r.get("pe"), "pb": r.get("pb"), "yld": _pct(r.get("yld")), "payout": _pct(r.get("payout")),
        "beta": r.get("beta"), "tv": tv, "roe": _pct(r.get("roe")), "opm": _pct(r.get("opm")),
        "eq": eq, "rg": rg, "og": og, "epsg": _pct(r.get("epsg")), "opl": opl,
        "earn": r.get("earn"), "fy_end": fy_end, "price": r.get("price"),
    }


def screen(recs: list[dict]) -> tuple[list[dict], dict, dict]:
    rows = [derive(r) for r in recs]
    med: dict[str, dict] = {}
    for sec in sorted({x["sector"] for x in rows}):
        g = [x for x in rows if x["sector"] == sec]
        pes = sorted(x["pe"] for x in g if x["pe"] and x["pe"] > 0)
        pbs = sorted(x["pb"] for x in g if x["pb"] and x["pb"] > 0)
        med[sec] = {"pe": statistics.median(pes) if pes else None, "pb": statistics.median(pbs) if pbs else None}

    C = CRITERIA
    fails: dict[str, int] = {}
    passed = []
    for x in rows:
        why = None
        m = med[x["sector"]]
        if x["eq"] is None or x["eq"] < C["min_equity_ratio"]:
            why = "自己資本比率"
        elif x["opl"] is None or x["opl"] <= 0:
            why = "営業赤字"
        elif x["tv"] is None or x["tv"] < C["min_turnover_oku"]:
            why = "流動性"
        elif x["rg"] is None or x["rg"] < C["min_rev_growth"]:
            why = "売上成長"
        elif not ((x["og"] is not None and x["og"] > 0) or (x["epsg"] is not None and x["epsg"] > 0)):
            why = "利益成長"
        else:
            exc = x["rg"] >= C["yield_exception_growth"] and x["og"] is not None and x["og"] >= C["yield_exception_growth"]
            x["yield_exception"] = bool(exc and (x["yld"] is None or x["yld"] < C["min_yield"]))
            if (x["yld"] is None and not exc) or (x["yld"] is not None and x["yld"] < C["min_yield"] and not exc):
                why = "配当利回り"
            elif x["payout"] is not None and x["payout"] > C["max_payout"]:
                why = "配当性向"
            elif x["pe"] is None or x["pe"] <= 0 or m["pe"] is None or x["pe"] > C["per_vs_median"] * m["pe"]:
                why = "PER"
            elif x["pb"] is None or m["pb"] is None or x["pb"] > C["pbr_vs_median"] * m["pb"]:
                why = "PBR"
        if why:
            fails[why] = fails.get(why, 0) + 1
        else:
            passed.append(x)

    def prank(key: str, desc: bool) -> dict:
        lst = sorted(passed, key=lambda r: (r[key] is None, -(r[key] or 0) if desc else (r[key] or 0)))
        n = len(lst)
        return {r["code"]: 1 - i / max(1, n - 1) for i, r in enumerate(lst)}

    ranks = {k: prank(k, desc) for k, desc in (("eq", True), ("rg", True), ("yld", True), ("pe", False))}
    for x in passed:
        s = sum(WEIGHTS[k] * ranks[k][x["code"]] for k in WEIGHTS)
        if x["beta"] is not None and x["beta"] > C["high_beta"]:
            s -= HIGH_BETA_PENALTY
        x["score"] = s
        x["flags"] = data_flags(x)
    passed.sort(key=lambda r: -r["score"])
    return passed, fails, med


def data_flags(x: dict) -> list[str]:
    """データ誤りの疑い（tools/README.md の既知の問題）を機械的に拾う。"""
    f = []
    if x.get("yld") and x.get("payout") and x.get("pe"):
        implied = x["payout"] / x["pe"]  # 利回り ≈ 配当性向 / PER
        if abs(x["yld"] - implied) > max(1.5, implied):
            f.append(f"利回り{x['yld']:.1f}%は配当性向/PERからの推定{implied:.1f}%と大きく乖離")
    if x.get("rg") is not None and x["rg"] > 100:
        f.append(f"売上成長{x['rg']:.0f}%（決算期変更・合併の可能性）")
    if x.get("roe") is None:
        f.append("ROE欠損")
    if x.get("fy_end") and x["fy_end"] < (dt.date.today() - dt.timedelta(days=550)).isoformat():
        f.append(f"通期データが古い（期末{x['fy_end']}）")
    return f


def dividend_history(yh: Yahoo, code: str) -> dict[int, float] | None:
    try:
        ch = yh.chart(code, rng="6y", interval="1mo", events="div")
    except Exception:
        return None
    divs = ((ch or {}).get("events") or {}).get("dividends") or {}
    by: dict[int, float] = {}
    for d in divs.values():
        y = dt.datetime.fromtimestamp(d["date"], JST).year
        by[y] = by.get(y, 0.0) + float(d["amount"])
    return dict(sorted(by.items()))


# ---------------------------------------------------------------- step 3: technicals
def rsi_wilder(closes: list[float], n: int = 14) -> float | None:
    if len(closes) <= n:
        return None
    g = l = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        g += max(d, 0)
        l += max(-d, 0)
    ag, al = g / n, l / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def judge_technical(close: float, ma25: float, ma75: float, rsi: float, vratio: float) -> dict:
    above = (close > ma25) + (close > ma75)
    ma = "◎" if above == 2 else "〇" if above == 1 else "×"
    if 30 <= rsi <= 65:
        rs = "◎"
    elif 65 < rsi < 70:
        rs = "〇"
    else:
        rs = "×"
    if vratio > VOL_FLAT_HIGH:
        vo = "◎"
    elif vratio >= VOL_FLAT_LOW:
        vo = "〇"
    else:
        vo = "×"
    return {"ma": ma, "rsi_mark": rs, "vol": vo, "verdict": overall_verdict([ma, rs, vo])}


def overall_verdict(marks: list[str]) -> str:
    n_good, n_mid, n_bad = marks.count("◎"), marks.count("〇"), marks.count("×")
    if n_good == 3 or (n_good == 2 and n_mid == 1):
        return "買いタイミング良好"
    if n_bad <= 1:
        return "要観察"
    return "見送り"


def technical_from_chart(ch: dict) -> dict | None:
    ts = ch.get("timestamp") or []
    q = ((ch.get("indicators") or {}).get("quote") or [{}])[0]
    cl, vo, days = [], [], []
    for i, t in enumerate(ts):
        c = (q.get("close") or [None] * len(ts))[i]
        v = (q.get("volume") or [None] * len(ts))[i]
        if c is not None and v is not None:
            cl.append(float(c))
            vo.append(float(v))
            days.append(dt.datetime.fromtimestamp(t, JST).date().isoformat())
    if len(cl) < 76:
        return None
    ma25 = sum(cl[-25:]) / 25
    ma75 = sum(cl[-75:]) / 75
    rsi = rsi_wilder(cl)
    v25 = sum(vo[-25:]) / 25
    vratio = (sum(vo[-5:]) / 5) / v25 if v25 else 0.0
    j = judge_technical(cl[-1], ma25, ma75, rsi, vratio)
    return {"last_date": days[-1], "close": cl[-1], "ma25": ma25, "ma75": ma75,
            "rsi": rsi, "vratio": vratio, **j}


def fetch_technicals(yh: Yahoo, codes: list[str]) -> dict[str, dict]:
    out = {}
    log(f"[technical] {len(codes)} 銘柄のテクニカル指標を計算します")
    for c in codes:
        try:
            ch = yh.chart(c)
            t = technical_from_chart(ch) if ch else None
            out[c] = t or {"error": "日足データ不足"}
        except Exception as e:
            out[c] = {"error": str(e)}
    return out


# ---------------------------------------------------------------- local files
def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def split_slash(line: str) -> list[str]:
    return [s.strip() for s in line.split("/")]


def parse_master(p: Path) -> list[dict]:
    rows = []
    lines = [l for l in read_text(p).splitlines() if l.strip()]
    for l in lines[1:]:
        f = split_slash(l)
        if len(f) >= 4:
            rows.append({"rank": f[0], "code": f[1], "name": f[2], "verdict": f[3],
                         "comment": "/".join(f[4:]).strip()})
    return rows


def parse_slash_table(p: Path) -> list[list[str]]:
    if not p.exists():
        return []
    lines = [l for l in read_text(p).splitlines() if l.strip()]
    return [split_slash(l) for l in lines[1:]]


PROPOSAL_RE = re.compile(r"新規提案_(\d{8})\.txt$")
CODE_RE = re.compile(r"銘柄コード\s*[：:]\s*(\d{4}[A-Z]?)")


def proposal_files(briefing: Path, before: str) -> list[tuple[str, Path]]:
    out = []
    for p in briefing.glob("新規提案_*.txt"):
        m = PROPOSAL_RE.search(p.name)
        if m and m.group(1) < before:
            out.append((m.group(1), p))
    return sorted(out)


def proposal_codes(p: Path) -> list[str]:
    return CODE_RE.findall(read_text(p))


def streaks(briefing: Path, today: str) -> tuple[str | None, dict[str, list[str]]]:
    """直近の新規提案ファイルから、銘柄ごとに連続して提案された日付の列を返す。
    今日も提案すれば「len(日付)+1 日連続」になる。"""
    files = proposal_files(briefing, today)
    if not files:
        return None, {}
    latest_date = files[-1][0]
    result: dict[str, list[str]] = {}
    for code in proposal_codes(files[-1][1]):
        dates = []
        for d, p in reversed(files):
            if code in proposal_codes(p):
                dates.append(d)
            else:
                break
        result[code] = list(reversed(dates))
    return latest_date, result


# ---------------------------------------------------------------- formatting
def fmt(v, nd=1, suffix=""):
    if v is None:
        return "-"
    return f"{v:,.{nd}f}{suffix}"


def build_markdown(ctx: dict) -> str:
    L: list[str] = []
    a = L.append
    a(f"# アナリストチーム入力データ {ctx['today']}")
    a("")
    a(f"- 作成: {ctx['created']}（scripts: analyst/analyst_data.py prepare）")
    a("- データ取得元: Yahoo Finance 非公式API。誤りが混じるため、下の「データ注意」は必ず確認してください。")
    a(f"- 指示書: `prompts/analyst_team.md` / 基準: `{PROFILE}`（基準の唯一の正はプロフィール）")
    a("")
    a("## このファイルの使い方（AIへ）")
    a("")
    a("1. `prompts/analyst_team.md` の仕事1〜3を、このデータを材料にして実行してください。")
    a("2. 下の定量フィルターはプロフィールの推奨基準を数値にしたものです。プロフィールの定性条件"
      "（3ヶ月〜1年以内の材料、減配が続いていないこと、セクター集中の回避など）はあなたが判断してください。")
    a("3. テクニカルの◎〇×と総合判定は analyst_team.md の基準で機械計算済みです。そのまま使ってください。"
      "（出来高の「ほぼ同水準」は 5日平均/25日平均 が "
      f"{VOL_FLAT_LOW}〜{VOL_FLAT_HIGH}）")
    a(f"4. `{MASTER}` は読み込み専用です。実行開始時の SHA-256 は下に記録しました。")
    a("5. 保存後、`python analyst/analyst_data.py verify` で形式とマスター不変を確認できます。")
    a("")

    a("## 仕事1 用: 定量フィルター通過銘柄（点数順）")
    a("")
    a(f"対象 {ctx['n_cands']} 銘柄（{'・'.join(sorted(set(SECTOR_MAP.values())))}、時価総額500億円以上）→ 通過 {len(ctx['passed'])} 銘柄。"
      f"不通過の内訳: " + ("、".join(f"{k} {v}" for k, v in sorted(ctx['fails'].items(), key=lambda kv: -kv[1])) or "なし"))
    a("")
    a("点数 = 自己資本比率40%・売上成長30%・配当利回り20%・割安度(PER)10% の順位点。ベータ1.5超は -0.1。")
    a("")
    a("| 順 | コード | 企業名 | セクター | 点数 | PER | PBR | ROE% | 営業利益率% | 売上成長% | 営業益成長% | EPS成長% "
      "| 自己資本比率% | 利回り% | 配当性向% | ベータ | 売買代金(億) | 決算日 |")
    a("|" + "---|" * 18)
    for i, x in enumerate(ctx["passed"], 1):
        a(f"| {i} | {x['code']} | {x['name']} | {x['sector']} | {x['score']:.3f} | {fmt(x['pe'])} | {fmt(x['pb'], 2)} "
          f"| {fmt(x['roe'])} | {fmt(x['opm'])} | {fmt(x['rg'])} | {fmt(x['og'])} | {fmt(x['epsg'])} "
          f"| {fmt(x['eq'])} | {fmt(x['yld'], 2)} | {fmt(x['payout'], 0)} | {fmt(x['beta'], 2)} | {fmt(x['tv'])} "
          f"| {x['earn'] or '-'} |")
    a("")
    a("セクター中央値: " + "、".join(
        f"{s} PER {fmt(m['pe'])} / PBR {fmt(m['pb'], 2)}" for s, m in ctx["medians"].items()))
    a("")

    notes = [(x, x["flags"]) for x in ctx["passed"] if x.get("flags") or x.get("yield_exception")]
    a("### データ注意")
    a("")
    if notes:
        for x, fl in notes:
            extra = list(fl)
            if x.get("yield_exception"):
                extra.append("利回り基準は高成長の例外で通過")
            a(f"- {x['code']} {x['name']}: " + "／".join(extra))
    else:
        a("- なし")
    a("")

    a("### 配当の推移（暦年合計、上位銘柄。減配が続いていないかの目安）")
    a("")
    for code, hist in ctx["dividends"].items():
        if hist is None:
            a(f"- {code}: 取得失敗")
        elif not hist:
            a(f"- {code}: 配当データなし")
        else:
            a(f"- {code}: " + " ".join(f"{y}={v:g}" for y, v in hist.items()))
    a("（株式分割や支払時期の影響を受けます。今年分は途中経過です）")
    a("")

    a("## 仕事2 用: テクニカル評価（機械判定済み）")
    a("")
    a("| コード | 企業名 | 終値 | 25日線 | 75日線 | 移動平均 | RSI | RSI評価 | 出来高5/25 | 出来高 | 総合判定 | 最終日 |")
    a("|" + "---|" * 12)
    for code in ctx["tech_codes"]:
        t = ctx["tech"].get(code, {})
        name = ctx["names"].get(code, "")
        if "error" in t:
            a(f"| {code} | {name} | 取得失敗: {t['error']} |" + " |" * 9)
            continue
        a(f"| {code} | {name} | {fmt(t['close'])} | {fmt(t['ma25'])} | {fmt(t['ma75'])} | {t['ma']} | {t['rsi']:.1f} "
          f"| {t['rsi_mark']} | {t['vratio']:.3f} | {t['vol']} | {t['verdict']} | {t['last_date']} |")
    a("")
    a("テクニカルスクリーニング結果.txt の RSI 欄は整数（四捨五入）で書いてください。")
    a("")

    a("## 仕事3 用: マスターと過去の提案")
    a("")
    a(f"`{MASTER}` SHA-256（実行開始時）: `{ctx['master_hash']}`")
    a(f"コピー: `{ctx['master_copy']}`")
    a("")
    a("| 順位 | コード | 企業名 | 統合判定 | 今回テクニカル | 前回テクニカル | 総合コメント |")
    a("|---|---|---|---|---|---|---|")
    for m in ctx["master"]:
        t = ctx["tech"].get(m["code"], {})
        now = t.get("verdict", "-") if "error" not in t else "取得失敗"
        if "ma" in t:
            now += f"（{t['ma']}・{t['rsi']:.0f}・{t['vol']}）"
        prev = ctx["prev_tech"].get(m["code"], "-")
        a(f"| {m['rank']} | {m['code']} | {m['name']} | {m['verdict']} | {now} | {prev} | {m['comment']} |")
    a("")
    mon = [m["code"] for m in ctx["master"] if m["verdict"] in ("最優先", "監視")]
    hold = [m["code"] for m in ctx["master"] if m["verdict"] == "保留"]
    a(f"- 現在の監視リスト（最優先・監視）: {', '.join(mon) or 'なし'}")
    a(f"- 保留の銘柄: {', '.join(hold) or 'なし'}")
    a("- 「前回テクニカル」は今回の実行前に保存されていたテクニカルスクリーニング結果.txt の値です。")
    a("")
    if ctx["prev_date"]:
        a(f"直近の新規提案ファイル: 新規提案_{ctx['prev_date']}.txt")
        for code, dates in ctx["streaks"].items():
            a(f"- {code} {ctx['names'].get(code, '')}: {len(dates)}回連続で提案（{', '.join(dates)}）"
              f" → 今日も提案すれば（{len(dates) + 1}日連続）")
        if not ctx["streaks"]:
            a("- 提案なし")
    else:
        a("過去の新規提案ファイルはありません。")
    a("")
    if ctx["errors"]:
        a("## 取得エラー")
        a("")
        for e in ctx["errors"][:50]:
            a(f"- {e}")
        if len(ctx["errors"]) > 50:
            a(f"- ほか {len(ctx['errors']) - 50} 件")
        a("")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- commands
def default_cache() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ai-investment-analyst"


def cmd_prepare(a) -> int:
    base = Path(a.base).resolve()
    briefing = base / BRIEFING
    master_p = briefing / MASTER
    if not master_p.exists():
        log(f"見つかりません: {master_p}")
        return 2
    today = a.date or dt.datetime.now(JST).strftime("%Y%m%d")
    run_dir = base / a.out_dir / today
    run_dir.mkdir(parents=True, exist_ok=True)
    cache = Path(a.cache) if a.cache else default_cache()
    cache.mkdir(parents=True, exist_ok=True)

    # マスターを最初に記録（読むだけ）
    master_hash = sha256(master_p)
    master_copy = run_dir / f"master_snapshot_{MASTER}"
    shutil.copyfile(master_p, master_copy)
    (run_dir / "master_sha256.txt").write_text(master_hash + "\n", encoding="utf-8")
    master = parse_master(master_p)
    prev_tech = {r[0]: f"{r[5]}（{r[2]}・{r[3]}・{r[4]}）" for r in parse_slash_table(briefing / TECH_FILE) if len(r) >= 6}
    prev_date, stk = streaks(briefing, today)

    yh = Yahoo(sleep=a.sleep)
    errors: list[str] = []
    try:
        universe = load_universe(yh, cache, a.universe_max_age, a.code_from, a.code_to)
        cands = make_candidates(universe)
        if a.limit:
            cands = cands[: a.limit]
        recs, errs = crawl_fundamentals(yh, cands)
        errors += errs
    except Exception as e:
        log(f"財務データの取得に失敗しました: {e}")
        return 3
    (run_dir / "fundamentals_raw.json").write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    passed, fails, med = screen(recs)

    top = passed[: a.top]
    dividends = {x["code"]: dividend_history(yh, x["code"]) for x in top}

    names = {x["code"]: x["name"] for x in passed}
    names.update({m["code"]: m["name"] for m in master})
    tech_codes: list[str] = []
    for c in [x["code"] for x in top] + [m["code"] for m in master] + list(stk):
        if c not in tech_codes:
            tech_codes.append(c)
    tech = fetch_technicals(yh, tech_codes)

    ctx = {
        "today": today, "created": dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"),
        "n_cands": len(cands), "passed": top, "fails": fails, "medians": med, "dividends": dividends,
        "tech_codes": tech_codes, "tech": tech, "names": names, "master": master,
        "master_hash": master_hash, "master_copy": master_copy.relative_to(base).as_posix(),
        "prev_tech": prev_tech, "prev_date": prev_date, "streaks": stk, "errors": errors,
    }
    md = build_markdown(ctx)
    out = run_dir / f"analyst_input_{today}.md"
    out.write_text(md, encoding="utf-8")
    (run_dir / "analyst_input.json").write_text(
        json.dumps(ctx, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    master_after = sha256(master_p)
    if master_after != master_hash:
        log("警告: データ準備中に統合スクリーニング結果.txt が変わりました")
    print(out)
    return 0


def cmd_verify(a) -> int:
    base = Path(a.base).resolve()
    briefing = base / BRIEFING
    today = a.date or dt.datetime.now(JST).strftime("%Y%m%d")
    run_dir = base / a.out_dir / today
    errs: list[str] = []
    warns: list[str] = []

    # 1. マスター不変
    hp = run_dir / "master_sha256.txt"
    master_changed = None
    if hp.exists():
        master_changed = sha256(briefing / MASTER) != hp.read_text(encoding="utf-8").strip()
        if master_changed:
            errs.append(f"{MASTER} が実行開始時から変わっています")
    else:
        warns.append(f"{hp} がないためマスター不変を確認できません（prepare を先に実行してください）")

    data = {}
    jp = run_dir / "analyst_input.json"
    if jp.exists():
        data = json.loads(jp.read_text(encoding="utf-8"))
    passed_codes = {x["code"] for x in data.get("passed", [])}
    tech = data.get("tech", {})

    # 2. ファンダ
    fp = briefing / FUND_FILE
    fund_codes: list[str] = []
    if not fp.exists():
        errs.append(f"{FUND_FILE} がありません")
    else:
        if dt.datetime.fromtimestamp(fp.stat().st_mtime, JST).strftime("%Y%m%d") != today:
            warns.append(f"{FUND_FILE} は今日更新されていません")
        lines = [l for l in read_text(fp).splitlines() if l.strip()]
        if not lines or lines[0].strip() != FUND_HEADER:
            errs.append(f"{FUND_FILE}: 1行目の見出しが指定と違います")
        body = lines[1:]
        if not 1 <= len(body) <= 10:
            errs.append(f"{FUND_FILE}: 銘柄数が {len(body)}（1〜10）")
        for i, l in enumerate(body, 2):
            f = split_slash(l)
            if len(f) < 7:
                errs.append(f"{FUND_FILE} {i}行目: 項目数が {len(f)}（7）")
                continue
            fund_codes.append(f[0])
            for col, v in zip(("PER", "ROE", "営業利益率", "売上高成長率"), f[2:6]):
                if not re.fullmatch(r"-?\d+(\.\d+)?", v):
                    errs.append(f"{FUND_FILE} {i}行目: {col}「{v}」が数字だけではありません")
            if passed_codes and f[0] not in passed_codes:
                warns.append(f"{FUND_FILE} {i}行目: {f[0]} はスクリプトの定量フィルター通過リストにありません")

    # 3. テクニカル
    tp = briefing / TECH_FILE
    tech_rows: dict[str, list[str]] = {}
    if not tp.exists():
        errs.append(f"{TECH_FILE} がありません")
    else:
        lines = [l for l in read_text(tp).splitlines() if l.strip()]
        if not lines or lines[0].strip() != TECH_HEADER:
            errs.append(f"{TECH_FILE}: 1行目の見出しが指定と違います")
        for i, l in enumerate(lines[1:], 2):
            f = split_slash(l)
            if len(f) < 7:
                errs.append(f"{TECH_FILE} {i}行目: 項目数が {len(f)}（7）")
                continue
            code = f[0]
            tech_rows[code] = f
            ma, rsi, vol, verdict, comment = f[2], f[3], f[4], f[5], "/".join(f[6:])
            if ma not in "◎〇×" or vol not in "◎〇×" or len(ma) != 1 or len(vol) != 1:
                errs.append(f"{TECH_FILE} {i}行目: 移動平均・出来高は ◎〇× の記号で")
            if not re.fullmatch(r"\d{1,3}", rsi):
                errs.append(f"{TECH_FILE} {i}行目: RSI「{rsi}」は整数だけで")
            else:
                r = int(rsi)
                rm = "◎" if 30 <= r <= 65 else "〇" if 65 < r < 70 else "×"
                exp = overall_verdict([ma, rm, vol])
                if verdict != exp:
                    errs.append(f"{TECH_FILE} {i}行目: 総合判定「{verdict}」は記号から決まる「{exp}」と違います")
            if len(comment) > 40:
                errs.append(f"{TECH_FILE} {i}行目: コメントが {len(comment)} 字（40字以内）")
            if "," in comment or "，" in comment:
                errs.append(f"{TECH_FILE} {i}行目: コメントにカンマがあります（読点、を使う）")
            t = tech.get(code)
            if t and "ma" in t and (ma, vol) != (t["ma"], t["vol"]):
                warns.append(f"{TECH_FILE} {i}行目: {code} の記号がスクリプト計算（{t['ma']}・{t['vol']}）と違います")
        if fund_codes and set(fund_codes) != set(tech_rows):
            errs.append(f"{TECH_FILE}: 銘柄がファンダスクリーニング結果と一致しません")

    # 4. 新規提案
    pp = briefing / f"新規提案_{today}.txt"
    if not pp.exists():
        errs.append(f"{pp.name} がありません")
    else:
        text = read_text(pp)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        codes = CODE_RE.findall(text)
        if master_changed is False and (not lines or lines[-1] != MASTER_OK) and NO_PROPOSAL not in text:
            errs.append(f"{pp.name}: 末尾に「{MASTER_OK}」がありません")
        if not codes and NO_PROPOSAL not in text:
            errs.append(f"{pp.name}: 提案も「{NO_PROPOSAL}」もありません")
        if len(codes) > 2:
            errs.append(f"{pp.name}: 提案が {len(codes)} 件（最大2件）")
        snap = run_dir / f"master_snapshot_{MASTER}"
        mon = {m["code"] for m in parse_master(snap if snap.exists() else briefing / MASTER)
               if m["verdict"] in ("最優先", "監視")}
        for c in codes:
            if c in mon:
                errs.append(f"{pp.name}: {c} はすでに監視リストにあります")
            if fund_codes and c not in fund_codes:
                errs.append(f"{pp.name}: {c} は今回のファンダスクリーニング結果にありません")
            row = tech_rows.get(c)
            if row and row[5] == "見送り":
                errs.append(f"{pp.name}: {c} はテクニカル判定が見送りです")
        for l in lines:
            m = re.match(r"提案理由\s*[：:]\s*(.*)", l)
            if m and len(m.group(1)) > 40:
                errs.append(f"{pp.name}: 提案理由が {len(m.group(1))} 字（40字以内）")
        if master_changed and not lines[0].startswith(("警告", "注意")) and "変わ" not in lines[0]:
            errs.append(f"{pp.name}: マスターが変わったことを冒頭に明記してください")

    for w in warns:
        print(f"警告: {w}")
    for e in errs:
        print(f"NG: {e}")
    if not errs:
        print("OK: すべての確認を通過しました" + ("（警告あり）" if warns else ""))
    return 1 if errs else 0


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default=str(here.parent),
                   help="AI投資チームのフォルダー（既定: このリポジトリ。例: \"G:\\マイドライブ\\AI投資チーム\"）")
    p.add_argument("--date", help="日付 YYYYMMDD（既定: 今日 JST）")
    p.add_argument("--out-dir", default="analyst_runs", help="入力データの保存先（base からの相対）")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("prepare", help="データを集めて AI 用の入力ファイルを作る")
    pr.add_argument("--cache", help="キャッシュフォルダー（既定: %%LOCALAPPDATA%% または ~/.cache の下）")
    pr.add_argument("--universe-max-age", type=float, default=7, help="銘柄一覧を作り直す間隔（日）")
    pr.add_argument("--code-from", type=int, default=1300)
    pr.add_argument("--code-to", type=int, default=9999)
    pr.add_argument("--top", type=int, default=25, help="入力ファイルに載せる通過銘柄の数")
    pr.add_argument("--limit", type=int, default=0, help="財務取得の銘柄数上限（試験用）")
    pr.add_argument("--sleep", type=float, default=0.25, help="リクエスト間の待ち秒数")
    sub.add_parser("verify", help="AI が保存したファイルを確認する")
    a = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    return cmd_prepare(a) if a.cmd == "prepare" else cmd_verify(a)


if __name__ == "__main__":
    sys.exit(main())
