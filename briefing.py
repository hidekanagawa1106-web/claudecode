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

「押し目」は深さを問わない箱で、-2% も -18% も同じところに入る。
深さで切る案を2つ実測して、どちらも支持されなかった（詳細は docs/usage.md）ので、
切らずに **MA25乖離を一覧と各行に必ず出す**ことで区別できるようにしている。
分類そのものより、乖離に見合った損切り幅と建玉を選ぶほうが実務に効く。

エントリーそのものは判定しない。場中に entry_check.py（/entry-check）を使う。

何を厚く出し、何を出さないか
----------------------------
朝に要るのは**前提情報**であって、エントリーの可否ではない。
RSI・出来高倍率・VWAP・ボリンジャーは、実際に買う瞬間に見れば足りる指標で、
8時に見ても場が開くまでに変わる。だから朝は出さない（/entry-check 側にある）。

そのぶん、朝でないと拾えないものを厚く出す:

  ・前夜の海外市場 — 個別ティッカーの変化率まで
  ・グループ別のマクロ — 朝スコアの内訳を項目ごとに
  ・ニューストピック — 見出しと、方向判定の根拠になった語
  ・銘柄ごとのニュース

定量的な部分は「大きなトレンドの向き」と「直近の値動き（前日のローソク足）」に絞る。
どちらも8時の時点で確定していて、場中に変わらないため。

使い方:
    python briefing.py
    python briefing.py --no-news        ニュース取得を省く
    python briefing.py --skip-overnight 海外市場の取得を省く
