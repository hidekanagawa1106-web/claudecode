"""
エントリー前チェック（購入判断の材料出し）
==========================================

「今この銘柄を買いたい」というときに実行し、運用方針_v3 に照らした
判定材料と、損切り・利確の推奨値を出す。

このスクリプトは売買を推奨しない。ルールに対して現在の状態がどうかを
並べるだけで、エントリーの可否はHideさん自身が判断する。

出すもの
--------
1. 順張りの必須2条件(§3-1)と見送り条件の該当状況
2. トレンドの位置（MA5/25/75・パーフェクトオーダー・RSI）
3. 禁止事項(§4)＝下降トレンドの途中で買っていないか（v3 では1項目のみ）
4. 日足ATR(14)と、そこから出す損切り・利確（§5）
5. 決算の予定と直近の開示
6. 逆張り4条件(§3-2)の充足状況（**日足**で判定する）
7. 連動グループと連動確認銘柄の当日の動き

v2 からの変更（実測にもとづく。根拠は verify/ と運用方針_v3）
------------------------------------------------------------
- **RSI 70超えを禁止事項から外した。** 参考表示に降格。3つの独立した
  データセットすべてで否定側だった（日足5年で超過5日 +0.22〜+0.30%、
  5分足1ヶ月で勝率57.2%）。RSIは買われすぎ警告ではなく、トレンドの
  強さの連続量として働いている
- **急騰銘柄の追いかけ禁止も外した。** §3-1 の見送り条件
  「ORB高値から+1%以上」に具体化した。日足では乖離が大きいほど成績が
  良く、一般則としては支持されなかった
- **損切りを銘柄タイプ別の表から日足ATR(14)基準に変えた。** 一律の -4% は
  ATR比 2.64%〜10.25% の銘柄群に対して 0.39〜1.52倍と4倍ぶれる
- **逆張り4条件を分足ではなく日足で見る。** 時間軸で符号が反転した

出せないもの
------------
**順張りの必須条件1（オープニングレンジ上抜け）は判定できない。**
1分足の寄り付き直後が欠けるため（2026-07-31 の実測でフジクラは09:19開始で
ORB窓が丸ごと無かった）。板・歩み値も取得できない。ここはチャート画像と
本人の目視に委ねる。VWAPと場中出来高は計算するが、配信の遅延は未計測。

必須2条件のうち機械で確かめられるのは条件2（前日終値時点の日足MA25の上）だけ。
これ単独では順張りの成立を意味しない。

使い方:
    python entry_check.py 6501
    python entry_check.py 6501 --price 5320   # 今の株価を指定して評価
"""

import argparse

import pandas as pd
import yaml

import earnings
import screen_daily as sd


def intraday(code: str) -> dict:
    """当日の1分足から VWAP と場中の出来高を計算する。

    Yahoo Finance は日本株の1分足を返すが、2つ制約がある。

    1. 寄り付き直後の4〜5分が欠けることがある。2026-07-31 の実測では
       トヨタ 09:04開始、日立・三菱UFJ・NTT 09:05開始、フジクラに至っては
       09:19開始で 9:00-9:15 のバーが1本も無かった。
       オープニングレンジはまさにこの欠けている区間なので、ORBの判定には使えない。
       ここはチャート画像から目視で確認する。
    2. 配信の遅延がどの程度かは未計測。場中に実行して実際の画面と
       突き合わせる必要がある。VWAPは遅延分だけ古い値になりうる。
    """
    import datetime as dt
    import zoneinfo

    import requests
    try:
        jst = zoneinfo.ZoneInfo("Asia/Tokyo")
        url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               f"{code}.T?range=1d&interval=1m")
        j = requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                         timeout=25).json()["chart"]["result"][0]
        q = j["indicators"]["quote"][0]
        d = pd.DataFrame({
            "t": [dt.datetime.fromtimestamp(x, jst) for x in j["timestamp"]],
            "h": q["high"], "l": q["low"], "c": q["close"], "v": q["volume"],
        }).dropna()
        if d.empty:
            return None
        hm = d["t"].dt.strftime("%H:%M")
        orb = d[hm <= "09:15"]
        tp = (d["h"] + d["l"] + d["c"]) / 3
        vwap = (tp * d["v"]).cumsum() / d["v"].cumsum()
        last = d.iloc[-1]
        # 板寄せ（注文一括約定）のバーは構造的に突出する。前場寄り・後場寄り・
        # 大引けの3箇所で、毎日必ず出る。これを平均に入れると母数が膨らみ、
        # 場中の出来高倍率が実態より小さく出る。§3-1 条件3の判定がここに
        # 乗っているため、除外して平均を取る。
        auction = (hm <= "09:05") | ((hm >= "12:30") & (hm <= "12:35")) | (hm >= "14:55")
        normal = d[~auction]
        recent = d["v"].tail(5).sum() / 5
        avg = normal["v"].mean() if len(normal) else d["v"].mean()
        return {
            "日付": d["t"].iloc[0].strftime("%Y-%m-%d"),
            "最初のバー": d["t"].iloc[0].strftime("%H:%M"),
            "最終バー": d["t"].iloc[-1].strftime("%H:%M"),
            "本数": len(d),
            "ORB本数": len(orb),
            "ORB高値": orb["h"].max() if len(orb) else None,
            "ORB安値": orb["l"].min() if len(orb) else None,
            "現在値": last["c"],
            "VWAP": round(vwap.iloc[-1], 1),
            "当日高値": d["h"].max(), "当日安値": d["l"].min(),
            "直近5分出来高倍率": round(recent / avg, 2) if avg else 0,
        }
    except Exception:
        return None


