#!/usr/bin/env python3
"""日経225向け 大引け坊主スクリーニング (docs/nikkei225-marubozu-screening.md 参照)。

J-Quants API v2（ダッシュボード発行のAPI Keyを`x-api-key`ヘッダーで直接使用、
v1のリフレッシュトークン方式は廃止済み）から日足OHLCVを取得し、直近の取引日について
「大引け坊主」（陽線/陰線とも、終値側にヒゲがほぼない実体足）を検出し、
RSI・出来高倍率のクオンツ条件を満たすかどうかを判定して
marubozu_candidates.csv に出力する。

quant_all_pass の閾値（出来高倍率・RSIバンド等）は元の仕様に具体的な数値
指定がなかったため、下記 "クオンツ条件のデフォルト閾値" は暫定値。
運用しながら --volume-ratio-threshold 等のCLI引数で調整すること。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    sys.exit("requests がインストールされていません。`pip install requests pandas` を実行してください。")

try:
    import pandas as pd
    import numpy as np
except ImportError:
    sys.exit("pandas がインストールされていません。`pip install requests pandas` を実行してください。")


JQUANTS_BASE_URL = "https://api.jquants.com/v2"
JQUANTS_DAILY_BARS_PATH = "/equities/bars/daily"

# J-Quants APIのレスポンス列名はプラン/バージョンによって揺れることがあるため、
# 実際に受け取った列名をここに追加していく（KeyErrorのメッセージに実列名が出る）。
# v2 `/equities/bars/daily` の列名: Date, Code, O/H/L/C, UL/LL, Vo/Va,
# AdjFactor, AdjO/AdjH/AdjL/AdjC/AdjVo（他に前場M*/後場A*の内訳列もある）。
# 分割等の調整後株価であるAdj系を優先し、無ければ非調整の生値にフォールバックする。
COLUMN_ALIASES: dict[str, list[str]] = {
    "date": ["Date"],
    "code": ["Code"],
    "open": ["AdjO", "O"],
    "high": ["AdjH", "H"],
    "low": ["AdjL", "L"],
    "close": ["AdjC", "C"],
    "volume": ["AdjVo", "Vo"],
}

# --- 大引け坊主の判定条件（デフォルト） ---
DEFAULT_SHADOW_TOLERANCE = 0.05  # 終値側のヒゲが「当日値幅」に対してこの比率以下ならヒゲなし扱い
DEFAULT_MIN_BODY_RATIO = 0.5     # 実体が当日値幅に対してこの比率以上ないと大引け坊主とみなさない

# --- クオンツ条件のデフォルト閾値（仕様未確定のため暫定値。要調整） ---
DEFAULT_VOLUME_RATIO_THRESHOLD = 1.5   # 出来高 / 直近20日平均出来高
DEFAULT_VOLUME_AVG_WINDOW = 20
DEFAULT_RSI_PERIOD = 14
DEFAULT_RSI_BULLISH_MIN = 50.0
DEFAULT_RSI_BULLISH_MAX = 75.0
DEFAULT_RSI_BEARISH_MIN = 25.0
DEFAULT_RSI_BEARISH_MAX = 50.0

DEFAULT_LOOKBACK_DAYS = 120  # 取得する暦日の遡り幅（RSI・出来高平均に十分な営業日数を確保する）
DEFAULT_REQUEST_DELAY = 0.3  # 1銘柄ごとのAPIリクエスト間隔（秒）


class JQuantsAuthError(RuntimeError):
    pass


class JQuantsClient:
    """J-Quants API v2クライアント。x-api-keyヘッダーで直接認証する（v1のトークン交換は廃止済み）。"""

    def __init__(self, api_key: str, request_delay: float = DEFAULT_REQUEST_DELAY):
        self.api_key = api_key
        self.request_delay = request_delay
        self.session = requests.Session()

    def fetch_daily_bars(self, code: str, date_from: str, date_to: str, max_retries: int = 3) -> list[dict]:
        url = f"{JQUANTS_BASE_URL}{JQUANTS_DAILY_BARS_PATH}"
        headers = {"x-api-key": self.api_key}
        params: dict[str, str] = {"code": code, "from": date_from, "to": date_to}

        all_data: list[dict] = []
        attempt = 0
        while True:
            resp = self.session.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code in (401, 403):
                raise JQuantsAuthError(
                    "JQUANTS_API_KEY（J-Quants APIキー）が無効です。"
                    "J-QuantsのダッシュボードでAPI Keyを確認・再発行してください。"
                )
            if resp.status_code == 429:
                attempt += 1
                if attempt > max_retries:
                    raise RuntimeError(f"{code}: リトライ上限に達しました（レート制限の可能性）。")
                time.sleep(2 ** attempt * 2)
                continue
            resp.raise_for_status()

            payload = resp.json()
            batch = payload.get("data", [])
            if isinstance(batch, list):
                all_data.extend(batch)

            pagination_key = payload.get("pagination_key")
            if not pagination_key:
                break
            params["pagination_key"] = pagination_key
            attempt = 0

        time.sleep(self.request_delay)
        return all_data


def resolve_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {}
    missing = []
    for canonical, candidates in COLUMN_ALIASES.items():
        found = next((c for c in candidates if c in df.columns), None)
        if found is None:
            missing.append(canonical)
        else:
            rename_map[found] = canonical
    if missing:
        raise KeyError(
            f"必要な列 {missing} が見つかりません。実際のレスポンス列名: {list(df.columns)}。"
            "COLUMN_ALIASESに実列名を追加してください。"
        )
    return df.rename(columns=rename_map)


def compute_rsi(close: pd.Series, period: int = DEFAULT_RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100.0)


def classify_marubozu(
    open_: float,
    high: float,
    low: float,
    close: float,
    shadow_tolerance: float = DEFAULT_SHADOW_TOLERANCE,
    min_body_ratio: float = DEFAULT_MIN_BODY_RATIO,
) -> Optional[str]:
    day_range = high - low
    if day_range <= 0:
        return None
    body = abs(close - open_)
    if body / day_range < min_body_ratio:
        return None
    if close >= open_:
        upper_shadow = high - close
        if upper_shadow / day_range <= shadow_tolerance:
            return "陽"
    else:
        lower_shadow = close - low
        if lower_shadow / day_range <= shadow_tolerance:
            return "陰"
    return None


@dataclass
class ScreenConfig:
    shadow_tolerance: float = DEFAULT_SHADOW_TOLERANCE
    min_body_ratio: float = DEFAULT_MIN_BODY_RATIO
    volume_ratio_threshold: float = DEFAULT_VOLUME_RATIO_THRESHOLD
    volume_avg_window: int = DEFAULT_VOLUME_AVG_WINDOW
    rsi_period: int = DEFAULT_RSI_PERIOD
    rsi_bullish_min: float = DEFAULT_RSI_BULLISH_MIN
    rsi_bullish_max: float = DEFAULT_RSI_BULLISH_MAX
    rsi_bearish_min: float = DEFAULT_RSI_BEARISH_MIN
    rsi_bearish_max: float = DEFAULT_RSI_BEARISH_MAX
    target_date: Optional[str] = None


def screen_one_code(client: JQuantsClient, code: str, date_from: str, date_to: str, cfg: ScreenConfig) -> Optional[dict]:
    quotes = client.fetch_daily_bars(code, date_from, date_to)
    if not quotes:
        return {"code": code, "error": "データなし（取得期間内に日足データがありません）"}

    df = pd.DataFrame(quotes)
    df = resolve_columns(df)
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    if cfg.target_date:
        df = df[df["date"] <= pd.Timestamp(cfg.target_date)]

    min_rows_needed = max(cfg.rsi_period, cfg.volume_avg_window) + 2
    if len(df) < min_rows_needed:
        return {"code": code, "error": f"データ不足（{len(df)}件、必要{min_rows_needed}件以上）"}

    df["rsi"] = compute_rsi(df["close"], cfg.rsi_period)
    df["avg_volume"] = df["volume"].shift(1).rolling(cfg.volume_avg_window).mean()

    target = df.iloc[-1]
    pattern = classify_marubozu(
        target["open"], target["high"], target["low"], target["close"],
        cfg.shadow_tolerance, cfg.min_body_ratio,
    )
    if pattern is None:
        return None

    volume_ratio = (
        target["volume"] / target["avg_volume"] if target["avg_volume"] and target["avg_volume"] > 0 else float("nan")
    )
    rsi = target["rsi"]

    fail_reasons = []
    if np.isnan(volume_ratio) or volume_ratio < cfg.volume_ratio_threshold:
        fail_reasons.append(f"volume_ratio<{cfg.volume_ratio_threshold}(actual:{volume_ratio:.2f})")

    if pattern == "陽":
        if not (cfg.rsi_bullish_min <= rsi <= cfg.rsi_bullish_max):
            fail_reasons.append(f"rsi_out_of_band[{cfg.rsi_bullish_min}-{cfg.rsi_bullish_max}](actual:{rsi:.1f})")
    else:
        if not (cfg.rsi_bearish_min <= rsi <= cfg.rsi_bearish_max):
            fail_reasons.append(f"rsi_out_of_band[{cfg.rsi_bearish_min}-{cfg.rsi_bearish_max}](actual:{rsi:.1f})")

    return {
        "code": code,
        "date": target["date"].strftime("%Y-%m-%d"),
        "pattern": pattern,
        "close": float(target["close"]),
        "rsi14": round(float(rsi), 1),
        "volume": int(target["volume"]),
        "avg_volume": round(float(target["avg_volume"]), 1) if not np.isnan(target["avg_volume"]) else None,
        "volume_ratio": round(float(volume_ratio), 2) if not np.isnan(volume_ratio) else None,
        "quant_all_pass": len(fail_reasons) == 0,
        "fail_reasons": ";".join(fail_reasons) if fail_reasons else "",
    }


def load_codes(codes_path: str) -> list[str]:
    df = pd.read_csv(codes_path, dtype=str, header=None)
    first_cell = str(df.iloc[0, 0]).strip()
    if not (len(first_cell) >= 4 and first_cell[:4].isalnum() and first_cell[0].isdigit()):
        df = df.iloc[1:]  # 1行目はヘッダー行とみなしてスキップ
    codes = df.iloc[:, 0].dropna().astype(str).str.strip().tolist()
    return [c for c in codes if c]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", required=True, help="証券コード一覧CSV（1列目がコード）")
    parser.add_argument("--output", default="marubozu_candidates.csv")
    parser.add_argument("--date", default=None, help="対象日(YYYY-MM-DD)。省略時は取得できた最新営業日")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--shadow-tolerance", type=float, default=DEFAULT_SHADOW_TOLERANCE)
    parser.add_argument("--min-body-ratio", type=float, default=DEFAULT_MIN_BODY_RATIO)
    parser.add_argument("--volume-ratio-threshold", type=float, default=DEFAULT_VOLUME_RATIO_THRESHOLD)
    parser.add_argument("--rsi-bullish-min", type=float, default=DEFAULT_RSI_BULLISH_MIN)
    parser.add_argument("--rsi-bullish-max", type=float, default=DEFAULT_RSI_BULLISH_MAX)
    parser.add_argument("--rsi-bearish-min", type=float, default=DEFAULT_RSI_BEARISH_MIN)
    parser.add_argument("--rsi-bearish-max", type=float, default=DEFAULT_RSI_BEARISH_MAX)
    parser.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY)
    args = parser.parse_args()

    api_key = os.environ.get("JQUANTS_API_KEY")
    if not api_key:
        print("環境変数 JQUANTS_API_KEY が設定されていません。", file=sys.stderr)
        return 1

    if not os.path.exists(args.codes):
        print(f"銘柄コード一覧が見つかりません: {args.codes}", file=sys.stderr)
        return 1

    codes = load_codes(args.codes)
    if not codes:
        print(f"{args.codes} から証券コードを読み取れませんでした。", file=sys.stderr)
        return 1

    to_date = date.fromisoformat(args.date) if args.date else date.today()
    from_date = to_date - timedelta(days=args.lookback_days)

    cfg = ScreenConfig(
        shadow_tolerance=args.shadow_tolerance,
        min_body_ratio=args.min_body_ratio,
        volume_ratio_threshold=args.volume_ratio_threshold,
        rsi_bullish_min=args.rsi_bullish_min,
        rsi_bullish_max=args.rsi_bullish_max,
        rsi_bearish_min=args.rsi_bearish_min,
        rsi_bearish_max=args.rsi_bearish_max,
        target_date=args.date,
    )

    client = JQuantsClient(api_key, request_delay=args.request_delay)

    candidates: list[dict] = []
    errors: list[tuple[str, str]] = []

    for i, code in enumerate(codes, start=1):
        print(f"[{i}/{len(codes)}] {code} を取得中...", file=sys.stderr)
        try:
            result = screen_one_code(client, code, from_date.isoformat(), to_date.isoformat(), cfg)
        except JQuantsAuthError as e:
            print(str(e), file=sys.stderr)
            return 1
        except Exception as e:  # noqa: BLE001 - 1銘柄の失敗で全体を止めない
            errors.append((code, str(e)))
            continue

        if result is None:
            continue
        if "error" in result:
            errors.append((code, result["error"]))
            continue
        candidates.append(result)

    if errors:
        print(f"\n{len(errors)}銘柄でエラー/データ不足が発生しました:", file=sys.stderr)
        for code, msg in errors:
            print(f"  {code}: {msg}", file=sys.stderr)

    out_df = pd.DataFrame(candidates, columns=[
        "code", "date", "pattern", "close", "rsi14",
        "volume", "avg_volume", "volume_ratio", "quant_all_pass", "fail_reasons",
    ])
    out_df.to_csv(args.output, index=False, encoding="utf-8-sig")

    pass_count = int(out_df["quant_all_pass"].sum()) if not out_df.empty else 0
    print(f"\n大引け坊主 検出: {len(out_df)}件（うち quant_all_pass=True: {pass_count}件） -> {args.output}")

    if not candidates and errors and len(errors) == len(codes):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