"""

import argparse
import datetime as dt
import sys
import zoneinfo

import pandas as pd
import yaml

import earnings
import overnight as ov
import screen_daily as sd

JST = zoneinfo.ZoneInfo("Asia/Tokyo")


def jst_today() -> str:
    """日本時間での今日の日付。

    実行コンテナは UTC で動いている。8:00 JST は前日の 23:00 UTC なので、
    dt.date.today() を使うと**前日**が返る。東証カレンダーの照会も
    見出しの日付もそれで1日ずれる。祝日をまたぐと、営業日なのに
    「休場」と判定して配信を落とす（2026-08-03 に別の原因で一度起きている）。
    """
    return dt.datetime.now(JST).date().isoformat()


STANCE_ORDER = ["順張りの土俵", "押し目", "トレンド下向き", "見送り"]

STANCE_NOTE = {
    "順張りの土俵": [
        "場中に §3-1 の4条件（ORB・VWAP・出来高1.5倍・連動銘柄）を確認してください。",
    ],
    # 「押し目」は乖離の深さを問わない箱なので、-2% と -18% が同じ見出しの下に並ぶ。
    # 深さで切ることは実測が支持しなかった（15銘柄×370営業日/857件で検証。
    # 乖離 -8% 超の162件のうち60%が半導体4銘柄の同一暴落で、その分を除いた
    # 他11銘柄65件は +2.62%/勝率60% と、むしろ成績の良い場面だった。
    # 終値>MA75 で切る案は、外す側のほうが +1.01% と良く、根拠にならなかった）。
    # 切らずに、深さが目に入るようにする。
    "押し目": [
        "MA25は上向きですが終値がその下。§3-2 の逆張り4条件が対象です。",
        "乖離が大きいものは押し目ではなく調整局面の可能性があります。",
        "同じ -4% の損切りでも、乖離 -2% の銘柄と -18% の銘柄では刈られる確率が違います。",
        "損切り幅と建玉の大きさを乖離に見合わせてください。",
    ],
    "トレンド下向き": [
        "MA25が20日前より下。順張りの対象外です（禁止事項1）。",
    ],
    "見送り": [
        "運用方針に触れます。今日は新規に入らない前提で見てください。",
    ],
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
    reaction = earnings_reaction(df, sig.get("earnings_disc_date"))

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
        "ret60": (round(close / df["close"].iloc[-61] * 100 - 100, 1)
                  if len(df) >= 61 else None),
        # 直近3営業日の値動き。「昨日だけ」では戻りの途中か継続かが分からない。
        "recent_chg": [round(df["close"].iloc[-i] / df["close"].iloc[-i - 1] * 100 - 100, 2)
                       for i in range(3, 0, -1)] if len(df) >= 4 else [],
        "candle": describe_candle(latest, vol_ratio, pattern),
        # 高値・安値の切り上げ。直近10日と、その前10日を比べる。
        # MA25の傾きだけだと「戻り高値を切り下げながらの反発」を拾ってしまう。
        "higher_high": (bool(df["high"].iloc[-10:].max() > df["high"].iloc[-20:-10].max())
                        if len(df) >= 20 else None),
        "higher_low": (bool(df["low"].iloc[-10:].min() > df["low"].iloc[-20:-10].min())
                       if len(df) >= 20 else None),
        "score": s_trend + s_rsi + s_vol + s_candle,
        "score_trend": s_trend, "score_rsi": s_rsi,
        "score_volume": s_vol, "score_candle": s_candle,
        "quant_all_pass": bool(above_ma25 and ma25_up and vol_ratio >= 1.2 and rsi < 70),
        "earnings_next": code in schedule,
        "reaction": reaction,
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


def earnings_reaction(df: pd.DataFrame, disc_date) -> list:
    """決算発表の前後で株価と出来高がどう動いたかを日ごとに返す。

    発表が引け後か寄り前かはAPIから判別できない。引け後発表なら反応は翌営業日に
    出るので、発表日とその後を並べて、出来高が跳ねた日を見てもらう形にする。

    ここでは値動きを並べるだけで、良い決算/悪い決算の判定はしない。
    見出しの語数え上げが翌日リターンを説明しなかった検証（6,569件、最大 r=0.16）
    と同じ理由で、内容の評価は読み手に委ねる。
    """
    if not disc_date:
        return []
    d = str(disc_date)[:10]
    idx = df.index[df["date"] >= d]
    if not len(idx):
        return []
    start = df.index.get_loc(idx[0])
    avg20 = df["volume"].iloc[max(0, start - 21):start - 1].mean()
    out = []
    for i in range(start, min(start + 4, len(df))):
        if i == 0:
            continue
        row, prev = df.iloc[i], df.iloc[i - 1]
        out.append({
            "date": row["date"],
            "label": "発表日" if i == start else f"翌{i - start}営業日",
            "gap": round(row["open"] / prev["close"] * 100 - 100, 2),
            "chg": round(row["close"] / prev["close"] * 100 - 100, 2),
            "vol": round(row["volume"] / avg20, 2) if avg20 else None,
        })
    if out:
        first = df.iloc[start - 1]["close"] if start else df.iloc[start]["close"]
        out[-1]["cum"] = round(df["close"].iloc[-1] / first * 100 - 100, 2)
    return out


def describe_candle(row, vol_ratio: float, pattern: str) -> str:
    """前営業日のローソク足を、形と読み方で書く。

    ストップ高・大引け坊主のラベルだけだと「上ヒゲが伸びた」「下で買われた」が
    落ちる。実体とヒゲの割合まで出して、そのまま読めるようにする。
    """
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    rng = h - l
    if rng <= 0:
        return f"値幅なし（{o:,.0f}円で寄り引け同値。ストップ配分の可能性）"

    body, upper, lower = abs(c - o), h - max(o, c), min(o, c) - l
    kind = "陽線" if c > o else ("陰線" if c < o else "同事線（寄り引け同値）")
    parts = [f"{kind} 実体{body / c * 100:.1f}%"]
    if upper / rng >= 0.05:
        parts.append(f"上ヒゲ{upper / c * 100:.1f}%")
    if lower / rng >= 0.05:
        parts.append(f"下ヒゲ{lower / c * 100:.1f}%")
    line = " / ".join(parts) + f"（安値{l:,.0f}〜高値{h:,.0f}円）"

    read = []
    if pattern and pattern != "-":
        read.append(pattern)
    if upper / rng >= 0.40:
        read.append("上ヒゲが長い。高値を買い上げたが押し戻された")
    if lower / rng >= 0.40:
        read.append("下ヒゲが長い。下値で買い戻しが入った")
    if body / rng >= 0.70:
        read.append("実体が大きく、方向感のはっきりした一日")
    elif body / rng <= 0.20:
        read.append("実体が小さく、方向感に乏しい")
    # 出来高は「同じ形でも意味が変わる」ところだけ添える。
    # 平常時の倍率まで書くとエントリー判断の指標に見えてしまう。
    if vol_ratio >= 1.5:
        read.append(f"大商い（20日平均の{vol_ratio:.1f}倍）")
    elif vol_ratio and vol_ratio <= 0.6:
        read.append(f"薄商い（20日平均の{vol_ratio:.1f}倍）")
    if read:
        line += "\n         → " + " / ".join(read)
    return line


def describe_trend(r: dict) -> str:
    """大きなトレンドの向き。1つの数字ではなく、向き・位置・波形の3点で書く。"""
    sl = r.get("ma25_slope")
    if sl is None:
        return "判定できません（データ不足）"
    label = ("はっきり上向き" if sl >= 3 else "緩やかに上向き" if sl >= 1
             else "横ばい" if sl > -1 else "緩やかに下向き" if sl > -3
             else "はっきり下向き")
    line = f"{label}（MA25が20日前比 {sl:+.1f}%）。終値はMA25を {r['ma25_gap']:+.1f}%"
    line += "、MA75の上" if r.get("above_ma75") else "、MA75の下"

    wave = []
    if r.get("higher_high") is not None:
        wave.append("高値切り上げ" if r["higher_high"] else "高値切り下げ")
        wave.append("安値切り上げ" if r["higher_low"] else "安値切り下げ")
    horizon = []
    if r.get("ret20") is not None:
        horizon.append(f"20日 {r['ret20']:+.1f}%")
    if r.get("ret60") is not None:
        horizon.append(f"60日 {r['ret60']:+.1f}%")
    tail = " / ".join(wave + horizon)
    return line + (f"\n         直近10日は{tail}" if tail else "")


def earnings_lines(r: dict, news: list) -> list:
    """決算まわりを、控えている場合と出た後で書き分ける。

    控えている場合   いつか / §2 イベントフィルタに触れるか
    出た後           中身 / 株価と出来高の反応 / それに触れた見出し

    どちらも判定はしない。§2 の該当は運用方針の条文をそのまま当てているだけ。
    """
    out = []
    d = r.get("earnings_days_ago")
    fresh = d is not None and not pd.isna(d) and int(d) <= 9

    if r.get("earnings_next"):
        out.append("⚠ 本日が決算発表日 — §2 イベントフィルタ（当日・翌日は半分以下か見送り）")
    else:
        nd, est = r.get("earnings_next_days"), r.get("earnings_next_est")
        if nd is not None and est:
            if nd < 0:
                out.append(f"⚠ 次回決算は {est} 前後と推定。推定日を過ぎており、"
                           "いつ出てもおかしくありません")
            elif nd <= 5:
                out.append(f"⚠ 次回決算は {est} 前後と推定（あと{nd}日）。"
                           "§2 イベントフィルタが近づいています")
            elif nd <= 14:
                out.append(f"次回決算は {est} 前後と推定（あと{nd}日）")
            else:
                out.append(f"次回決算は {est} 前後と推定（あと{nd}日）。当面は影響なし")
            out.append("  ※ 前年同期の開示日から推定した目安です。確定日ではありません")

    if not fresh:
        if not r.get("earnings_next"):
            out.insert(0, "直近10営業日以内の決算発表なし")
        return out

    d = int(d)
    when = "前営業日" if d == 0 else f"{d + 1}営業日前"
    body = f"決算が{when}（{r.get('earnings_disc_date')}）に発表"
    yoy = r.get("earnings_op_yoy")
    if yoy is not None and not pd.isna(yoy):
        body += f" / 営業利益 前年同期比 {yoy:+.1f}%"
    prog = r.get("earnings_progress")
    if prog is not None and not pd.isna(prog):
        body += f" / 通期進捗率 {prog:.1f}%"
    rev = sd.REVISION_LABEL.get(r.get("earnings_revision"))
    if rev:
        body += f" / 通期予想は{rev}"
    out.insert(0, body)

    react = r.get("reaction") or []
    if react:
        out.append("株価の反応:")
        for x in react:
            v = f" 出来高{x['vol']:.1f}倍" if x.get("vol") else ""
            out.append(f"  {x['label']} {x['date']}  始値ギャップ{x['gap']:+.2f}% / "
                       f"終値{x['chg']:+.2f}%{v}")
        cum = react[-1].get("cum")
        if cum is not None:
            out.append(f"  発表前の終値からの累計 {cum:+.2f}%")
        big = max(react, key=lambda x: x.get("vol") or 0)
        if big.get("vol") and big["vol"] >= 1.5:
            out.append(f"  → 出来高が跳ねたのは{big['label']}。"
                       "引け後発表ならここが実質の反応日です")

    hit = [a for a in (news or []) if any(
        w in a["見出し"] for w in ("決算", "純利益", "営業利益", "最高益",
                                   "上方修正", "下方修正", "増益", "減益",
                                   "受注", "業績", "四半期"))]
    if hit:
        out.append("決算に触れた見出し:")
        for a in hit[:4]:
            out.append(f"  [{a['日時']}] {a['見出し'][:56]}（{a['発信元']}）")
        out.append("  ※ 反応の良し悪しは判定していません。見出しの言い回しと"
                   "上の値動きを合わせて読んでください")
    return out


def print_markets(macro):
    """前夜の海外市場。指標ごとに畳んで、個別ティッカーの内訳まで出す。

    同じ指標が複数グループに現れる（ドル円は半導体・メガバンク・自動車の3つ）ため、
    グループ単位で並べると同じ数字が何度も出てしまう。ここでは指標を主語にする。
    """
    print("\n■ 前提1  前夜の海外市場\n")
    if not macro:
        print("  （未取得です。--skip-overnight で実行した場合はここが空になります）")
        return
    seen = {}
    for gname, items in macro["groups_items"].items():
        for i in items:
            if not i["内訳"]:
                continue
            e = seen.setdefault(i["名前"], {"i": i, "groups": []})
            e["groups"].append(gname)
    if not seen:
        print("  価格指標を取得できませんでした。")
        return
    for name, e in sorted(seen.items(), key=lambda x: -abs(x[1]["i"]["変化率"] or 0)):
        i = e["i"]
        flag = "★急変" if i["急変"] else "     "
        print(f"  {flag} {name:<26}{i['変化率']:+7.2f}%")
        if len(i["内訳"]) > 1:
            print("           内訳: " + " / ".join(f"{t} {c:+.2f}%" for t, c in i["内訳"]))
        for t, ah in (i.get("時間外") or []):
            print(f"           時間外: {t} {ah['変化率']:+.2f}%")
        print(f"           影響先: {' / '.join(e['groups'])}")
    print(f"\n  基準日: 前営業日の終値どうしの比較。★は急変閾値を超えたもの。")
    print("  ※ 8:00 JST は 19:00 ET（夏時間）。米国の時間外取引は 20:00 ET まで続きます。")


def print_groups(macro):
    """グループ別の朝スコア。合計だけでなく、何がプラスで何がマイナスかを出す。"""
    print("\n\n■ 前提2  グループ別のマクロ（朝スコアの内訳）\n")
    if not macro:
        print("  （未取得です）")
        return
    for gname, (total, th) in macro["scores"].items():
        verdict = "追い風が出ている" if total >= th else "閾値未達"
        print(f"  ● {gname}   朝スコア {total:+d} / 閾値 {th:+d}   {verdict}")
        for i in macro["groups_items"][gname]:
            if i["内訳"]:
                what = f"{i['変化率']:+.2f}%"
            elif i.get("トピック"):
                d = i.get("方向")
                what = ("強まる方向" if d == 1 else "弱まる方向" if d == -1 else "中立")
                if i.get("語数"):
                    what += f"（強{i['語数'][0]}語/弱{i['語数'][1]}語）"
            elif i["見出し"]:
                what = f"ニュース{len(i['見出し'])}件（採点なし）"
            else:
                # ticker もトピックも持たない項目。自動では取れないので、
                # 「取得に失敗した」ではなく「手で見る欄」であることを示す
                what = "自動取得なし（手動確認）"
            sens = i.get("感応度")
            tail = ""
            if i.get("不感帯内"):
                tail = f"  ※不感帯±{i['不感帯']}%の内側。ノイズとみなして0点"
            if sens == -1:
                tail += "  ※上昇はこのグループには逆風"
            if i.get("採点対象") is False:
                tail += "  ※採点対象外（表示のみ）"
            mark = "＋" if i["点"] > 0 else ("－" if i["点"] < 0 else "・")
            print(f"      {mark} {i['名前'][:22]:<23}{what:<24}{i['点']:+d}点{tail}")
        print()
    print("  ※ 朝スコアは銘柄の点数に加算していません。並べているだけで、順位付けにも")
    print("     使っていません。マクロとテクニカルは互いのゲートにしない設計です。")
    print("  ※ 商社・海運・自動車の3グループは60営業日の実測に基づきます。")
    print("     半導体・メガバンク・防衛・保険はトピックを含むため閾値・感応度が未検証です。")


def print_topics(macro):
    """ニューストピック。方向判定の根拠になった見出しと語をそのまま出す。"""
    print("\n■ 前提3  今朝のニューストピック\n")
    topics = (macro or {}).get("topics") or {}
    if not topics:
        print("  （未取得です）")
        return
    for name, t in topics.items():
        d = t["方向"]
        label = ("強まる方向" if d == 1 else "弱まる方向" if d == -1 else "中立")
        print(f"  ● {name}   {label}（強まる語 {t['強']} / 弱まる語 {t['弱']}）")
        if not t["見出し"]:
            print("      該当する見出しなし（直近36時間・許可ソース内）")
        for a, u, dw in t["根拠"]:
            hit = " ".join(f"+{w}" for w in u) + " " + " ".join(f"-{w}" for w in dw)
            print(f"      ・[{a['日時']}] {a['見出し'][:60]}")
            print(f"           {a['発信元']}   一致した語: {hit.strip()}")
        rest = [a for a in t["見出し"] if a not in [x[0] for x in t["根拠"]]]
        for a in rest[:3]:
            print(f"      ・[{a['日時']}] {a['見出し'][:60]}  ({a['発信元']})")
        print()
    print("  ※ 見出しの単語を数えているだけです。「据え置き」と「利上げ」が同居する")
    print("     見出しは相殺されて中立になります。根拠は全部出しているので、")
    print("     違うと思ったらここで上書きしてください。")


def print_briefing(rows, names, macro, news, today, log_note):
    nm = lambda c: names.get(c, "")
    bar = "━" * 66
    group_scores = (macro or {}).get("scores") or {}
    print(bar)
    print(f"【朝のブリーフィング】{today}   ウォッチ{len(rows)}銘柄")
    print(bar)

    if macro:
        print("\n■ 今日の地合い（要約）\n")
        for line in macro["notes"]:
            print(f"  {line}")

    print_markets(macro)
    print_groups(macro)
    print_topics(macro)

    print("\n" + bar)
    print(f"■ 15銘柄の状態")
    print(bar)
    # MA25乖離は一覧にも出す。同じ「押し目」でも -1.7% と -17.9% では
    # 損切り幅も建玉の大きさも変わるため、詳細ブロックに畳むと見落とす。
    print("\n  " + f"{'コード':<6}{'銘柄':<15}{'終値':>9}{'前日比':>8}"
          f"{'1単元':>8}  {'トレンド':<8}{'MA25乖離':>9}  {'前日の足':<12}今日の土俵")
    for r in rows:
        sl = r.get("ma25_slope")
        tl = ("↑↑" if sl is not None and sl >= 3 else "↑" if sl is not None and sl >= 1
              else "→" if sl is not None and sl > -1 else "↓" if sl is not None and sl > -3
              else "↓↓")
        kind = r["candle"].split(" ")[0].split("（")[0]
        print("  " + f"{r['code']:<6}{nm(r['code'])[:13]:<15}"
              f"{r['prev_close']:>8,.0f}円{r['chg']:>+7.2f}%"
              f"{r['unit_cost'] / 10000:>7.1f}万  {tl:<9}{r['ma25_gap']:>+8.1f}%  "
              f"{kind:<12}{r['stance']}")

    counts = {s: sum(1 for r in rows if r["stance"] == s) for s in STANCE_ORDER}
    print("\n  内訳: " + " / ".join(f"{s} {counts[s]}件" for s in STANCE_ORDER))

    for stance in STANCE_ORDER:
        group = [r for r in rows if r["stance"] == stance]
        if not group:
            continue
        print(f"\n\n■ {stance}  {len(group)}件")
        for line in STANCE_NOTE[stance]:
            print(f"  {line}")
        print()
        for r in group:
            # 1要素1行の箇条書きにする。要素を「｜」で連ねると、読み手が
            # どこで切れるかを探しながら読むことになる。
            print(f"  ── {r['code']} {nm(r['code'])} " + "─" * 6)
            print(f"     終値 {r['prev_close']:,.0f}円（前日比 {r['chg']:+.2f}%）")
            print(f"     1単元 {r['unit_cost'] / 10000:,.1f}万円 / MA25から "
                  f"{r['ma25_gap']:+.1f}% / {r['driver']}")
            if r["blockers"]:
                print()
                for w in r["blockers"]:
                    print(f"     ⚠ 見送り理由: {w}")

            arts = (news or {}).get(r["code"])
            print("\n     ● 決算")
            for line in earnings_lines(r, arts):
                print(f"       {line}")

            print("\n     ● マクロ")
            # グループ名の有無と朝スコアの有無は別。--skip-overnight のときに
            # 「連動グループなし」と出してしまうと事実と食い違う。
            g = r["group"]
            if not g:
                # 単独銘柄はセクターの朝スコアを持たないが、それだけだと
                # マクロ欄が空になる。全体の地合いくらいは出しておく。
                print("       連動グループなし（単独銘柄）。"
                      "セクター単位の追い風・逆風は判定対象外です")
                mkt = (macro or {}).get("market")
                if mkt:
                    print("       全体の地合い: " + " / ".join(
                        f"{n} {v:+.2f}%" for n, v in mkt))
            else:
                tot, th = group_scores.get(g, (None, None))
                if tot is None:
                    print(f"       {g}（朝スコア未取得）")
                else:
                    verdict = "追い風が出ている" if tot >= th else "追い風なし（閾値未達）"
                    print(f"       {g} 朝スコア {tot:+d} / 閾値 {th:+d} — {verdict}")
                    for i in (macro or {}).get("groups_items", {}).get(g, []):
                        if i["点"] != 0 or i["急変"]:
                            v = (f"{i['変化率']:+.2f}%" if i["内訳"] else
                                 ("強まる方向" if i.get("方向") == 1 else
                                  "弱まる方向" if i.get("方向") == -1 else "中立"))
                            star = " ★急変" if i["急変"] else ""
                            print(f"         {i['名前']} {v} → {i['点']:+d}点{star}")

            print("\n     ● ニュース")
            if arts:
                for a in arts:
                    print(f"       [{a['日時']}] {a['見出し'][:58]}")
                    print(f"                 {a['発信元']}")
                print("       ※ 内容の良し悪しは判定していません。読んでご判断ください")
            elif arts is not None:
                print("       該当なし（直近36時間・許可ソース内）")
            else:
                print("       未取得（--no-news / --skip-overnight で実行しています）")

            print("\n     ● トレンドと直近の値動き")
            for line in describe_trend(r).split("\n"):
                print(f"       {line.strip()}")
            for line in r["candle"].split("\n"):
                print(f"       {line.strip()}")
            if r.get("recent_chg"):
                print("       直近3日: "
                      + " → ".join(f"{v:+.2f}%" for v in r["recent_chg"]))
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
    print("※ RSI・出来高倍率・VWAP・ボリンジャーはここに出していません。場が開くまでに")
    print("   変わる指標なので、買う瞬間に /entry-check で確認してください。")
    print("※ このブリーフィングは前提情報と日足の状態を並べたもので、買いのサインでは")
    print("   ありません。エントリーは場中の順張り4条件で別途判断してください。")


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

    today = jst_today()
    headers = sd.get_headers()
    is_open, day_label = sd.trading_day_status(today, headers)
    if is_open is False:
        print(f"【朝のブリーフィング】{today}  東証: {day_label}")
        print("\n  本日は取引がありません。ブリーフィングは出しません。")
        print("  ※ 日足は前営業日までしか無いため、pick_date が前回と同じでも")
        print("     休場日とは限りません。判定はこのカレンダーだけを根拠にしています。")
        return

    macro = None if args.skip_overnight else collect_macro(conf)
    group_scores = (macro or {}).get("scores") or {}

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
            # 朝はニュースを厚く出す方針なので、1銘柄あたり3件→5件にしている
            news[r["code"]] = ov.fetch_news(kw, nc.get("許可ソース", []),
                                            nc.get("収集時間", 36), 5, None, company=kw)

    log_note = record(rows, group_scores, args.log, headers)
    order = {s: i for i, s in enumerate(STANCE_ORDER)}
    rows.sort(key=lambda r: (order[r["stance"]], -(r["ma25_gap"] or 0)))
    print_briefing(rows, names, macro, news,
                   f"{today}  東証: {day_label}", log_note)


def collect_macro(conf: dict) -> dict:
    """マクロを丸ごと集めて返す。要約だけでなく項目の内訳も保持する。

    朝に厚く出したいのはここなので、evaluate_item が返す内訳
    （個別ティッカーの変化率・時間外・トピックの根拠見出し）を捨てずに持ち回る。
    銘柄の採点には一切使わない点は morning.py と同じ。
    """
    news_conf = (conf.get("共通") or {}).get("ニュース") or {}
    topics = {n: ov.evaluate_topic(n, t, news_conf, None)
              for n, t in ((conf.get("共通") or {}).get("ニューストピック") or {}).items()}
    notes, scores, items_by_group, hot, alerts = [], {}, {}, [], []
    for gname, g in conf["グループ"].items():
        sc = g.get("朝スコア")
        if not sc:
            continue
        items = [ov.evaluate_item(i, None, news_conf, topics) for i in sc["項目"]]
        total = sum(i["点"] for i in items if i["自動"])
        scores[gname] = (total, sc["閾値"])
        items_by_group[gname] = items
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
    # 単独銘柄はグループの朝スコアを持たないため、マクロ欄が空になる。
    # 市場全体の指標だけ取り出して、どの銘柄にも出せるようにしておく。
    # 採点には使わない。地合いの背景として並べるだけ。
    market = []
    for want in ("日経平均", "ドル円"):
        for items in items_by_group.values():
            hit = next((i for i in items
                        if i["名前"] == want and i["変化率"] is not None), None)
            if hit:
                market.append((want, hit["変化率"]))
                break
    return {"notes": notes, "scores": scores, "market": market,
            "groups_items": items_by_group, "topics": topics}


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
