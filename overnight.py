"""
前夜の海外市場から、当日の先行シグナルを出す
=============================================

日本市場が開く前に確定している情報から「今日どのセクターに火がつきそうか」を
判定する。翌朝(寄り付き前)に実行する想定。

なぜ引け後のスクリーニングと分けるのか
--------------------------------------
日本のT日の値動きを動かすのは、T-1日の米国市場（T日の朝5〜6時に引ける）である。
引け後(T-1日 15:30)に走る screen_daily.py は、その夜に起きる米国市場を
構造的に見られない。したがって先行シグナルは朝のジョブでしか出せない。

実例: 2026-07-30の米国市場でマイクロソフトが+15.51%、SOX指数が+8.19%。
翌7/31の日本市場で半導体・AI関連20銘柄中18銘柄が+3%以上、8銘柄がストップ高。
7/24〜7/29のSOXは-4.3%/-2.2%/-4.5%/-5.3%と下げ続けており、日本の半導体株は
MA25の下にいた。テクニカルだけを見る限り候補には一切出てこない。
この銘柄群を拾うには、前夜の海外市場を起点にするしかない。

やること
--------
1. driver_map.yaml の朝スコア項目のうち ticker があるものを自動取得して採点
2. 急変閾値を超えた指標を「異常検知」として明示（スコアより目立たせる）
3. 閾値を超えたグループについて、ユニバースの該当銘柄を浮上させる
   このとき前日までのトレンド（MA25の上か下か）は問わない

やらないこと
------------
ニュース性の項目（利上げ観測・地政学・通商報道など）は自動判定しない。
「要確認」として列挙するだけで、判断は朝の確認に委ねる。

使い方:
    python overnight.py                    # 直近の米国市場の引けを使う
    python overnight.py --asof 2026-07-31  # その日の日本市場の寄り付き向けに再現
"""

import argparse
import datetime as dt

import pandas as pd
import requests
import yaml

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=3mo&interval=1d"
UA = {"User-Agent": "Mozilla/5.0"}


