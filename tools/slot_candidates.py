"""ウォッチ枠の入れ替え候補を出す。

任天堂と良品計画は、この2つで弱かった:
  ・連動確認（§3-1 条件4）が機能しない ― 日経225の中で最大相関 0.43 / 0.42
  ・任天堂は順張りの土俵に乗る日が 200営業日で 4% しかない

入れ替えるなら、その2点を満たすものを探す。母集団は日経225。

    python tools/slot_candidates.py
"""
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import screen_daily as sd

CACHE = os.environ.get("SLOT_CACHE", "/tmp/n225_bars.pkl")
OUT = {"7974", "7453"}      # 抜く候補
MIN_TURNOVER = 150.0        # 億円/日。毎朝見る枠なので厚みを要求する
MIN_PEER = 0.55             # 連動確認が成立する最低ライン
MAX_VS_CURRENT = 0.60       # 残す13銘柄と重ならないこと


def load_bars(codes):
    if os.path.exists(CACHE):
        b = pickle.load(open(CACHE, "rb"))
        missing = [c for c in codes if c not in b]
    else:
        b, missing = {}, list(codes)
    if missing:
        h = sd.get_headers()
        for i, c in enumerate(missing, 1):
            try:
                b[c] = sd.normalize_columns(sd.fetch_daily_bars(c, h, lookback_days=300))
            except Exception as e:
                print(f"  ! {c}: {e}", file=sys.stderr)
            if i % 25 == 0:
                print(f"  {i}/{len(missing)}", file=sys.stderr, flush=True)
        pickle.dump(b, open(CACHE, "wb"))
    return b


def main():
    wl = pd.read_csv("watchlist.csv", dtype={"code": str})
    n225 = pd.read_csv("nikkei225_codes.csv", dtype={"code": str})
    nm = pd.read_csv("company_master.csv", dtype={"code": str})
    N = dict(zip(nm["code"], nm["CoName"]))
    S = dict(zip(nm["code"], nm["S33Nm"]))
    watch = list(wl["code"])
    keep = [c for c in watch if c not in OUT]
    bars = load_bars(sorted(set(n225["code"]) | set(watch)))

    rets, turn, atr, stance, last = {}, {}, {}, {}, {}
    for c, df in bars.items():
        if len(df) < 200:
            continue
        cl, h, l = df["close"], df["high"], df["low"]
        rets[c] = cl.pct_change().dropna()
        turn[c] = (cl * df["volume"]).tail(20).mean() / 1e8
        last[c] = cl.iloc[-1]
        tr = pd.concat([h - l, (h - cl.shift()).abs(),
                        (l - cl.shift()).abs()], axis=1).max(axis=1)
        atr[c] = tr.rolling(14).mean().iloc[-1] / cl.iloc[-1] * 100
        ma = cl.rolling(25).mean()
        ok = (~ma.isna()) & (~ma.shift(20).isna())
        d = pd.DataFrame({"c": cl, "ma": ma, "r": ma.shift(20)})[ok].tail(200)
        # 順張りの土俵に乗っていた日の割合。毎朝の枠として意味があるか。
        stance[c] = ((d["ma"] > d["r"]) & (d["c"] > d["ma"])).mean() * 100
    CO = pd.DataFrame(rets).dropna().corr()

    def peermax(c):
        """売買代金80億以上の中での最大相関。連動確認に使えるかを見る。"""
        o = [x for x in CO.columns if x != c and turn.get(x, 0) >= 80]
        return CO.loc[c, o].max() if o else np.nan

    print("■ いまの15銘柄\n")
    print(f"{'コード':<7}{'銘柄':<16}{'順張り成立率':>11}{'連動先':>7}{'ATR%':>7}{'代金':>8}")
    for c in watch:
        mark = "  ← 入れ替え候補" if c in OUT else ""
        print(f"{c:<7}{N.get(c, '')[:14]:<16}{stance.get(c, np.nan):>10.0f}%"
              f"{peermax(c):>7.2f}{atr.get(c, 0):>7.1f}{turn.get(c, 0):>7.0f}億{mark}")

    rows = []
    for c in CO.columns:
        if c in watch or turn.get(c, 0) < MIN_TURNOVER:
            continue
        pk, vs = peermax(c), CO.loc[c, keep].max()
        if pk < MIN_PEER or vs >= MAX_VS_CURRENT:
            continue
        rows.append((stance.get(c, 0), c, pk, vs, atr.get(c, 0), turn[c], last[c]))

    print(f"\n\n■ 入れ替え候補"
          f"（代金{MIN_TURNOVER:.0f}億以上 / 連動先{MIN_PEER}以上 / 残す13銘柄と{MAX_VS_CURRENT}未満）\n")
    print(f"{'コード':<7}{'銘柄':<16}{'業種':<10}{'順張り':>7}{'連動先':>7}"
          f"{'対現行':>7}{'ATR%':>6}{'代金':>7}{'1単元':>7}")
    for st, c, pk, vs, a, t, px in sorted(rows, reverse=True)[:15]:
        print(f"{c:<7}{N.get(c, '')[:14]:<16}{(S.get(c) or '')[:8]:<10}{st:>6.0f}%"
              f"{pk:>7.2f}{vs:>7.2f}{a:>6.1f}{t:>6.0f}億{px * 100 / 10000:>6.1f}万")
    if not rows:
        print("  条件を満たす銘柄がありませんでした。")


if __name__ == "__main__":
    main()
