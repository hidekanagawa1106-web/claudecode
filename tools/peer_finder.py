"""ウォッチ15銘柄それぞれについて、値動きが近い参考銘柄を出す。

用途は 運用方針_v2 §3-1 条件4「連動銘柄が同方向に動いているか」の確認。
証券アプリのお気に入りに入れておき、買う直前に同じ方向へ動いているかを
目で確かめるための一覧。売買対象ではない。

母集団は日経225。ユニバース74銘柄では狭すぎて、業種によっては
連動先が1つも見つからない。

参考銘柄に求めるもの:
  ・値動きが近い（日次リターン相関）
  ・場中に板が見える（売買代金の下限を置く）
  ・確認する意味がある同士で固まらない（同じ塊から採りすぎない）

    python tools/peer_finder.py
    python tools/peer_finder.py --top 5 --min-turnover 100
"""
import argparse
import os
import pickle
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import screen_daily as sd

CACHE = os.environ.get("PEER_CACHE", "/tmp/n225_bars.pkl")


def load_bars(codes):
    if os.path.exists(CACHE):
        b = pickle.load(open(CACHE, "rb"))
        missing = [c for c in codes if c not in b]
    else:
        b, missing = {}, list(codes)
    if missing:
        h = sd.get_headers()
        for i, code in enumerate(missing, 1):
            try:
                b[code] = sd.normalize_columns(
                    sd.fetch_daily_bars(code, h, lookback_days=300))
            except Exception as e:
                print(f"  ! {code}: {e}", file=sys.stderr)
            if i % 25 == 0:
                print(f"  {i}/{len(missing)}", file=sys.stderr, flush=True)
        pickle.dump(b, open(CACHE, "wb"))
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--min-turnover", type=float, default=80.0,
                    help="20日平均売買代金の下限（億円）")
    ap.add_argument("--min-days", type=int, default=150)
    args = ap.parse_args()

    wl = pd.read_csv("watchlist.csv", dtype={"code": str})
    n225 = pd.read_csv("nikkei225_codes.csv", dtype={"code": str})
    nm = pd.read_csv("company_master.csv", dtype={"code": str})
    N = dict(zip(nm["code"], nm["CoName"]))
    S = dict(zip(nm["code"], nm["S33Nm"]))
    watch = list(wl["code"])
    codes = sorted(set(n225["code"]) | set(watch))
    bars = load_bars(codes)

    rets, turn = {}, {}
    for c, df in bars.items():
        if len(df) < args.min_days:
            continue
        rets[c] = df.set_index("date")["close"].pct_change().dropna()
        turn[c] = (df["close"] * df["volume"]).tail(20).mean() / 1e8
    C = pd.DataFrame(rets).dropna().corr()
    print(f"相関の計算: {len(C)}銘柄 × "
          f"{len(pd.DataFrame(rets).dropna())}営業日\n")

    liquid = {c for c in C.columns if turn.get(c, 0) >= args.min_turnover}
    for code in watch:
        if code not in C.columns:
            print(f"■ {code} {N.get(code, '')} — データ不足で判定できません\n")
            continue
        cand = [c for c in C.columns
                if c != code and (c in liquid or c in watch)]
        s = C.loc[code, cand].sort_values(ascending=False)
        print(f"■ {code} {N.get(code, '')}   {S.get(code, '')}")
        for i, (c, v) in enumerate(s.head(args.top).items(), 1):
            mark = " ←ウォッチ中" if c in watch else ""
            print(f"   {i}. {c}  {N.get(c, '')[:18]:<20}{(S.get(c) or '')[:8]:<10}"
                  f"相関{v:.2f}  代金{turn.get(c, 0):>5.0f}億{mark}")
        print()

    print("=" * 70)
    print("お気に入り登録用（コードのみ・重複を除く）")
    print("=" * 70)
    for code in watch:
        if code not in C.columns:
            continue
        cand = [c for c in C.columns if c != code and (c in liquid or c in watch)]
        peers = C.loc[code, cand].sort_values(ascending=False).head(args.top)
        print(f"{code} {N.get(code, '')[:14]}: " + " ".join(peers.index))


if __name__ == "__main__":
    main()
