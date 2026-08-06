"""運用方針に入っていない要素が勝率に効くかを、2つの母集団で確かめる。

広い母集団（条件1+2）で検出力を稼ぎ、狭い母集団（順張り4条件）で
方向が一致するかを見る。両方で同じ向きに出た要素だけを候補にする。
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

R = pd.read_csv(os.path.join(HERE, "features.csv"), dtype={"code": str}, low_memory=False)
R = R.dropna(subset=["f60", "volr"])
rng = np.random.default_rng(7)

WIDE = (R.orb_dev > 0) & (R.vwap_dev > 0)
STRICT = WIDE & (R.volr >= 1.5) & R.c4_all


def boot_win(s, n=1500):
    """勝率の日クラスタ・ブートストラップ95%CI。"""
    g = {d: (x.f60 > 0).values for d, x in s.groupby("day")}
    ks = list(g)
    if len(ks) < 5:
        return (np.nan, np.nan)
    out = [np.concatenate([g[k] for k in rng.choice(ks, len(ks), replace=True)]).mean()
           for _ in range(n)]
    return tuple(np.percentile(out, [2.5, 97.5]))


def show(name, mask, base, label):
    s = R[base & mask]
    t = R[base & ~mask]
    if len(s) < 30 or len(t) < 30:
        print(f"  {name:26s} {label}: サンプル不足 ({len(s)}/{len(t)})")
        return None
    ws, wt = (s.f60 > 0).mean(), (t.f60 > 0).mean()
    lo, hi = boot_win(s)
    print(f"  {name:26s} {label}: 該当 n={len(s):6d} 勝率 {ws*100:5.1f}% "
          f"[{lo*100:4.1f},{hi*100:4.1f}]  / 非該当 n={len(t):6d} 勝率 {wt*100:5.1f}%  "
          f"差 {(ws-wt)*100:+5.1f}pt  f60 {s.f60.mean()*100:+.3f}%")
    return ws - wt


FACTORS = [
    ("その日最初の上抜け", R.first_break),
    ("日経225が前日比プラス", R.idx_chg > 0),
    ("日経225がVWAP上", R.idx_vwap == True),           # noqa: E712
    ("上ギャップで始まった", R.gap > 0),
    ("ORBレンジが狭い(<0.7%)", R.orb_range < 0.007),
    ("ORBレンジが広い(>1.5%)", R.orb_range > 0.015),
    ("パーフェクトオーダー", R.perfect == True),        # noqa: E712
    ("日足MA25の上", R.ma25_pos > 0),
    ("日足MA75の上", R.ma75_pos > 0),
    ("ATRが大きい(>2.5%)", R.atr > 2.5),
    ("当日出来高が20日平均超", R.dvol > 1.0),
    ("前場(11:30まで)", R.mins < 150),
    ("10:00-11:30", (R.mins >= 60) & (R.mins < 150)),
    ("大引け前1時間(14時以降)", R.mins >= 300),
    ("VWAP乖離が小さい(<0.3%)", R.vwap_dev < 0.003),
    ("VWAP乖離が大きい(>1%)", R.vwap_dev > 0.01),
    ("ORB乖離が小さい(<0.5%)", R.orb_dev < 0.005),
    ("当日騰落が+2%未満", R.day_chg < 0.02),
]

print(f"母集団 全バー n={len(R)}  勝率 {(R.f60>0).mean()*100:.1f}%")
print(f"広い母集団（条件1+2） n={WIDE.sum()}  勝率 {(R[WIDE].f60>0).mean()*100:.1f}%")
print(f"狭い母集団（順張り4条件） n={STRICT.sum()}  勝率 {(R[STRICT].f60>0).mean()*100:.1f}%")
print()
res = []
for name, m in FACTORS:
    print(f"[{name}]")
    dw = show(name, m, WIDE, "広")
    ds = show(name, m, STRICT, "狭")
    res.append((name, dw, ds))
    print()

print("=== 両方で同じ向きに出た要素 ===")
for name, dw, ds in res:
    if dw is None or ds is None or np.isnan(dw) or np.isnan(ds):
        continue
    if np.sign(dw) == np.sign(ds) and abs(dw) > 0.01:
        print(f"  {name:26s} 広 {dw*100:+5.1f}pt / 狭 {ds*100:+5.1f}pt")
