"""
常時ウォッチ・ユニバース生成/更新スクリプト
==========================================

日経225の中から、以下の条件で「常時ウォッチ」ユニバース(10〜30銘柄)を生成・更新する。
- 株価が概ね5,000円以下（100株で約50万円以内に収まる）
- 対象が多すぎる場合は、直近20日平均出来高が多い順に上位N銘柄に絞る（流動性優先）
  ※ この「出来高で絞る」部分は仮の基準です。時価総額や別の軸で絞りたい場合は
    screen_universe() のソート条件を調整してください。

運用イメージ: 月1回程度、このスクリプトで universe.csv を更新する。
毎日の screen_daily.py はこの universe.csv の範囲内だけをスクリーニングする
(225銘柄全部を毎日叩くとAPI呼び出し数がかさむため)。

事前準備:
- nikkei225_codes.csv (1列目に証券コード, ヘッダーあり)
- driver_tags.csv (任意。code,driver の2列。半導体/金融/防衛/内需/その他 等で
  ご自身がタグ付けしたもの。用意しない場合は全銘柄 "unclassified" 扱いになる)
- 環境変数 JQUANTS_API_KEY

使い方:
    python build_universe.py --codes nikkei225_codes.csv --max-price 5000 --max-universe 30
"""

import os
import sys
import time
import argparse
import pandas as pd
import requests

API_BASE = "https://api.jquants.com/v2"

COLUMN_ALIASES = {
    "date": ["date", "Date"],
    "open": ["open", "Open", "O"],
    "high": ["high", "High", "H"],
    "low": ["low", "Low", "L"],
    "close": ["close", "Close", "C"],
    "volume": ["volume", "Volume", "Vo", "vo"],
}


def get_headers() -> dict:
    api_key = os.environ.get("JQUANTS_API_KEY")
    if not api_key:
        raise RuntimeError("環境変数 JQUANTS_API_KEY が未設定です")
    return {"x-api-key": api_key}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {}
    for standard_name, candidates in COLUMN_ALIASES.items():
        for c in candidates:
            if c in df.columns:
                rename_map[c] = standard_name
                break
        else:
            raise KeyError(
                f"カラム '{standard_name}' に対応する列が見つかりません。"
                f" 実際のレスポンス列: {list(df.columns)}"
            )
    return df.rename(columns=rename_map)


def fetch_daily_bars(code: str, headers: dict, lookback_days: int = 20) -> pd.DataFrame:
    params = {"code": code}
    resp = requests.get(f"{API_BASE}/equities/bars/daily", params=params, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        records = data
    else:
        # v2 は {"data": [...]}、v1 系は {"daily_quotes": [...]} で返る
        records = data.get("data", data.get("daily_quotes", []))
    df = pd.DataFrame(records)
    df = normalize_columns(df)
    return df.sort_values("date").tail(lookback_days).reset_index(drop=True)


def load_driver_tags(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    df = pd.read_csv(path, dtype=str)
    return dict(zip(df.iloc[:, 0], df.iloc[:, 1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", required=True, help="日経225銘柄コード一覧CSV")
    parser.add_argument("--driver-tags", default="driver_tags.csv", help="code,driver の2列CSV(任意)")
    parser.add_argument("--max-price", type=float, default=5000)
    parser.add_argument("--max-universe", type=int, default=30)
    parser.add_argument("--out", default="universe.csv")
    parser.add_argument("--sleep", type=float, default=0.3)
    args = parser.parse_args()

    headers = get_headers()
    codes_df = pd.read_csv(args.codes, dtype=str)
    codes = codes_df.iloc[:, 0].tolist()
    driver_map = load_driver_tags(args.driver_tags)

    rows = []
    for i, code in enumerate(codes):
        try:
            df = fetch_daily_bars(code, headers, lookback_days=20)
            if df.empty:
                continue
            last_close = df["close"].iloc[-1]
            if last_close > args.max_price:
                continue
            avg_vol = df["volume"].mean()
            rows.append({
                "code": code,
                "driver": driver_map.get(code, "unclassified"),
                "last_close": last_close,
                "avg_volume_20d": avg_vol,
            })
        except Exception as e:
            print(f"[warn] {code}: {e}", file=sys.stderr)
        time.sleep(args.sleep)
        if (i + 1) % 20 == 0:
            print(f"{i + 1}/{len(codes)} 件処理済み...")

    if not rows:
        print(f"株価{args.max_price}円以下の条件に合致する銘柄がありませんでした。")
        return

    uni_df = pd.DataFrame(rows).sort_values("avg_volume_20d", ascending=False)
    uni_df = uni_df.head(args.max_universe)
    uni_df["updated_date"] = pd.Timestamp.now().strftime("%Y-%m-%d")
    uni_df.to_csv(args.out, index=False, encoding="utf-8-sig")

    print(f"\nユニバースを {len(uni_df)} 銘柄で更新しました → {args.out}")
    print(uni_df.to_string(index=False))

    driver_counts = uni_df["driver"].value_counts()
    unclassified = driver_counts.get("unclassified", 0)
    if unclassified > 0:
        print(f"\n※ {unclassified}銘柄がドライバー未分類(unclassified)です。"
              f"driver_tags.csv への追加を検討してください。")


if __name__ == "__main__":
    main()
