"""日足だけで組み直したらどうなるかを、5年ぶんで確かめる。

5分足は1ヶ月しか遡れないが、日足は数年取れる。検証できる土俵で
素朴な仕掛けを並べ、優位が測れるものがあるかを見る。
"""
import datetime as dt
import os
import time

import numpy as np
import pandas as pd
import requests

import orb_backtest as ob

HERE = os.path.dirname(os.path.abspath(__file__))
D5 = os.path.join(HERE, "daily5y")
os.makedirs(D5, exist_ok=True)
TAGS = pd.read_csv("/home/user/claudecode/driver_tags.csv", dtype={"code": str})


def daily(code):
    p = os.path.join(D5, f"{code}.csv")
    if os.path.exists(p):
        d = pd.read_csv(p)
    else:
        u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.T"
             f"?range=5y&interval=1d")
        try:
            j = requests.get(u, headers=ob.UA, timeout=30).json()["chart"]["result"][0]
        except Exception:
            return None
        q = j["indicators"]["quote"][0]
        d = pd.DataFrame({
            "day": [dt.datetime.fromtimestamp(x, ob.JST).date() for x in j["timestamp"]],
            "o": q["open"], "h": q["high"], "l": q["low"], "c": q["close"],
            "v": q["volume"],
        }).dropna()
        d.to_csv(p, index=False)
        time.sleep(0.25)
    d["day"] = pd.to_datetime(d["day"]).dt.date
    return d.sort_values("day").reset_index(drop=True)


def features(code):
    d = daily(code)
    if d is None or len(d) < 300:
        return None
    c, h, l, o, v = d["c"], d["h"], d["l"], d["o"], d["v"]
    d["ma5"], d["ma25"], d["ma75"] = c.rolling(5).mean(), c.rolling(25).mean(), c.rolling(75).mean()
    d["ma200"] = c.rolling(200).mean()
    d["rsi"] = ob.rsi(c)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean() / c * 100
    d["volr"] = v / v.rolling(20).mean()
    d["hi25"] = h.rolling(25).max().shift(1)      # 前日までの25日高値
    d["hi60"] = h.rolling(60).max().shift(1)
    d["lo25"] = l.rolling(25).min().shift(1)
    d["gap"] = o / c.shift(1) - 1
    d["chg"] = c / c.shift(1) - 1
    d["chg5"] = c / c.shift(5) - 1
    d["chg20"] = c / c.shift(20) - 1
    d["body"] = (c - o) / o
    d["code"] = code
    # 翌日の寄りで入って、n日後の終値で出る（当日終値では入れないため）
    entry = o.shift(-1)
    for n in [1, 3, 5, 10]:
        d[f"f{n}"] = c.shift(-(n)) / entry - 1
    # 翌日寄り〜10日後までの最大上昇/最大下落（-4%/+7%がどちらに当たるか）
    fh = pd.concat([h.shift(-(i + 1)) for i in range(10)], axis=1).max(axis=1)
    fl = pd.concat([l.shift(-(i + 1)) for i in range(10)], axis=1).min(axis=1)
    d["mfe10"] = fh / entry - 1
    d["mae10"] = fl / entry - 1
    return d


if __name__ == "__main__":
    out = []
    for code in TAGS["code"]:
        f = features(code)
        if f is not None:
            out.append(f)
    R = pd.concat(out, ignore_index=True)
    R.to_csv(os.path.join(HERE, "daily5y.csv"), index=False)
    print("rows", len(R), "codes", R.code.nunique(),
          "period", R.day.min(), "-", R.day.max())
