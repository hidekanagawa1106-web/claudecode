"""
エントリー前チェック（購入判断の材料出し）
==========================================

「今この銘柄を買いたい」というときに実行し、運用方針_v2 に照らした
判定材料と、損切り・利確の推奨値を出す。

このスクリプトは売買を推奨しない。ルールに対して現在の状態がどうかを
並べるだけで、エントリーの可否はHideさん自身が判断する。

出すもの
--------
1. トレンドの位置（MA5/25/75・パーフェクトオーダー・RSI）
2. 禁止事項(§4)のうち日足で機械判定できるものの該当状況
3. 値動きの大きさ（日中値幅・ギャップ幅の実測）
4. §5 に沿った損切り・利確の推奨値と、その妥当性チェック
5. 決算の予定と直近の開示
6. 連動グループと連動確認銘柄の当日の動き

出せないもの
------------
ORB・VWAP・場中の出来高は分足が要るため判定できない。
板・歩み値も取得できない。ここはチャート画像と本人の目視に委ねる。

使い方:
    python entry_check.py 6501
    python entry_check.py 6501 --price 5320   # 今の株価を指定して評価
"""

import argparse

import pandas as pd
import yaml

import earnings
import screen_daily as sd


def volatility(df: pd.DataFrame, n: int = 14) -> dict:
    """日中値幅とギャップ幅の実測。損切り幅が現実的かを見るために使う。"""
    d = df.tail(n + 1)
    tr = ((d["high"] - d["low"]) / d["close"] * 100).tail(n)
    gap = ((d["open"] / d["close"].shift(1) - 1) * 100).abs().tail(n)
    return {"日中値幅": round(tr.mean(), 2), "日中値幅最大": round(tr.max(), 2),
            "ギャップ": round(gap.mean(), 2), "ギャップ最大": round(gap.max(), 2)}


