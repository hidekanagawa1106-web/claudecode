"""順張り4条件 + RSI の同時成立を、driver_tags.csv の74銘柄で数える。

条件4の連動銘柄は、同じ driver タグを持つ他の銘柄を最大2つ使う（両方が同方向）。
"""
import os
import time

import numpy as np
import pandas as pd

import orb_backtest as ob

TAGS = pd.read_csv("/home/user/claudecode/driver_tags.csv", dtype={"code": str})

_cache = {}


def bars(code):
    if code not in _cache:
        try:
            _cache[code] = ob.fetch(code)
        except Exception:
            _cache[code] = None
        time.sleep(0.3)
    return _cache[code]


def peer_map(code):
    d = bars(code)
    if d is None or d.empty:
        return None
    prev, last = {}, {}
    for day, g in d.groupby("day"):
        last[day] = g["c"].iloc[-1]
    days = sorted(last)
    for i, day in enumerate(days):
        if i:
            prev[day] = last[days[i - 1]]
    m = {}
    for _, r in d.iterrows():
        pc = prev.get(r["day"])
        if pc:
            m[(r["day"], r["hm"])] = bool(r["c"] > pc)
    return m


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
        orb = g[g["hm"] <= "09:10"]
        if orb.empty:
            continue
        oh = orb["h"].max()
        tp = (g["h"] + g["l"] + g["c"]) / 3
        g = g.assign(vwap=(tp * g["v"]).cumsum() / g["v"].cumsum())
        nv = g["v"].where(~g["auction"])
        g = g.assign(avgv=nv.expanding().mean().shift(1))
        post = g[(g["hm"] > "09:10") & (g["hm"] < "14:55") & (~g["auction"])]
        for _, r in post.iterrows():
            pk = [pm.get((day, r["hm"])) for pm in pms]
            rows.append({
                "code": code, "day": day, "hm": r["hm"],
                "c1": bool(r["c"] > oh),
                "c2": bool(r["c"] > r["vwap"]),
                "c3": bool(r["avgv"] > 0 and r["v"] >= 1.5 * r["avgv"]),
                "c4": all(x is True for x in pk),
                "rsi5": r["rsi5"], "rsi_d": dr.get(day, np.nan),
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    out = []
    for drv, g in TAGS.groupby("driver"):
        codes = g["code"].tolist()
        if len(codes) < 2:
            continue
        for c in codes:
            peers = [x for x in codes if x != c][:2]
            r = one(c, peers)
            if r is not None and len(r):
                out.append(r)
                print(f"{c} ({drv}) {len(r)}本", flush=True)
    R = pd.concat(out, ignore_index=True)
    R.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "universe_bars.csv"), index=False)
    print("total bars", len(R), "codes", R.code.nunique(), "days", R.day.nunique())
