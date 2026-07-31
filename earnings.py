"""
決算情報の取得（記録専用・スコアには使わない）
==============================================

2種類の情報を扱う。

1. 翌営業日の決算発表予定 … JPXが無料公開しているExcelから取得
   https://www.jpx.co.jp/listing/event-schedules/financial-announcement/
   毎営業日17時頃に「翌営業日分」が更新される。
   ※ JPX自身が「掲載外の会社が発表することがあり、予定変更もありうる」と
     注意書きしているため、この一覧は完全ではない。

2. 発表済み決算の内容 … J-Quants /v2/fins/summary から算出
   - 前年同期比の増益率
   - 通期会社予想に対する進捗率
   - 通期予想の修正方向（予想の修正開示も独立行として入っている）

重要: ここで得た値は picks_log.csv に記録するだけで、
銘柄の選定・順位付けには一切使わない。
決算がスイングの成績に効くかどうかはサンプルが貯まってから
review.py で検証し、配点するかどうかはHideさんが判断する。

制約:
- アナリストコンセンサスは取得できない。会社予想比・前年同期比での代用であり、
  「コンセンサス比では未達」という決算を良判定する可能性がある。
- 営業利益(OP)が空の企業がある（会計基準・開示形式による）。
  その場合は純利益(NP)にフォールバックする。
"""

import os
import re
import time
import datetime as dt

import pandas as pd
import requests

API_BASE = "https://api.jquants.com/v2"
JPX_INDEX = "https://www.jpx.co.jp/listing/event-schedules/financial-announcement/index.html"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def get_with_retry(url: str, params: dict, headers: dict, max_retries: int = 5):
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


# --------------------------------------------------------------------------
# 1. 翌営業日の決算発表予定（JPX無料Excel）
# --------------------------------------------------------------------------

def fetch_jpx_schedule(cache_path: str = "kessan_schedule.xlsx") -> set:
    """翌営業日に決算発表を予定している銘柄コードの集合を返す。

    取得に失敗した場合は空集合を返す（記録用の情報なので、
    ここで落として日次スクリーニング全体を止めない）。
    """
    try:
        today = dt.date.today().isoformat()
        stamp = cache_path + ".date"
        cached = os.path.exists(cache_path) and \
            os.path.exists(stamp) and open(stamp).read().strip() == today
        if not cached:
            idx = requests.get(JPX_INDEX, headers={"User-Agent": UA}, timeout=45)
            idx.raise_for_status()
            # 「翌営業日分」は kessan.xlsx。月次アーカイブ(kessan05_0703.xlsx等)は除く
            m = re.search(r'href="([^"]*/kessan\.xlsx)"', idx.text)
            if not m:
                return set()
            url = requests.compat.urljoin(JPX_INDEX, m.group(1))
            xls = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            xls.raise_for_status()
            with open(cache_path, "wb") as f:
                f.write(xls.content)
            with open(stamp, "w") as f:
                f.write(today)

        df = pd.read_excel(cache_path, skiprows=4)
        df = df.iloc[:, :2]
        df.columns = ["予定日", "code"]
        df = df.dropna(subset=["code"])
        codes = df["code"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        return set(codes)
    except Exception:
        return set()


# --------------------------------------------------------------------------
# 2. 発表済み決算の内容（J-Quants）
# --------------------------------------------------------------------------

def _num(series):
    return pd.to_numeric(series, errors="coerce")


def _pick(row, primary: str, fallback: str):
    """営業利益が空の企業があるため、純利益にフォールバックする。"""
    for col in (primary, fallback):
        v = pd.to_numeric(row.get(col), errors="coerce")
        if pd.notna(v):
            return v, col
    return None, None


def fetch_earnings_signals(code: str, headers: dict, bar_dates: list) -> dict:
    """直近の決算内容を算出する。

    bar_dates: 日足の日付リスト(昇順)。決算開示から何営業日経過したかを
               実際の立会日ベースで数えるために使う。
    """
    out = {"earnings_days_ago": None, "earnings_op_yoy": None,
           "earnings_progress": None, "earnings_revision": None}
    try:
        resp = get_with_retry(f"{API_BASE}/fins/summary", {"code": code}, headers)
        payload = resp.json()
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        df = pd.DataFrame(rows)
        if df.empty:
            return out
        df = df.sort_values("DiscDate")
    except Exception:
        return out

    pick_date = bar_dates[-1]
    past = df[df["DiscDate"] <= pick_date]
    if past.empty:
        return out

    # 実績を伴う開示（予想の修正のみの行を除く）を最新の決算とみなす
    actual = past[_num(past["Sales"]).notna()]
    if actual.empty:
        return out
    latest = actual.iloc[-1]
    disc = latest["DiscDate"]

    # 立会日ベースの経過営業日数
    out["earnings_days_ago"] = sum(1 for d in bar_dates if disc < d <= pick_date)

    # 前年同期比: 同じ四半期区分で、決算期(CurFYSt)が異なる直近の実績と比較
    same_q = actual[actual["CurPerType"] == latest["CurPerType"]]
    prev = same_q[same_q["CurFYSt"] != latest["CurFYSt"]]
    cur_val, used_col = _pick(latest, "OP", "NP")
    if cur_val is not None and not prev.empty:
        prev_val = pd.to_numeric(prev.iloc[-1].get(used_col), errors="coerce")
        if pd.notna(prev_val) and prev_val > 0:
            out["earnings_op_yoy"] = round((cur_val / prev_val - 1) * 100, 1)

    # 予想は必ず同じ決算期(CurFYSt)のもの同士で比較する。
    # 決算期をまたいで比べると、新年度の初回予想を「上方修正」と誤判定する。
    same_fy = past[past["CurFYSt"] == latest["CurFYSt"]]
    fcast_col = "FOP" if used_col == "OP" else "FNP"
    fcast = _num(same_fy[fcast_col]).dropna()

    # 通期進捗率: 累計実績 / 通期会社予想（通期実績の場合は着地の達成率になる）
    if cur_val is not None and len(fcast) and fcast.iloc[-1] > 0:
        out["earnings_progress"] = round(cur_val / fcast.iloc[-1] * 100, 1)

    # 通期予想の修正方向: 同一決算期内での直近2つの予想値を比較
    if len(fcast) >= 2:
        now, before = fcast.iloc[-1], fcast.iloc[-2]
        if before and before != 0:
            diff = (now / before - 1) * 100
            out["earnings_revision"] = "up" if diff > 1 else ("down" if diff < -1 else "flat")
    elif len(fcast) == 1:
        out["earnings_revision"] = "initial"  # その期の初回予想（修正なし）

    return out