def fetch_series(ticker: str) -> pd.DataFrame:
    """日次終値を (date, close) で返す。取得できなければ空。"""
    try:
        url = CHART.format(requests.utils.quote(ticker, safe=""))
        j = requests.get(url, headers=UA, timeout=30).json()["chart"]["result"][0]
        rows = [
            (dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"), c)
            for t, c in zip(j["timestamp"], j["indicators"]["quote"][0]["close"])
            if c is not None
        ]
        return pd.DataFrame(rows, columns=["date", "close"])
    except Exception:
        return pd.DataFrame(columns=["date", "close"])


def change_before(df: pd.DataFrame, asof: str):
    """asof(日本の取引日)より前で最後に確定した終値の騰落率を返す。

    日本のT日の寄り付きが参照できるのは、T日より前に引けた米国市場まで。
    """
    d = df[df["date"] < asof] if asof else df
    if len(d) < 2:
        return None, None
    last, prev = d["close"].iloc[-1], d["close"].iloc[-2]
    return round((last / prev - 1) * 100, 2), d["date"].iloc[-1]


def evaluate_item(item: dict, asof: str) -> dict:
    """1項目を採点する。tickerが無い項目は判定せず「要確認」で返す。"""
    tickers = item.get("ticker") or []
    out = {"名前": item["名前"], "自動": bool(tickers), "変化率": None,
           "点": 0, "急変": False, "基準日": None, "内訳": []}
    if not tickers:
        return out
    chgs = []
    for t in tickers:
        c, d = change_before(fetch_series(t), asof)
        if c is not None:
            chgs.append((t, c))
            out["基準日"] = d
    if not chgs:
        out["自動"] = False
        return out
    out["内訳"] = chgs
    avg = sum(c for _, c in chgs) / len(chgs)
    out["変化率"] = round(avg, 2)
    out["点"] = 1 if avg > 0 else (-1 if avg < 0 else 0)
    th = item.get("急変閾値")
    if th:
        # 平均でも個別でも、どちらかが閾値を超えたら急変とみなす
        out["急変"] = abs(avg) >= th or any(abs(c) >= th for _, c in chgs)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="driver_map.yaml")
    ap.add_argument("--universe", default="universe.csv")
    ap.add_argument("--names", default="company_master.csv")
    ap.add_argument("--asof", default=None,
                    help="対象の日本の取引日(YYYY-MM-DD)。省略時は直近の海外引けを使う")
    args = ap.parse_args()

    conf = yaml.safe_load(open(args.map, encoding="utf-8"))
    uni = pd.read_csv(args.universe, dtype={"code": str}).fillna({"group": ""})
    names = {}
    try:
        nm = pd.read_csv(args.names, dtype={"code": str})
        names = dict(zip(nm["code"], nm["CoName"]))
    except Exception:
        pass

    asof = args.asof
    print("=" * 66)
    print(f"【前夜の海外市場シグナル】{'対象: ' + asof + ' の寄り付き向け' if asof else '直近の海外引け'}")
    print("=" * 66)

    results, alerts, hot = {}, [], []
    for gname, g in conf["グループ"].items():
        sc = g.get("朝スコア")
        if not sc:
            continue
        items = [evaluate_item(i, asof) for i in sc["項目"]]
        total = sum(i["点"] for i in items if i["自動"])
        results[gname] = (items, total, sc["閾値"])
        for i in items:
            if i["急変"]:
                alerts.append((gname, i))
        # グループを浮上させる条件は2つ。
        #   A. 朝スコアが閾値以上（運用方針 §2 の通常判定）
        #   B. プラス方向の急変が1つでもある
        # Bを別扱いにするのは、±1点の等重み採点だと SOX +8.19% が
        # ドル円 -1.91% と同じ重みになり、材料が埋もれてしまうため。
        # 2026-07-31はスコア+2(閾値+3未満)で埋もれたが、実際にはSOXが+8.19%、
        # サンディスクが+25.99%、マイクロソフトが+15.51%で、日本の半導体は
        # 18/20銘柄が+3%以上動いた。
        up_alerts = [i for i in items if i["急変"] and (i["変化率"] or 0) > 0]
        if total >= sc["閾値"]:
            hot.append((gname, f"朝スコア {total:+d} が閾値 +{sc['閾値']} 以上"))
        elif up_alerts:
            why = " / ".join(f"{i['名前']} {i['変化率']:+.2f}%" for i in up_alerts)
            hot.append((gname, f"プラス方向の急変あり（{why}）※朝スコアは {total:+d}"))

    if alerts:
        print("\n■ 急変検知（閾値超え）")
        for gname, i in alerts:
            detail = " ".join(f"{t} {c:+.2f}%" for t, c in i["内訳"])
            arrow = "上昇材料" if (i["変化率"] or 0) > 0 else "下落材料"
            print(f"  [{gname}] {i['名前']} 平均 {i['変化率']:+.2f}%  ← {arrow}")
            print(f"      {detail}")
    else:
        print("\n■ 急変検知: なし")

    print("\n■ グループ別 朝スコア")
    for gname, (items, total, th) in results.items():
        verdict = "→ 場中に条件を探す" if total >= th else "→ 何もしない"
        print(f"  {gname}  {total:+d} / 閾値 +{th}  {verdict}")
        for i in items:
            if i["自動"]:
                mark = " ★急変" if i["急変"] else ""
                print(f"      {i['名前']:<22} {i['変化率']:+7.2f}%  ({i['点']:+d}){mark}")
            else:
                print(f"      {i['名前']:<22}    要確認  (自動取得できません)")
        veto = conf["グループ"][gname].get("見送り") or []
        for v in veto:
            print(f"      ⚠ 見送り条件: {v}")

    print("\n■ 浮上した銘柄")
    if not hot:
        print("  閾値を超えたグループはありません。")
    for gname, why in hot:
        sub = uni[uni["group"] == gname]
        print(f"  [{gname}] {len(sub)}銘柄 — 前日までのトレンドは問わず提示します")
        print(f"    浮上理由: {why}")
        for _, r in sub.iterrows():
            print(f"    {r['code']} {names.get(r['code'], '')[:16]:<17} "
                  f"{r['driver']:<12} tier={r['tier']}")

    print("\n※ ニュース性の項目（利上げ観測・地政学・通商報道など）は自動判定していません。")
    print("※ ここに出たグループは「材料が来ている」という判定までで、"
          "エントリーは場中の順張り4条件で別途判断してください。")


if __name__ == "__main__":
    main()
