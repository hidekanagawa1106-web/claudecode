"""朝スコアの不感帯を較正し、効果を測る。

    python tools/deadzone_calibrate.py

不感帯が「同じ朝に2回実行したら結果が変わる」を減らすか測る。

前回は「前日とスコアが変わる日の割合」を測ったが、これは別の問題だった。
Hideさんが見た症状は日をまたぐ変化ではなく、数分違いの2回の実行で
半導体・AI関連が +3/+3 から +1/+3 に変わったこと。原因はドル円が
+0.01% から -0.01% に振れて、+1点が -1点になったこと。

なので測るべきは「指標がわずかに動いたとき、グループの判定が変わる確率」。
各指標に 0.05σ（1日の値動きの5%ぶん）の摂動を与えて、
閾値到達の可否がひっくり返る割合を数える。

あわせて発火率（朝スコアが閾値以上になる日の割合）も出す。不感帯を広げると
安定はするが発火しにくくなるので、両方を並べないと選べない。
"""
import os
import sys

import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import overnight as ov

CONF = yaml.safe_load(open("driver_map.yaml", encoding="utf-8"))
PCTS = [10, 15, 20, 30]
JITTER = 0.05      # 摂動の大きさ（各指標の日次σに対する比）
TRIALS = 200
# 摂動は案ごとに引き直さず、全案で同じ乱数を使う（対応のある比較にする）。
# 引き直すと案の差より試行のばらつきのほうが大きくなり、40試行では
# 同じ設定でも 半導体が 1.6% と 1.2% のように動いてしまう。
rng = np.random.default_rng(0)
NOISE = {}


def noise_for(name, s):
    """項目ごとに TRIALS 本の摂動を1度だけ作って使い回す。"""
    if name not in NOISE:
        NOISE[name] = rng.normal(0, JITTER * s.std(), (TRIALS, len(s)))
    return NOISE[name]


def scored_items():
    for g, d in CONF["グループ"].items():
        sc = d.get("朝スコア")
        if not sc:
            continue
        for i in sc["項目"]:
            yield g, sc["閾値"], i


