"""売買ルールの検証エンジン。

2026-08-09 〜 08-10 に運用方針 v4（1321のスイング）を検証したときのコード。
結論と数字は `docs/policy.md` §11 にある。**同じ検証をやり直すならこれを使う。**

このスクリプトが存在する理由
----------------------------
最初の検証は書き捨てのコードで回し、条文と突き合わせていなかった。その結果
§1（同時保有1つ）と §6-3（MA130下向きで手仕舞い）が実装から漏れ、1取引あたり
+1.55% と報告した数字が、条文どおりに直すと +0.74% になった。**定義の差だけで
「優位性」の半分が消えた。**方針を変えるたびに回し直せる状態にしておかないと、
同じことが起きる。

先読み（look-ahead）の扱い
--------------------------
日本株は 9:00-15:30 JST。方針 §3-0 は「引け後に判定し、翌営業日の寄り成行で建てる」。
したがって営業日 T の寄りで建てるトレードが使ってよいのは:

    自分の日足    date <= T-1 の大引けまで
    米国株・米金利 date <= T-1 の米セッション（23:30 JST(T-1) 〜 06:00 JST(T) に確定）
    ドル円・CME先物 date <= T-1 の終値

`fetch_macro()` は merge_asof(direction="backward") でこれを寄せる。
**シグナルは i 日目の確定足で作り、約定は i+1 日目の始値。** ここを崩さないこと。

使い方
------
    python tools/backtest.py rules   --code 1321          # v4の条文どおりに回す
    python tools/backtest.py dip     --code 1321          # 押し目買いを回す
    python tools/backtest.py compare --code 1321          # 主要な設計を横並びで
    python tools/backtest.py sweep   --code 1321          # 利確・損切りを振る
    python tools/backtest.py universe --codes 1321,1306,1655,1343   # 複数銘柄を横断

    --source jquants (既定, 日本のETF) / yahoo (指数・米国ETF)
    --split      前半・後半に割って表示する（頑健性の確認。これを必ず見ること）
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

JQ = "https://api.jquants.com/v2/equities/bars/daily"
YF = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
UA = {"User-Agent": "Mozilla/5.0"}


# --------------------------------------------------------------------------
# データ取得
# --------------------------------------------------------------------------

def fetch_jquants(code: str, start: str = "2021-08-10") -> pd.DataFrame:
    """J-Quants の日足。**調整後（AdjO/AdjH/AdjL/AdjC）を使う。**

    生の O/H/L/C を使うと分割・併合が入った銘柄で結果が壊れる。1306(TOPIX) を
    生値で測ったとき期間リターンが -78% と出た（実際は +100% 超）。
    """
    key = os.environ.get("JQUANTS_API_KEY")
    if not key:
        raise RuntimeError("環境変数 JQUANTS_API_KEY が未設定です")
    for attempt in range(4):
        r = requests.get(JQ, params={"code": code, "from": start},
                         headers={"x-api-key": key}, timeout=60)
        if r.status_code == 200:
            break
        time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"{code}: J-Quants から取得できません ({r.status_code}) {r.text[:200]}")
    body = r.json()
    rows = body if isinstance(body, list) else body.get("data", [])
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"{code}: データが空です")
    df["date"] = pd.to_datetime(df["Date"])
    df = df.rename(columns={"AdjO": "open", "AdjH": "high", "AdjL": "low", "AdjC": "close"})
    return (df[["date", "open", "high", "low", "close"]]
            .dropna().sort_values("date").reset_index(drop=True))


def fetch_yahoo(symbol: str) -> pd.DataFrame:
    """Yahoo の日足を全期間。adjclose 比で OHLC も調整する。

    range=max は月足を返す。日足が要るときは period1/period2 を明示すること。
    """
    r = requests.get(YF.format(symbol),
                     params={"period1": 0, "period2": 2000000000, "interval": "1d"},
                     headers=UA, timeout=60)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"date": pd.to_datetime(res["timestamp"], unit="s").normalize(),
                       "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"]})
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
    if adj is not None:
        f = pd.Series(adj) / df["close"]
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] * f
    return df.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)


def load(code: str, source: str) -> pd.DataFrame:
    return fetch_yahoo(code) if source == "yahoo" else fetch_jquants(code)


# --------------------------------------------------------------------------
# 指標
# --------------------------------------------------------------------------

def add_indicators(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d["ma25"] = d.close.rolling(25).mean()
    d["ma130"] = d.close.rolling(130).mean()          # 26週
    d["ma200"] = d.close.rolling(200).mean()
    d["ma25_slope"] = d.ma25 - d.ma25.shift(5)
    d["ma130_slope"] = d.ma130 - d.ma130.shift(10)
    delta = d.close.diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean().replace(0, np.nan)
    d["rsi"] = 100 - 100 / (1 + up / dn)
    tr = pd.concat([d.high - d.low,
                    (d.high - d.close.shift()).abs(),
                    (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean() / d.close * 100   # ATR14 を % で持つ
    d["dev"] = (d.close / d.ma25 - 1) * 100            # MA25 からの乖離
    d["dd250"] = (d.close / d.high.rolling(250).max() - 1) * 100
    return d.reset_index(drop=True)


# --------------------------------------------------------------------------
# エントリー条件
# --------------------------------------------------------------------------

def signals_v4(d: pd.DataFrame, warmup: int = 140) -> list:
    """方針 §3-1 / §3-2（順張り）。デデュープはパターンごとに5営業日。

    §3-1 MA25を上抜けた日 / §3-2 MA25の上で押して陽線で返した日
    共通: MA130上向き(§2) / MA25上向き / RSI<70(§4-2)
    """
    a, b = [], []
    for i in range(warmup, len(d) - 1):
        if not d.ma130_slope.iloc[i] > 0:      # §2 レジームフィルタ
            continue
        if not d.ma25_slope.iloc[i] > 0:
            continue
        if not d.rsi.iloc[i] < 70:             # §4-2
            continue
        c, o, lo, m = d.close.iloc[i], d.open.iloc[i], d.low.iloc[i], d.ma25.iloc[i]
        if c > m and d.close.iloc[i - 1] <= d.ma25.iloc[i - 1]:
            if not a or i - a[-1] >= 5:
                a.append(i)
            continue
        if c > m and lo <= m * 1.01 and c > o:
            if not b or i - b[-1] >= 5:
                b.append(i)
    return sorted(set(a + b))


def signals_dip(d: pd.DataFrame, dev_th: float = -4.0, bull: bool = False,
                bounce: bool = False, warmup: int = 140) -> list:
    """押し目買い（逆張り）。MA25から dev_th% 以上下げた日。

    bull=True で MA130上向きの局面に限定する。**日経57年の実測では、この
    フィルタを付けると成績が下がる**（付けない +0.18% / 付ける -0.19%）。
    それでも付ける価値があるかは docs/policy.md §11 を読んで判断すること。
    """
    out, last = [], -99
    for i in range(warmup, len(d) - 1):
        if bull and not d.ma130_slope.iloc[i] > 0:
            continue
        if not d.dev.iloc[i] <= dev_th:
            continue
        if bounce and not d.close.iloc[i] > d.open.iloc[i]:
            continue
        if i - last < 5:
            continue
        last = i
        out.append(i)
    return out


def signals_everyday(d: pd.DataFrame, warmup: int = 140) -> list:
    """ベースレート用。無条件に毎日買う。**必ずこれと比べること。**

    条件付きの成績が、これを上回らないなら、その条件には意味がない。
    """
    return list(range(warmup, len(d) - 1))


# --------------------------------------------------------------------------
# シミュレータ
# --------------------------------------------------------------------------

def simulate(d, entries, *, stop_atr=2.5, stop_floor=3.0, stop_cap=6.0,
             target=7.0, target_atr=None, exit_ma25=True, ma25_buffer=0.99,
             exit_ma25_touch=False, exit_ma130=True, max_days=30, sequential=True):
    """シグナル日 i の翌営業日 i+1 の始値で建て、下記のどれかで決済する。

    損切り  逆指値。ATR14 × stop_atr（stop_floor〜stop_cap でクリップ）
    利確    指値。target%（target_atr を渡すと ATR14 × target_atr）
    手仕舞い exit_ma25:       終値 < MA25 × ma25_buffer → 翌寄り（順張り用）
             exit_ma25_touch: 高値 >= MA25 → その場で利確（押し目買い用）
             exit_ma130:      MA130 が下向きに転じたら翌寄り（§6-3）
             max_days:        営業日数の上限

    **押し目買いで exit_ma25 を使ってはいけない。** MA25 より下で買う設計なので、
    建てた瞬間に手仕舞い条件が成立する。実測で41回中20回がそれだった。

    sequential=True は §1「同時保有は常に1つ」。False にすると重複建てを許す
    （ベースレートを厚く取りたいときだけ）。
    """
    n = len(d)
    o, h, l, c = d.open.values, d.high.values, d.low.values, d.close.values
    ma25, atr, slope = d.ma25.values, d.atr.values, d.ma130_slope.values
    rows, busy, held = [], -1, 0

    for i in entries:
        e = i + 1
        if e >= n - 1 or (sequential and e <= busy):
            continue
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        entry = o[e]
        if not np.isfinite(entry) or entry <= 0:
            continue                      # Yahoo は始値が欠けたバーを返すことがある
        stop_pct = -min(max(a * stop_atr, stop_floor), stop_cap)
        tgt_pct = a * target_atr if target_atr else target
        stop_px, tgt_px = entry * (1 + stop_pct / 100), entry * (1 + tgt_pct / 100)
        res, limit = None, min(e + max_days - 1, n - 1)

        for k in range(e, limit + 1):
            # 寄りギャップを先に見る。逆指値・指値は寄りを守らない
            if o[k] <= stop_px:
                res = ((o[k] / entry - 1) * 100, k, "損切(ギャップ)"); break
            if o[k] >= tgt_px:
                res = ((o[k] / entry - 1) * 100, k, "利確(ギャップ)"); break
            # 同じ足で両方に触れたら損切り優先（保守側）
            if l[k] <= stop_px:
                res = (stop_pct, k, "損切"); break
            if h[k] >= tgt_px:
                res = (tgt_pct, k, "利確"); break
            if exit_ma25_touch and h[k] >= ma25[k]:
                fill = max(o[k], ma25[k])
                res = ((fill / entry - 1) * 100, k, "MA25回帰"); break
            if exit_ma25 and k > e and c[k] < ma25[k] * ma25_buffer:
                nx = min(k + 1, n - 1)
                res = ((o[nx] / entry - 1) * 100, nx, "MA25割れ"); break
            if exit_ma130 and k > e and slope[k] <= 0:
                nx = min(k + 1, n - 1)
                res = ((o[nx] / entry - 1) * 100, nx, "MA130下向き"); break

        if res is None:
            res = ((c[limit] / entry - 1) * 100, limit, "日数上限")
        if sequential:
            busy = res[1]
            held += res[1] - e + 1
        rows.append({"entry_date": d.date.iloc[e].date(), "exit_date": d.date.iloc[res[1]].date(),
                     "days": res[1] - e + 1, "pnl": res[0], "reason": res[2],
                     "stop": stop_pct, "target": tgt_pct})
    return pd.DataFrame(rows), held


def metrics(trades, d, held, *, size=0.36, warmup=140):
    """建玉を資金の size 倍としたときの成績。

    size は方針 §5 の「リスク予算1.5% ÷ 損切り幅」から出る建玉比率。
    1321・損切り-4% なら 0.36 前後になる。
    """
    if trades.empty:
        return None
    p = trades.pnl.values
    win, lose = p[p > 0], p[p <= 0]
    eq = (1 + p / 100 * size).cumprod()
    years = (d.date.iloc[-1] - d.date.iloc[warmup]).days / 365.25
    payoff = abs(win.mean() / lose.mean()) if len(win) and len(lose) else np.nan
    run = mx = 0
    for x in (p <= 0).astype(int):
        run = run + 1 if x else 0
        mx = max(mx, run)
    bh = ((d.close.iloc[-1] / d.close.iloc[warmup]) ** (1 / years) - 1) * 100
    exposure = held / max(1, len(d) - warmup - 1) * size
    return {
        "n": len(p), "years": years, "per_year": len(p) / years,
        "win_rate": (p > 0).mean() * 100, "avg": p.mean(),
        "win_avg": win.mean() if len(win) else np.nan,
        "lose_avg": lose.mean() if len(lose) else np.nan,
        "payoff": payoff, "breakeven_wr": 100 / (1 + payoff) if payoff == payoff else np.nan,
        "max_lose_streak": mx, "worst": p.min(),
        "annual": (eq[-1] ** (1 / years) - 1) * 100,
        "dd": (eq / np.maximum.accumulate(eq) - 1).min() * 100,
        "in_market": held / max(1, len(d) - warmup - 1) * 100,
        "buy_hold": bh,
        # 露出だけで説明できる分。実績がこれを超えなければタイミングに意味はない
        "beta_return": exposure * bh,
        "alpha": (eq[-1] ** (1 / years) - 1) * 100 - exposure * bh,
    }


def show(label, m, trades=None):
    if m is None:
        print(f"  {label}: 取引なし")
        return
    print(f"\n  【{label}】")
    print(f"    取引 {m['n']}回（年{m['per_year']:.1f}回）  勝率 {m['win_rate']:.0f}%  "
          f"1取引 {m['avg']:+.2f}%  最大連敗 {m['max_lose_streak']}  最悪 {m['worst']:+.2f}%")
    print(f"    勝ち平均 {m['win_avg']:+.2f}% / 負け平均 {m['lose_avg']:+.2f}%  "
          f"ペイオフ {m['payoff']:.2f}  損益分岐勝率 {m['breakeven_wr']:.0f}%")
    print(f"    年率 {m['annual']:+.2f}%  資金DD {m['dd']:.1f}%  建玉のある日 {m['in_market']:.0f}%")
    print(f"    買い持ち年率 {m['buy_hold']:+.1f}%  /  露出で説明できる分 {m['beta_return']:+.2f}%  "
          f"→ 超過α {m['alpha']:+.2f}%")
    if trades is not None and not trades.empty:
        print("    決済理由: " + " / ".join(f"{k}{v}回" for k, v in trades.reason.value_counts().items()))


# --------------------------------------------------------------------------
# 設計のプリセット
# --------------------------------------------------------------------------

PRESETS = {
    # 方針 v4 の条文どおり（順張り）
    "v4": dict(signal=signals_v4, sim=dict(stop_atr=2.5, target=7.0, exit_ma25=True,
                                           ma25_buffer=0.99, exit_ma130=True, max_days=30)),
    # 押し目買い。出口は MA25 へのタッチ（MA25割れでは切らない）
    "dip": dict(signal=lambda d: signals_dip(d, -4.0, bull=False),
                sim=dict(stop_atr=3.0, target=1e9, exit_ma25=False,
                         exit_ma25_touch=True, exit_ma130=False, max_days=30)),
    "dip_bull": dict(signal=lambda d: signals_dip(d, -4.0, bull=True),
                     sim=dict(stop_atr=3.0, target=1e9, exit_ma25=False,
                              exit_ma25_touch=True, exit_ma130=False, max_days=30)),
    # ベースレート。条件付きの成績はこれと比べて初めて意味を持つ
    "base": dict(signal=signals_everyday, sim=dict(stop_atr=2.5, target=7.0, exit_ma25=True,
                                                   ma25_buffer=0.99, exit_ma130=True, max_days=30)),
}


def run_preset(d, name, size=0.36, sequential=True):
    cfg = PRESETS[name]
    ent = cfg["signal"](d)
    tr, held = simulate(d, ent, sequential=sequential, **cfg["sim"])
    return tr, metrics(tr, d, held, size=size)


# --------------------------------------------------------------------------
# サブコマンド
# --------------------------------------------------------------------------

def cmd_single(args, preset):
    d = add_indicators(load(args.code, args.source))
    print(f"{args.code}  {d.date.iloc[0].date()} 〜 {d.date.iloc[-1].date()}  {len(d)}本")
    tr, m = run_preset(d, preset, size=args.size)
    show(preset, m, tr)
    if args.verbose and not tr.empty:
        print()
        print(tr.to_string(index=False))
    if args.split:
        half = len(d) // 2
        for label, sl in [("前半", d.iloc[:half]), ("後半", pd.concat([d.iloc[:140], d.iloc[half:]]))]:
            sl = sl.reset_index(drop=True)
            t2, m2 = run_preset(sl, preset, size=args.size)
            show(f"{preset} / {label}", m2)


def cmd_compare(args):
    d = add_indicators(load(args.code, args.source))
    print(f"{args.code}  {d.date.iloc[0].date()} 〜 {d.date.iloc[-1].date()}  {len(d)}本\n")
    print(f"{'設計':<14}{'取引':>6}{'年間':>6}{'勝率':>7}{'1取引':>9}{'年率':>9}"
          f"{'資金DD':>9}{'在庫':>7}{'超過α':>9}")
    for name in ("v4", "dip", "dip_bull", "base"):
        tr, m = run_preset(d, name, size=args.size, sequential=True)
        if m is None:
            continue
        label = "base(常時)" if name == "base" else name
        print(f"{label:<14}{m['n']:>6}{m['per_year']:>6.1f}{m['win_rate']:>6.0f}%"
              f"{m['avg']:>+8.2f}%{m['annual']:>+8.2f}%{m['dd']:>8.1f}%"
              f"{m['in_market']:>6.0f}%{m['alpha']:>+8.2f}%")

    # 無条件エントリーの「1取引あたり」だけを別に出す。重複建てを許すので
    # 年率・在庫率・DD は意味を持たない（同じ日に何本も建てた前提になる）。
    tr_r, _ = simulate(d, signals_everyday(d), sequential=False, **PRESETS["base"]["sim"])
    if not tr_r.empty:
        p = tr_r.pnl.values
        print(f"{'base(無条件)':<14}{len(p):>6}{'-':>6}{(p > 0).mean() * 100:>6.0f}%"
              f"{p.mean():>+8.2f}%{'-':>9}{'-':>9}{'-':>7}{'-':>9}")

    yrs = (d.date.iloc[-1] - d.date.iloc[140]).days / 365.25
    bh = ((d.close.iloc[-1] / d.close.iloc[140]) ** (1 / yrs) - 1) * 100
    dd = (d.close / d.close.cummax() - 1).min() * 100
    print(f"{'買い持ち':<14}{'-':>6}{'-':>6}{'-':>7}{'-':>9}{bh:>+8.2f}%{dd:>8.1f}%{100:>6}%{'-':>9}")

    print("\n  超過α = 年率 −（建玉のある日の割合 × 建玉比率 × 買い持ち年率）")
    print("  これがプラスでなければ、ルールは『露出を減らしただけ』で何も生んでいない。")
    print("  base(常時) は建玉を切らさず持ち続けた場合、base(無条件) は毎日エントリーした")
    print("  場合の1取引あたり。エントリー条件の質は後者と比べる。")


def cmd_sweep(args):
    d = add_indicators(load(args.code, args.source))
    print(f"{args.code}  利確・損切りを振る（エントリーと手仕舞いは v4 のまま）\n")
    print(f"{'利確':>7}{'損切ATR倍':>10}{'取引':>6}{'勝率':>7}{'1取引':>9}{'年率':>9}{'超過α':>9}")
    for target in (3, 5, 7, 10, 15):
        for satr in (2.0, 2.5, 3.0):
            ent = signals_v4(d)
            tr, held = simulate(d, ent, stop_atr=satr, target=target,
                                exit_ma25=True, ma25_buffer=0.99, exit_ma130=True, max_days=30)
            m = metrics(tr, d, held, size=args.size)
            if m is None:
                continue
            print(f"{target:>6}%{satr:>10.1f}{m['n']:>6}{m['win_rate']:>6.0f}%"
                  f"{m['avg']:>+8.2f}%{m['annual']:>+8.2f}%{m['alpha']:>+8.2f}%")
    print("\n  利確を +2〜3% まで浅くすると、売買コスト込みでマイナスになる。")
    print("  負けの大きさは利確幅で変わらないため、ペイオフレシオだけが潰れる。")


def cmd_universe(args):
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    print(f"{'code':<10}{'取引':>6}{'勝率':>7}{'1取引':>9}{'年率':>9}{'買持':>9}{'在庫':>7}{'超過α':>9}")
    rows = []
    for code in codes:
        try:
            d = add_indicators(load(code, args.source))
        except Exception as exc:
            print(f"{code:<10} 取得失敗: {exc}")
            continue
        if len(d) < 400:
            print(f"{code:<10} データ不足 ({len(d)}本)")
            continue
        tr, m = run_preset(d, args.preset, size=args.size)
        if m is None:
            print(f"{code:<10} 取引なし")
            continue
        rows.append(m)
        print(f"{code:<10}{m['n']:>6}{m['win_rate']:>6.0f}%{m['avg']:>+8.2f}%"
              f"{m['annual']:>+8.2f}%{m['buy_hold']:>+8.1f}%{m['in_market']:>6.0f}%{m['alpha']:>+8.2f}%")
        time.sleep(0.2)
    if rows:
        a = np.array([r["alpha"] for r in rows])
        t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 else np.nan
        print(f"\n  超過α: 平均 {a.mean():+.3f}%  中央値 {np.median(a):+.3f}%  "
              f"プラス {int((a > 0).sum())}/{len(a)}  t={t:+.2f}")
        print(f"  買い持ちに勝った銘柄: "
              f"{sum(1 for r in rows if r['annual'] > r['buy_hold'])}/{len(rows)}")
        print("\n  注意: 同じ指数の別上場（1655/2558/1557 など）は独立した観測ではない。")
        print("  銘柄数をそのまま検定の n として読まないこと。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="売買ルールの検証エンジン")
    ap.add_argument("command", choices=["rules", "dip", "compare", "sweep", "universe"])
    ap.add_argument("--code", default="1321")
    ap.add_argument("--codes", default="1321,1306,1655,1343,1540")
    ap.add_argument("--source", default="jquants", choices=["jquants", "yahoo"])
    ap.add_argument("--preset", default="dip", choices=list(PRESETS))
    ap.add_argument("--size", type=float, default=0.36,
                    help="建玉が総資金に占める比率（方針§5の逆算結果。既定0.36）")
    ap.add_argument("--split", action="store_true", help="前半・後半に割って表示する")
    ap.add_argument("--verbose", action="store_true", help="全取引を一覧表示する")
    args = ap.parse_args(argv)

    if args.command == "rules":
        cmd_single(args, "v4")
    elif args.command == "dip":
        cmd_single(args, "dip")
    elif args.command == "compare":
        cmd_compare(args)
    elif args.command == "sweep":
        cmd_sweep(args)
    elif args.command == "universe":
        cmd_universe(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
