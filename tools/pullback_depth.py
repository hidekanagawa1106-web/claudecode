"""「押し目」を乖離の深さで切るべきか、実測で確かめる。

きっかけは 2026-08-04 のブリーフィング。KOKUSAI（MA25から -17.9%）が「押し目」、
村田（-19.6%）が「トレンド下向き」に分かれた。どちらも高値から約-50%で、
差は天井の時期が20営業日前を挟んだかどうかだけだった。
MA25から -17.9% 下を「押し目」と呼ぶのは言葉として無理があるので、
深さで切る案を2つ測った。

結果はどちらも不支持。詳細は docs/usage.md。フィルタは足さず、
乖離を表示するだけにした。

    python tools/pullback_depth.py
"""
import os
import pickle
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import screen_daily as sd

CACHE = os.environ.get("PULLBACK_CACHE", "/tmp/wl_bars.pkl")
SEMI = {"5803", "9984", "6981", "6525"}
HOLD = 5


def load_bars(codes):
    if os.path.exists(CACHE):
        return pickle.load(open(CACHE, "rb"))
    h = sd.get_headers()
    b = {c: sd.normalize_columns(sd.fetch_daily_bars(c, h, lookback_days=400))
         for c in codes}
    pickle.dump(b, open(CACHE, "wb"))
    return b


def observations(bars):
    """押し目候補（MA25上向き かつ 終値<MA25）を1日1行にする。"""
    rows = []
    for code, df in bars.items():
        c, h, l = df["close"], df["high"], df["low"]
        tr = pd.concat([h - l, (h - c.shift()).abs(),
                        (l - c.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean() / c * 100
        ma25 = c.rolling(25).mean()
        ma75 = c.rolling(75).mean()
        ref = ma25.shift(20)
        for i in range(len(df) - HOLD - 1):
            if pd.isna(ref.iloc[i]) or pd.isna(ma75.iloc[i]) or pd.isna(atr.iloc[i]):
                continue
            if ma25.iloc[i] <= ref.iloc[i] or c.iloc[i] >= ma25.iloc[i]:
                continue
            px = c.iloc[i]
            gap = (px / ma25.iloc[i] - 1) * 100
            rows.append({
                "code": code, "date": df["date"].iloc[i], "semi": code in SEMI,
                "gap": gap, "n_atr": gap / atr.iloc[i],
                "above75": bool(px > ma75.iloc[i]),
                # 最大逆行(MAE)も見る。押し目買いで効くのは平均より「どこまで踏まれるか」
                "fwd": c.iloc[i + HOLD] / px * 100 - 100,
                "mae": l.iloc[i + 1:i + 1 + HOLD].min() / px * 100 - 100,
            })
    return pd.DataFrame(rows)


def line(label, s):
    if not len(s):
        print(f"  {label:<22} 該当なし")
        return
    print(f"  {label:<22}{len(s):>5}件  {HOLD}日リターン{s['fwd'].mean():>+6.2f}%  "
          f"勝率{(s['fwd'] > 0).mean() * 100:>4.0f}%  最大逆行{s['mae'].mean():>+6.2f}%")


def main():
    wl = pd.read_csv("watchlist.csv", dtype={"code": str})
    d = observations(load_bars(list(wl["code"])))
    print(f"押し目候補 {len(d)}件（{d['code'].nunique()}銘柄 / "
          f"{d['date'].min()}〜{d['date'].max()} / 保有{HOLD}営業日）\n")
    print("■ 現行: MA25上向き かつ 終値<MA25")
    line("全体", d)
    for label, keep in (("終値>MA75 を追加", d["above75"]),
                        ("乖離 -8% 以内を追加", d["gap"] >= -8)):
        print(f"\n■ {label}")
        line("残る（押し目）", d[keep])
        line("外れる（急落）", d[~keep])

    print("\n■ 乖離 -8% 超を、半導体4銘柄とそれ以外に分ける")
    deep = d[d["gap"] < -8]
    line("半導体4銘柄", deep[deep["semi"]])
    line("他の11銘柄", deep[~deep["semi"]])
    print("  → 符号が反転する。深さではなく銘柄で分かれている。")
    print("     半導体分は2026年6〜7月の同一の暴落が相関0.5〜0.7の4銘柄に現れたもので、")
    print("     独立した観測ではない。ルールの根拠にはできない。")

    print("\n■ ATR倍率で正規化しても同じか")
    for label, sub in (("半導体4", d[d["semi"]]), ("他11", d[~d["semi"]])):
        parts = []
        for lo, hi in ((-99, -2), (-2, -1), (-1, 0)):
            s = sub[(sub["n_atr"] >= lo) & (sub["n_atr"] < hi)]
            if len(s):
                parts.append(f"{lo}〜{hi}ATR {len(s):>3}件 {s['fwd'].mean():+.2f}%")
        print(f"  【{label:<5}】" + "   ".join(parts))
    print("  → どの深さでも半導体だけがマイナス。ATR正規化も分離しない。")


if __name__ == "__main__":
    main()
