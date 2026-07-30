"""
バリュエーション参考レポート（選定には使わない）
================================================

universe.csv の銘柄について、PER・PBR・直近の決算開示日を一覧化する。

重要: このスクリプトは銘柄の選定・除外を一切行わない。
screen_daily.py のスクリーニング結果とは独立しており、
「候補に挙がった銘柄の割高・割安を手で判断するための参考値」を出すだけ。

理由: PER/PBR は数ヶ月〜数年で効く指標で、数日〜数週間のスイングの
時間軸とは噛み合わない。割安上位には構造的な赤字銘柄が並びやすく、
順張りで狙いたいテーマ主導銘柄はむしろ割高側に出る。
したがってランキングやフィルタには使わず、判断材料としてのみ添える。

データ元:
- 株価: /v2/equities/bars/daily の直近終値
- EPS/BPS: /v2/fins/summary（EPS=実績, FEPS=会社予想, BPS=通期決算時のみ更新）

使い方:
    python fundamentals_report.py --universe universe.csv --out fundamentals.csv
"""

import os
import sys
import time
import argparse
import pandas as pd
import requests

API_BASE = "https://api.jquants.com/v2"


def get_headers() -> dict:
    api_key = os.environ.get("JQUANTS_API_KEY")
    if not api_key:
        raise RuntimeError("環境変数 JQUANTS_API_KEY が未設定です")
    return {"x-api-key": api_key}


def get_with_retry(url: str, params: dict, headers: dict, max_retries: int = 5):
    """429(レート制限)は指数バックオフで待って再試行する。"""
    wait = 2.0
    for attempt in range(max_retries):
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        if attempt < max_retries - 1:
            time.sleep(wait)
            wait *= 2
    resp.raise_for_status()
    return resp


def _records(payload):
    if isinstance(payload, list):
        return payload
    return payload.get("data", payload.get("daily_quotes", []))


def fetch_last_close(code: str, headers: dict):
    resp = get_with_retry(f"{API_BASE}/equities/bars/daily", {"code": code}, headers)
    df = pd.DataFrame(_records(resp.json()))
    if df.empty:
        return None
    df = df.sort_values("Date")
    return float(df["C"].iloc[-1])


def fetch_fundamentals(code: str, headers: dict) -> dict:
    """直近の通期実績(EPS/BPS)と会社予想EPS、最新の開示日を取り出す。"""
    resp = get_with_retry(f"{API_BASE}/fins/summary", {"code": code}, headers)
    df = pd.DataFrame(_records(resp.json()))
    if df.empty:
        return {}
    df = df.sort_values("DiscDate")
    num = lambda s: pd.to_numeric(s, errors="coerce")

    out = {"直近開示日": df["DiscDate"].iloc[-1], "決算種別": df["CurPerType"].iloc[-1]}
    fy = df[df["CurPerType"] == "FY"]
    for col, key in [("BPS", "BPS"), ("EPS", "EPS実績")]:
        if len(fy) and num(fy[col]).notna().any():
            out[key] = float(num(fy[col]).dropna().iloc[-1])
    if "FEPS" in df.columns and num(df["FEPS"]).notna().any():
        out["EPS予想"] = float(num(df["FEPS"]).dropna().iloc[-1])
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", default="universe.csv")
    parser.add_argument("--out", default="fundamentals.csv")
    parser.add_argument("--names", default="company_master.csv",
                        help="code,CoName を含むCSV(任意。銘柄名の表示に使う)")
    parser.add_argument("--sleep", type=float, default=0.2)
    args = parser.parse_args()

    headers = get_headers()
    uni = pd.read_csv(args.universe, dtype={"code": str})

    names = {}
    if os.path.exists(args.names):
        nm = pd.read_csv(args.names, dtype={"code": str})
        names = dict(zip(nm["code"], nm["CoName"]))

    rows = []
    for _, u in uni.iterrows():
        code = u["code"]
        row = {"code": code, "銘柄名": names.get(code, ""),
               "tier": u.get("tier", ""), "driver": u.get("driver", "")}
        try:
            close = fetch_last_close(code, headers)
            row["株価"] = close
            row.update(fetch_fundamentals(code, headers))
        except Exception as e:
            print(f"[warn] {code}: {e}", file=sys.stderr)
        rows.append(row)
        time.sleep(args.sleep)

    df = pd.DataFrame(rows)
    for col in ["株価", "BPS", "EPS実績", "EPS予想"]:
        if col not in df.columns:
            df[col] = pd.NA
    df["PBR"] = (df["株価"] / df["BPS"]).round(2)
    df["予想PER"] = (df["株価"] / df["EPS予想"]).round(1)
    df["実績PER"] = (df["株価"] / df["EPS実績"]).round(1)
    # 赤字予想は PER が負になり「割安」に見えてしまうため明示的に潰す
    df.loc[df["EPS予想"] <= 0, "予想PER"] = pd.NA
    df.loc[df["EPS実績"] <= 0, "実績PER"] = pd.NA
    # 予想が取れていない銘柄は False ではなく空欄（不明）にする
    df["赤字予想"] = (df["EPS予想"] <= 0).where(df["EPS予想"].notna())

    cols = ["code", "銘柄名", "tier", "driver", "株価", "予想PER", "実績PER",
            "PBR", "赤字予想", "直近開示日", "決算種別"]
    df[cols].to_csv(args.out, index=False, encoding="utf-8-sig")

    print(f"バリュエーション参考値を出力しました → {args.out}（{len(df)}銘柄）")
    print(df[cols].to_string(index=False))
    miss = df["EPS予想"].isna().sum()
    if miss:
        print(f"\n※ {miss}銘柄は会社予想EPSが取得できず、予想PERは空欄です。")
    print("※ BPSは通期決算時にのみ更新されるため、PBRは最大1年古い純資産に基づきます。")
    print("※ この数値は選定・除外には使っていません。判断材料としてご確認ください。")


if __name__ == "__main__":
    main()
