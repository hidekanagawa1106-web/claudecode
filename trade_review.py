"""
取引の振り返り（実売買の記録と検証）
====================================

取引が終わったあとに実行し、運用方針_v3 §8 に沿った記録を残しつつ、
「その損切り・利確の水準は適切だったか」を日足データで検証する。

このスクリプトの核心
--------------------
損益そのものは本人が既に知っている。検証する価値があるのは、決済したあとに
価格がどう動いたかである。

- 損切りで終わった → その後戻したなら、損切りが浅すぎた可能性がある
- 利確で終わった   → その後さらに伸びたなら、利確が早すぎた可能性がある
- 保有中に利確ラインへ一度到達していたのに決済されずに下がった → 指値の置き方の問題

これらは日足から機械的に判定できる。感想ではなく数字で「何が勝敗を分けたか」を出す。

やらないこと
------------
勝ち負けの原因を断定しない。1回の取引はほぼ運で決まるため、
サンプルが貯まるまでは個別の反省より記録の蓄積を優先する。

v3 で変わったこと
------------------
- **損切り・利確の水準を日足ATR(14)で評価する。** §5 が銘柄タイプ別の固定%から
  「ATRの1.5〜2倍、利確はその1.75倍」に変わったため。ATR は entry_check.py と
  同じ定義（前日終値までで確定した値）を使うので、エントリーした朝に見えていた
  数字と同じもので後から評価できる
- **§8 の追加記録項目**（日足RSI・場中RSI・MA乖離率・エントリー時刻・ORB乖離率・
  参考条件・削除された規律の該当状況）を trades.csv に持つ。これが無いと
  v3 の変更が正しかったかを後から判定できない

使い方:
    # 1件を分析（--save を付けると trades.csv に追記）
    python trade_review.py --code 8306 --entry-date 2026-07-30 --entry-price 3525 \\
        --exit-date 2026-07-31 --exit-price 3571 --shares 100 \\
        --stop 3384 --target 3771 --reason 利確 \\
        --rsi-daily 62.1 --dev-ma25 1.4 --entry-time 10:35 --orb-gap 0.4 \\
        --refs "VWAP上/出来高1.1倍/連動○" --discipline "その日1銘柄目・後付けなし" --save

    # 累積の集計（§9 の判定基準に対する現在地）
    python trade_review.py --stats
"""

import argparse
import os

import pandas as pd

import entry_check as ec
import screen_daily as sd

TRADES_COLUMNS = [
    "entry_date", "code", "銘柄名", "driver", "group",
    "entry_price", "shares", "stop_price", "target_price",
    "exit_date", "exit_price", "exit_reason",
    "pnl_pct", "pnl_yen", "保有営業日数",
    "朝スコア", "エントリー根拠", "ルール遵守", "違反した条件",
    # §5 の検証。エントリー時点で確定していた日足ATR(14)と、それに対する実際の幅。
    # entry_check.py と同じ定義（前日終値までで確定した値）を使う。
    "entry_atr", "損切り幅ATR倍", "利確幅ATR倍", "リスクリワード",
    # v3 で追加した記録項目（運用方針 §8）。これが無いと v3 の変更の当否を
    # 後から判定できない。埋まっていない列は集計から自動的に外れる。
    "日足RSI", "場中RSI", "MA5乖離率", "MA25乖離率", "エントリー時刻",
    "ORB乖離率", "参考条件", "削除規律の該当",
    "pick_date", "反省",
]


TRADES_TEXT_COLUMNS = [
    "entry_date", "code", "銘柄名", "driver", "group", "exit_date", "exit_reason",
    "朝スコア", "エントリー根拠", "ルール遵守", "違反した条件",
    "エントリー時刻", "参考条件", "削除規律の該当", "pick_date", "反省",
]