def counter_trend(df: pd.DataFrame) -> dict:
    """逆張り4条件（§3-2）の充足状況を**日足**から出す。

    v2 は §3-2 を場中の分足で判定していたが、v3 で日足に変えた。
    時間軸で符号が反転したため。

    |                  | 日足5年               | 5分足1ヶ月                |
    |------------------|----------------------|--------------------------|
    | RSI30以下         | +0.64% [+0.12, +1.13] | -0.209% [-0.392, -0.020] |
    | 下降MAから大幅下方乖離 | +0.61%（10日 +1.38%）  | -0.938%（勝率32.4%）       |

    日単位では「行き過ぎからの反発」でも、分単位では落下の途中でしかない。
    v2 が §3-2 を見ていた場中が、最も不利な時間軸だった。

    条件の中身（-2σ／RSI30以下／出来高急増／反発の陽線）は v2 から変えていない。

    判定はしない。どの条件が立っているかを並べるだけで、
    エントリーの可否はHideさん自身が決める。
    """
    try:
        if len(df) < 30:
            return {"エラー": f"日足が{len(df)}本しかありません"}
        c, o, hi, lo, v = (df["close"], df["open"], df["high"],
                           df["low"], df["volume"])
        ma20, sd20 = c.rolling(20).mean(), c.rolling(20).std()
        bb_low = ma20 - 2 * sd20
        # §3-2 条件2 は「短期・長期とも RSI 30以下」。楽天の既定に合わせて 9 と 14
        rsi9, rsi14 = sd.compute_rsi(c, 9), sd.compute_rsi(c, 14)
        avg_v20 = v.rolling(20).mean().iloc[-1]
        vr = v.iloc[-1] / avg_v20 if avg_v20 else 0

        body = c.iloc[-1] - o.iloc[-1]
        upper = hi.iloc[-1] - max(c.iloc[-1], o.iloc[-1])
        prev_body = o.iloc[-2] - c.iloc[-2]      # 直前が陰線なら正
        sub = {
            "陽線": bool(c.iloc[-1] > o.iloc[-1]),
            "直前が陰線": bool(prev_body > 0),
            "実体が直前陰線の70%以上": bool(prev_body > 0 and body >= prev_body * 0.70),
            "出来高が直前以上": bool(v.iloc[-1] >= v.iloc[-2]),
            "上ヒゲが実体の30%以下": bool(body > 0 and upper <= body * 0.30),
        }
        return {
            "足": "日足", "本数": len(df),
            "最終バー": str(df["date"].iloc[-1])[:10],
            "c1": bool(lo.iloc[-1] <= bb_low.iloc[-1]),
            "c1詳細": f"安値 {lo.iloc[-1]:,.1f} / -2σ {bb_low.iloc[-1]:,.1f}",
            "c2": bool(rsi9 <= 30 and rsi14 <= 30),
            "c2詳細": f"RSI(9) {rsi9:.1f} / RSI(14) {rsi14:.1f}",
            "c3": bool(vr >= 1.5), "c3詳細": f"前20日平均の {vr:.2f}倍",
            "c4": all(sub.values()), "c4内訳": sub,
        }
    except Exception as e:
        return {"エラー": str(e)[:60]}


