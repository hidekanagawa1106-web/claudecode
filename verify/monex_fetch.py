"""対象15銘柄の日足を J-Quants から取る（分割調整済みの Adj 系列を使う）。"""
import os
import time

import pandas as pd
import requests

H = {"x-api-key": os.environ["JQUANTS_API_KEY"]}
B = "https://api.jquants.com/v2"
HERE = os.path.dirname(os.path.abspath(__file__))

CODES = {
    "7453": "良品計画", "7011": "三菱重工業", "6702": "富士通", "8058": "三菱商事",
    "8306": "三菱UFJ", "9433": "KDDI", "7203": "トヨタ自動車",
    "8766": "東京海上HD", "6752": "パナソニックHD", "5803": "フジクラ",
    "6506": "安川電機", "6981": "村田製作所", "9984": "ソフトバンクG",
    "9101": "日本郵船", "6525": "KOKUSAI ELECTRIC",
}

if __name__ == "__main__":
    out = []
    for code, name in CODES.items():
        r = requests.get(f"{B}/equities/bars/daily", headers=H,
                         params={"code": code, "from": "2021-08-06", "to": "2026-08-05"},
                         timeout=40)
        d = pd.DataFrame(r.json()["data"])
        if d.empty:
            print(code, name, "データなし")
            continue
        d = d.rename(columns={"AdjO": "o", "AdjH": "h", "AdjL": "l",
                              "AdjC": "c", "AdjVo": "v", "Va": "value"})
        d["day"] = pd.to_datetime(d["Date"]).dt.date
        d["code"], d["name"] = code, name
        keep = ["code", "name", "day", "o", "h", "l", "c", "v", "value", "UL", "LL"]
        d = d[[k for k in keep if k in d.columns]].sort_values("day")
        for col in ["o", "h", "l", "c", "v", "value"]:
            d[col] = pd.to_numeric(d[col], errors="coerce")
        out.append(d)
        print(f"{code} {name:12s} {len(d)}日  {d.day.min()}〜{d.day.max()}", flush=True)
        time.sleep(0.2)
    R = pd.concat(out, ignore_index=True)
    R.to_csv(os.path.join(HERE, "monex15.csv"), index=False)
    print("total", len(R), "codes", R.code.nunique())
