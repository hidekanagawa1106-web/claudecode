"""
朝のブリーフィング（8:00 JST 実行）— 固定15銘柄版
==================================================

これまでとの違い
----------------
`morning.py` は74銘柄をスクリーニングし、スコア順に5件へ絞って出していた。
このスクリプトは**絞らない**。`watchlist.csv` の15銘柄を毎朝そのまま全部評価する。

絞るのをやめた理由:

  1. 4軸100点スコアは順位付けに効いていない。74銘柄×170営業日・6,442観測で
     翌日リターンとの相関 r=0.013 (p=0.28)。五分位も単調にならなかった
  2. 毎日メンバーが入れ替わると、銘柄ごとの値動きの癖が身につかない
  3. 「今日どれを見るか」より「持っている15銘柄が今日どういう状態か」の方が
     場中の判断に直結する

そのかわり、順位は付けない。15銘柄を**今日どの土俵にいるか**で並べる。
これは新しい判定ではなく、運用方針_v2 のどの節が適用されるかを書き出しているだけ。

  順張りの土俵   終値>MA25 かつ MA25が20日前より上   → §3-1 の4条件を場中に確認
  押し目         MA25は上向きだが終値がその下        → §3-2 の逆張り4条件が対象
  トレンド下向き  MA25が20日前より下                → 順張り対象外（禁止事項1）
  見送り         決算当日/翌日、または RSI>70        → §2 イベントフィルタ / 禁止事項2

エントリーそのものは判定しない。場中に entry_check.py（/entry-check）を使う。

使い方:
    python briefing.py
    python briefing.py --no-news        ニュース取得を省く
    python briefing.py --skip-overnight 海外市場の取得を省く
"""

import argparse
import datetime as dt
import sys

import pandas as pd
import yaml

import earnings
import overnight as ov
import screen_daily as sd

STANCE_ORDER = ["順張りの土俵", "押し目", "トレンド下向き", "見送り"]

STANCE_NOTE = {
    "順張りの土俵": "場中に §3-1 の4条件（ORB・VWAP・出来高1.5倍・連動銘柄）を確認してください。",
    "押し目": "MA25は上向きですが終値がその下。入るなら §3-2 の逆張り4条件での判断になります。",
    "トレンド下向き": "MA25が20日前より下。順張りの対象外です（禁止事項1）。",
    "見送り": "運用方針に触れます。今日は新規に入らない前提で見てください。",
}


def evaluate(code: str, urow: dict, headers: dict, schedule: set) -> dict:
    """1銘柄を評価する。絞り込みはしないので、全銘柄で決算シグナルまで取る。"""
    df = sd.fetch_daily_bars(code, headers)
    if len(df) < 26:
        raise ValueError("日足データが26本未満")
    latest = df.iloc[-1]
    close = latest["close"]

    ma5 = df["close"].rolling(5).mean().iloc[-1]
    ma25s = df["close"].rolling(25).mean()
    ma25_now, ma25_prev = ma25s.iloc[-1], ma25s.iloc[-2]
    ma75 = df["close"].rolling(75).mean().iloc[-1] if len(df) >= 75 else None
    ma25_ref = ma25s.iloc[-21] if len(ma25s) >= 21 else ma25_prev

    above_ma25 = bool(close > ma25_now)
    ma25_up = bool(pd.notna(ma25_ref) and ma25_now > ma25_ref)

    avg_vol20 = df["volume"].iloc[-21:-1].mean()
    vol_ratio = (latest["volume"] / avg_vol20) if avg_vol20 else 0
    rsi = sd.compute_rsi(df["close"])

    # ATR は「1単元あたり1日いくら動くか」を出すために使う。
    # 建玉の大きさが銘柄ごとに10倍違うため、%だけでは実感が湧かない。
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]

    s_trend, perfect = sd.score_trend(close, ma5, ma25_now, ma25_prev, ma75)
    s_rsi = sd.score_rsi(rsi)
    s_vol = sd.score_volume(vol_ratio)
    s_candle, pattern = sd.score_candle(latest)
    sig = earnings.fetch_earnings_signals(code, headers, df["date"].tolist())

    r = {
        "pick_date": latest["date"],
        "code": code,
        "driver": urow.get("driver", ""),
        "group": urow.get("group", "") or "",
        "role": urow.get("role", ""),
        "prev_close": close,
        "chg": round((close / df["close"].iloc[-2] - 1) * 100, 2),
        "ma25_break": above_ma25,
        "ma25_up": ma25_up,
        "ma25_gap": round((close / ma25_now - 1) * 100, 2),
        "ma25_slope": (round((ma25_now / ma25_ref - 1) * 100, 2)
                       if pd.notna(ma25_ref) and ma25_ref else None),
        "above_ma75": bool(ma75 is not None and close > ma75),
        "perfect_order": perfect,
        "pattern": pattern,
        "rsi": round(rsi, 1),
        "volume_ratio": round(vol_ratio, 2),
        "volume_ok": vol_ratio >= 1.2,
        "atr_pct": round(atr / close * 100, 2),
        "atr_yen_unit": int(atr * 100),
        "unit_cost": int(close * 100),
        "ret20": (round(close / df["close"].iloc[-21] * 100 - 100, 1)
                  if len(df) >= 21 else None),
        "score": s_trend + s_rsi + s_vol + s_candle,
        "score_trend": s_trend, "score_rsi": s_rsi,
        "score_volume": s_vol, "score_candle": s_candle,
        "quant_all_pass": bool(above_ma25 and ma25_up and vol_ratio >= 1.2 and rsi < 70),
        "earnings_next": code in schedule,
        **sig,
    }
    r["blockers"] = sd.check_blockers(r)
    if r["blockers"]:
        r["stance"] = "見送り"
    elif above_ma25 and ma25_up:
        r["stance"] = "順張りの土俵"
    elif ma25_up:
        r["stance"] = "押し目"
    else:
        r["stance"] = "トレンド下向き"
    return r


