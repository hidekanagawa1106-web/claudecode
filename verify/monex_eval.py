"""マネックスの解説8本のテクニカルを、対象15銘柄の日足で検証する。

判定はすべて当日終値まででおこない、翌日の寄りで約定、n日後の終値で決済する。
チャート形状のパターン（ダブルボトム・逆三尊・三角保合い・ソーサー・酒田五法）は
数値ルールに置き換えた近似で、定義はこのファイルに書いたとおり。
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
R = pd.read_csv(os.path.join(HERE, "monex15.csv"), dtype={"code": str})
R["day"] = pd.to_datetime(R["day"]).dt.date
R = R.sort_values(["code", "day"]).reset_index(drop=True)
rng = np.random.default_rng(17)


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def build(g):
    g = g.copy()
    c, h, l, o, v = g.c, g.h, g.l, g.o, g.v
    g["ma5"], g["ma25"], g["ma75"] = c.rolling(5).mean(), c.rolling(25).mean(), c.rolling(75).mean()
    g["ma25_up"] = g.ma25 > g.ma25.shift(5)
    g["dev25"] = c / g.ma25 - 1
    # --- 001 移動平均線 ---
    g["gc"] = (g.ma5 > g.ma25) & (g.ma5.shift(1) <= g.ma25.shift(1))
    g["gran1"] = g.ma25_up & (c > g.ma25) & (c.shift(1) <= g.ma25.shift(1))
    g["gran2"] = g.ma25_up & (c < g.ma25)                      # 上昇MAへの押し目
    g["gran4"] = (~g.ma25_up) & (g.dev25 <= -0.10)              # 下降MAから大きく下方乖離
    # --- 003 ボリンジャーバンド(20, 2σ) ---
    m20, s20 = c.rolling(20).mean(), c.rolling(20).std()
    g["bb_up2"], g["bb_lo2"], g["bb_up1"] = m20 + 2 * s20, m20 - 2 * s20, m20 + s20
    g["bb_break"] = (c > g.bb_up2) & (c.shift(1) <= g.bb_up2.shift(1))
    g["bb_rev"] = c < g.bb_lo2
    g["bb_walk"] = (c > g.bb_up1) & (c.shift(1) > g.bb_up1.shift(1)) & (c.shift(2) > g.bb_up1.shift(2))
    width = 4 * s20 / m20
    g["bb_squeeze"] = (width <= width.rolling(60).quantile(0.20)) & (c > g.bb_up2)
    # --- 005 RSI(14) ---
    g["rsi"] = rsi(c)
    g["rsi_low"] = g.rsi <= 30
    g["rsi_high"] = g.rsi >= 70
    lo20 = l.rolling(20).min()
    g["rsi_diver"] = (l <= lo20) & (g.rsi > g.rsi.rolling(20).min() + 3)   # 強気ダイバージェンス
    # --- 008 ダブルボトム（近似）---
    # 直近10日の安値と、その前(11〜40日前)の安値が3%以内で並び、
    # 間の高値（ネックライン）を当日終値が上抜けた日
    lo_recent = l.rolling(10).min()
    lo_prev = l.shift(10).rolling(30).min()
    neck = h.shift(1).rolling(30).max()
    g["dbl_bottom"] = ((lo_recent / lo_prev - 1).abs() <= 0.03) & (c > neck) & (c.shift(1) <= neck.shift(1))
    # --- 009 逆三尊（近似）---
    # 60日を3分割し、中央区間の安値が最も低く、両肩が5%以内、ネックライン上抜け
    l1 = l.shift(40).rolling(20).min()
    l2 = l.shift(20).rolling(20).min()
    l3 = l.rolling(20).min()
    neck3 = h.shift(1).rolling(40).max()
    g["hs_bottom"] = ((l2 < l1) & (l2 < l3) & ((l3 / l1 - 1).abs() <= 0.05)
                      & (c > neck3) & (c.shift(1) <= neck3.shift(1)))
    # --- 010 三角保合い（近似）---
    # 20日レンジ幅が過去120日の下位25%で、高値切り下げ・安値切り上げ、当日20日高値を上抜け
    # レンジ幅は「前日まで」で測る。ブレイク当日を含めると幅が広がって自己矛盾する。
    rng20 = ((h.rolling(20).max() - l.rolling(20).min()) / c).shift(1)
    hi20 = h.shift(1).rolling(20).max()
    g["triangle"] = ((rng20 <= rng20.rolling(120).quantile(0.25))
                     & (h.shift(1).rolling(10).max() < h.shift(11).rolling(10).max())
                     & (l.shift(1).rolling(10).min() > l.shift(11).rolling(10).min())
                     & (c > hi20) & (c.shift(1) <= hi20.shift(1)))
    # --- 020 ソーサーボトム（近似）---
    # 60日前比マイナス、直近20日の変動が過去120日の下位30%、20日高値を上抜け
    vol20 = c.pct_change().rolling(20).std().shift(1)
    g["saucer"] = ((c / c.shift(60) - 1 < -0.05)
                   & (vol20 <= vol20.rolling(120).quantile(0.30))
                   & (c > hi20) & (c.shift(1) <= hi20.shift(1)))
    # --- 013 酒田五法 ---
    up = c > o
    g["sanpei"] = up & up.shift(1) & up.shift(2) & (c > c.shift(1)) & (c.shift(1) > c.shift(2))
    g["sanku"] = (l > h.shift(1)) & (l.shift(1) > h.shift(2)) & (l.shift(2) > h.shift(3))
    rng5 = (h.rolling(5).max() - l.rolling(5).min()) / c
    g["sanpo"] = (rng5.shift(1) <= 0.03) & (c > h.shift(1).rolling(5).max())
    # --- 結果（翌日寄り→n日後の終値）---
    entry = o.shift(-1)
    for n in [1, 3, 5, 10]:
        g[f"f{n}"] = c.shift(-n) / entry - 1
    fh = pd.concat([h.shift(-(i + 1)) for i in range(10)], axis=1).max(axis=1)
    fl = pd.concat([l.shift(-(i + 1)) for i in range(10)], axis=1).min(axis=1)
    g["mfe10"], g["mae10"] = fh / entry - 1, fl / entry - 1
    return g


R = pd.concat([build(g) for _, g in R.groupby("code")], ignore_index=True)
for n in [1, 3, 5, 10]:
    R[f"ex{n}"] = R[f"f{n}"] - R.groupby("day")[f"f{n}"].transform("mean")

SIGNALS = [
    ("001 ゴールデンクロス(5/25)", "gc"),
    ("001 グランビル1 MA上抜け", "gran1"),
    ("001 グランビル2 上昇MAへの押し目", "gran2"),
    ("001 グランビル4 下降MAから-10%乖離", "gran4"),
    ("003 BB +2σ上抜け(順張り)", "bb_break"),
    ("003 BB -2σ割れ(逆張り)", "bb_rev"),
    ("003 BB バンドウォーク", "bb_walk"),
    ("003 BB スクイーズ→上放れ", "bb_squeeze"),
    ("005 RSI30以下", "rsi_low"),
    ("005 RSI70以上", "rsi_high"),
    ("005 RSI強気ダイバージェンス", "rsi_diver"),
    ("008 ダブルボトム", "dbl_bottom"),
    ("009 逆三尊", "hs_bottom"),
    ("010 三角保合い上放れ", "triangle"),
    ("020 ソーサーボトム", "saucer"),
    ("013 赤三兵", "sanpei"),
    ("013 三空踏み上げ", "sanku"),
    ("013 三法(保合い上放れ)", "sanpo"),
]


def boot(s, col, n=1500):
    g = {d: x[col].values for d, x in s.groupby("day")}
    ks = list(g)
    if len(ks) < 20:
        return (np.nan, np.nan)
    o = [np.concatenate([g[k] for k in rng.choice(ks, len(ks), replace=True)]).mean()
         for _ in range(n)]
    return tuple(np.percentile(o, [2.5, 97.5]))


if __name__ == "__main__":
    base = R.dropna(subset=["ex5"])
    print(f"対象 {base.code.nunique()}銘柄 / {len(base)}銘柄日 / "
          f"{base.day.min()}〜{base.day.max()}")
    print(f"母集団の素の5日リターン {base.f5.mean()*100:+.2f}%（超過は定義上0）")
    print()
    print(f"{'シグナル':34s} {'n':>5s} {'超過5日':>8s} {'95%CI':>18s} {'超過10日':>8s} {'勝率':>6s} 年別")
    rows = []
    for label, col in SIGNALS:
        s = base[base[col] == True]        # noqa: E712
        if len(s) < 20:
            print(f"{label:34s} {len(s):5d}  発生が少なく判定不能")
            continue
        lo, hi = boot(s, "ex5")
        yr = s.assign(y=pd.to_datetime(s.day).dt.year).groupby("y").ex5.mean()
        sign = "".join("+" if x > 0 else "-" for x in yr)
        print(f"{label:34s} {len(s):5d} {s.ex5.mean()*100:+7.2f}% "
              f"[{lo*100:+6.2f},{hi*100:+6.2f}] {s.ex10.mean()*100:+7.2f}% "
              f"{(s.ex5>0).mean()*100:5.1f}% {sign}")
        rows.append((label, len(s), s.ex5.mean(), lo, hi, s.ex10.mean()))
    print()
    print("=== 15銘柄の現在（2026-08-05終値時点）のシグナル ===")
    last = R.sort_values("day").groupby("code").tail(1)
    for _, r in last.sort_values("code").iterrows():
        on = [lab for lab, col in SIGNALS if r.get(col) == True]   # noqa: E712
        print(f"{r['code']} {r['name']:16s} RSI {r['rsi']:5.1f}  MA25乖離 {r['dev25']*100:+5.1f}%  "
              f"{'／'.join(on) if on else '（点灯なし）'}")
