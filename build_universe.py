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
    "turnover": ["turnover", "Turnover", "Va", "va"],
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


def fetch_daily_bars(code: str, headers: dict, lookback_days: int = 20) -> pd.DataFrame:
    params = {"code": code}
    resp = get_with_retry(f"{API_BASE}/equities/bars/daily", params, headers)
    data = resp.json()
    if isinstance(data, list):
        records = data
    else:
        # v2 は {"data": [...]}、v1 系は {"daily_quotes": [...]} で返る
        records = data.get("data", data.get("daily_quotes", []))
    df = pd.DataFrame(records)
    df = normalize_columns(df)
    return df.sort_values("date").tail(lookback_days).reset_index(drop=True)


def load_driver_tags(path: str) -> tuple:
    """(code -> driver, code -> 連動グループ) を返す。

    driver はレポート表示用の細かい分類。group は「同じ材料で一緒に動く」
    銘柄群で、同日に複数を選ばないための上限判定に使う。
    連動先を持たない銘柄は group を空欄にし、上限判定の対象外とする。
    """
    if not path or not os.path.exists(path):
        return {}, {}
    df = pd.read_csv(path, dtype=str)
    drivers = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
    groups = {}
    if df.shape[1] >= 3:
        groups = {c: g for c, g in zip(df.iloc[:, 0], df.iloc[:, 2].fillna(""))}
    return drivers, groups


def load_keep_codes(path: str) -> set:
    """条件を満たさなくてもユニバースに残すコード一覧(1列目がコード)。"""
    if not path or not os.path.exists(path):
        return set()
    df = pd.read_csv(path, dtype=str)
    return set(df.iloc[:, 0].str.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", required=True, help="日経225銘柄コード一覧CSV")
    parser.add_argument("--driver-tags", default="driver_tags.csv", help="code,driver の2列CSV(任意)")
    parser.add_argument("--max-price", type=float, default=5000)
    parser.add_argument("--min-turnover", type=float, default=120,
                        help="20日平均売買代金の下限(億円)。順位ではなく絶対閾値で切る")
    parser.add_argument("--keep-codes", default="keep_codes.csv",
                        help="条件を満たさなくても継続ウォッチするコード一覧CSV(任意)")
    parser.add_argument("--max-universe", type=int, default=50,
                        help="core銘柄の上限。watch銘柄はこの上限の対象外")
    parser.add_argument("--out", default="universe.csv")
    parser.add_argument("--sleep", type=float, default=0.3)
    args = parser.parse_args()

    headers = get_headers()
    codes_df = pd.read_csv(args.codes, dtype=str)
    codes = codes_df.iloc[:, 0].tolist()
    driver_map, group_map = load_driver_tags(args.driver_tags)

    keep_codes = load_keep_codes(args.keep_codes)
    min_turnover_yen = args.min_turnover * 1e8

    rows = []
    for i, code in enumerate(codes):
        try:
            df = fetch_daily_bars(code, headers, lookback_days=20)
            if df.empty:
                continue
            last_close = df["close"].iloc[-1]
            avg_turnover = df["turnover"].mean()
            # core: 価格・売買代金の両条件を満たす銘柄（売買候補）
            # watch: 条件は満たさないが keep_codes.csv で継続ウォッチ指定された銘柄
            is_core = (last_close <= args.max_price) and (avg_turnover >= min_turnover_yen)
            if not is_core and code not in keep_codes:
                continue
            rows.append({
                "code": code,
                "driver": driver_map.get(code, "unclassified"),
                "group": group_map.get(code, ""),
                "tier": "core" if is_core else "watch",
                "last_close": last_close,
                "avg_volume_20d": df["volume"].mean(),
                "avg_turnover_20d": avg_turnover,
            })
        except Exception as e:
            print(f"[warn] {code}: {e}", file=sys.stderr)
        time.sleep(args.sleep)
        if (i + 1) % 20 == 0:
            print(f"{i + 1}/{len(codes)} 件処理済み...")

    if not rows:
        print(f"株価{args.max_price}円以下・売買代金{args.min_turnover}億円以上の"
              f"条件に合致する銘柄がありませんでした。")
        return

    all_df = pd.DataFrame(rows).sort_values("avg_turnover_20d", ascending=False)
    # core だけ max_universe で上限を掛け、watch は上限の対象外
    core_df = all_df[all_df["tier"] == "core"].head(args.max_universe)
    watch_df = all_df[all_df["tier"] == "watch"]
    uni_df = pd.concat([core_df, watch_df], ignore_index=True)
    uni_df["updated_date"] = pd.Timestamp.now().strftime("%Y-%m-%d")
    uni_df.to_csv(args.out, index=False, encoding="utf-8-sig")

    print(f"\nユニバースを更新しました → {args.out}")
    print(f"  core  {len(core_df)}銘柄（{args.max_price:.0f}円以下 かつ "
          f"20日平均売買代金 {args.min_turnover:.0f}億円以上）")
    print(f"  watch {len(watch_df)}銘柄（条件外だが継続ウォッチ指定）")
    disp = uni_df.copy()
    disp["代金(億円)"] = (disp["avg_turnover_20d"] / 1e8).round(0)
    print(disp[["code", "driver", "tier", "last_close", "代金(億円)"]].to_string(index=False))

    driver_counts = uni_df["driver"].value_counts()
    unclassified = driver_counts.get("unclassified", 0)
    if unclassified > 0:
        print(f"\n※ {unclassified}銘柄がドライバー未分類(unclassified)です。"
              f"driver_tags.csv への追加を検討してください。")


if __name__ == "__main__":
    main()
