"""順張り4条件 + RSI70以下 の同時成立回数を、5分足で数える。

制約: Yahooの5分足は1ヶ月しか遡れない。1日目の寄り付き足が欠ける日がある。
"""
import argparse
import datetime as dt
import zoneinfo

import numpy as np
import pandas as pd
import requests

JST = zoneinfo.ZoneInfo("Asia/Tokyo")
UA = {"User-Agent": "Mozilla/5.0"}


def fetch(code, rng="1mo", interval="5m"):
    u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.T"
         f"?range={rng}&interval={interval}")
    j = requests.get(u, headers=UA, timeout=30).json()["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    d = pd.DataFrame({
        "t": [dt.datetime.fromtimestamp(x, JST) for x in j["timestamp"]],
        "o": q["open"], "h": q["high"], "l": q["low"], "c": q["close"],
        "v": q["volume"],
    }).dropna().reset_index(drop=True)
    d["day"] = d["t"].dt.date
    d["hm"] = d["t"].dt.strftime("%H:%M")
    return d


def rsi(s, n=14):
    delta = s.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def daily_rsi(code):
    d = fetch(code, rng="6mo", interval="1d")
    d["rsi"] = rsi(d["c"])
    return dict(zip(d["day"], d["rsi"]))


def peer_direction(codes):
    """連動銘柄の「その時点で前日比プラスか」を (日付, HH:MM) -> bool で返す。"""
    out = {}
    for c in codes:
        d = fetch(c)
        prev_close = {}
        last = {}
        for day, g in d.groupby("day"):
            last[day] = g["c"].iloc[-1]
        days = sorted(last)
        for i, day in enumerate(days):
            if i:
                prev_close[day] = last[days[i - 1]]
        m = {}
        for _, r in d.iterrows():
            pc = prev_close.get(r["day"])
            if pc:
                m[(r["day"], r["hm"])] = bool(r["c"] > pc)
        out[c] = m
    return out


def run(code, peers, orb_margin=0.0, verbose=True):
    d = fetch(code)
    dr = daily_rsi(code)
    pm = peer_direction(peers)

    auction = ((d["hm"] <= "09:05") | ((d["hm"] >= "12:30") & (d["hm"] <= "12:35"))
               | (d["hm"] >= "14:55"))
    d["auction"] = auction
    d["rsi5"] = rsi(d["c"])          # 5分足RSIは日をまたいで連続で計算する

    rows = []
    day_summary = []
    for day, g in d.groupby("day"):
        g = g.reset_index(drop=True)
        orb = g[g["hm"] <= "09:10"]          # 09:00,09:05,09:10 の足 = 09:00-09:15
        if len(orb) == 0:
            continue
        orb_high = orb["h"].max()
        first_bar = g["hm"].iloc[0]
        tp = (g["h"] + g["l"] + g["c"]) / 3
        g["vwap"] = (tp * g["v"]).cumsum() / g["v"].cumsum()
        # 出来高平均: その時点までの非板寄せ足の平均（先読みしない）
        nv = g["v"].where(~g["auction"])
        g["avgv"] = nv.expanding().mean().shift(1)
        # 板寄せ（12:30-12:35）と大引け前は評価対象から外す。
        # 出来高スパイクが構造的で、条件3がシグナルとして意味を持たないため。
        post = g[(g["hm"] > "09:10") & (g["hm"] < "14:55") & (~g["auction"])]

        hits = 0
        for _, r in post.iterrows():
            c1 = r["c"] > orb_high * (1 + orb_margin)
            c2 = r["c"] > r["vwap"]
            c3 = bool(r["avgv"]) and r["v"] >= 1.5 * r["avgv"]
            pk = [pm[p].get((day, r["hm"])) for p in peers]
            c4_all = bool(pk) and all(x is True for x in pk)
            c4_any = any(x is True for x in pk)
            rows.append({
                "day": day, "hm": r["hm"], "c1": c1, "c2": c2, "c3": c3,
                "c4_all": c4_all, "c4_any": c4_any,
                "rsi5": r["rsi5"], "rsi_d": dr.get(day, np.nan),
                "close": r["c"], "orb_high": orb_high,
            })
            if c1 and c2 and c3 and c4_all:
                hits += 1
        day_summary.append({"day": day, "first_bar": first_bar,
                            "orb_bars": len(orb), "orb_high": orb_high,
                            "hits_1_4": hits})

    R = pd.DataFrame(rows)
    S = pd.DataFrame(day_summary)
    return R, S


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("--peers", default="8316,8411")
    ap.add_argument("--margin", type=float, default=0.0)
    a = ap.parse_args()
    R, S = run(a.code, a.peers.split(","), a.margin)

    n = len(R)
    print(f"=== {a.code}  評価バー数 {n}本 / {S['day'].nunique()}営業日 "
          f"({S['day'].min()}〜{S['day'].max()})")
    print(f"ORB窓の足が3本揃った日: {(S['orb_bars']==3).sum()}日 / {len(S)}日")
    print()
    for label, m in [
        ("条件1 ORB上抜け", R.c1),
        ("条件2 VWAP上", R.c2),
        ("条件3 出来高1.5倍", R.c3),
        ("条件4 連動2銘柄とも同方向", R.c4_all),
        ("条件4(緩) どちらか同方向", R.c4_any),
    ]:
        print(f"  {label:28s} {m.sum():5d}本 ({m.mean()*100:5.1f}%)")
    print()
    c14 = R.c1 & R.c2 & R.c3 & R.c4_all
    c14any = R.c1 & R.c2 & R.c3 & R.c4_any
    c12 = R.c1 & R.c2
    c123 = R.c1 & R.c2 & R.c3
    for label, m in [("1+2", c12), ("1+2+3", c123),
                     ("1+2+3+4(厳)", c14), ("1+2+3+4(緩)", c14any)]:
        print(f"  {label:14s} {m.sum():5d}本  該当日数 {R[m]['day'].nunique():3d}日")
    print()
    print("--- RSIフィルタを足すと ---")
    for name, base in [("1+2+3+4(厳)", c14), ("1+2+3+4(緩)", c14any)]:
        for rl, col in [("5分足RSI", "rsi5"), ("日足RSI", "rsi_d")]:
            ok = base & (R[col] <= 70)
            print(f"  {name} かつ {rl}≤70 : {ok.sum():4d}本  "
                  f"該当日数 {R[ok]['day'].nunique():2d}日   "
                  f"（1-4成立時の{rl}中央値 {R[base][col].median():.1f} / "
                  f"70超え {(R[base][col] > 70).sum()}本）")
    print()
    print("--- 1-4成立バーの内訳（厳） ---")
    if c14.any():
        print(R[c14][["day", "hm", "close", "orb_high", "rsi5", "rsi_d"]]
              .to_string(index=False))
    print()
    print("--- 日別 ---")
    print(S.to_string(index=False))
