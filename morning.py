"""
朝の統合ジョブ（8:00 JST 実行）
================================

引け後と翌朝に分かれていた2つの処理を、朝1本に統合したもの。

なぜ朝に寄せたか
----------------
日本のT日を動かすのはT-1日の米国市場で、その引けは T日 05:00〜06:00 JST。
前日15:30に走らせても、その夜に起きる米国市場は構造的に見られない。
一方、日本の日足は15:30に確定してその後変わらないので、
テクニカルの候補は朝に計算しても前夜とまったく同じ結果になる。
分ける必然性がなく、1本にした方が冪等性の管理も1箇所で済む。

実行時刻 8:00 JST についての注意
--------------------------------
8:00 JST は 19:00 ET（夏時間）で、米国の時間外取引はまだ 20:00 ET まで続く。
引け後に決算が出た銘柄は、この時点ではまだ動き切っていない可能性がある。
冬時間だとさらに1時間ずれる。

出すもの
--------
銘柄を主役にしたウォッチリスト。以前はパイプラインの構造をそのまま
（第1部 海外市場 / 第2部 テクニカル / 第3部 交差）並べていたため、
「結局どれを見ればいいのか」を読み手が組み立てる必要があった。

1. 今日の地合い — 1〜3行。グループ別の内訳が要るときは overnight.py
2. ウォッチする銘柄 — なぜ出たかを点数ではなく言葉で
3. 見送り推奨 — テクニカルは通ったが運用方針に触れる銘柄。5枠は消費しない
4. 材料が出ている銘柄 — MA25の下。順張り対象外
5. 本日決算発表

使い方:
    python morning.py
    python morning.py --format brief
"""

import argparse
import datetime as dt

import pandas as pd
import yaml

