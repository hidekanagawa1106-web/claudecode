"""運用方針の順張り4条件・逆張り4条件を5分足で計算し、単独効果と
「RSI70以上の状態の中での効果」を分けて測る。

条件4（連動銘柄が同方向）は、対象15銘柄のうち同時刻に前日比プラスの
割合が過半かどうかで代用する（15銘柄はセクターがばらけているため、
driver_map の連動グループをそのまま使えない）。
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
rng = np.random.default_rng(29)

RAW = pd.read_csv(os.path.join(HERE, "monex15_5m.csv"), dtype={"code": str})
RAW["t"] = pd.to_datetime(RAW["t"])
E = pd.read_csv(os.path.join(HERE, "monex5m_eval.csv"), dtype={"code": str}, low_memory=False)
E["t"] = pd.to_datetime(E["t"])


def per_code(g):
    g = g.sort_values("t").reset_index(drop=True).copy()
    c, h, l, o, v = g.c, g.h, g.l, g.o, g.v
    g["auction"] = ((g.hm <= "09:05") | ((g.hm >= "12:30") & (g.hm <= "12:35")) | (g.hm >= "14:55"))
    out = []
    prev_close = None
    for day, sub in g.groupby("day"):
        sub = sub.copy()
        orb = sub[sub.hm <= "09:10"]
        sub["orb_high"] = orb.h.max() if len(orb) else np.nan
        tp = (sub.h + sub.l + sub.c) / 3
        sub["vwap"] = (tp * sub.v).cumsum() / sub.v.cumsum()
        nv = sub.v.where(~sub.auction)
        sub["avgv"] = nv.expanding().mean().shift(1)
        sub["prev_close"] = prev_close if prev_close is not None else np.nan
        prev_close = sub.c.iloc[-1]
        out.append(sub)
    g = pd.concat(out).sort_values("t").reset_index(drop=True)
    # 順張り4条件（§3-1）
    g["c1_orb"] = g.c > g.orb_high
    g["c2_vwap"] = g.c > g.vwap
    g["c3_vol"] = g.v >= 1.5 * g.avgv
    # 逆張り4条件（§3-2）
    m20, s20 = g.c.rolling(20).mean(), g.c.rolling(20).std()
    g["k1_bb"] = g.l <= (m20 - 2 * s20)
    g["k3_vol"] = g.v >= 1.5 * g.v.rolling(20).mean()
    body = g.c - g.o
    prev_body = g.o.shift(1) - g.c.shift(1)          # 直前が陰線なら正
    upper = g.h - g[["c", "o"]].max(axis=1)
    g["k4_rebound"] = ((body > 0) & (prev_body > 0) & (body >= prev_body * 0.70)
                       & (g.v >= g.v.shift(1)) & (upper <= body * 0.30))
    g["up_on_day"] = (g.c > g.prev_close).fillna(False)
    return g


RAW = pd.concat([per_code(g) for _, g in RAW.groupby("code")], ignore_index=True)
# 条件4の代用: 同時刻に前日比プラスの銘柄が過半
RAW["c4_breadth"] = RAW.groupby("t")["up_on_day"].transform("mean") > 0.5

COLS = ["code", "t", "c1_orb", "c2_vwap", "c3_vol", "k1_bb", "k3_vol", "k4_rebound", "c4_breadth"]
D = E.merge(RAW[COLS], on=["code", "t"], how="left")
for c in ["c1_orb", "c2_vwap", "c3_vol", "k1_bb", "k3_vol", "k4_rebound", "c4_breadth"]:
    D[c] = D[c].fillna(False).astype(bool)
D["k2_rsi"] = D.rsi <= 30
D["hi_rsi"] = D.rsi >= 70


def boot(s, col="ex_f60", n=1500):
    g = {d: x[col].values for d, x in s.groupby("day")}
    ks = list(g)
    if len(ks) < 5:
        return (np.nan, np.nan)
    o = [np.concatenate([g[k] for k in rng.choice(ks, len(ks), replace=True)]).mean()
         for _ in range(n)]
    return tuple(np.percentile(o, [2.5, 97.5]))


def line(label, m, indent=""):
    s = D[m]
    if len(s) < 30:
        print(f"{indent}{label:34s} n={len(s):5d}  少ない")
        return
    lo, hi = boot(s)
    print(f"{indent}{label:34s} n={len(s):5d}  超過60分 {s.ex_f60.mean()*100:+7.3f}% "
          f"[{lo*100:+6.3f},{hi*100:+6.3f}]  勝率 {(s.ex_f60>0).mean()*100:5.1f}%")


CONDS = [
    ("順1 ORB上抜け", D.c1_orb),
    ("順2 VWAP上", D.c2_vwap),
    ("順3 出来高1.5倍(当日平均比)", D.c3_vol),
    ("順4 連動(過半が前日比プラス)", D.c4_breadth),
    ("逆1 BB -2σタッチ", D.k1_bb),
    ("逆2 RSI30以下", D.k2_rsi),
    ("逆3 出来高急増(20本平均比)", D.k3_vol),
    ("逆4 反発の陽線1本確定", D.k4_rebound),
]

if __name__ == "__main__":
    print(f"評価 {len(D)}本 / {D.code.nunique()}銘柄 / {D.day.nunique()}営業日")
    line("母集団（全バー）", pd.Series(True, index=D.index))
    print()
    print("=== ① 各条件の単独効果 ===")
    for lab, m in CONDS:
        line(lab, m)
    print()
    print("=== ② 条件をそろえた場合 ===")
    line("順張り4条件すべて", D.c1_orb & D.c2_vwap & D.c3_vol & D.c4_breadth)
    line("順張り 1+2+3", D.c1_orb & D.c2_vwap & D.c3_vol)
    line("逆張り4条件すべて", D.k1_bb & D.k2_rsi & D.k3_vol & D.k4_rebound)
    line("逆張り 1+2", D.k1_bb & D.k2_rsi)
    print()
    print("=== ③ RSI70以上を土台に、各条件を足す ===")
    base = D.hi_rsi
    line("RSI70以上のみ", base)
    for lab, m in CONDS:
        if lab.startswith("逆2"):
            continue
        line(f"  ＋{lab}", base & m, indent="")
    print()
    print("=== ④ 逆に、各条件にRSI70以上を足す ===")
    for lab, m in CONDS[:4]:
        line(f"{lab}のみ", m)
        line(f"  ＋RSI70以上", m & base)
    D.to_csv(os.path.join(HERE, "rule_cond_5m.csv"), index=False)