def outlook(r: dict) -> list:
    """今日の見通し。新しい判定は足さず、測った値を言葉にするだけ。"""
    out = []
    sl, gap = r.get("ma25_slope"), r.get("ma25_gap")
    if sl is not None:
        strength = ("しっかり上向き" if sl >= 3 else "緩やかに上向き" if sl >= 1
                    else "ほぼ横ばい" if sl > -1 else "下向き")
        out.append(f"MA25は20日前比 {sl:+.1f}%（{strength}）、終値はMA25を {gap:+.1f}%")
    if r.get("perfect_order"):
        out.append("MA5 > MA25 > MA75 の並び")
    elif not r.get("above_ma75"):
        out.append("終値はMA75の下")

    d = r.get("earnings_days_ago")
    if r.get("earnings_next"):
        out.append("本日が決算発表日")
    elif d is not None and not pd.isna(d) and int(d) <= 9:
        d = int(d)
        when = "前営業日" if d == 0 else f"{d + 1}営業日前"
        line = f"決算が{when}に発表"
        yoy = r.get("earnings_op_yoy")
        if yoy is not None and not pd.isna(yoy):
            line += f"。営業利益 前年同期比 {yoy:+.1f}%"
        if sd.REVISION_LABEL.get(r.get("earnings_revision")) == "上方修正":
            line += "。通期予想を上方修正"
        prog = r.get("earnings_progress")
        if prog is not None and not pd.isna(prog):
            line += f"。進捗率 {prog:.1f}%"
        out.append(line)
    else:
        out.append("直近10営業日以内の決算発表なし")

    if r.get("pattern") and r["pattern"] != "-":
        out.append(f"前営業日のローソク足は{r['pattern']}")
    return out


