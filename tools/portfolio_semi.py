"""半導体を増やした15銘柄を組む。キオクシア(285A)は除外。

方針が変わった:
  旧「値動きが重ならないように散らす」
  新「売買代金が集まっている場所に居る。ただし同じ動きの重複は避ける」

半導体の中身はさらに5つの塊に割れている（装置・部品・電線・材料・AI投資）。
装置どうしは0.69〜0.76、部品どうしは0.72〜0.81でほぼ同じ動きなので、
塊ごとに1枠までとする。フジクラは電線枠を既に埋めている。
"""
import pickle

import numpy as np
import pandas as pd

SCRATCH = "/tmp/claude-0/-home-user-claudecode/0b95ceac-38f2-58ff-ba66-ce0e2b989406/scratchpad"

FIXED = ["5803", "8306", "8058", "7203", "7011"]
CUR = FIXED + ["8766", "7974", "7453", "9433", "2802", "4568", "9501", "6702",
               "9101", "6752"]
# 抜く3枠: 売買代金の下位で、実測グループを担っていないもの
DROP = ["2802", "4568", "9501"]           # 味の素 / 第一三共 / 東京電力
KEEP = [c for c in CUR if c not in DROP]

VARIANTS = {
    "A 代金最大":      ["6857", "9984", "6981"],   # 検査装置 / AI投資 / 電子部品
    "B 費用おさえる":   ["6525", "9984", "6981"],   # 前工程装置(廉価) / AI投資 / 電子部品
    "C 装置を厚く":    ["8035", "9984", "6981"],   # 前工程装置(最大) / AI投資 / 電子部品
    "D 素材を混ぜる":   ["6857", "9984", "4063"],   # 検査装置 / AI投資 / 材料
}
SEMI_ALL = ["9984", "6857", "6981", "8035", "6920", "5801", "6146", "3436",
            "6723", "5802", "6762", "4063", "5803", "6976", "4062", "6525",
            "7735", "5016"]


def main():
    b = pickle.load(open(f"{SCRATCH}/bars200.pkl", "rb"))
    for f in ["bars_add.pkl", "bars_new.pkl"]:
        b.update(pickle.load(open(f"{SCRATCH}/{f}", "rb")))
    nm = pd.read_csv("/home/user/claudecode/company_master.csv", dtype={"code": str})
    N = dict(zip(nm["code"], nm["CoName"]))
    rets = {c: d.set_index("date")["close"].pct_change().dropna()
            for c, d in b.items() if len(d) >= 120}
    C = pd.DataFrame(rets).dropna().corr()
    semi = [s for s in SEMI_ALL if s in C.columns]

    def to(c):
        d = b[c]
        return (d["close"] * d["volume"]).tail(20).mean() / 1e8

    def atr(c):
        d = b[c]
        h, l, cl = d["high"], d["low"], d["close"]
        tr = pd.concat([h - l, (h - cl.shift()).abs(),
                        (l - cl.shift()).abs()], axis=1).max(axis=1)
        return tr.rolling(14).mean().iloc[-1] / cl.iloc[-1] * 100

    def stats(m):
        s = C.loc[m, m]
        off = [s.loc[a, x] for i, a in enumerate(m) for x in m[i + 1:]]
        return dict(mean=np.mean(off), mx=max(off),
                    atr=np.mean([atr(c) for c in m]),
                    semi=np.mean([C.loc[c, semi].mean() for c in m]),
                    to=sum(to(c) for c in m),
                    cost=sum(b[c]["close"].iloc[-1] * 100 for c in m) / 10000)

    base = stats(CUR)
    print("=" * 88)
    print("比較表  ※ 半導体連動 = 15銘柄の平均が半導体グループとどれだけ一緒に動くか")
    print("=" * 88)
    print(f"{'案':<14}{'平均相関':>9}{'最大':>7}{'ATR%':>7}"
          f"{'半導体連動':>10}{'合計代金':>10}{'総額':>10}")
    print(f"{'現行15':<14}{base['mean']:>9.3f}{base['mx']:>7.2f}{base['atr']:>7.2f}"
          f"{base['semi']:>10.2f}{base['to']:>8,.0f}億{base['cost']:>8,.0f}万")
    results = {}
    for k, add in VARIANTS.items():
        m = KEEP + add
        s = stats(m)
        results[k] = (m, s)
        print(f"{k:<14}{s['mean']:>9.3f}{s['mx']:>7.2f}{s['atr']:>7.2f}"
              f"{s['semi']:>10.2f}{s['to']:>8,.0f}億{s['cost']:>8,.0f}万")

    for k, (m, s) in results.items():
        print("\n" + "=" * 88)
        print(f"【{k}】")
        print("=" * 88)
        print(f"{'':<4}{'コード':<7}{'銘柄':<17}{'1単元':>8}{'代金':>8}"
              f"{'ATR%':>6}{'60日':>8}{'半導体連動':>10}")
        for i, c in enumerate(m, 1):
            d = b[c]
            px = d["close"].iloc[-1]
            r60 = px / d["close"].iloc[-61] * 100 - 100
            mark = "★" if c in FIXED else ("＋" if c in VARIANTS[k] else " ")
            print(f"{mark}{i:>2}. {c:<7}{N.get(c,'')[:15]:<17}{px*100/10000:>7.1f}万"
                  f"{to(c):>6.0f}億{atr(c):>6.1f}{r60:>+7.1f}%"
                  f"{C.loc[c,semi].mean():>10.2f}")
        hi = [(C.loc[a, x], a, x) for i, a in enumerate(m) for x in m[i + 1:]
              if C.loc[a, x] >= 0.55]
        print("  相関0.55以上の組み合わせ: " + (", ".join(
            f"{N.get(a,'')[:8]}⇔{N.get(x,'')[:8]} {v:.2f}"
            for v, a, x in sorted(hi, reverse=True)) if hi else "なし"))


if __name__ == "__main__":
    main()