def main():
    series = {}
    for _, _, i in scored_items():
        for t in (i.get("ticker") or []):
            if t not in series:
                df = ov.fetch_series(t)
                series[t] = (df["close"].pct_change().dropna() * 100
                             if len(df) else None)
    avg_of = {}
    for _, _, i in scored_items():
        if not i.get("ticker") or i["名前"] in avg_of:
            continue
        cols = [series[t] for t in i["ticker"] if series.get(t) is not None]
        if cols:
            avg_of[i["名前"]] = pd.concat(cols, axis=1).dropna().mean(axis=1)
    dz = {n: {p: np.percentile(s.abs(), p) for p in PCTS} for n, s in avg_of.items()}

    groups = {}
    for g, th, i in scored_items():
        groups.setdefault((g, th), []).append(i)

    print(f"■ わずかな摂動（各指標の日次σの{JITTER:.0%}）で判定がひっくり返る割合\n")
    print(f"{'グループ':<14}{'閾値':>5}" + "".join(
        f"{lab:>14}" for lab in ["不感帯なし"] + [f"{p}%点" for p in PCTS]))
    tot = {m: [] for m in [None] + PCTS}
    for (g, th), its in groups.items():
        row = []
        for mode in [None] + PCTS:
            live = [i for i in its
                    if i.get("ticker") and i["名前"] in avg_of
                    and i.get("採点") is not False]
            if not live:
                row.append("-")
                continue
            base = None
            flips = []
            for i in live:
                s = avg_of[i["名前"]]
                band = dz[i["名前"]][mode] if mode else 0.0
                p = np.sign(s.where(s.abs() >= band, 0.0)) * i.get("感応度", 1)
                base = p if base is None else base.add(p, fill_value=0)
            base_fire = base >= th
            for k in range(TRIALS):
                pert = None
                for i in live:
                    s = avg_of[i["名前"]]
                    band = dz[i["名前"]][mode] if mode else 0.0
                    s2 = s + noise_for(i["名前"], s)[k]
                    p = np.sign(s2.where(s2.abs() >= band, 0.0)) * i.get("感応度", 1)
                    pert = p if pert is None else pert.add(p, fill_value=0)
                flips.append(((pert >= th) != base_fire).mean())
            v = np.mean(flips) * 100
            tot[mode].append(v)
            row.append(f"{v:.1f}%")
        print(f"{g:<14}{th:>+5}" + "".join(f"{c:>14}" for c in row))
    print(f"\n{'平均':<14}{'':>5}" + "".join(
        f"{np.mean(tot[m]):>13.1f}%" for m in [None] + PCTS))

    # 点数そのものの振れ幅も見る
    print(f"\n■ 同じ摂動で、朝スコアの点数が変わる割合と、変わったときの振れ幅\n")
    print(f"{'グループ':<14}" + "".join(
        f"{lab:>20}" for lab in ["不感帯なし"] + [f"{p}%点" for p in PCTS]))
    for (g, th), its in groups.items():
        row = []
        for mode in [None] + PCTS:
            live = [i for i in its if i.get("ticker") and i["名前"] in avg_of
                    and i.get("採点") is not False]
            if not live:
                row.append("-")
                continue
            base = None
            for i in live:
                s = avg_of[i["名前"]]
                band = dz[i["名前"]][mode] if mode else 0.0
                p = np.sign(s.where(s.abs() >= band, 0.0)) * i.get("感応度", 1)
                base = p if base is None else base.add(p, fill_value=0)
            ch, mag = [], []
            for k in range(TRIALS):
                pert = None
                for i in live:
                    s = avg_of[i["名前"]]
                    band = dz[i["名前"]][mode] if mode else 0.0
                    s2 = s + noise_for(i["名前"], s)[k]
                    p = np.sign(s2.where(s2.abs() >= band, 0.0)) * i.get("感応度", 1)
                    pert = p if pert is None else pert.add(p, fill_value=0)
                d = (pert - base).abs()
                ch.append((d > 0).mean())
                mag.append(d[d > 0].mean() if (d > 0).any() else 0)
            row.append(f"{np.mean(ch)*100:>6.0f}% / {np.mean(mag):.2f}点")
        print(f"{g:<14}" + "".join(f"{c:>20}" for c in row))

    # 発火率。不感帯を広げるほど下がるので、安定性とセットで見ないと選べない。
    # トピック項目は過去再現できないため 0点固定。絶対値は実運用より低く出る。
    print("\n■ 発火率（朝スコアが閾値以上になる日の割合。トピックは0点固定）\n")
    print(f"{'グループ':<14}{'閾値':>5}" + "".join(
        f"{lab:>14}" for lab in ["不感帯なし"] + [f"{p}%点" for p in PCTS]))
    for (g, th), its in groups.items():
        row = []
        for mode in [None] + PCTS:
            score = None
            for i in its:
                if not i.get("ticker") or i["名前"] not in avg_of:
                    continue
                if i.get("採点") is False:
                    continue
                s = avg_of[i["名前"]]
                band = dz[i["名前"]][mode] if mode else 0.0
                p = np.sign(s.where(s.abs() >= band, 0.0)) * i.get("感応度", 1)
                score = p if score is None else score.add(p, fill_value=0)
            row.append("-" if score is None else f"{(score >= th).mean() * 100:.0f}%")
        print(f"{g:<14}{th:>+5}" + "".join(f"{c:>14}" for c in row))

    print("\n■ 各案の不感帯の実数値\n")
    print(f"{'項目':<26}" + "".join(f"{f'{p}%点':>10}" for p in PCTS)
          + f"{'急変閾値':>10}")
    for name in avg_of:
        th = next((i.get("急変閾値") for _, _, i in scored_items()
                   if i["名前"] == name), None)
        print(f"{name[:24]:<26}" + "".join(f"{dz[name][p]:>9.2f}%" for p in PCTS)
              + f"{th if th else '-':>10}")


if __name__ == "__main__":
    main()