def volatility(df: pd.DataFrame, n: int = 14) -> dict:
    """日中値幅とギャップ幅の実測。損切り幅が現実的かを見るために使う。"""
    d = df.tail(n + 1)
    tr = ((d["high"] - d["low"]) / d["close"] * 100).tail(n)
    gap = ((d["open"] / d["close"].shift(1) - 1) * 100).abs().tail(n)
    return {"日中値幅": round(tr.mean(), 2), "日中値幅最大": round(tr.max(), 2),
            "ギャップ": round(gap.mean(), 2), "ギャップ最大": round(gap.max(), 2)}


def atr(df: pd.DataFrame, n: int = 14) -> dict:
    """日足ATR(14)。v3 の損切り幅の基準（§5）。

    1日ぶんの「真の変動幅」を次の最大値として出し、n日平均する。

        ① 高値 − 安値
        ② |高値 − 前日終値|
        ③ |安値 − 前日終値|

    ②③はギャップを取りこぼさないために要る。前日3,000円→翌日3,200円で寄って
    3,200〜3,250円で動いた日は、①だけだと50円だが実際は250円動いている。

    **当日の足は落とす。** 当日のATRは引けるまで確定しないため、場中の
    エントリー判断には前日終値までで確定した値を使う（§5）。引け後に実行した
    場合は1日ぶん古い値になるが、場中と引け後で基準が変わるほうが害が大きい。
    """
    d = df.iloc[:-1]
    prev_close = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"],
                    (d["high"] - prev_close).abs(),
                    (d["low"] - prev_close).abs()], axis=1).max(axis=1)
    a = tr.tail(n).mean()
    base = d["close"].iloc[-1]
    return {"ATR": a, "ATR比": a / base * 100, "基準日": str(d["date"].iloc[-1])[:10]}


