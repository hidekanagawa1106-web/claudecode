"""
日次スクリーニング + 記録スクリプト
====================================

毎日（大引け後）に実行する想定。流れは以下の3ステップ:

1. 前回ピックアップした銘柄について、その後の値動きが実データで取得できていれば
   picks_log.csv に記録する（記録するだけで、ロジックは変えない）
2. universe.csv の範囲内で、本日の大引け坊主＋運用ルール条件をスクリーニングする
3. quant_all_passとドライバーの偏りを考慮しつつ上位N銘柄(デフォルト5)を選び、
   picks_log.csv に新規行として追記する

重要な制約:
- J-Quants側で取得できるのは日足OHLCVです。オープニングレンジブレイク(ORB)の
  ような分足ベースの条件は、このスクリプトの記録には含まれていません。
  記録できるのは「前日終値→翌日始値のギャップ率」「翌日の始値→終値の当日変化率」
  までです。ORBまで含めた検証をしたい場合は、別途分足データの取得手段が必要です。
- 「材料の裏付け」(運用方針_v2 セクション7 [3])は自動判定していません。
  quant_all_pass=True の銘柄についても、材料面は手動で確認してください。
- ここで出力される銘柄は「翌日ウォッチする候補」であり、実際のエントリーは
  場中に順張り4条件(ORB・VWAP・出来高・連動銘柄)を満たすかで別途判断してください。

事前準備:
- universe.csv (build_universe.py で生成済み)
- picks_log.csv (初回実行時に自動生成される)
- 環境変数 JQUANTS_API_KEY

使い方（毎日、大引け後に実行）:
    python screen_daily.py --universe universe.csv --log picks_log.csv --top-n 5
"""

import os
import sys
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

PICKS_LOG_COLUMNS = [
    "pick_date", "code", "driver", "pattern", "ma25_break", "volume_ok",
    "rsi", "quant_all_pass", "pick_rank", "prev_close",
    "outcome_date", "next_open", "next_high", "next_low", "next_close",
    "gap_pct", "day_change_pct", "outcome_recorded",
]


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
            raise KeyError(f"カラム '{standard_name}' が見つかりません: {list(df.columns)}")
    return df.rename(columns=rename_map)


def fetch_daily_bars(code: str, headers: dict, lookback_days: int = 60) -> pd.DataFrame:
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