def stop_target(vol: dict, price: float) -> dict:
    """運用方針 §5 に沿った推奨値。

    §5 はスイング -4% / +7% を定めているが、「値動きの大きさに応じて幅を変える。
    ここを一律にすると機能しない」とも書かれている。そこで固定値をそのまま出しつつ、
    その銘柄の実測ボラティリティと突き合わせて妥当かどうかを添える。
    """
    swing_stop, swing_target = -4.0, 7.0
    tr = vol["日中値幅"]
    # 損切り幅が日中値幅より狭いと、方向が合っていてもノイズで刈られる
    ratio = abs(swing_stop) / tr if tr else 0
    if ratio < 1.0:
        verdict = "狭すぎる。日中の振れ幅に届かず、ノイズで刈られる可能性が高い"
        adj = -round(tr * 1.5, 1)
    elif ratio < 1.3:
        verdict = "やや狭い。日中値幅とほぼ同水準"
        adj = -round(tr * 1.5, 1)
    elif ratio > 3.0:
        verdict = "広い。損失額が大きくなるためポジションサイズを小さくする"
        adj = None
    else:
        verdict = "妥当な水準"
        adj = None
    return {"損切り率": swing_stop, "利確率": swing_target,
            "損切り価格": round(price * (1 + swing_stop / 100), 1),
            "利確価格": round(price * (1 + swing_target / 100), 1),
            "日中値幅比": round(ratio, 2), "評価": verdict, "調整案": adj}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("code")
    ap.add_argument("--price", type=float, default=None,
                    help="現在値。省略時は直近終値を使う")
    ap.add_argument("--universe", default="universe.csv")
    ap.add_argument("--map", default="driver_map.yaml")
    ap.add_argument("--names", default="company_master.csv")
    args = ap.parse_args()

    h = sd.get_headers()
    code = args.code
    uni = pd.read_csv(args.universe, dtype={"code": str}).fillna({"group": ""})
    names = {}
    try:
        nm = pd.read_csv(args.names, dtype={"code": str})
        names = dict(zip(nm["code"], nm["CoName"]))
    except Exception:
        pass

    row = uni[uni["code"] == code]
    driver = row.iloc[0]["driver"] if len(row) else "(ユニバース外)"
    group = (row.iloc[0]["group"] if len(row) else "") or ""

    df = sd.fetch_daily_bars(code, h)
    latest = df.iloc[-1]
    price = args.price or latest["close"]

    ma5 = df["close"].rolling(5).mean().iloc[-1]
    ma25s = df["close"].rolling(25).mean()
    ma25, ma25_prev = ma25s.iloc[-1], ma25s.iloc[-2]
    ma75 = df["close"].rolling(75).mean().iloc[-1] if len(df) >= 75 else float("nan")
    rsi = sd.compute_rsi(df["close"])
    avg_vol20 = df["volume"].iloc[-21:-1].mean()
    vol_ratio = latest["volume"] / avg_vol20 if avg_vol20 else 0
    chg5 = (latest["close"] / df["close"].iloc[-6] - 1) * 100 if len(df) > 6 else 0
    vol = volatility(df)

    print("=" * 62)
    print(f"【エントリー前チェック】{code} {names.get(code, '')}")
    print("=" * 62)
    print(f"  ドライバー: {driver} / 連動グループ: {group or '単独'}")
    print(f"  直近終値 {latest['close']:,.1f}（{latest['date']}） / 評価価格 {price:,.1f}")

    print("\n■ トレンドの位置")
    order = price > ma5 > ma25 > (ma75 if pd.notna(ma75) else -1)
    print(f"  MA5 {ma5:,.1f} / MA25 {ma25:,.1f} / MA75 "
          f"{ma75:,.1f}" if pd.notna(ma75) else "  MA75 データ不足")
    print(f"  価格 > MA25: {'○' if price > ma25 else '×'}   "
          f"MA25の向き: {'上向き' if ma25 >= ma25_prev else '下向き'}   "
          f"パーフェクトオーダー: {'○' if order else '×'}")
    print(f"  RSI(14) {rsi:.1f}   出来高 前20日平均比 {vol_ratio:.2f}倍   直近5日 {chg5:+.2f}%")

    print("\n■ 禁止事項(§4)の自動チェック")
    ng = []
    if not (price > ma25 and ma25 >= ma25_prev):
        ng.append("1. 下降トレンドの途中で買う ← 価格がMA25の下、またはMA25が下向き")
    if rsi >= 70:
        ng.append(f"2. RSI 70超えで新規に買う ← 現在 {rsi:.1f}")
    if chg5 >= 15:
        ng.append(f"7. 急騰銘柄を後から追いかける ← 直近5日で {chg5:+.1f}%")
    if ng:
        for x in ng:
            print(f"  ⚠ 抵触: {x}")
    else:
        print("  日足で判定できる範囲では抵触なし（3・4・5・6は場中と本人の運用の話）")

    print("\n■ 値動きの大きさ（直近14日の実測）")
    print(f"  日中値幅 平均 {vol['日中値幅']}% / 最大 {vol['日中値幅最大']}%")
    print(f"  ギャップ 平均 {vol['ギャップ']}% / 最大 {vol['ギャップ最大']}%")

    st = stop_target(vol, price)
    print("\n■ 損切り・利確の推奨（§5 スイング基準）")
    print(f"  損切り {st['損切り率']:+.1f}% → {st['損切り価格']:,.1f}円")
    print(f"  利確   {st['利確率']:+.1f}% → {st['利確価格']:,.1f}円")
    print(f"  損切り幅は日中値幅の {st['日中値幅比']}倍 … {st['評価']}")
    if st["調整案"]:
        print(f"  → 調整するなら {st['調整案']:+.1f}% 程度（日中値幅の1.5倍）")
    if vol["ギャップ"] >= abs(st["損切り率"]) * 0.7:
        print(f"  ⚠ 平均ギャップ {vol['ギャップ']}% が損切り幅に近い。"
              f"寄り付きで逆指値を飛ばされる可能性がある")
    print("  ※ ポジションサイズは損切り幅から逆算してください（§5）")

    print("\n■ 決算")
    sig = earnings.fetch_earnings_signals(code, h, df["date"].tolist())
    sched = earnings.fetch_jpx_schedule()
    da = sig.get("earnings_days_ago")
    if da is not None:
        print(f"  直近の開示: {int(da)}営業日前 / 前年同期比 {sig.get('earnings_op_yoy')}% "
              f"/ 進捗率 {sig.get('earnings_progress')}%")
    else:
        print("  直近5営業日以内の開示なし")
    if code in sched:
        print("  ⚠ 翌営業日に決算発表予定 → §2 イベントフィルタ: ポジション半分以下、または見送り")
    else:
        print("  翌営業日の決算発表予定: なし")

    print("\n■ 連動確認（§3-1 条件4）")
    conf = yaml.safe_load(open(args.map, encoding="utf-8"))
    g = (conf.get("グループ") or {}).get(group) if group else None
    if not g or not g.get("連動確認"):
        print("  この銘柄は単独扱い、または連動確認先が未定義です")
    else:
        for kind, lst in (g["連動確認"] or {}).items():
            print(f"  {kind}: {', '.join(lst)}")
        peers = [c for c in uni[uni["group"] == group]["code"] if c != code]
        moves = []
        for p in peers[:6]:
            try:
                pd_ = sd.fetch_daily_bars(p, h, lookback_days=5)
                mv = (pd_["close"].iloc[-1] / pd_["close"].iloc[-2] - 1) * 100
                moves.append(f"{p} {names.get(p, '')[:8]} {mv:+.2f}%")
            except Exception:
                pass
        if moves:
            print("  同グループ銘柄の直近日の動き: " + " / ".join(moves))

    print("\n※ ORB・VWAP・場中の出来高・板は取得できません。チャートで確認してください。")
    print("※ これは判断材料の一覧であり、売買の推奨ではありません。")


if __name__ == "__main__":
    main()
