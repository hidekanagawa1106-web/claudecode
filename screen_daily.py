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
import time
import argparse
import pandas as pd
import requests

import earnings

API_BASE = "https://api.jquants.com/v2"

# 分割調整済み(Adj*)を優先する。未調整のO/H/L/C/Voを使うと、分割をまたいだ
# MA25・MA75・RSI・出来高倍率がすべて壊れる（例: 2026-07-30の8309は4分割で
# 見かけ上-75.6%、RSIも11.5まで潰れていた）。
COLUMN_ALIASES = {
    "date": ["date", "Date"],
    "open": ["AdjO", "open", "Open", "O"],
    "high": ["AdjH", "high", "High", "H"],
    "low": ["AdjL", "low", "Low", "L"],
    "close": ["AdjC", "close", "Close", "C"],
    "volume": ["AdjVo", "volume", "Volume", "Vo", "vo"],
}

PICKS_LOG_COLUMNS = [
    "pick_date", "code", "track", "driver", "group", "tier", "pattern", "ma25_break", "volume_ok",
    "rsi", "quant_all_pass", "pick_rank", "prev_close",
    # スコア（順位付けに使用）
    "score", "score_trend", "score_rsi", "score_volume", "score_candle",
    "volume_ratio", "perfect_order",
    # 決算（記録のみ。順位付けには一切使わない）
    "earnings_days_ago", "earnings_op_yoy", "earnings_progress",
    "earnings_revision", "earnings_next", "catalyst_reasons",
    # マクロ（記録のみ。順位付けには一切使わない）
    # 商社以外の5グループは閾値・感応度が未検証のため、点数に混ぜずに記録だけ残す。
    # サンプルが貯まれば「マクロが良い日の候補は成績が良かったか」を後から測れる。
    "macro_score", "macro_threshold",
    # 成果
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


def get_with_retry(url: str, params: dict, headers: dict, max_retries: int = 5):
    """429(レート制限)は指数バックオフで待って再試行する。

    途中で取得に失敗すると、その銘柄が候補から漏れたまま picks_log.csv に
    記録されてしまうため、取得は諦めずにリトライする。
    """
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


def fetch_daily_bars(code: str, headers: dict, lookback_days: int = 120) -> pd.DataFrame:
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


# --------------------------------------------------------------------------
# スコアリング
#
# ハードフィルタは「終値 > MA25 かつ MA25上向き」のみ。
# 大引け坊主は必須条件ではなく、ローソク足軸の加点要素として扱う。
#
# 注: ハードフィルタを通った銘柄は score_trend の 15+10=25点を必ず獲得するため、
# その25点は全候補に共通の下駄になる。順位は残り75点分で決まる。
# --------------------------------------------------------------------------

def score_trend(close, ma5, ma25, ma25_prev, ma75) -> tuple:
    score = 0
    if close > ma25:
        score += 15
    if ma25 >= ma25_prev:
        score += 10
    perfect = ma75 is not None and pd.notna(ma75) and close > ma5 > ma25 > ma75
    if perfect:
        score += 15
    return score, bool(perfect)


def score_rsi(rsi: float) -> int:
    if rsi >= 75:
        return 0
    if rsi >= 70:
        return 5
    if rsi >= 65:
        return 12
    if rsi >= 50:
        return 20
    return 8


def score_volume(ratio: float) -> int:
    if ratio >= 2.0:
        return 20
    if ratio >= 1.5:
        return 15
    if ratio >= 1.2:
        return 10
    if ratio >= 1.0:
        return 5
    return 0


def score_candle(row) -> tuple:
    """ストップ高 +30 / 陽の大引け坊主 +20 / 陽線・上ヒゲ10%以内 +10
    / 陰の大引け坊主 -10 / ストップ安 -30

    ストップ高は値幅がゼロになるため大引け坊主の判定が成立せず、
    売り手がいないので出来高も激減する。素の配点のままだと最強の需給が
    最低点になってしまうため、ローソク足の判定より先に見る。
    """
    if str(row.get("UL", "0")).strip() == "1":
        return 30, "ストップ高"
    if str(row.get("LL", "0")).strip() == "1":
        return -30, "ストップ安"
    pattern = check_marubozu(row)
    if pattern == "陽の大引け坊主":
        return 20, pattern
    if pattern == "陰の大引け坊主":
        return -10, pattern
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    rng = h - l
    if rng > 0 and c > o and (h - c) / rng <= 0.10:
        return 10, "陽線(上ヒゲ小)"
    return 0, "-"


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
            # 分割調整値は遡って再計算されるため、記録時の prev_close は
            # 分割をまたぐと基準がずれる。取得し直した pick_date の終値を優先する。
            same_day = df[df["date"] == row["pick_date"]]
            prev_close = same_day["close"].iloc[-1] if not same_day.empty else row.get("prev_close")
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


def detect_catalysts(rows: list, min_chg: float = 3.0,
                     min_group_ratio: float = 1 / 3, min_group_count: int = 3) -> list:
    """上昇トレンド条件を満たさない銘柄から、明確な需給変化があったものを拾う。

    順張りのハードフィルタは「下降トレンドの途中で買う」(運用方針 §4 禁止事項1)を
    防ぐためのものだが、セクター全体が外部材料で急反発した日を丸ごと取りこぼす。
    実例として2026-07-31、半導体・AI関連19銘柄のうち18銘柄が+3%以上動いたが、
    7月の調整でMA25の下にいたため1銘柄も候補に出なかった。

    検知するのは「材料がありそうな値動きの形」だけで、材料そのもの
    (海外決算・マクロ指標など)は判定しない。そこは翌朝の確認に委ねる。
    """
    # セクター一斉高: 同一連動グループの一定割合が同日に大きく上げた
    group_total, group_hot = {}, {}
    for r in rows:
        g = r.get("group") or ""
        if not g:
            continue
        group_total[g] = group_total.get(g, 0) + 1
        if r["_chg"] >= min_chg:
            group_hot[g] = group_hot.get(g, 0) + 1
    hot_groups = {
        g: (group_hot[g], group_total[g]) for g in group_hot
        if group_hot[g] >= min_group_count
        and group_hot[g] / group_total[g] >= min_group_ratio
    }

    out = []
    for r in rows:
        if r["_passes_trend"]:
            continue  # 順張りトラックで拾うため二重に出さない
        reasons = []
        if r["pattern"] == "ストップ高":
            reasons.append("ストップ高")
        g = r.get("group") or ""
        if g in hot_groups:
            hot, tot = hot_groups[g]
            reasons.append(f"セクター一斉高({g} {tot}銘柄中{hot}銘柄が+{min_chg:.0f}%以上)")
        if r["_chg"] >= 5.0 and r["volume_ratio"] >= 2.0:
            reasons.append(f"大幅高+出来高{r['volume_ratio']:.1f}倍")
        if reasons:
            out.append({**r, "catalyst_reasons": " / ".join(reasons)})
    out.sort(key=lambda r: r["_chg"], reverse=True)
    return out


def screen_universe(universe_df: pd.DataFrame, headers: dict, schedule: set):
    """全銘柄を採点する。

    戻り値は (順張り候補, 材料検知, ファネル集計, 除外理由)。
    材料検知のためにセクター全体の動きを見る必要があるため、
    ハードフィルタで落ちた銘柄も指標を計算してから振り分ける。
    """
    all_rows, rejected = [], []
    funnel = {"母集団": 0, "データ不足": 0, "上昇トレンド": 0}

    for _, urow in universe_df.iterrows():
        code = urow["code"]
        funnel["母集団"] += 1
        try:
            df = fetch_daily_bars(code, headers)
            if len(df) < 26:
                funnel["データ不足"] += 1
                rejected.append({"code": code, "reason": "日足データが26本未満"})
                continue
            latest = df.iloc[-1]

            ma5 = df["close"].rolling(5).mean().iloc[-1]
            ma25s = df["close"].rolling(25).mean()
            ma25_now, ma25_prev = ma25s.iloc[-1], ma25s.iloc[-2]
            ma75 = df["close"].rolling(75).mean().iloc[-1] if len(df) >= 75 else None

            cond_ma25_break = latest["close"] > ma25_now
            cond_ma25_trend = ma25_now >= ma25_prev
            passes_trend = bool(cond_ma25_break and cond_ma25_trend)

            # 順張りのハードフィルタは上昇トレンドのみ。ただし落ちた銘柄も
            # セクター一斉高の判定に必要なため、指標は全銘柄で計算する。
            if passes_trend:
                funnel["上昇トレンド"] += 1
            else:
                why = "終値がMA25の下" if not cond_ma25_break else "MA25が下向き"
                rejected.append({"code": code, "driver": urow.get("driver", ""),
                                 "reason": why})

            avg_vol20 = df["volume"].iloc[-21:-1].mean()
            vol_ratio = (latest["volume"] / avg_vol20) if avg_vol20 else 0
            rsi = compute_rsi(df["close"])

            s_trend, perfect = score_trend(latest["close"], ma5, ma25_now, ma25_prev, ma75)
            s_rsi = score_rsi(rsi)
            s_vol = score_volume(vol_ratio)
            s_candle, pattern = score_candle(latest)

            # 決算は記録のみ。スコアには加算しない。
            # 全74銘柄で叩くとAPI負荷が倍増するため、順張り候補のみ取得する。
            sig = (earnings.fetch_earnings_signals(code, headers, df["date"].tolist())
                   if passes_trend else {})

            all_rows.append({
                "_passes_trend": passes_trend,
                "pick_date": latest["date"],
                "code": code,
                "driver": urow.get("driver", "unclassified"),
                "group": urow.get("group", ""),
                "tier": urow.get("tier", ""),
                "pattern": pattern,
                "ma25_break": cond_ma25_break,
                "volume_ok": vol_ratio >= 1.2,
                "rsi": round(rsi, 1),
                "quant_all_pass": bool(cond_ma25_break and cond_ma25_trend
                                       and vol_ratio >= 1.2 and rsi < 70),
                "prev_close": latest["close"],
                "score": s_trend + s_rsi + s_vol + s_candle,
                "score_trend": s_trend, "score_rsi": s_rsi,
                "score_volume": s_vol, "score_candle": s_candle,
                "volume_ratio": round(vol_ratio, 2),
                "perfect_order": perfect,
                "earnings_next": code in schedule,
                "_chg": round((latest["close"] / df["close"].iloc[-2] - 1) * 100, 2),
                **sig,
            })
        except Exception as e:
            print(f"[warn] {code}: {e}", file=sys.stderr)
            rejected.append({"code": code, "reason": f"取得失敗: {e}"})

    passed = [r for r in all_rows if r["_passes_trend"]]
    catalysts = detect_catalysts(all_rows)
    return passed, catalysts, funnel, rejected


def select_top_n(candidates: list, top_n: int, max_per_driver: int = 2) -> list:
    """スコア降順（同点は出来高倍率）で並べ、同じ連動グループが
    max_per_driver 銘柄を超えないように上位N銘柄を選ぶ。

    上限は driver(表示用の細かい分類)ではなく group(連動グループ)で掛ける。
    同じ材料で一緒に動く銘柄を同日に複数持つのを避けるための制限なので、
    半導体・半導体製造装置・電線DCのように分類が違っても一緒に動く群は
    まとめて数える必要がある。
    group が空欄の銘柄は連動先を持たないため、上限判定の対象外とする。
    """
    ranked = sorted(candidates, key=lambda r: (r["score"], r["volume_ratio"]), reverse=True)
    selected, group_count = [], {}
    for r in ranked:
        # CSVの空欄は NaN(float) で読まれるため、文字列化してから判定する
        raw = r.get("group")
        g = "" if raw is None or pd.isna(raw) else str(raw).strip()
        if g and group_count.get(g, 0) >= max_per_driver:
            continue
        selected.append(r)
        if g:
            group_count[g] = group_count.get(g, 0) + 1
        if len(selected) >= top_n:
            break
    return selected


REVISION_LABEL = {"up": "上方修正", "down": "下方修正",
                  "flat": "据え置き", "initial": "初回予想"}


def _mark(value: int, high: int, mid: int) -> str:
    return "◎" if value >= high else ("○" if value >= mid else ("△" if value > 0 else "×"))


def format_macro(group: str, group_scores: dict) -> str:
    """所属グループの朝スコアを「+2/+2」の形にする。判定はせず、材料として並べるだけ。"""
    if not group_scores:
        return ""
    hit = group_scores.get((group or "").strip())
    if hit is None:
        return "  -  "
    total, threshold = hit
    return f"{total:+d}/{threshold:+d}"


def print_report(selected, candidates, funnel, rejected, names, pick_date,
                 top_n, brief=False, flagged=None, catalysts=None,
                 group_scores=None):
    fmt_e = lambda v: "-" if v is None or pd.isna(v) else v
    nm = lambda c: names.get(c, "")
    mac = lambda r: format_macro(r.get("group"), group_scores)

    print("=" * 66)
    print(f"【翌日ウォッチ優先リスト】  データ: {pick_date} 大引けまで")
    print("=" * 66)

    if not brief:
        print("\n■ 絞り込みファネル")
        print(f"  母集団                              {funnel['母集団']:>3}")
        print(f"  └ 上昇トレンド(終値>MA25 & MA25上向き)  {funnel['上昇トレンド']:>3}"
              f"  (-{funnel['母集団'] - funnel['上昇トレンド'] - funnel['データ不足']})")
        print(f"     └ ドライバー上限2銘柄で上位{top_n}銘柄を提示 → {len(selected)}")

    print("\n■ 優先度ランキング")
    if not selected:
        print("  該当なし（上昇トレンド条件を満たす銘柄がありませんでした）")
    else:
        head = "  順 コード 銘柄               ドライバー      点数  ト 過 出 パ  決算"
        print(head + ("     マクロ" if group_scores else ""))
        for i, r in enumerate(selected, 1):
            ec = "決算前" if r["earnings_next"] else (
                f"{int(r['earnings_days_ago'])}日前" if r.get("earnings_days_ago") is not None
                and not pd.isna(r["earnings_days_ago"]) and r["earnings_days_ago"] <= 3 else "-")
            print(f"  {i:>2} {r['code']:<5} {nm(r['code'])[:16]:<17} {r['driver'][:12]:<13}"
                  f" {r['score']:>4}  {_mark(r['score_trend'],35,25)} "
                  f"{_mark(r['score_rsi'],20,12)} {_mark(r['score_volume'],15,10)} "
                  f"{_mark(r['score_candle']+10,25,15)}  {ec:<6}"
                  + (f" {mac(r):>7}" if group_scores else ""))
        print("  ト=トレンド 過=過熱度(RSI) 出=出来高 パ=ローソク足")
        if group_scores:
            print("  マクロ=所属グループの朝スコア/閾値。点数には加算していません（材料として併記）。")

    if not brief and selected:
        print("\n■ 上位銘柄の所見")
        for i, r in enumerate(selected, 1):
            print(f"  {i}. {r['code']} {nm(r['code'])}  {r['score']}点 / "
                  f"{r['prev_close']:,.1f}円 ({r['_chg']:+.2f}%)")
            print(f"     {r['pattern']} / RSI {r['rsi']} / 出来高 {r['volume_ratio']}倍"
                  f"{' / パーフェクトオーダー' if r['perfect_order'] else ''}")
            if r.get("earnings_days_ago") is not None and not pd.isna(r["earnings_days_ago"]) \
                    and r["earnings_days_ago"] <= 5:
                print(f"     決算: {int(r['earnings_days_ago'])}営業日前に発表 / "
                      f"前年同期比 {fmt_e(r['earnings_op_yoy'])}% / "
                      f"進捗率 {fmt_e(r['earnings_progress'])}% / "
                      f"通期予想 {REVISION_LABEL.get(r['earnings_revision'], '-')}")
            if r["earnings_next"]:
                print("     ⚠ 翌営業日に決算発表予定")

        rest = [c for c in candidates if c not in selected]
        rest.sort(key=lambda r: r["score"], reverse=True)
        if rest:
            print("\n■ 次点（上位に届かなかった通過銘柄）")
            for r in rest[:5]:
                print(f"  {r['code']} {nm(r['code'])[:14]:<15} {r['score']:>3}点 "
                      f"/ RSI {r['rsi']} / 出来高 {r['volume_ratio']}倍 / {r['pattern']}")

    if not brief and rejected:
        print("\n■ 除外された銘柄（見落とし確認用）")
        agg = {}
        for r in rejected:
            agg.setdefault(r["reason"], []).append(r["code"])
        for reason, codes in sorted(agg.items(), key=lambda kv: -len(kv[1])):
            shown = " ".join(f"{c}({nm(c)[:6]})" for c in codes[:8])
            more = f" ほか{len(codes) - 8}銘柄" if len(codes) > 8 else ""
            print(f"  [{reason}] {len(codes)}銘柄")
            print(f"    {shown}{more}")

    if selected:
        dist = {}
        for r in selected:
            dist[r["driver"]] = dist.get(r["driver"], 0) + 1
        print("\n■ ドライバー分布: " + " / ".join(f"{k}{v}" for k, v in dist.items()))
        if len(dist) <= 2:
            print("  ※ 特定ドライバーに偏っています。同じ材料で動く銘柄を重複して"
                  "持たないようご注意ください。")

    if flagged:
        print("\n■ 材料検知（上昇トレンド条件は満たさないが、明確な需給変化あり）")
        for r in flagged:
            print(f"  {r['code']} {nm(r['code'])[:14]:<15} {r['_chg']:+6.2f}%  "
                  f"RSI {r['rsi']:>4}  出来高 {r['volume_ratio']:.2f}倍"
                  + (f"  マクロ {mac(r)}" if group_scores else ""))
            print(f"     {r['catalyst_reasons']}")
        shown = {r["code"] for r in flagged}
        rest = [r for r in (catalysts or []) if r["code"] not in shown]
        if rest:
            print(f"  ほか{len(rest)}銘柄: "
                  + " ".join(f"{r['code']}({nm(r['code'])[:6]} {r['_chg']:+.1f}%)"
                             for r in rest[:8]))
        print("  ※ 上昇の材料そのものは判定していません。海外決算・マクロ指標などを"
              "個別にご確認ください。")
        print("  ※ これらはMA25の下にあり順張りの条件を満たしません（禁止事項1）。"
              "エントリーするなら逆張り4条件(§3-2)での判断になります。")

    nxt = [c for c in candidates if c["earnings_next"]]
    print(f"\n■ 翌営業日に決算発表（ユニバース内の通過銘柄）: {len(nxt)}銘柄")
    if nxt:
        print("  " + " / ".join(f"{c['code']} {nm(c['code'])}" for c in nxt))
    print("  ※ 決算はスコアに加算していません。記録のみで、効果はサンプルが"
          "貯まってから検証します。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", required=True)
    parser.add_argument("--log", default="picks_log.csv")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--max-per-driver", type=int, default=2)
    parser.add_argument("--format", choices=["brief", "full"], default="full")
    parser.add_argument("--names", default="company_master.csv")
    parser.add_argument("--no-schedule", action="store_true",
                        help="JPXの決算発表予定日を取得しない")
    args = parser.parse_args()

    headers = get_headers()
    universe_df = pd.read_csv(args.universe, dtype={"code": str})
    log_df = load_log(args.log)
    log_df = record_outcomes(log_df, headers)

    names = {}
    if os.path.exists(args.names):
        nm = pd.read_csv(args.names, dtype={"code": str})
        names = dict(zip(nm["code"], nm["CoName"]))

    schedule = set() if args.no_schedule else earnings.fetch_jpx_schedule()
    if not schedule and not args.no_schedule:
        print("[warn] JPXの決算発表予定日を取得できませんでした。"
              "earnings_next は全てFalseとして記録します。", file=sys.stderr)

    candidates, catalysts, funnel, rejected = screen_universe(universe_df, headers, schedule)
    selected = select_top_n(candidates, args.top_n, args.max_per_driver) if candidates else []
    # 材料検知も同じ上限で絞る。18銘柄が一斉高しても全部は張れない。
    flagged = select_top_n(catalysts, args.top_n, args.max_per_driver) if catalysts else []

    for track, rows in (("順張り", selected), ("材料検知", flagged)):
        for i, r in enumerate(rows):
            new_row = {c: r.get(c) for c in PICKS_LOG_COLUMNS if c in r}
            new_row["track"] = track
            new_row["pick_rank"] = i + 1
            new_row["outcome_recorded"] = False
            log_df = pd.concat([log_df, pd.DataFrame([new_row])], ignore_index=True)
    log_df.to_csv(args.log, index=False, encoding="utf-8-sig")

    pick_date = (candidates or catalysts)[0]["pick_date"] if (candidates or catalysts) else "-"
    print_report(selected, candidates, funnel, rejected, names, pick_date,
                 args.top_n, brief=(args.format == "brief"), flagged=flagged,
                 catalysts=catalysts)

    completed = log_df[log_df["outcome_recorded"] == True]
    print(f"\n記録済みサンプル数: {len(completed)}件"
          f"（20件たまったら review.py でのレビューを検討してください）")

    print("\n※ 材料の裏付け(セクション7 [3])は自動判定していません。手動確認をお願いします。")
    print("※ ここに出た銘柄も、実際のエントリーは場中の順張り4条件を満たすかどうかで判断してください。")
    print("※ ここでの「値動きの記録」は日足ベースです。ORB(寄り付き後の分足)までは検証していません。")


if __name__ == "__main__":
    main()