def load_trades(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        df = pd.read_csv(path, dtype={"code": str})
        for c in TRADES_COLUMNS:
            if c not in df.columns:
                df[c] = None
        # 空のまま保存された列は読み直すと float64 になる。pandas 3 はそこへ
        # 文字列を代入すると TypeError を投げる（picks_log.csv で実際に発生）。
        # ここは concat で追記しているので現状は当たらないが、揃えておく。
        for c in TRADES_TEXT_COLUMNS:
            if c in df.columns and df[c].dtype != object:
                df[c] = df[c].astype(object)
        return df
    return pd.DataFrame(columns=TRADES_COLUMNS)


def analyze(code, entry_date, entry_price, exit_date, exit_price,
            stop, target, headers, after_days=5) -> dict:
    """保有期間中と決済後の値動きから、損切り・利確の水準を検証する。"""
    df = sd.fetch_daily_bars(code, headers)
    hold = df[(df["date"] >= entry_date) & (df["date"] <= exit_date)]
    after = df[df["date"] > exit_date].head(after_days)
    out = {"保有営業日数": len(hold)}
    if hold.empty:
        return out

    out["保有中の最高値"] = hold["high"].max()
    out["保有中の最安値"] = hold["low"].min()
    out["最大含み益率"] = round((hold["high"].max() / entry_price - 1) * 100, 2)
    out["最大含み損率"] = round((hold["low"].min() / entry_price - 1) * 100, 2)

    if target:
        out["利確ライン到達"] = bool(hold["high"].max() >= target)
    if stop:
        out["損切りライン到達"] = bool(hold["low"].min() <= stop)

    # §5 の基準。entry_check.py が当日の足を落とすのと同じ理由で、エントリー日を
    # 含めた系列を渡す（ec.atr が末尾を落とすので、前日終値までで確定した値になる）。
    # これはエントリーした朝に entry_check.py が表示していたはずの値と一致する。
    upto = df[df["date"] <= entry_date]
    if len(upto) >= 16:
        a = ec.atr(upto)
        out["エントリー時ATR"] = a["ATR"]
        out["エントリー時ATR比"] = a["ATR比"]
        out["ATR基準日"] = a["基準日"]
        if stop and stop < entry_price:
            out["損切り幅"] = entry_price - stop
            out["損切り幅ATR倍"] = round(out["損切り幅"] / a["ATR"], 2)
        if target and target > entry_price:
            out["利確幅"] = target - entry_price
            out["利確幅ATR倍"] = round(out["利確幅"] / a["ATR"], 2)
        if out.get("損切り幅") and out.get("利確幅"):
            out["リスクリワード"] = round(out["利確幅"] / out["損切り幅"], 2)

    if not after.empty:
        out["決済後の最高値"] = after["high"].max()
        out["決済後の最安値"] = after["low"].min()
        out["決済後の最大上昇率"] = round((after["high"].max() / exit_price - 1) * 100, 2)
        out["決済後の最大下落率"] = round((after["low"].min() / exit_price - 1) * 100, 2)
        out["決済後の営業日数"] = len(after)
    return out


def rule_checks(a: dict) -> list:
    """発注した損切り・利確が §5 の基準に沿っていたかを返す。

    v3 §5: 損切り = 日足ATR(14)の1.5〜2倍、利確 = 損切り幅の1.75倍。
    ATR は entry_check.py と同じ定義（前日終値までで確定した値）を使うので、
    エントリーの朝に見えていた数字と同じもので後から評価できる。

    **損益では判定しない。** 幅が基準どおりでも負けるし、外れていても勝つ。
    ここで見るのは執行がルールに沿っていたかだけで、それが §8 の
    「条件が揃っていた取引と揃っていなかった取引の成績差」の材料になる。
    """
    v = []
    if "エントリー時ATR" not in a:
        return v
    mult = a.get("損切り幅ATR倍")
    if mult is not None:
        if mult < 1.5:
            v.append(f"損切り幅がATRの{mult}倍。§5 の下限1.5倍より狭い。"
                     f"1日の平均変動幅に近く、方向が合っていてもノイズで刈られる水準")
        elif mult > 2.0:
            v.append(f"損切り幅がATRの{mult}倍。§5 の上限2.0倍より広い。"
                     f"1回あたりの損失額が想定より大きくなる")
        else:
            v.append(f"損切り幅はATRの{mult}倍。§5 の1.5〜2倍の範囲内")
    rr = a.get("リスクリワード")
    if rr is not None:
        if rr < 1.5:
            v.append(f"リスクリワードが{rr}倍。§5 の目安1.75倍を下回る。"
                     f"損切りは利確の1.5倍の頻度で当たるため、この比率では期待値が負けやすい")
        else:
            v.append(f"リスクリワードは{rr}倍（§5 の目安は1.75倍）")
    return v


def verdicts(pnl_pct: float, a: dict, reason: str) -> list:
    """数字から言えることだけを並べる。原因は断定しない。"""
    v = []
    up = a.get("決済後の最大上昇率")
    dn = a.get("決済後の最大下落率")
    n = a.get("決済後の営業日数", 0)

    if pnl_pct < 0 and up is not None and up >= abs(pnl_pct):
        line = (f"決済後{n}営業日で最大 {up:+.2f}% 戻している。"
                f"損切り幅が浅かった可能性がある（損切り自体は§0のルール通り）")
        mult = a.get("損切り幅ATR倍")
        if mult is not None and mult < 1.5:
            line += f"。損切り幅がATRの{mult}倍しかなかったことと符合する"
        v.append(line)
    if pnl_pct > 0 and up is not None and up >= 3.0:
        v.append(f"決済後{n}営業日でさらに最大 {up:+.2f}% 伸びている。"
                 f"利確が早かった可能性がある")
    if pnl_pct > 0 and dn is not None and dn <= -3.0:
        v.append(f"決済後{n}営業日で最大 {dn:+.2f}% 下げている。利確の判断は結果的に妥当")
    if a.get("利確ライン到達") and reason != "利確":
        v.append("保有中に利確ラインへ到達していたが、利確では決済されていない。"
                 "指値の置き方か、約定しなかった可能性を確認する")
    peak = a.get("最大含み益率")
    if peak is not None and peak - pnl_pct >= 2.0:
        v.append(f"保有中は最大 {peak:+.2f}% まで乗っていたが {pnl_pct:+.2f}% で決済している。"
                 f"差は {peak - pnl_pct:.2f}pt。利確の置き方か、伸びを戻したかを確認する")
    if a.get("最大含み損率") is not None and a["最大含み損率"] <= -3.0 and pnl_pct > 0:
        v.append(f"保有中に最大 {a['最大含み損率']:.2f}% の含み損を抱えてから戻している。"
                 f"損切りラインとの距離を確認する")
    if not v:
        v.append("決済後の値動きから特筆すべき点はなし")
    return v


def print_stats(df: pd.DataFrame):
    """§9 の判定基準（20回以上／勝率65%以上／累計プラス）に対する現在地。"""
    done = df[df["exit_date"].notna()].copy()
    print("=" * 62)
    print(f"【累積の成績】{len(done)}取引")
    print("=" * 62)
    if done.empty:
        print("  記録がありません。")
        return
    done["pnl_pct"] = pd.to_numeric(done["pnl_pct"], errors="coerce")
    win = (done["pnl_pct"] > 0).sum()
    rate = win / len(done) * 100
    print(f"  勝率 {rate:.1f}%（{win}/{len(done)}）  平均損益 {done['pnl_pct'].mean():+.2f}%")
    yen = pd.to_numeric(done["pnl_yen"], errors="coerce").sum()
    print(f"  累計損益 {yen:+,.0f}円")
    print(f"  最大の勝ち {done['pnl_pct'].max():+.2f}% / 最大の負け {done['pnl_pct'].min():+.2f}%")

    if "ルール遵守" in done and done["ルール遵守"].notna().any():
        print("\n  [ルール遵守別]  ※§8「条件が揃っていた取引と揃っていなかった取引の成績差」")
        for val, sub in done.groupby(done["ルール遵守"].astype(str)):
            w = (sub["pnl_pct"] > 0).sum()
            print(f"    {val:<8} n={len(sub):>3}  勝率 {w/len(sub)*100:>5.1f}%  "
                  f"平均 {sub['pnl_pct'].mean():+.2f}%")

    for col, label in [("group", "連動グループ別"), ("exit_reason", "決済理由別"),
                       ("エントリー時刻", "エントリー時刻別"), ("参考条件", "参考条件別")]:
        if col in done and done[col].notna().any():
            print(f"\n  [{label}]")
            for val, sub in done.groupby(done[col].astype(str)):
                w = (sub["pnl_pct"] > 0).sum()
                print(f"    {val:<14} n={len(sub):>3}  勝率 {w/len(sub)*100:>5.1f}%  "
                      f"平均 {sub['pnl_pct'].mean():+.2f}%")

    # §5 の損切り幅が守られていた取引と、外れていた取引の差。
    # ここが v3 で ATR 基準に変えた判断そのものの検証になる。
    if "損切り幅ATR倍" in done:
        m = pd.to_numeric(done["損切り幅ATR倍"], errors="coerce")
        if m.notna().any():
            band = m.apply(lambda x: "-" if pd.isna(x)
                           else ("§5内(1.5〜2.0)" if 1.5 <= x <= 2.0
                                 else ("狭い(<1.5)" if x < 1.5 else "広い(>2.0)")))
            print("\n  [損切り幅 × ATR（§5 の基準を守れていたか）]")
            for val, sub in done[band != "-"].groupby(band[band != "-"]):
                w = (sub["pnl_pct"] > 0).sum()
                print(f"    {val:<16} n={len(sub):>3}  勝率 {w/len(sub)*100:>5.1f}%  "
                      f"平均 {sub['pnl_pct'].mean():+.2f}%")

    # v3 で RSI70超えの禁止を撤回した。その判断が正しかったかを実取引で測る。
    # 運用方針 §4 は「実取引が20件ほど溜まってから確定する」としている。
    if "日足RSI" in done:
        r = pd.to_numeric(done["日足RSI"], errors="coerce")
        if r.notna().any():
            band = r.apply(lambda x: "-" if pd.isna(x)
                           else ("70以上" if x >= 70
                                 else ("50〜70" if x >= 50 else "50未満")))
            print("\n  [日足RSI別]  ※§4 の RSI70超え撤回の検証。20件で確定する")
            for val, sub in done[band != "-"].groupby(band[band != "-"]):
                w = (sub["pnl_pct"] > 0).sum()
                print(f"    {val:<16} n={len(sub):>3}  勝率 {w/len(sub)*100:>5.1f}%  "
                      f"平均 {sub['pnl_pct'].mean():+.2f}%")
            n70 = int((r >= 70).sum())
            if n70 < 20:
                print(f"    ※ RSI70以上の記録は {n70}件。20件たまるまで条文は確定しません")

    print("\n  [§9 資金増額の判定基準]")
    print(f"    取引20回以上 : {'○' if len(done) >= 20 else '×'}（現在 {len(done)}回）")
    print(f"    勝率65%以上  : {'○' if rate >= 65 else '×'}（現在 {rate:.1f}%）")
    print(f"    累計プラス   : {'○' if yen > 0 else '×'}")
    if len(done) < 20:
        print("    ※ 20回未満のうちは勝率のブレが大きく、判断材料になりません")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code")
    ap.add_argument("--entry-date")
    ap.add_argument("--entry-price", type=float)
    ap.add_argument("--exit-date")
    ap.add_argument("--exit-price", type=float)
    ap.add_argument("--shares", type=int, default=100)
    ap.add_argument("--stop", type=float, default=None, help="発注した損切りの逆指値")
    ap.add_argument("--target", type=float, default=None, help="発注した利確の指値")
    ap.add_argument("--reason", default="", help="損切り / 利確 / 手動 など")
    ap.add_argument("--score", default="", help="その日の朝スコアと内訳")
    ap.add_argument("--basis", default="", help="エントリー根拠（揃った条件）")
    ap.add_argument("--rule", default="", help="ルール遵守: 遵守 / 一部逸脱 / 逸脱")
    ap.add_argument("--violation", default="", help="違反した条件")
    ap.add_argument("--memo", default="", help="反省")
    # v3 で追加した記録項目（§8）。埋めないと v3 の変更の当否を後から測れない。
    ap.add_argument("--rsi-daily", type=float, default=None, help="エントリー時の日足RSI(14)")
    ap.add_argument("--rsi-intraday", type=float, default=None, help="エントリー時の場中RSI(14)")
    ap.add_argument("--dev-ma5", type=float, default=None, help="日足MA5からの乖離率(%%)")
    ap.add_argument("--dev-ma25", type=float, default=None, help="日足MA25からの乖離率(%%)")
    ap.add_argument("--entry-time", default="", help="エントリー時刻（例 10:35。前場/後場でも可）")
    ap.add_argument("--orb-gap", type=float, default=None, help="ORB高値からの乖離率(%%)")
    ap.add_argument("--refs", default="",
                    help="参考条件の充足（例 VWAP上/出来高1.2倍/連動○）")
    ap.add_argument("--discipline", default="",
                    help="v2 から削除した規律の該当状況（その日何銘柄目か・根拠・後付けの有無）")
    ap.add_argument("--trades", default="trades.csv")
    ap.add_argument("--log", default="picks_log.csv")
    ap.add_argument("--universe", default="universe.csv")
    ap.add_argument("--names", default="company_master.csv")
    ap.add_argument("--save", action="store_true", help="trades.csv に追記する")
    ap.add_argument("--stats", action="store_true", help="累積の集計だけ出す")
    args = ap.parse_args()

    trades = load_trades(args.trades)
    if args.stats:
        print_stats(trades)
        return

    required = [args.code, args.entry_date, args.entry_price, args.exit_date, args.exit_price]
    if any(x is None for x in required):
        ap.error("--code --entry-date --entry-price --exit-date --exit-price は必須です")

    h = sd.get_headers()
    names = {}
    try:
        nm = pd.read_csv(args.names, dtype={"code": str})
        names = dict(zip(nm["code"], nm["CoName"]))
    except Exception:
        pass
    uni = pd.read_csv(args.universe, dtype={"code": str}).fillna({"group": ""})
    row = uni[uni["code"] == args.code]
    driver = row.iloc[0]["driver"] if len(row) else ""
    group = (row.iloc[0]["group"] if len(row) else "") or ""

    pnl_pct = round((args.exit_price / args.entry_price - 1) * 100, 2)
    pnl_yen = round((args.exit_price - args.entry_price) * args.shares)
    a = analyze(args.code, args.entry_date, args.entry_price,
                args.exit_date, args.exit_price, args.stop, args.target, h)

    print("=" * 62)
    print(f"【取引の振り返り】{args.code} {names.get(args.code, '')}")
    print("=" * 62)
    print(f"  {args.entry_date} {args.entry_price:,.1f} 買 → "
          f"{args.exit_date} {args.exit_price:,.1f} 売  {args.shares}株"
          f"{'  ' + args.reason if args.reason else ''}")
    print(f"  損益 {pnl_pct:+.2f}%  {pnl_yen:+,}円   保有 {a.get('保有営業日数', '?')}営業日")
    if driver:
        print(f"  ドライバー {driver} / 連動グループ {group or '単独'}")

    print("\n■ 保有中の値動き")
    if "最大含み益率" in a:
        print(f"  最大含み益 {a['最大含み益率']:+.2f}%（高値 {a['保有中の最高値']:,.1f}） / "
              f"最大含み損 {a['最大含み損率']:+.2f}%（安値 {a['保有中の最安値']:,.1f}）")
    if args.target:
        print(f"  利確ライン {args.target:,.1f} への到達: "
              f"{'○ 到達していた' if a.get('利確ライン到達') else '× 未到達'}")
    if args.stop:
        print(f"  損切りライン {args.stop:,.1f} への到達: "
              f"{'○ 到達していた' if a.get('損切りライン到達') else '× 未到達'}")

    print("\n■ 損切り・利確の水準（§5 = 日足ATR(14)の1.5〜2倍）")
    if "エントリー時ATR" in a:
        print(f"  エントリー時の日足ATR(14) {a['エントリー時ATR']:,.1f}円"
              f"（終値比 {a['エントリー時ATR比']:.2f}% / {a['ATR基準日']}までで確定）")
        print(f"  §5 どおりなら損切り幅は "
              f"{a['エントリー時ATR'] * 1.5:,.0f}〜{a['エントリー時ATR'] * 2.0:,.0f}円")
        rc = rule_checks(a)
        for v in rc:
            print(f"  → {v}")
        if not rc:
            print("  → --stop / --target が未指定のため評価できません")
    else:
        print("  エントリー日より前の日足が足りず、ATRを計算できませんでした")

    print("\n■ 決済後の値動き（水準が適切だったかの検証）")
    if "決済後の最大上昇率" in a:
        print(f"  決済後{a['決済後の営業日数']}営業日: "
              f"最大 {a['決済後の最大上昇率']:+.2f}% / {a['決済後の最大下落率']:+.2f}%")
        for v in verdicts(pnl_pct, a, args.reason):
            print(f"  → {v}")
    else:
        print("  決済後の日足がまだ出ていません。数日後に再実行してください")

    print("\n■ システムの候補に出ていたか")
    hit = None
    if os.path.exists(args.log):
        log = pd.read_csv(args.log, dtype={"code": str})
        m = log[(log["code"] == args.code) & (log["pick_date"] < args.entry_date)]
        if len(m):
            hit = m.iloc[-1]
    if hit is not None:
        print(f"  {hit['pick_date']} に {hit.get('track', '')}トラックで候補入り"
              f"（{hit.get('score', '?')}点・{hit.get('pattern', '')}・RSI {hit.get('rsi', '?')}）")
    else:
        print("  候補には出ていません（裁量エントリー）")

    if args.save:
        rec = {
            "entry_date": args.entry_date, "code": args.code,
            "銘柄名": names.get(args.code, ""), "driver": driver, "group": group,
            "entry_price": args.entry_price, "shares": args.shares,
            "stop_price": args.stop, "target_price": args.target,
            "exit_date": args.exit_date, "exit_price": args.exit_price,
            "exit_reason": args.reason, "pnl_pct": pnl_pct, "pnl_yen": pnl_yen,
            "保有営業日数": a.get("保有営業日数"), "朝スコア": args.score,
            "エントリー根拠": args.basis, "ルール遵守": args.rule,
            "違反した条件": args.violation,
            "entry_atr": (round(a["エントリー時ATR"], 1)
                          if "エントリー時ATR" in a else None),
            "損切り幅ATR倍": a.get("損切り幅ATR倍"),
            "利確幅ATR倍": a.get("利確幅ATR倍"),
            "リスクリワード": a.get("リスクリワード"),
            "日足RSI": args.rsi_daily, "場中RSI": args.rsi_intraday,
            "MA5乖離率": args.dev_ma5, "MA25乖離率": args.dev_ma25,
            "エントリー時刻": args.entry_time, "ORB乖離率": args.orb_gap,
            "参考条件": args.refs, "削除規律の該当": args.discipline,
            "pick_date": hit["pick_date"] if hit is not None else "",
            "反省": args.memo,
        }
        dup = ((trades["entry_date"].astype(str) == args.entry_date)
               & (trades["code"].astype(str) == args.code)).any() if len(trades) else False
        if dup:
            print(f"\n  ※ {args.entry_date} の {args.code} は既に記録済みです。追記しませんでした。")
        else:
            trades = pd.concat([trades, pd.DataFrame([rec])], ignore_index=True)
            trades.to_csv(args.trades, index=False, encoding="utf-8-sig")
            print(f"\n  → {args.trades} に記録しました（通算 {len(trades)}件）")
            # 空欄のまま貯めると §8 の月次集計がそのぶん測れなくなる。
            missing = [k for k in ("日足RSI", "場中RSI", "MA5乖離率", "MA25乖離率",
                                   "エントリー時刻", "ORB乖離率", "参考条件",
                                   "削除規律の該当")
                       if rec.get(k) in (None, "")]
            if missing:
                print(f"  ※ v3 の記録項目が未入力です: {' / '.join(missing)}")
                print("     この列が空のままだと §8 の月次集計でその取引は数えられません")

    print("\n※ 1回の取引の勝敗はほぼ運で決まります。個別の反省より記録の蓄積を優先してください。")
    print("※ 累積の集計は python trade_review.py --stats で見られます。")


if __name__ == "__main__":
    main()
