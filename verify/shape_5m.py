"""ローソク足の形状と、日足移動平均とのバランスを5分足で測る。

日足MAは「前日終値まで」で計算したものを使う。当日の日足MAは引けるまで
確定しないので、場中の判断には使えない。
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
rng = np.random.default_rng(37)

RAW = pd.read_csv(os.path.join(HERE, "monex15_5m.csv"), dtype={"code": str})
RAW["t"] = pd.to_datetime(RAW["t"])
DAY = pd.read_csv(os.path.join(HERE, "monex15.csv"), dtype={"code": str})
DAY["day"] = pd.to_datetime(DAY["day"]).dt.date
E = pd.read_csv(os.path.join(HERE, "monex5m_eval.csv"), dtype={"code": str}, low_memory=False)
E["t"] = pd.to_datetime(E["t"])


def daily_context(g):
    """前日までで確定している日足の指標を、翌営業日に貼るための表を作る。"""
    g = g.sort_values("day").copy()
    c = g.c
    g["d_ma5"], g["d_ma25"], g["d_ma75"] = (c.rolling(5).mean(), c.rolling(25).mean(),
                                           c.rolling(75).mean())
    g["d_perfect"] = (g.d_ma5 > g.d_ma25) & (g.d_ma25 > g.d_ma75)
    g["d_ma25_up"] = g.d_ma25 > g.d_ma25.shift(5)
    tr = pd.concat([g.h - g.l, (g.h - c.shift()).abs(), (g.l - c.shift()).abs()], axis=1).max(axis=1)
    g["d_atr"] = tr.rolling(14).mean()
    # 1営業日ずらす: その日の場中に見えているのは前日までの値
    out = g[["day", "d_ma5", "d_ma25", "d_ma75", "d_perfect", "d_ma25_up", "d_atr"]].copy()
    out[["d_ma5", "d_ma25", "d_ma75", "d_perfect", "d_ma25_up", "d_atr"]] = \
        out[["d_ma5", "d_ma25", "d_ma75", "d_perfect", "d_ma25_up", "d_atr"]].shift(1)
    out["code"] = g.code.iloc[0]
    # 貼り先は「次の営業日」
    out["day"] = list(g.day[1:]) + [None]
    return out.dropna(subset=["day"])


def shape(g):
    g = g.sort_values("t").reset_index(drop=True).copy()
    o, h, l, c, v = g.o, g.h, g.l, g.c, g.v
    rng_bar = (h - l).replace(0, np.nan)
    body = c - o
    g["body_pct"] = body / o * 100                      # 実体の大きさ（符号つき%）
    g["body_ratio"] = body.abs() / rng_bar              # 実体が足全体に占める割合
    g["upper_ratio"] = (h - np.maximum(c, o)) / rng_bar  # 上ヒゲの割合
    g["lower_ratio"] = (np.minimum(c, o) - l) / rng_bar  # 下ヒゲの割合
    g["clv"] = (c - l) / rng_bar                        # 足の中での終値の位置（0=安値,1=高値）
    g["bar_size"] = (h - l) / c * 100                   # 足の値幅（%）
    g["size_rel"] = g.bar_size / g.bar_size.rolling(20).mean()  # 直近20本比
    up = c > o
    g["up3"] = (up & (up.shift(1) == True) & (up.shift(2) == True)).fillna(False)
    # 包み足・はらみ足
    g["engulf"] = (up & (up.shift(1) == False) & (c >= o.shift(1))
                  & (o <= c.shift(1))).fillna(False)
    g["harami"] = ((h <= h.shift(1)) & (l >= l.shift(1))).fillna(False)
    # 分足MAの並び
    g["m_ma5"], g["m_ma25"] = c.rolling(5).mean(), c.rolling(25).mean()
    g["m_spread"] = (g.m_ma5 / g.m_ma25 - 1) * 100
    g["m_spread_up"] = g.m_spread > g.m_spread.shift(3)   # 分足MAの開きが拡大中
    # 当日レンジ内の位置
    out = []
    for _, sub in g.groupby("day"):
        sub = sub.copy()
        hi, lo = sub.h.cummax(), sub.l.cummin()
        sub["day_pos"] = (sub.c - lo) / (hi - lo).replace(0, np.nan)
        out.append(sub)
    return pd.concat(out)


D = pd.concat([shape(g) for _, g in RAW.groupby("code")], ignore_index=True)
DC = pd.concat([daily_context(g) for _, g in DAY.groupby("code")], ignore_index=True)
D["day"] = pd.to_datetime(D["day"]).dt.date
D = D.merge(DC, on=["code", "day"], how="left")
for n, col in [(5, "d_ma5"), (25, "d_ma25"), (75, "d_ma75")]:
    D[f"dev_d{n}"] = D.c / D[col] - 1

COLS = (["code", "t", "body_pct", "body_ratio", "upper_ratio", "lower_ratio", "clv",
         "bar_size", "size_rel", "up3", "engulf", "harami", "m_spread", "m_spread_up",
         "day_pos", "d_perfect", "d_ma25_up", "dev_d5", "dev_d25", "dev_d75"])
X = E.merge(D[COLS], on=["code", "t"], how="left")


def boot(s, col="ex_f60", n=1200):
    g = {d: x[col].values for d, x in s.groupby("day")}
    ks = list(g)
    if len(ks) < 5:
        return (np.nan, np.nan)
    o = [np.concatenate([g[k] for k in rng.choice(ks, len(ks), replace=True)]).mean()
         for _ in range(n)]
    return tuple(np.percentile(o, [2.5, 97.5]))


def line(label, m):
    s = X[m]
    if len(s) < 100:
        print(f"  {label:34s} n={len(s):5d}  少ない")
        return
    lo, hi = boot(s)
    star = "★" if (lo > 0 or hi < 0) else " "
    print(f"{star} {label:34s} n={len(s):5d}  超過60分 {s.ex_f60.mean()*100:+7.3f}% "
          f"[{lo*100:+6.3f},{hi*100:+6.3f}]  勝率 {(s.ex_f60>0).mean()*100:5.1f}%")


def buckets(label, col, edges):
    print(f"\n--- {label} ---")
    x = X.dropna(subset=[col]).copy()
    x["b"] = pd.cut(x[col], edges)
    for b, s in x.groupby("b", observed=True):
        if len(s) < 100:
            continue
        lo, hi = boot(s)
        star = "★" if (lo > 0 or hi < 0) else " "
        print(f"{star} {str(b):16s} n={len(s):5d}  超過60分 {s.ex_f60.mean()*100:+7.3f}% "
              f"[{lo*100:+6.3f},{hi*100:+6.3f}]  勝率 {(s.ex_f60>0).mean()*100:5.1f}%")


if __name__ == "__main__":
    print(f"評価 {len(X)}本 / {X.code.nunique()}銘柄 / {X.day.nunique()}営業日")
    print("★ = 95%CIがゼロを含まない")
    buckets("陽線・陰線の実体の大きさ（%）", "body_pct", [-5, -0.3, -0.1, 0, 0.1, 0.3, 5])
    buckets("実体が足全体に占める割合", "body_ratio", [0, 0.2, 0.4, 0.6, 0.8, 1.0])
    buckets("上ヒゲの割合", "upper_ratio", [0, 0.1, 0.25, 0.5, 1.0])
    buckets("下ヒゲの割合", "lower_ratio", [0, 0.1, 0.25, 0.5, 1.0])
    buckets("終値の位置 CLV（0=安値 1=高値）", "clv", [0, 0.2, 0.4, 0.6, 0.8, 1.0])
    buckets("足の値幅（直近20本比）", "size_rel", [0, 0.5, 1.0, 1.5, 2.5, 100])
    buckets("当日レンジ内の位置", "day_pos", [0, 0.2, 0.4, 0.6, 0.8, 1.0])
    buckets("日足MA25からの乖離（%）", "dev_d25", [-1, -0.05, -0.02, 0, 0.02, 0.05, 1])
    buckets("日足MA5からの乖離（%）", "dev_d5", [-1, -0.03, -0.01, 0, 0.01, 0.03, 1])
    buckets("分足MA5とMA25の開き（%）", "m_spread", [-5, -0.3, -0.1, 0, 0.1, 0.3, 5])
    print("\n--- 形のパターン ---")
    line("陽線3本連続", X.up3 == True)                         # noqa: E712
    line("包み足（陽の包み）", X.engulf == True)                 # noqa: E712
    line("はらみ足", X.harami == True)                          # noqa: E712
    line("大陽線（実体0.3%超・上ヒゲ10%以下）",
         (X.body_pct > 0.3) & (X.upper_ratio <= 0.1))
    line("上ヒゲの長い陽線（実体正・上ヒゲ50%超）",
         (X.body_pct > 0) & (X.upper_ratio > 0.5))
    line("下ヒゲの長い陽線（実体正・下ヒゲ50%超）",
         (X.body_pct > 0) & (X.lower_ratio > 0.5))
    print("\n--- 日足の地合いとの組み合わせ ---")
    line("日足パーフェクトオーダー", X.d_perfect == True)         # noqa: E712
    line("日足MA25が上向き", X.d_ma25_up == True)                # noqa: E712
    line("日足MA25の上（乖離+）", X.dev_d25 > 0)
    line("日足MA25の上＋RSI70以上", (X.dev_d25 > 0) & (X.rsi >= 70))
    line("日足MA25の下＋RSI70以上", (X.dev_d25 <= 0) & (X.rsi >= 70))
    line("RSI70以上＋CLV0.8超", (X.rsi >= 70) & (X.clv > 0.8))
    line("RSI70以上＋上ヒゲ25%以下", (X.rsi >= 70) & (X.upper_ratio <= 0.25))
    line("RSI70以上＋上ヒゲ50%超", (X.rsi >= 70) & (X.upper_ratio > 0.5))
    X.to_csv(os.path.join(HERE, "shape_5m.csv"), index=False)