def print_briefing(rows, names, macro_notes, group_scores, news, today, log_note):
    nm = lambda c: names.get(c, "")
    bar = "━" * 62
    print(bar)
    print(f"【朝のブリーフィング】{today}   ウォッチ{len(rows)}銘柄")
    print(bar)

    print("\n■ 今日の地合い\n")
    for line in (macro_notes or ["（前夜の海外市場は未取得です）"]):
        print(f"  {line}")

    print("\n\n■ 15銘柄の状態（一覧）\n")
    print("  " + f"{'コード':<6}{'銘柄':<15}{'終値':>9}{'前日比':>8}"
          f"{'MA25乖離':>9}{'RSI':>6}{'出来高':>7}{'1単元':>8}  今日の土俵")
    for r in rows:
        print("  " + f"{r['code']:<6}{nm(r['code'])[:13]:<15}"
              f"{r['prev_close']:>8,.0f}円{r['chg']:>+7.2f}%"
              f"{r['ma25_gap']:>+8.1f}%{r['rsi']:>6.1f}"
              f"{r['volume_ratio']:>6.2f}倍{r['unit_cost']/10000:>6.1f}万  {r['stance']}")

    counts = {s: sum(1 for r in rows if r["stance"] == s) for s in STANCE_ORDER}
    print("\n  内訳: " + " / ".join(f"{s} {counts[s]}件" for s in STANCE_ORDER))

    for stance in STANCE_ORDER:
        group = [r for r in rows if r["stance"] == stance]
        if not group:
            continue
        print(f"\n\n■ {stance}  {len(group)}件")
        print(f"  {STANCE_NOTE[stance]}\n")
        for r in group:
            print(f"  {r['code']} {nm(r['code'])}   {r['prev_close']:,.0f}円"
                  f"   1単元 {r['unit_cost']/10000:,.1f}万円   {r['driver']}")
            if r["blockers"]:
                for w in r["blockers"]:
                    print(f"     見送り理由: {w}")
            print("     [今日の見通し]")
            for line in outlook(r):
                print(f"       ・{line}")

            # グループ名の有無と朝スコアの有無は別。--skip-overnight のときに
            # 「連動グループなし」と出してしまうと事実と食い違う。
            g = r["group"]
            if not g:
                print("     [マクロ] 連動グループなし（単独銘柄）")
            else:
                tot, th = (group_scores or {}).get(g, (None, None))
                if tot is None:
                    print(f"     [マクロ] {g}（朝スコア未取得）")
                else:
                    verdict = "追い風が出ている" if tot >= th else "追い風なし（閾値未達）"
                    print(f"     [マクロ] {g} 朝スコア {tot:+d}/閾値{th:+d} — {verdict}")

            arts = (news or {}).get(r["code"])
            if arts:
                print("     [ニュース] 直近36時間・日経/ロイター/Bloomberg等")
                for a in arts:
                    print(f"       ・[{a['日時']}] {a['見出し'][:52]} ({a['発信元']})")
                print("       ※ 内容の良し悪しは判定していません。読んでご判断ください")
            elif arts is not None:
                print("     [ニュース] 該当なし（直近36時間・許可ソース内）")

            print(f"     [エントリー時に見る] RSI {r['rsi']} / 前営業日の出来高 "
                  f"平均比{r['volume_ratio']:.2f}倍 / ATR {r['atr_pct']:.1f}%"
                  f"（1単元あたり1日 約{r['atr_yen_unit']:,}円）")
            print()

    nxt = [r for r in rows if r.get("earnings_next")]
    if nxt:
        print(f"\n■ 本日決算発表  {len(nxt)}件\n")
        print("  " + " / ".join(f"{r['code']} {nm(r['code'])}" for r in nxt))
        print("  → §2「決算・会合をまたぐ持ち越しはしない」。今日は新規に入らない")

    if log_note:
        print(f"\n  （{log_note}）")

    print("\n" + bar)
    print("※ 材料の裏付けは自動判定していません。決算・ニュースは個別にご確認ください。")
    print("※ 実際のエントリーは場中に entry_check.py（/entry-check）で判定してください。")
    print("   このブリーフィングは日足の状態を並べたもので、買いのサインではありません。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watchlist", default="watchlist.csv")
    ap.add_argument("--map", default="driver_map.yaml")
    ap.add_argument("--names", default="company_master.csv")
    ap.add_argument("--log", default="watch_log.csv")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--skip-overnight", action="store_true")
    args = ap.parse_args()

    conf = yaml.safe_load(open(args.map, encoding="utf-8"))
    wl = pd.read_csv(args.watchlist, dtype={"code": str}).fillna(
        {"group": "", "driver": "", "role": ""})
    names = {}
    try:
        nm = pd.read_csv(args.names, dtype={"code": str})
        names = dict(zip(nm["code"], nm["CoName"]))
    except Exception:
        pass

    today = dt.date.today().isoformat()
    headers = sd.get_headers()
    is_open, day_label = sd.trading_day_status(today, headers)
    if is_open is False:
        print(f"【朝のブリーフィング】{today}  東証: {day_label}")
        print("\n  本日は取引がありません。ブリーフィングは出しません。")
        print("  ※ 日足は前営業日までしか無いため、pick_date が前回と同じでも")
        print("     休場日とは限りません。判定はこのカレンダーだけを根拠にしています。")
        return

    macro_notes, group_scores = [], {}
    if not args.skip_overnight:
        macro_notes, group_scores = collect_macro(conf)

    schedule = earnings.fetch_jpx_schedule()
    rows = []
    for _, urow in wl.iterrows():
        code = urow["code"]
        try:
            rows.append(evaluate(code, urow.to_dict(), headers, schedule))
        except Exception as e:
            print(f"[warn] {code}: {e}", file=sys.stderr)
    if not rows:
        print("日足を1銘柄も取得できませんでした。処理を中止します。")
        return

    news = {}
    if not args.no_news and not args.skip_overnight:
        nc = (conf.get("共通") or {}).get("ニュース") or {}
        for r in rows:
            kw = names.get(r["code"]) or r["code"]
            news[r["code"]] = ov.fetch_news(kw, nc.get("許可ソース", []),
                                            nc.get("収集時間", 36), 3, None, company=kw)

    log_note = record(rows, group_scores, args.log, headers)
    order = {s: i for i, s in enumerate(STANCE_ORDER)}
    rows.sort(key=lambda r: (order[r["stance"]], -(r["ma25_gap"] or 0)))
    print_briefing(rows, names, macro_notes, group_scores, news,
                   f"{today}  東証: {day_label}", log_note)


