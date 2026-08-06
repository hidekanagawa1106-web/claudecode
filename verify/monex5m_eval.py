"""マネックスの解説8本のテクニカルを、対象15銘柄の5分足で検証する。

日足版(monex_eval.py)と同じシグナル定義を、単位を「日」から「本(5分足)」に
読み替えて適用する。移動平均25は25本＝約2時間、RSI14は14本＝70分。

執行の前提: あるバーの終値でシグナルを判定し、次のバーの寄りで約定、
30分後(6本)・60分後(12本)・その日の引けで決済する。持ち越しはしない。
60分後リターンが当日内に収まるよう、引けまで12本以上残るバーだけを評価する。

移動平均やRSIは日をまたいで連続で計算する（チャートソフトの既定と同じ）。
そのため寄り付き直後のバーは、前日の値を含んだ窓で判定している。
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
rng = np.random.default_rng(23)


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def build(g, gran4_th):
    g = g.sort_values("t").reset_index(drop=True).copy()
    c, h, l, o, v = g.c, g.h, g.l, g.o, g.v
    g["ma5"], g["ma25"], g["ma75"] = c.rolling(5).mean(), c.rolling(25).mean(), c.rolling(75).mean()
    g["ma25_up"] = g.ma25 > g.ma25.shift(5)
    g["dev25"] = c / g.ma25 - 1
    # 001 移動平均線
    g["gc"] = (g.ma5 > g.ma25) & (g.ma5.shift(1) <= g.ma25.shift(1))
    g["gran1"] = g.ma25_up & (c > g.ma25) & (c.shift(1) <= g.ma25.shift(1))
    g["gran2"] = g.ma25_up & (c < g.ma25)
    g["gran4"] = (~g.ma25_up) & (g.dev25 <= gran4_th)
    # 003 ボリンジャーバンド(20本, 2σ)
    m20, s20 = c.rolling(20).mean(), c.rolling(20).std()
    g["bb_up2"], g["bb_lo2"], g["bb_up1"] = m20 + 2 * s20, m20 - 2 * s20, m20 + s20
    g["bb_break"] = (c > g.bb_up2) & (c.shift(1) <= g.bb_up2.shift(1))
    g["bb_rev"] = c < g.bb_lo2
    g["bb_walk"] = (c > g.bb_up1) & (c.shift(1) > g.bb_up1.shift(1)) & (c.shift(2) > g.bb_up1.shift(2))
    width = 4 * s20 / m20
    g["bb_squeeze"] = (width <= width.rolling(60).quantile(0.20)) & (c > g.bb_up2)
    # 005 RSI(14本)
    g["rsi"] = rsi(c)
    g["rsi_low"] = g.rsi <= 30
    g["rsi_high"] = g.rsi >= 70
    g["rsi_diver"] = (l <= l.rolling(20).min()) & (g.rsi > g.rsi.rolling(20).min() + 3)
    # 008 ダブルボトム
    lo_recent, lo_prev = l.rolling(10).min(), l.shift(10).rolling(30).min()
    neck = h.shift(1).rolling(30).max()
    g["dbl_bottom"] = ((lo_recent / lo_prev - 1).abs() <= 0.01) & (c > neck) & (c.shift(1) <= neck.shift(1))
    # 009 逆三尊
    l1, l2, l3 = l.shift(40).rolling(20).min(), l.shift(20).rolling(20).min(), l.rolling(20).min()
    neck3 = h.shift(1).rolling(40).max()
    g["hs_bottom"] = ((l2 < l1) & (l2 < l3) & ((l3 / l1 - 1).abs() <= 0.02)
                      & (c > neck3) & (c.shift(1) <= neck3.shift(1)))
    # 010 三角保合い
    rng20 = ((h.rolling(20).max() - l.rolling(20).min()) / c).shift(1)
    hi20 = h.shift(1).rolling(20).max()
    g["triangle"] = ((rng20 <= rng20.rolling(120).quantile(0.25))
                     & (h.shift(1).rolling(10).max() < h.shift(11).rolling(10).max())
                     & (l.shift(1).rolling(10).min() > l.shift(11).rolling(10).min())
                     & (c > hi20) & (c.shift(1) <= hi20.shift(1)))
    # 020 ソーサーボトム
    vol20 = c.pct_change().rolling(20).std().shift(1)
    g["saucer"] = ((c / c.shift(60) - 1 < -0.01)
                   & (vol20 <= vol20.rolling(120).quantile(0.30))
                   & (c > hi20) & (c.shift(1) <= hi20.shift(1)))
    # 013 酒田五法
    up = c > o
    g["sanpei"] = up & up.shift(1) & up.shift(2) & (c > c.shift(1)) & (c.shift(1) > c.shift(2))
    g["sanku"] = (l > h.shift(1)) & (l.shift(1) > h.shift(2)) & (l.shift(2) > h.shift(3))
    rng5 = (h.rolling(5).max() - l.rolling(5).min()) / c
    g["sanpo"] = (rng5.shift(1) <= 0.005) & (c > h.shift(1).rolling(5).max())

    # --- 結果: 次のバーの寄りで約定し、当日内で決済する ---
    g["entry"] = o.shift(-1)
    g["f30"] = np.nan
    g["f60"] = np.nan
    g["feod"] = np.nan
    g["mfe60"] = np.nan
    g["mae60"] = np.nan
    g["bars_left"] = 0
    for day, idx in g.groupby("day").groups.items():
        idx = list(idx)
        sub = g.loc[idx]
        n = len(idx)
        for k, i in enumerate(idx):
            left = n - 1 - k
            g.at[i, "bars_left"] = left
            e = g.at[i, "entry"]
            if left < 1 or not np.isfinite(e):
                continue
            j30, j60 = idx[min(k + 6, n - 1)], idx[min(k + 12, n - 1)]
            g.at[i, "f30"] = g.at[j30, "c"] / e - 1
            g.at[i, "f60"] = g.at[j60, "c"] / e - 1
            g.at[i, "feod"] = g.at[idx[-1], "c"] / e - 1
            win = idx[k + 1:k + 13]
            if win:
                g.at[i, "mfe60"] = sub.loc[win, "h"].max() / e - 1
                g.at[i, "mae60"] = sub.loc[win, "l"].min() / e - 1
    return g


SIGNALS = [
    ("001 ゴールデンクロス(5/25本)", "gc"),
    ("001 グランビル1 MA上抜け", "gran1"),
    ("001 グランビル2 上昇MAへの押し目", "gran2"),
    ("001 グランビル4 下降MAから大幅下方乖離", "gran4"),
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
    if len(ks) < 5:
        return (np.nan, np.nan)
    o = [np.concatenate([g[k] for k in rng.choice(ks, len(ks), replace=True)]).mean()
         for _ in range(n)]
    return tuple(np.percentile(o, [2.5, 97.5]))


if __name__ == "__main__":
    R = pd.read_csv(os.path.join(HERE, "monex15_5m.csv"), dtype={"code": str})
    R["t"] = pd.to_datetime(R["t"])
    # グランビル4 の「大きく下方乖離」は日足の-10%をそのまま使えない。
    # 5分足の乖離分布の下位1%を閾値にする。
    tmp = []
    for _, g in R.groupby("code"):
        g = g.sort_values("t")
        tmp.append(g.c / g.c.rolling(25).mean() - 1)
    th = float(pd.concat(tmp).quantile(0.01))
    print(f"グランビル4の閾値（5分足の乖離分布の下位1%）: {th*100:.2f}%")

    R = pd.concat([build(g, th) for _, g in R.groupby("code")], ignore_index=True)
    auction = ((R.hm <= "09:05") | ((R.hm >= "12:30") & (R.hm <= "12:35")) | (R.hm >= "14:55"))
    E = R[(~auction) & (R.bars_left >= 12)].dropna(subset=["f60"]).copy()
    for col in ["f30", "f60", "feod"]:
        E[f"ex_{col}"] = E[col] - E.groupby("t")[col].transform("mean")

    print(f"\n対象 {E.code.nunique()}銘柄 / 評価 {len(E)}本 / {E.day.nunique()}営業日 "
          f"({E.day.min()}〜{E.day.max()})")
    print(f"母集団の素の60分後リターン {E.f60.mean()*100:+.3f}%  勝率 {(E.f60>0).mean()*100:.1f}%")
    print()
    print(f"{'シグナル':32s} {'n':>5s} {'超過30分':>8s} {'超過60分':>8s} {'95%CI(60分)':>18s} {'超過引け':>8s} {'勝率':>6s}")
    for label, col in SIGNALS:
        s = E[E[col] == True]      # noqa: E712
        if len(s) < 20:
            print(f"{label:32s} {len(s):5d}  発生が少なく判定不能")
            continue
        lo, hi = boot(s, "ex_f60")
        print(f"{label:32s} {len(s):5d} {s.ex_f30.mean()*100:+7.3f}% {s.ex_f60.mean()*100:+7.3f}% "
              f"[{lo*100:+6.3f},{hi*100:+6.3f}] {s.ex_feod.mean()*100:+7.3f}% "
              f"{(s.ex_f60>0).mean()*100:5.1f}%")
    E.to_csv(os.path.join(HERE, "monex5m_eval.csv"), index=False)
