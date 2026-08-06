"""同じ日の全銘柄平均を引いた「超過リターン」で比べ直す。

5年の日本株は右肩上がりで、母集団の5日リターンが +0.48% ある。
素の平均で比べると、どの仕掛けも「上がった」ように見えてしまう。
その日の全銘柄平均を引けば、地合いの寄与を落とせる。
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

R = pd.read_csv(os.path.join(HERE, "daily5y.csv"), dtype={"code": str}, low_memory=False)
R["day"] = pd.to_datetime(R["day"]).dt.date
R = R.dropna(subset=["f5", "f10", "ma200", "volr"])
rng = np.random.default_rng(5)

R["rank5"] = R.groupby("day")["chg5"].rank(pct=True)
R["rank20"] = R.groupby("day")["chg20"].rank(pct=True)
for n in [5, 10]:
    R[f"ex{n}"] = R[f"f{n}"] - R.groupby("day")[f"f{n}"].transform("mean")
R["ma25_up"] = R.groupby("code")["ma25"].transform(lambda s: s > s.shift(10))


def boot(s, col, n=1500):
    g = {d: x[col].values for d, x in s.groupby("day")}
    ks = list(g)
    if len(ks) < 30:
        return (np.nan, np.nan)
    o = [np.concatenate([g[k] for k in rng.choice(ks, len(ks), replace=True)]).mean()
         for _ in range(n)]
    return tuple(np.percentile(o, [2.5, 97.5]))


def line(label, m):
    s = R[m]
    if len(s) < 150:
        print(f"  {label:32s} n={len(s):6d} サンプル不足")
        return
    lo, hi = boot(s, "ex5")
    yr = s.assign(y=pd.to_datetime(s.day).dt.year).groupby("y").ex5.mean() * 100
    sign = "".join("+" if x > 0 else "-" for x in yr)
    print(f"  {label:32s} n={len(s):6d}  超過5日 {s.ex5.mean()*100:+.2f}% "
          f"[{lo*100:+.2f},{hi*100:+.2f}]  超過10日 {s.ex10.mean()*100:+.2f}%  "
          f"勝率(超過) {(s.ex5>0).mean()*100:4.1f}%  年別 {sign}")


print(f"n={len(R)} 銘柄{R.code.nunique()} 期間 {R.day.min()}〜{R.day.max()}")
print(f"母集団の素の5日リターン {R.f5.mean()*100:+.2f}%（超過は定義上 0.00%）")
print()
print("--- 順張り系（超過リターン）---")
line("25日高値ブレイク", R.c > R.hi25)
line("25日高値ブレイク＋出来高1.5倍", (R.c > R.hi25) & (R.volr >= 1.5))
line("60日高値ブレイク＋出来高1.5倍", (R.c > R.hi60) & (R.volr >= 1.5))
line("パーフェクトオーダー", (R.ma5 > R.ma25) & (R.ma25 > R.ma75))
line("大陽線(実体2%超)＋出来高2倍", (R.body > 0.02) & (R.volr >= 2))
line("ギャップアップ2%超", R.gap > 0.02)
line("20日騰落率 上位10%", R.rank20 > 0.9)
line("5日騰落率 上位10%", R.rank5 > 0.9)
print()
print("--- 逆張り系（超過リターン）---")
line("RSI30以下", R.rsi <= 30)
line("RSI25以下", R.rsi <= 25)
line("RSI30以下＋MA200上", (R.rsi <= 30) & (R.c > R.ma200))
line("RSI30以下＋MA25が上向き", (R.rsi <= 30) & R.ma25_up)
line("25日安値タッチ", R.l <= R.lo25)
line("25日安値タッチ＋MA200上", (R.l <= R.lo25) & (R.c > R.ma200))
line("5日騰落率 下位10%", R.rank5 < 0.1)
line("5日騰落率 下位10%＋MA200上", (R.rank5 < 0.1) & (R.c > R.ma200))
line("5日騰落率 下位10%＋20日上位50%", (R.rank5 < 0.1) & (R.rank20 > 0.5))
print()
print("--- 禁止事項§4-2の逆（超過リターン）---")
line("RSI70超", R.rsi > 70)
line("RSI70超＋25日高値ブレイク", (R.rsi > 70) & (R.c > R.hi25))
print()
print("--- 保有期間別（RSI30以下）---")
s = R[R.rsi <= 30]
for n in [1, 3, 5, 10]:
    ex = s[f"f{n}"] - R.groupby("day")[f"f{n}"].transform("mean").loc[s.index]
    print(f"  {n:2d}日保有: 超過 {ex.mean()*100:+.2f}%  勝率 {(s[f'f{n}']>0).mean()*100:4.1f}%")
print()
print("--- 決済: 10日以内に -4%/+7% のどちらへ先に届くか ---")
for lab, m in [("母集団", pd.Series(True, index=R.index)),
               ("RSI30以下", R.rsi <= 30),
               ("25日高値＋出来高1.5倍", (R.c > R.hi25) & (R.volr >= 1.5)),
               ("20日騰落率 上位10%", R.rank20 > 0.9)]:
    s = R[m].dropna(subset=["mfe10", "mae10"])
    tp = (s.mfe10 >= 0.07) & (s.mae10 > -0.04)
    sl = (s.mae10 <= -0.04) & (s.mfe10 < 0.07)
    both = (s.mfe10 >= 0.07) & (s.mae10 <= -0.04)
    print(f"  {lab:24s} n={len(s):6d}  +7%のみ {tp.mean()*100:4.1f}%  "
          f"-4%のみ {sl.mean()*100:4.1f}%  両方 {both.mean()*100:4.1f}%  "
          f"未達 {(1-tp.mean()-sl.mean()-both.mean())*100:4.1f}%")
