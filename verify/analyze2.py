"""候補要素の重ね掛けと、決済側（利確・損切りの当たり順）の確認。"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

R = pd.read_csv(os.path.join(HERE, "features.csv"), dtype={"code": str}, low_memory=False)
I = pd.read_csv(os.path.join(HERE, "idx.csv"))
I["day"] = pd.to_datetime(I["day"]).dt.date
R["day"] = pd.to_datetime(R["day"]).dt.date
R = R.drop(columns=["idx_chg", "idx_vwap"]).merge(I, on=["day", "hm"], how="left")
for c in ["idx_above_avg", "idx_above_open", "perfect", "first_break", "c4_all"]:
    R[c] = R[c].astype(str).str.lower().map({"true": True, "false": False})
R = R.dropna(subset=["f60", "volr"])
rng = np.random.default_rng(11)

WIDE = (R.orb_dev > 0) & (R.vwap_dev > 0)
STRICT = WIDE & (R.volr >= 1.5) & R.c4_all


def bwin(s, n=2000):
    g = {d: (x.f60 > 0).values for d, x in s.groupby("day")}
    ks = list(g)
    if len(ks) < 5:
        return (np.nan, np.nan)
    o = [np.concatenate([g[k] for k in rng.choice(ks, len(ks), replace=True)]).mean()
         for _ in range(n)]
    return tuple(np.percentile(o, [2.5, 97.5]))


def line(label, m):
    s = R[m]
    if len(s) < 25:
        print(f"  {label:38s} n={len(s):5d} サンプル不足")
        return
    lo, hi = bwin(s)
    sd = s.groupby(["code", "day"]).ngroups
    print(f"  {label:38s} n={len(s):5d} 銘柄日{sd:4d}  勝率 {(s.f60>0).mean()*100:5.1f}% "
          f"[{lo*100:4.1f},{hi*100:4.1f}]  f60 {s.f60.mean()*100:+.3f}%  "
          f"引け {s.feod.mean()*100:+.3f}%")


print("指数VWAPの取り違えを直した再測定")
line("広い母集団（条件1+2）", WIDE)
line("  ＋日経225が当日平均より上", WIDE & R.idx_above_avg)
line("  ＋日経225が当日平均より下", WIDE & (R.idx_above_avg == False))
line("  ＋日経225が前日比プラス", WIDE & (R.idx_chg > 0))
line("  ＋日経225が寄り値より上", WIDE & (R.idx_above_open == True))
print()
print("順張り4条件に、候補要素を1つずつ足す")
line("順張り4条件のみ", STRICT)
for lab, m in [("上ギャップで始まった", R.gap > 0),
               ("当日出来高が20日平均超", R.dvol > 1.0),
               ("14時以降を除く", R.mins < 300),
               ("日経225が当日平均より上", R.idx_above_avg),
               ("ORB乖離0.5%以内", R.orb_dev < 0.005)]:
    line(f"  ＋{lab}", STRICT & m)
print()
print("重ね掛け")
g = R.gap > 0
v = R.dvol > 1.0
t = R.mins < 300
line("4条件＋上ギャップ＋出来高20日超", STRICT & g & v)
line("4条件＋上ギャップ＋出来高20日超＋14時前", STRICT & g & v & t)
line("同上＋ORB乖離0.5%以内", STRICT & g & v & t & (R.orb_dev < 0.005))
print()
print("=== 決済側: 60分以内にどちらが先に当たるか（成行想定・コスト無視）===")
for lab, base in [("順張り4条件", STRICT), ("4条件＋上ギャップ＋出来高20日超", STRICT & g & v)]:
    s = R[base].dropna(subset=["mfe60", "mae60"])
    for tp, sl in [(0.005, 0.005), (0.01, 0.005), (0.01, 0.01), (0.02, 0.01)]:
        hit = (s.mfe60 >= tp) & (s.mae60 > -sl)      # 利確だけ到達
        loss = (s.mae60 <= -sl) & (s.mfe60 < tp)     # 損切りだけ到達
        both = (s.mfe60 >= tp) & (s.mae60 <= -sl)    # 順序不明
        print(f"  {lab:30s} +{tp*100:.1f}%/-{sl*100:.1f}%  "
              f"利確のみ {hit.mean()*100:4.1f}%  損切りのみ {loss.mean()*100:4.1f}%  "
              f"両方到達(順序不明) {both.mean()*100:4.1f}%")
    print()
