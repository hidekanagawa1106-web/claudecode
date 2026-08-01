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
1. 前夜の海外市場（急変検知・グループ別 朝スコア・浮上したグループ）
2. 日足のテクニカル候補（順張りトラック）
3. 両者の交差 — 材料が来ているグループの中でテクニカルも整っている銘柄

使い方:
    python morning.py
    python morning.py --format brief
"""

import argparse

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
    nm_of = lambda c: names.get(c, "")

    hot = []
    if not args.skip_overnight:
        news_conf = (conf.get("共通") or {}).get("ニュース") or {}
        topics = {n: ov.evaluate_topic(n, t, news_conf, None)
                  for n, t in ((conf.get("共通") or {}).get("ニューストピック") or {}).items()}
        print("=" * 66)
        print("【朝の統合ブリーフィング】第1部 — 前夜の海外市場")
        print("=" * 66)
        alerts = []
        for gname, g in conf["グループ"].items():
            sc = g.get("朝スコア")
            if not sc:
                continue
            items = [ov.evaluate_item(i, None, news_conf, topics) for i in sc["項目"]]
            total = sum(i["点"] for i in items if i["自動"])
            up = [i for i in items if i["急変"] and (i["変化率"] or 0) > 0]
            for i in items:
                if i["急変"]:
                    alerts.append((gname, i))
            if total >= sc["閾値"]:
                hot.append((gname, f"朝スコア {total:+d} が閾値 +{sc['閾値']} 以上"))
            elif up:
                why = " / ".join(f"{i['名前']} {i['変化率']:+.2f}%" for i in up)
                hot.append((gname, f"プラス方向の急変（{why}）※朝スコアは {total:+d}"))
            verdict = "→ 場中に条件を探す" if total >= sc["閾値"] else "→ 何もしない"
            print(f"\n  {gname}  {total:+d} / 閾値 +{sc['閾値']}  {verdict}")
            for i in items:
                if i["自動"] and i.get("方向") is not None:
                    arrow = {1: "強まる", -1: "弱まる", 0: "中立"}[i["方向"]]
                    sl = "追い風" if (i.get("感応度") or 0) > 0 else "逆風"
                    print(f"      {i['名前']:<22} {arrow} × {sl}  ({i['点']:+d})")
                elif i["自動"]:
                    print(f"      {i['名前']:<22} {i['変化率']:+7.2f}%  ({i['点']:+d})"
                          f"{' ★急変' if i['急変'] else ''}")
                    for t, r in (i.get("時間外") or []):
                        print(f"         ・{t} 時間外 {r['変化率']:+.2f}% ← 引け後の決算反応の可能性")
                else:
                    print(f"      {i['名前']:<22}    自動取得不可 → 要確認")
        if alerts:
            print("\n  ■ 急変検知")
            for gname, i in alerts:
                d = " ".join(f"{t} {c:+.2f}%" for t, c in i["内訳"])
                print(f"    [{gname}] {i['名前']} {i['変化率']:+.2f}% "
                      f"← {'上昇材料' if (i['変化率'] or 0) > 0 else '下落材料'}   {d}")
        print("\n  ※ 8:00時点では米国の時間外取引がまだ続いています（20:00 ETまで）。")

    print("\n" + "=" * 66)
    print("【朝の統合ブリーフィング】第2部 — 日足のテクニカル候補")
    print("=" * 66)
    schedule = earnings.fetch_jpx_schedule()
    log_df = sd.load_log(args.log)
    log_df = sd.record_outcomes(log_df, sd.get_headers())
    cands, catalysts, funnel, rejected = sd.screen_universe(uni, sd.get_headers(), schedule)
    selected = sd.select_top_n(cands, args.top_n, args.max_per_driver) if cands else []

    # 同じ pick_date の行が既にあれば追記しない（二重起動・リトライ対策）
    pick_date = (cands or catalysts)[0]["pick_date"] if (cands or catalysts) else None
    already = (pick_date is not None and len(log_df)
               and (log_df["pick_date"].astype(str) == str(pick_date)).any())
    if already:
        print(f"\n  ※ {pick_date} の記録は既にあります。追記をスキップしました。")
    else:
        for i, r in enumerate(selected):
            row = {c: r.get(c) for c in sd.PICKS_LOG_COLUMNS if c in r}
            row.update({"track": "順張り", "pick_rank": i + 1, "outcome_recorded": False})
            log_df = pd.concat([log_df, pd.DataFrame([row])], ignore_index=True)
        log_df.to_csv(args.log, index=False, encoding="utf-8-sig")

    sd.print_report(selected, cands, funnel, rejected, names, pick_date or "-",
                    args.top_n, brief=(args.format == "brief"),
                    flagged=[], catalysts=catalysts)

    print("\n" + "=" * 66)
    print("【朝の統合ブリーフィング】第3部 — 材料 × テクニカルの交差")
    print("=" * 66)
    hot_names = [g for g, _ in hot]
    if not hot_names:
        print("  前夜の海外市場で浮上したグループはありません。")
    else:
        picked = {r["code"] for r in selected}
        for gname, why in hot:
            sub = uni[uni["group"] == gname]
            both = [c for c in sub["code"] if c in picked]
            print(f"\n  [{gname}] {why}")
            if both:
                print(f"    ★ テクニカルも整っている: "
                      + " / ".join(f"{c} {nm_of(c)}" for c in both))
            else:
                print("    テクニカル候補との重なりなし"
                      "（材料はあるが、日足では上昇トレンド条件を満たしていない）")
            print(f"    グループ全{len(sub)}銘柄: "
                  + " ".join(f"{c}({nm_of(c)[:6]})" for c in sub["code"][:10]))
    print("\n  ※ 材料の内容そのものは判定していません。海外決算・マクロを個別にご確認ください。")
    print("  ※ エントリーは場中の順張り4条件で別途判断してください。"
          "銘柄ごとの詳細は entry_check.py を使ってください。")


if __name__ == "__main__":
    main()