def stop_target(atr_val: float, price: float) -> dict:
    """運用方針 §5（v3）に沿った損切り・利確。

    **損切り = 日足ATR(14)の1.5〜2倍。利確 = 損切り幅の1.75倍。**

    v2 の銘柄タイプ別の表（高ボラデイトレ -2〜3%／大型株デイトレ -0.6%／
    スイング -4%／逆張り -1.5%）は撤廃された。ATR が銘柄ごとの値動きの大きさを
    そのまま反映するので、タイプで場合分けすると二重になる。

    一律の%が機能しない理由は実測で出ている。対象15銘柄のATR比は
    2.64%（KDDI）〜10.25%（KOKUSAI）と4倍近い開きがあり、同じ -4% が
    前者ではATRの1.52倍、後者では0.39倍にあたった。0.39倍は1日の平均変動幅より
    狭く、方向が合っていてもノイズで刈られる水準になる。

    利確が1.75倍なのは、10日以内にどちらへ先に届くかを測ると損切りのほうが
    1.5倍の頻度で当たったため（母集団で +7%到達 23.2% 対 -4%到達 36.0%）。
    到達しやすさの差を埋めるだけの幅が要る。
    """
    lo_w, hi_w = atr_val * 1.5, atr_val * 2.0
    mid = (lo_w + hi_w) / 2
    return {
        "損切り幅下限": lo_w, "損切り幅上限": hi_w,
        "損切り価格下限": round(price - hi_w, 1),   # 幅が広い = 価格は下
        "損切り価格上限": round(price - lo_w, 1),
        "損切り率下限": -lo_w / price * 100, "損切り率上限": -hi_w / price * 100,
        "利確幅": mid * 1.75, "利確価格": round(price + mid * 1.75, 1),
        "利確率": mid * 1.75 / price * 100,
    }


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

    print("\n■ 順張りの必須2条件（§3-1）")
    print("  1. オープニングレンジ上抜け … 判定できません。チャート画像で確認してください")
    print(f"  {'○' if price > ma25_prev else '×'} 2. 前日終値時点の日足MA25の上 … "
          f"価格 {price:,.1f} / MA25(前日) {ma25_prev:,.1f}")
    print("  ※ VWAP・場中の出来高・連動は v3 では参考。欠けても見送りにはしません")

    print("\n■ トレンドの位置")
    order = price > ma5 > ma25 > (ma75 if pd.notna(ma75) else -1)
    print(f"  MA5 {ma5:,.1f} / MA25 {ma25:,.1f} / MA75 "
          f"{ma75:,.1f}" if pd.notna(ma75) else "  MA75 データ不足")
    print(f"  価格 > MA25: {'○' if price > ma25 else '×'}   "
          f"MA25の向き: {'上向き' if ma25 >= ma25_prev else '下向き'}   "
          f"パーフェクトオーダー: {'○' if order else '×'}")
    print(f"  RSI(14) {rsi:.1f}   出来高 前20日平均比 {vol_ratio:.2f}倍   直近5日 {chg5:+.2f}%")

    print("\n■ 禁止事項(§4)の自動チェック")
    if not (price > ma25 and ma25 >= ma25_prev):
        print("  ⚠ 抵触: 下降トレンドの途中で買う ← 価格がMA25の下、またはMA25が下向き")
    else:
        print("  抵触なし（v3 の禁止事項は1項目のみ）")

    # v3 で禁止事項から外した2つ。ブロックはしないが、§8 の記録対象として出す。
    print("\n■ 参考（v3 で禁止事項から外した項目。記録して後で検証する）")
    print(f"  RSI(14) {rsi:.1f} … "
          + ("高い。実測では高いほど順行しやすい" if rsi >= 70
             else "低い状態での順張りには注意する" if rsi < 50
             else "中位"))
    print("    v2 の「RSI70超えで新規に買わない」は撤回。日足5年で超過5日 +0.22〜+0.30%、"
          "5分足1ヶ月で勝率57.2%と、3つの独立したデータすべてで否定側だった")
    print(f"  直近5日 {chg5:+.2f}% … "
          + ("急騰。ORB高値からの乖離を必ず見てください" if chg5 >= 15 else "通常"))
    print("    v2 の「急騰銘柄を後から追いかけない」は §3-1 の見送り条件"
          "「ORB高値から+1%以上」に具体化した")

    print("\n■ 値動きの大きさ（直近14日の実測）")
    print(f"  日中値幅 平均 {vol['日中値幅']}% / 最大 {vol['日中値幅最大']}%")
    print(f"  ギャップ 平均 {vol['ギャップ']}% / 最大 {vol['ギャップ最大']}%")

    a = atr(df)
    st = stop_target(a["ATR"], price)
    print("\n■ 損切り・利確（§5 = 日足ATR(14)の1.5〜2倍）")
    print(f"  日足ATR(14) {a['ATR']:,.1f}円（終値比 {a['ATR比']:.2f}%）"
          f" ※{a['基準日']}までで確定")
    print(f"  損切り {st['損切り率上限']:+.1f}〜{st['損切り率下限']:+.1f}% → "
          f"{st['損切り価格下限']:,.1f}〜{st['損切り価格上限']:,.1f}円")
    print(f"  利確   {st['利確率']:+.1f}% → {st['利確価格']:,.1f}円"
          f"（損切り幅の1.75倍）")
    print("  ※ v2 の銘柄タイプ別の表（-2〜3%／-0.6%／-4%／-1.5%）は撤廃されました")
    if vol["ギャップ"] >= abs(st["損切り率上限"]) * 0.7:
        print(f"  ⚠ 平均ギャップ {vol['ギャップ']}% が損切り幅に近い。"
              f"寄り付きで逆指値を飛ばされる可能性がある")
    print("  ※ 持ち越すなら、サイズは損切り幅ではなく想定される最悪ギャップから"
          "逆算してください（§5）")
    print("  ※ デイトレに使うなら日足ATRの1.5〜2倍は広すぎます。当日の値幅"
          f"（平均 {vol['日中値幅']}%）に読み替えてください")

    intra = intraday(code)
    print("\n■ 当日の場中（1分足）")
    if not intra:
        print("  1分足を取得できませんでした")
    else:
        print(f"  {intra['日付']} {intra['最初のバー']}〜{intra['最終バー']}  {intra['本数']}本")
        print(f"  現在値 {intra['現在値']:,.1f} / VWAP {intra['VWAP']:,.1f} → "
              f"価格はVWAPの{'上（買い方優勢）' if intra['現在値'] > intra['VWAP'] else '下'}"
              f"（v3 では参考）")
        print(f"  当日高値 {intra['当日高値']:,.1f} / 安値 {intra['当日安値']:,.1f}"
              f" / 直近5分の出来高倍率 {intra['直近5分出来高倍率']}倍（v3 では参考）")
        if intra["ORB本数"] < 10:
            print(f"  ⚠ 9:00-9:15のバーが{intra['ORB本数']}本しかありません"
                  f"（最初のバーが{intra['最初のバー']}）。"
                  f"オープニングレンジは信頼できないため、チャート画像で確認してください")
        else:
            print(f"  参考: 9:00-9:15 高値 {intra['ORB高値']:,.1f} / "
                  f"安値 {intra['ORB安値']:,.1f}（{intra['ORB本数']}本。欠損があれば不正確）")
        print("  ※ 配信の遅延は未計測です。画面の値と食い違う場合は画面を優先してください")

        print("\n■ 見送り条件（§3-1。いずれかに当たれば入らない）")
        # 最終バーの時刻では判定できない。引け後に実行すれば必ず15:30になり、
        # 常に「14:00以降」と出てしまう。見るのはエントリーする瞬間の時刻。
        import datetime as _dt
        import zoneinfo as _zi
        now = _dt.datetime.now(_zi.ZoneInfo("Asia/Tokyo"))
        hhmm = now.strftime("%H:%M")
        if "09:00" <= hhmm <= "15:30":
            print(f"  {'⚠ 該当' if hhmm >= '14:00' else '該当なし'}: "
                  f"14:00以降の新規エントリー … 現在 {hhmm} JST")
        else:
            print(f"  場外の実行です（現在 {hhmm} JST）。"
                  f"14:00以降かどうかはエントリーする時点の時刻で判断してください")
        if intra["ORB高値"]:
            gap = (price / intra["ORB高値"] - 1) * 100
            print(f"  {'⚠ 該当' if gap >= 1.0 else '該当なし'}: "
                  f"ORB高値から+1%以上 … 乖離 {gap:+.2f}%"
                  + ("（ORBが欠損しているため不正確）" if intra["ORB本数"] < 10 else ""))
        else:
            print("  ORB高値を取得できないため判定できません")

    ct = counter_trend(df)
    print("\n■ 逆張り4条件（§3-2）日足で判定")
    if ct.get("エラー"):
        print(f"  取得できませんでした: {ct['エラー']}")
    else:
        marks = [("1. ボリンジャー -2σ タッチ", ct["c1"], ct["c1詳細"]),
                 ("2. RSI 30以下（短期・長期とも）", ct["c2"], ct["c2詳細"]),
                 ("3. 出来高急増", ct["c3"], ct["c3詳細"]),
                 ("4. 反発の陽線1本確定", ct["c4"], "")]
        print(f"  {ct['本数']}本 / 最終バー {ct['最終バー']}")
        for lab, ok, detail in marks:
            print(f"  {'○' if ok else '×'} {lab:<28} {detail}")
        if not ct["c4"]:
            ng = [k for k, v in ct["c4内訳"].items() if not v]
            print(f"     └ 満たしていない要素: {' / '.join(ng)}")
        n = sum(ct[k] for k in ("c1", "c2", "c3", "c4"))
        print(f"  → {n}/4。§3-2 は全条件必須のため"
              + ("成立しています" if n == 4 else "成立していません"))
        print("  ※ v3 で判定する足を分足から日足に変えました。時間軸で符号が反転し、"
              "RSI30以下は日足5年 +0.64% に対し5分足1ヶ月では -0.209% でした")
        print("  ※ 逆張りは §4 禁止事項（下降トレンドの途中で買う）の例外です。"
              "順張りの必須2条件が揃わない日の補完として使ってください")

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

    print("\n■ 連動確認（§3-1 の参考項目。v3 では必須ではありません）")
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

    print("\n※ 順張りの必須条件1（オープニングレンジ上抜け）と板・歩み値は取得できません。"
          "チャート画像で確認してください。")
    print("※ これは判断材料の一覧であり、売買の推奨ではありません。")


if __name__ == "__main__":
    main()