def compute_rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean().iloc[-1]
    avg_loss = loss.rolling(period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def check_marubozu(row, wick_threshold: float = 0.05):
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    rng = h - l
    if rng <= 0:
        return None
    if c > o:
        if (h - c) / rng <= wick_threshold:
            return "陽の大引け坊主"
    elif c < o:
        if (c - l) / rng <= wick_threshold:
            return "陰の大引け坊主"
    return None


def load_log(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        df = pd.read_csv(path, dtype={"code": str})
        for col in PICKS_LOG_COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df
    return pd.DataFrame(columns=PICKS_LOG_COLUMNS)


def record_outcomes(log_df: pd.DataFrame, headers: dict) -> pd.DataFrame:
    """記録待ち(outcome_recorded != True)の銘柄について、翌営業日の値動きを埋める。"""
    pending_idx = log_df[log_df["outcome_recorded"] != True].index
    for idx in pending_idx:
        row = log_df.loc[idx]
        try:
            df = fetch_daily_bars(row["code"], headers, lookback_days=10)
            after = df[df["date"] > row["pick_date"]]
            if after.empty:
                continue  # まだ翌営業日のデータが出ていない
            next_bar = after.iloc[0]
            prev_close = row.get("prev_close")
            log_df.at[idx, "outcome_date"] = next_bar["date"]
            log_df.at[idx, "next_open"] = next_bar["open"]
            log_df.at[idx, "next_high"] = next_bar["high"]
            log_df.at[idx, "next_low"] = next_bar["low"]
            log_df.at[idx, "next_close"] = next_bar["close"]
            if prev_close:
                log_df.at[idx, "gap_pct"] = round((next_bar["open"] / prev_close - 1) * 100, 2)
            if next_bar["open"]:
                log_df.at[idx, "day_change_pct"] = round((next_bar["close"] / next_bar["open"] - 1) * 100, 2)
            log_df.at[idx, "outcome_recorded"] = True
        except Exception as e:
            print(f"[warn] outcome記録失敗 {row['code']}: {e}", file=sys.stderr)
    return log_df


def screen_universe(universe_df: pd.DataFrame, headers: dict):
    results = []
    for _, urow in universe_df.iterrows():
        code = urow["code"]
        try:
            df = fetch_daily_bars(code, headers)
            if len(df) < 26:
                continue
            latest = df.iloc[-1]
            pattern = check_marubozu(latest)
            if pattern is None:
                continue

            ma25 = df["close"].rolling(25).mean()
            ma25_now, ma25_prev = ma25.iloc[-1], ma25.iloc[-2]
            cond_ma25_break = latest["close"] > ma25_now
            cond_ma25_trend = ma25_now >= ma25_prev

            avg_vol20 = df["volume"].iloc[-21:-1].mean()
            cond_volume = latest["volume"] >= avg_vol20 * 1.2

            rsi = compute_rsi(df["close"])
            cond_rsi = rsi < 70

            quant_all_pass = cond_ma25_break and cond_ma25_trend and cond_volume and cond_rsi

            results.append({
                "pick_date": latest["date"],
                "code": code,
                "driver": urow.get("driver", "unclassified"),
                "pattern": pattern,
                "ma25_break": cond_ma25_break,
                "volume_ok": cond_volume,
                "rsi": round(rsi, 1),
                "quant_all_pass": quant_all_pass,
                "prev_close": latest["close"],  # 翌日gap計算用に当日終値を保存
                "_volume_ratio": (latest["volume"] / avg_vol20) if avg_vol20 else 0,
            })
        except Exception as e:
            print(f"[warn] {code}: {e}", file=sys.stderr)
    return results


def select_top_n(candidates: list, top_n: int, max_per_driver: int = 2) -> list:
    """quant_all_pass優先、出来高倍率で並び替えたうえで、
    同じドライバーが max_per_driver 銘柄を超えないように上位N銘柄を選ぶ。
    """
    ranked = sorted(candidates, key=lambda r: (r["quant_all_pass"], r["_volume_ratio"]), reverse=True)
    selected, driver_count = [], {}
    for r in ranked:
        d = r["driver"]
        if driver_count.get(d, 0) >= max_per_driver:
            continue
        selected.append(r)
        driver_count[d] = driver_count.get(d, 0) + 1
        if len(selected) >= top_n:
            break
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", required=True)
    parser.add_argument("--log", default="picks_log.csv")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--max-per-driver", type=int, default=2)
    args = parser.parse_args()

    headers = get_headers()
    universe_df = pd.read_csv(args.universe, dtype={"code": str})
    log_df = load_log(args.log)

    log_df = record_outcomes(log_df, headers)

    candidates = screen_universe(universe_df, headers)
    if not candidates:
        print("本日、大引け坊主に該当する銘柄はありませんでした。")
        log_df.to_csv(args.log, index=False, encoding="utf-8-sig")
        return

    selected = select_top_n(candidates, args.top_n, args.max_per_driver)

    for i, r in enumerate(selected):
        new_row = {c: r.get(c) for c in PICKS_LOG_COLUMNS if c in r}
        new_row["pick_rank"] = i + 1
        new_row["outcome_recorded"] = False
        log_df = pd.concat([log_df, pd.DataFrame([new_row])], ignore_index=True)

    log_df.to_csv(args.log, index=False, encoding="utf-8-sig")

    print(f"\n本日の候補（上位{len(selected)}銘柄、明日のウォッチ対象）:")
    for r in selected:
        print(f"  {r['code']} [{r['driver']}] {r['pattern']} RSI={r['rsi']} "
              f"quant_all_pass={r['quant_all_pass']}")

    completed = log_df[log_df["outcome_recorded"] == True]
    print(f"\n記録済みサンプル数: {len(completed)}件"
          f"（20件たまったら review.py でのレビューを検討してください）")

    print("\n※ 材料の裏付け(セクション7 [3])は自動判定していません。手動確認をお願いします。")
    print("※ ここに出た銘柄も、実際のエントリーは場中の順張り4条件を満たすかどうかで判断してください。")
    print("※ ここでの「値動きの記録」は日足ベースです。ORB(寄り付き後の分足)までは検証していません。")


if __name__ == "__main__":
    main()