import earnings
import overnight as ov
import screen_daily as sd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="universe.csv")
    ap.add_argument("--map", default="driver_map.yaml")
    ap.add_argument("--names", default="company_master.csv")
    ap.add_argument("--log", default="picks_log.csv")
    ap.add_argument("--top-n", type=int, default=5)
    ap.add_argument("--max-per-driver", type=int, default=2)
    ap.add_argument("--format", choices=["brief", "full"], default="full")
    ap.add_argument("--skip-overnight", action="store_true")
    args = ap.parse_args()

    conf = yaml.safe_load(open(args.map, encoding="utf-8"))
    uni = pd.read_csv(args.universe, dtype={"code": str}).fillna({"group": ""})
    names = {}
    try:
        nm = pd.read_csv(args.names, dtype={"code": str})
        names = dict(zip(nm["code"], nm["CoName"]))
    except Exception:
        pass

    today = dt.date.today().isoformat()
    is_open, day_label = sd.trading_day_status(today, sd.get_headers())
    if is_open is False:
        print(f"【朝のウォッチリスト】{today}  東証: {day_label}")
        print("\n  本日は取引がありません。ブリーフィングは出しません。")
        print("  ※ 日足は前営業日までしか無いため、pick_date が前回と同じでも")
        print("     休場日とは限りません。判定はこのカレンダーだけを根拠にしています。")
        return

    hot = []
    macro_notes = []
    group_scores = {}   # グループ名 -> (朝スコア, 閾値)。候補一覧に材料として併記する
    if not args.skip_overnight:
        news_conf = (conf.get("共通") or {}).get("ニュース") or {}
        topics = {n: ov.evaluate_topic(n, t, news_conf, None)
                  for n, t in ((conf.get("共通") or {}).get("ニューストピック") or {}).items()}
        alerts = []
        for gname, g in conf["グループ"].items():
            sc = g.get("朝スコア")
            if not sc:
                continue
            items = [ov.evaluate_item(i, None, news_conf, topics) for i in sc["項目"]]
            total = sum(i["点"] for i in items if i["自動"])
            group_scores[gname] = (total, sc["閾値"])
            # 急変が「そのグループにとってプラスか」は点で判断する。
            # 生の変化率だと、感応度 -1 の指標(自動車の米10年債など)が
            # 上昇しただけで追い風として浮上してしまう。
            # 採点対象外の項目は点が0なので、ここでも浮上させない。
            up = [i for i in items if i["急変"] and i["点"] > 0]
            for i in items:
                if i["急変"]:
                    alerts.append((gname, i))
            if total >= sc["閾値"]:
                hot.append((gname, f"朝スコア {total:+d} が閾値 +{sc['閾値']} 以上"))
            elif up:
                why = " / ".join(f"{i['名前']} {i['変化率']:+.2f}%" for i in up)
                hot.append((gname, f"プラス方向の急変（{why}）※朝スコアは {total:+d}"))
        # 地合いは1〜3行に畳む。グループ別の内訳が要るときは overnight.py を叩く。
        tail = [g for g, _ in hot]
        if tail:
            macro_notes.append("追い風が出ているグループ: " + " / ".join(tail))
        else:
            macro_notes.append(
                f"セクター単位の追い風はありません（{len(group_scores)}グループすべて閾値未達）。")
        # 同じ指標が複数グループに属するため、指標ごとに畳んで影響先を並べる
        # （ドル円は半導体・メガバンク・自動車の3グループに出てくる）
        by_ind = {}
        for gname, i in alerts:
            e = by_ind.setdefault(i["名前"], {"変化率": i["変化率"], "追": [], "逆": []})
            if i["点"] > 0:
                e["追"].append(gname)
            elif i["点"] < 0:
                e["逆"].append(gname)
        for name, e in by_ind.items():
            parts = []
            if e["追"]:
                parts.append("追い風: " + "・".join(e["追"]))
            if e["逆"]:
                parts.append("逆風: " + "・".join(e["逆"]))
            macro_notes.append(
                f"急変 {name} {e['変化率']:+.2f}%"
                + (f"  → {' / '.join(parts)}" if parts else "  → 採点対象外"))
        macro_notes.append(
            "※ 8:00時点では米国の時間外取引がまだ続いています（20:00 ETまで）。")

    schedule = earnings.fetch_jpx_schedule()
    log_df = sd.load_log(args.log)
    log_df = sd.record_outcomes(log_df, sd.get_headers())
    cands, catalysts, funnel, rejected = sd.screen_universe(uni, sd.get_headers(), schedule)
    selected, blocked = (sd.select_top_n(cands, args.top_n, args.max_per_driver)
                         if cands else ([], []))

    # 同じ pick_date の行が既にあれば追記しない（二重起動・リトライ対策）
    pick_date = (cands or catalysts)[0]["pick_date"] if (cands or catalysts) else None
    already = (pick_date is not None and len(log_df)
               and (log_df["pick_date"].astype(str) == str(pick_date)).any())
    if already:
        log_note = f"{pick_date} の記録は既にあります。picks_log.csv への追記はしていません。"
    else:
        log_note = None
        for i, r in enumerate(selected):
            row = {c: r.get(c) for c in sd.PICKS_LOG_COLUMNS if c in r}
            row.update({"track": "順張り", "pick_rank": i + 1, "outcome_recorded": False})
            m = group_scores.get((r.get("group") or "").strip())
            if m:
                row["macro_score"], row["macro_threshold"] = m
            log_df = pd.concat([log_df, pd.DataFrame([row])], ignore_index=True)
        log_df.to_csv(args.log, index=False, encoding="utf-8-sig")

    sd.print_report(selected, cands, funnel, rejected, names, pick_date or "-",
                    args.top_n, brief=(args.format == "brief"),
                    flagged=catalysts[:5], catalysts=catalysts,
                    group_scores=group_scores, blocked=blocked,
                    macro_notes=macro_notes, today=f"{today}  東証: {day_label}",
                    log_note=log_note)

if __name__ == "__main__":
    main()
