"""順張り条件を「回数」ではなく「その後どうなったか」で評価するためのデータ作り。

各5分足について、条件の素の数値（ORBからの乖離率・出来高倍率・RSI）と、
その後の値動き（30分後・60分後・引け）を並べて保存する。
閾値の当てはめは集計側で行う。
"""
import os
import time

import numpy as np
import pandas as pd

import orb_backtest as ob

TAGS = pd.read_csv("/home/user/claudecode/driver_tags.csv", dtype={"code": str})
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bars")
os.makedirs(CACHE, exist_ok=True)

_mem = {}


def bars(code):
    if code in _mem:
        return _mem[code]
    p = os.path.join(CACHE, f"{code}.csv")
    if os.path.exists(p):
        d = pd.read_csv(p, parse_dates=["t"])
        d["day"] = pd.to_datetime(d["day"]).dt.date
    else:
        try:
            d = ob.fetch(code)
        except Exception:
            d = None
        time.sleep(0.3)
        if d is not None:
            d.to_csv(p, index=False)
    _mem[code] = d
    return d


def peer_map(code):
    d = bars(code)
    if d is None or d.empty:
        return None
    last = {day: g["c"].iloc[-1] for day, g in d.groupby("day")}
    days = sorted(last)
    prev = {day: last[days[i - 1]] for i, day in enumerate(days) if i}
    return {(r["day"], r["hm"]): bool(r["c"] > prev[r["day"]])
            for _, r in d.iterrows() if r["day"] in prev}


def one(code, peers):
    d = bars(code)
    if d is None or len(d) < 100:
        return None
    dr = ob.daily_rsi(code)
    pms = [p for p in (peer_map(x) for x in peers) if p]
    if not pms:
        return None
    d = d.copy()
    d["auction"] = ((d["hm"] <= "09:05") | ((d["hm"] >= "12:30") & (d["hm"] <= "12:35"))
                    | (d["hm"] >= "14:55"))
    d["rsi5"] = ob.rsi(d["c"])
    rows = []
    for day, g in d.groupby("day"):
        g = g.reset_index(drop=True)
        orb = g[g["hm"] <= "09:10"]
        if orb.empty:
            continue
        oh = orb["h"].max()
        tp = (g["h"] + g["l"] + g["c"]) / 3
        g["vwap"] = (tp * g["v"]).cumsum() / g["v"].cumsum()
        nv = g["v"].where(~g["auction"])
        g["avgv"] = nv.expanding().mean().shift(1)
        # 当日内のみの先行リターン（持ち越さない）
        close_eod = g["c"].iloc[-1]
        for i, r in g.iterrows():
            if not (r["hm"] > "09:10" and r["hm"] < "14:55" and not r["auction"]):
                continue
            f30 = g["c"].iloc[min(i + 6, len(g) - 1)]
            f60 = g["c"].iloc[min(i + 12, len(g) - 1)]
            # 60分以内の最大上昇・最大下落（利確/損切りのどちらが先に来るか用）
            win = g.iloc[i + 1:i + 13]
            pk = [pm.get((day, r["hm"])) for pm in pms]
            rows.append({
                "code": code, "day": day, "hm": r["hm"], "close": r["c"],
                "orb_dev": r["c"] / oh - 1,
                "vwap_dev": r["c"] / r["vwap"] - 1,
                "volr": (r["v"] / r["avgv"]) if r["avgv"] else np.nan,
                "c4_all": all(x is True for x in pk),
                "c4_any": any(x is True for x in pk),
                "rsi5": r["rsi5"], "rsi_d": dr.get(day, np.nan),
                "f30": f30 / r["c"] - 1, "f60": f60 / r["c"] - 1,
                "feod": close_eod / r["c"] - 1,
                "mfe60": (win["h"].max() / r["c"] - 1) if len(win) else np.nan,
                "mae60": (win["l"].min() / r["c"] - 1) if len(win) else np.nan,
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = []
    for drv, g in TAGS.groupby("driver"):
        codes = g["code"].tolist()
        if len(codes) < 2:
            continue
        for c in codes:
            r = one(c, [x for x in codes if x != c][:2])
            if r is not None and len(r):
                out.append(r)
                print(f"{c} ({drv}) {len(r)}", flush=True)
    R = pd.concat(out, ignore_index=True)
    R.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "outcomes.csv"), index=False)
    print("total", len(R), "codes", R.code.nunique())
