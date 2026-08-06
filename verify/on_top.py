"""「RSI70以上 かつ 日足MA25の上」を前提に、そこへ何を足すと変わるかを総当たりする。

前提条件そのものが n=1,107 / 22営業日しかないので、足すほど薄くなる。
n と、銘柄別・日別に符号がそろっているかを必ず併記する。
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
rng = np.random.default_rng(41)

X = pd.read_csv(os.path.join(HERE, "shape_5m.csv"), dtype={"code": str}, low_memory=False)
X["t"] = pd.to_datetime(X["t"])
C = pd.read_csv(os.path.join(HERE, "rule_cond_5m.csv"), dtype={"code": str}, low_memory=False)
C["t"] = pd.to_datetime(C["t"])
X = X.merge(C[["code", "t", "c1_orb", "c2_vwap", "c3_vol", "k3_vol", "k4_rebound"]],
            on=["code", "t"], how="left")
I = pd.read_csv(os.path.join(HERE, "idx.csv"))
I["day"] = pd.to_datetime(I["day"]).dt.date
X["day"] = pd.to_datetime(X["day"]).dt.date
X = X.merge(I, on=["day", "hm"], how="left")
for c in ["c1_orb", "c2_vwap", "c3_vol", "k3_vol", "k4_rebound"]:
    X[c] = X[c].fillna(False).astype(bool)

BASE = (X.rsi >= 70) & (X.dev_d25 > 0)
X["mins"] = (X.hm.str[:2].astype(int) - 9) * 60 + X.hm.str[3:].astype(int)


def boot(s, col="ex_f60", n=1200):
    g = {d: x[col].values for d, x in s.groupby("day")}
    ks = list(g)
    if len(ks) < 5:
        return (np.nan, np.nan)
    o = [np.concatenate([g[k] for k in rng.choice(ks, len(ks), replace=True)]).mean()
         for _ in range(n)]
    return tuple(np.percentile(o, [2.5, 97.5]))


def line(label, m, minimum=60):
    s = X[BASE & m]
    if len(s) < minimum:
        print(f"   {label:32s} n={len(s):4d}  少ない")
        return
    lo, hi = boot(s)
    bycode = s.groupby("code").ex_f60.mean()
    byday = s.groupby("day").ex_f60.mean()
    star = "★" if (lo > 0 or hi < 0) else " "
    print(f"{star}  {label:32s} n={len(s):4d}  超過60分 {s.ex_f60.mean()*100:+7.3f}% "
          f"[{lo*100:+6.3f},{hi*100:+6.3f}]  勝率 {(s.ex_f60>0).mean()*100:5.1f}%  "
          f"銘柄 {(bycode>0).sum()}/{len(bycode)}  日 {(byday>0).sum()}/{len(byday)}")


ADDONS = [
    ("── マネックスの指標 ──", None),
    ("BB +2σ上抜け", X.bb_break == True),                      # noqa: E712
    ("BB バンドウォーク", X.bb_walk == True),                    # noqa: E712
    ("BB スクイーズ→上放れ", X.bb_squeeze == True),               # noqa: E712
    ("ゴールデンクロス(5/25本)", X.gc == True),                   # noqa: E712
    ("グランビル1 MA上抜け", X.gran1 == True),                    # noqa: E712
    ("三法(保合い上放れ)", X.sanpo == True),                      # noqa: E712
    ("赤三兵", X.sanpei == True),                               # noqa: E712
    ("ダブルボトム", X.dbl_bottom == True),                      # noqa: E712
    ("逆三尊", X.hs_bottom == True),                            # noqa: E712
    ("── 運用方針の条件 ──", None),
    ("ORB上抜け", X.c1_orb),
    ("VWAP上", X.c2_vwap),
    ("出来高1.5倍(当日平均比)", X.c3_vol),
    ("出来高1.5倍(20本平均比)", X.k3_vol),
    ("反発の陽線1本確定", X.k4_rebound),
    ("── 位置 ──", None),
    ("日足MA5の上", X.dev_d5 > 0),
    ("日足MA5から+3%以上", X.dev_d5 > 0.03),
    ("日足MA75の上", X.dev_d75 > 0),
    ("日足MA25から+5%以上", X.dev_d25 > 0.05),
    ("日足MA25から0〜+2%", (X.dev_d25 > 0) & (X.dev_d25 <= 0.02)),
    ("当日レンジ上位20%", X.day_pos > 0.8),
    ("当日レンジ下位40%", X.day_pos < 0.4),
    ("分足MA5>MA25の開きが+0.3%超", X.m_spread > 0.3),
    ("── 形 ──", None),
    ("陽線", X.body_pct > 0),
    ("小さい足(直近20本比1倍未満)", X.size_rel < 1.0),
    ("大きい足(直近20本比1.5倍超)", X.size_rel > 1.5),
    ("上ヒゲ25%以下", X.upper_ratio <= 0.25),
    ("下ヒゲ25%以下", X.lower_ratio <= 0.25),
    ("CLV0.6超", X.clv > 0.6),
    ("── 時間・地合い ──", None),
    ("前場(11:30まで)", X.mins < 150),
    ("9時台のみ", X.mins < 60),
    ("後場(12:30以降)", X.mins >= 210),
    ("日経225が前日比プラス", X.idx_chg > 0),
    ("日経225が当日平均より上", X.idx_above_avg == True),          # noqa: E712
    ("── RSIの水準 ──", None),
    ("RSI70〜80", X.rsi < 80),
    ("RSI80以上", X.rsi >= 80),
]

if __name__ == "__main__":
    print("前提: RSI(14本)70以上 かつ 前日までの日足MA25の上")
    line("前提のみ", pd.Series(True, index=X.index))
    print("\n★ = 95%CIがゼロを含まない／銘柄・日は超過がプラスだった数")
    for label, m in ADDONS:
        if m is None:
            print(f"\n{label}")
            continue
        line(label, m)
