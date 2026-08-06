"""対象15銘柄の5分足を取る（Yahoo、1ヶ月）。J-Quantsの分足は契約外のため。"""
import os
import time

import pandas as pd

import orb_backtest as ob
from monex_fetch import CODES

HERE = os.path.dirname(os.path.abspath(__file__))

if __name__ == "__main__":
    out = []
    for code, name in CODES.items():
        try:
            d = ob.fetch(code)
        except Exception as e:
            print(code, name, "失敗", str(e)[:50])
            continue
        d["code"], d["name"] = code, name
        out.append(d)
        print(f"{code} {name:16s} {len(d)}本  {d.day.min()}〜{d.day.max()}", flush=True)
        time.sleep(0.3)
    R = pd.concat(out, ignore_index=True)
    R.to_csv(os.path.join(HERE, "monex15_5m.csv"), index=False)
    print("total", len(R), "codes", R.code.nunique(), "days", R.day.nunique())
