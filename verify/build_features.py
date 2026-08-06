"""順張り条件の「外側」にある要素を、勝率と結び付けて測るためのデータ作り。

いま運用方針に入っていない要素を片っ端から並べる:
  地合い（指数）・寄り付きのギャップ・ORBレンジの幅・ブレイクの初回かどうか
  ・日足のトレンド位置・ボラティリティ・時間帯・VWAPからの乖離
"""
import os
import time

import numpy as np
import pandas as pd

import orb_backtest as ob

HERE = os.path.dirname(os.path.abspath(__file__))
BARS = os.path.join(HERE, "bars")
DAILY = os.path.join(HERE, "daily")
os.makedirs(DAILY, exist_ok=True)
TAGS = pd.read_csv("/home/user/claudecode/driver_tags.csv", dtype={"code": str})

_mem = {}


def bars(code):
    if code not in _mem:
        p = os.path.join(BARS, f"{code}.csv")
        if not os.path.exists(p):
            _mem[code] = None
        else:
            d = pd.read_csv(p)
            d["day"] = pd.to_datetime(d["day"]).dt.date
            _mem[code] = d
    return _mem[code]


def daily(code, symbol=None):
    p = os.path.join(DAILY, f"{code}.csv")
    if os.path.exists(p):
        d = pd.read_csv(p)
    else:
        sym = symbol or f"{code}.T"
        u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
             f"?range=1y&interval=1d")
        import requests
        j = __import__("requests").get(u, headers=ob.UA, timeout=30).json()
        j = j["chart"]["result"][0]
        q = j["indicators"]["quote"][0]
        import datetime as dt
        d = pd.DataFrame({
            "day": [dt.datetime.fromtimestamp(x, ob.JST).date() for x in j["timestamp"]],
            "o": q["open"], "h": q["high"], "l": q["low"], "c": q["close"],
            "v": q["volume"],
        }).dropna()
        d.to_csv(p, index=False)
        time.sleep(0.3)
    d["day"] = pd.to_datetime(d["day"]).dt.date
    d = d.sort_values("day").reset_index(drop=True)
    d["ma5"] = d["c"].rolling(5).mean()
    d["ma25"] = d["c"].rolling(25).mean()
    d["ma75"] = d["c"].rolling(75).mean()
    tr = pd.concat([d["h"] - d["l"], (d["h"] - d["c"].shift()).abs(),
                    (d["l"] - d["c"].shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean() / d["c"] * 100
    d["rsi"] = ob.rsi(d["c"])
    d["prev_c"] = d["c"].shift(1)
    d["vol20"] = d["v"].rolling(20).mean().shift(1)
    return d.set_index("day")


def index_map():
    """日経225の5分足から、各(日, HH:MM)時点の前日比とVWAP位置を出す。"""
    import datetime as dt

    import requests
    u = ("https://query1.finance.yahoo.com/v8/finance/chart/%5EN225"
         "?range=1mo&interval=5m")
    j = requests.get(u, headers=ob.UA, timeout=30).json()["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    d = pd.DataFrame({
        "t": [dt.datetime.fromtimestamp(x, ob.JST) for x in j["timestamp"]],
        "h": q["high"], "l": q["low"], "c": q["close"], "v": q["volume"],
    }).dropna()
    d["day"] = d["t"].dt.date
    d["hm"] = d["t"].dt.strftime("%H:%M")
    last = {day: g["c"].iloc[-1] for day, g in d.groupby("day")}
    days = sorted(last)
    prev = {day: last[days[i - 1]] for i, day in enumerate(days) if i}
    out = {}
    for day, g in d.groupby("day"):
        if day not in prev:
            continue
        pc = prev[day]
        tp = (g["h"] + g["l"] + g["c"]) / 3
        vw = (tp * g["v"]).cumsum() / g["v"].cumsum() if g["v"].sum() else g["c"]
        for (_, r), w in zip(g.iterrows(), vw):
            out[(day, r["hm"])] = (r["c"] / pc - 1, bool(r["c"] > w))
    return out


def peer_map(code):
    d = bars(code)
    if d is None:
        return None
    last = {day: g["c"].iloc[-1] for day, g in d.groupby("day")}
    days = sorted(last)
    prev = {day: last[days[i - 1]] for i, day in enumerate(days) if i}
    return {(r["day"], r["hm"]): bool(r["c"] > prev[r["day"]])
            for _, r in d.iterrows() if r["day"] in prev}


def one(code, peers, idx):
    d = bars(code)
    if d is None or len(d) < 100:
        return None
    dd = daily(code)
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
        if orb.empty or day not in dd.index:
            continue
        dr = dd.loc[day]
        oh, ol = orb["h"].max(), orb["l"].min()
        orb_range = (oh - ol) / oh
        day_open = g["o"].iloc[0]
        gap = (day_open / dr["prev_c"] - 1) if dr["prev_c"] else np.nan
        tp = (g["h"] + g["l"] + g["c"]) / 3
        g["vwap"] = (tp * g["v"]).cumsum() / g["v"].cumsum()
        nv = g["v"].where(~g["auction"])
        g["avgv"] = nv.expanding().mean().shift(1)
        close_eod = g["c"].iloc[-1]
        broken = False
        for i, r in g.iterrows():
            above = r["c"] > oh
            first = above and not broken
            if above:
                broken = True
            if not (r["hm"] > "09:10" and r["hm"] < "14:55" and not r["auction"]):
                continue
            win = g.iloc[i + 1:i + 13]
            ic, iv = idx.get((day, r["hm"]), (np.nan, None))
            pk = [pm.get((day, r["hm"])) for pm in pms]
            rows.append({
                "code": code, "day": day, "hm": r["hm"], "close": r["c"],
                "orb_dev": r["c"] / oh - 1, "vwap_dev": r["c"] / r["vwap"] - 1,
                "volr": (r["v"] / r["avgv"]) if r["avgv"] else np.nan,
                "c4_all": all(x is True for x in pk),
                "rsi5": r["rsi5"], "rsi_d": dr["rsi"],
                # --- ここから運用方針に入っていない要素 ---
                "first_break": bool(first),
                "orb_range": orb_range,
                "gap": gap,
                "day_chg": r["c"] / dr["prev_c"] - 1 if dr["prev_c"] else np.nan,
                "idx_chg": ic, "idx_vwap": iv,
                "ma5_pos": r["c"] / dr["ma5"] - 1 if dr["ma5"] else np.nan,
                "ma25_pos": r["c"] / dr["ma25"] - 1 if dr["ma25"] else np.nan,
                "ma75_pos": r["c"] / dr["ma75"] - 1 if dr["ma75"] else np.nan,
                "perfect": bool(dr["ma5"] > dr["ma25"] > dr["ma75"]),
                "atr": dr["atr"],
                "dvol": dr["v"] / dr["vol20"] if dr["vol20"] else np.nan,
                "mins": (int(r["hm"][:2]) - 9) * 60 + int(r["hm"][3:]),
                # --- 結果 ---
                "f30": g["c"].iloc[min(i + 6, len(g) - 1)] / r["c"] - 1,
                "f60": g["c"].iloc[min(i + 12, len(g) - 1)] / r["c"] - 1,
                "feod": close_eod / r["c"] - 1,
                "mfe60": (win["h"].max() / r["c"] - 1) if len(win) else np.nan,
                "mae60": (win["l"].min() / r["c"] - 1) if len(win) else np.nan,
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    idx = index_map()
    print("index bars", len(idx), flush=True)
    out = []
    for drv, g in TAGS.groupby("driver"):
        codes = g["code"].tolist()
        if len(codes) < 2:
            continue
        for c in codes:
            r = one(c, [x for x in codes if x != c][:2], idx)
            if r is not None and len(r):
                out.append(r)
    R = pd.concat(out, ignore_index=True)
    R.to_csv(os.path.join(HERE, "features.csv"), index=False)
    print("total", len(R), "codes", R.code.nunique())