def collect_macro(conf: dict):
    """地合いを1〜3行にまとめる。morning.py と同じ扱いで、銘柄の採点には使わない。"""
    news_conf = (conf.get("共通") or {}).get("ニュース") or {}
    topics = {n: ov.evaluate_topic(n, t, news_conf, None)
              for n, t in ((conf.get("共通") or {}).get("ニューストピック") or {}).items()}
    notes, scores, hot, alerts = [], {}, [], []
    for gname, g in conf["グループ"].items():
        sc = g.get("朝スコア")
        if not sc:
            continue
        items = [ov.evaluate_item(i, None, news_conf, topics) for i in sc["項目"]]
        total = sum(i["点"] for i in items if i["自動"])
        scores[gname] = (total, sc["閾値"])
        # 急変がプラスかどうかは点で判断する。生の変化率だと感応度 -1 の指標
        # （自動車の米10年債など）が上昇しただけで追い風に見えてしまう。
        up = [i for i in items if i["急変"] and i["点"] > 0]
        alerts += [(gname, i) for i in items if i["急変"]]
        if total >= sc["閾値"]:
            hot.append(gname)
        elif up:
            why = " / ".join(f"{i['名前']} {i['変化率']:+.2f}%" for i in up)
            hot.append(f"{gname}（{why}）")
    notes.append("追い風が出ているグループ: " + " / ".join(hot) if hot else
                 f"セクター単位の追い風はありません（{len(scores)}グループすべて閾値未達）。")
    by_ind = {}
    for gname, i in alerts:
        e = by_ind.setdefault(i["名前"], {"変化率": i["変化率"], "追": [], "逆": []})
        (e["追"] if i["点"] > 0 else e["逆"] if i["点"] < 0 else []).append(gname)
    for name, e in by_ind.items():
        parts = []
        if e["追"]:
            parts.append("追い風: " + "・".join(e["追"]))
        if e["逆"]:
            parts.append("逆風: " + "・".join(e["逆"]))
        notes.append(f"急変 {name} {e['変化率']:+.2f}%"
                     + (f"  → {' / '.join(parts)}" if parts else "  → 採点対象外"))
    notes.append("※ 8:00時点では米国の時間外取引がまだ続いています（20:00 ETまで）。")
    return notes, scores


def record(rows, group_scores, path, headers):
    """15銘柄ぶんを毎日記録する。絞っていないので、後から
    「どの土俵の銘柄が翌日どうなったか」を土俵別に測れる。"""
    log_df = sd.load_log(path)
    log_df = sd.record_outcomes(log_df, headers)
    pick_date = rows[0]["pick_date"]
    if len(log_df) and (log_df["pick_date"].astype(str) == str(pick_date)).any():
        log_df.to_csv(path, index=False, encoding="utf-8-sig")
        return f"{pick_date} の記録は既にあります。{path} への追記はしていません。"
    for r in rows:
        row = {c: r.get(c) for c in sd.PICKS_LOG_COLUMNS if c in r}
        row.update({"track": r["stance"], "outcome_recorded": False})
        m = group_scores.get(r["group"])
        if m:
            row["macro_score"], row["macro_threshold"] = m
        log_df = pd.concat([log_df, pd.DataFrame([row])], ignore_index=True)
    log_df.to_csv(path, index=False, encoding="utf-8-sig")
    return None


if __name__ == "__main__":
    main()
