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


GNEWS = "https://news.google.com/rss/search?q={}&hl=ja&gl=JP&ceid=JP:ja"


def fetch_news(keyword: str, allow: list, hours: int, limit: int,
               asof: str = None) -> list:
    """Google News のキーワード検索から、許可した発信元の見出しだけを拾う。

    見出しと配信元・配信時刻を集めるところまでで、内容の良し悪しは判定しない。
    同じ利上げ報道でも銀行にはプラス、自動車にはマイナスに働くため、
    +1/-1 の判断は文意を読める朝の確認に委ねる。

    Bloombergとロイターは自社のRSSを直接取得できなかったが、
    Google News 経由なら発信元として拾える。
    """
    import email.utils as eut
    import xml.etree.ElementTree as ET

    try:
        url = GNEWS.format(requests.utils.quote(keyword))
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        items = ET.fromstring(r.content).findall(".//item")
    except Exception:
        return []

    base = (dt.datetime.strptime(asof, "%Y-%m-%d") if asof else dt.datetime.utcnow())
    out = []
    for it in items:
        src = (it.findtext("source") or "").strip()
        if allow and not any(a in src for a in allow):
            continue
        pub = it.findtext("pubDate")
        when = None
        if pub:
            try:
                when = eut.parsedate_to_datetime(pub).replace(tzinfo=None)
            except Exception:
                pass
        if when:
            age = (base - when).total_seconds() / 3600
            if age > hours or age < -24:
                continue
        title = (it.findtext("title") or "").split(" - ")[0].strip()
        out.append({"見出し": title, "発信元": src,
                    "日時": when.strftime("%m-%d %H:%M") if when else "-"})
        if len(out) >= limit:
            break
    return out


def evaluate_item(item: dict, asof: str, news_conf: dict = None) -> dict:
    """1項目を採点する。tickerが無い項目は採点せず、ニュース見出しを添えて返す。"""
    tickers = item.get("ticker") or []
    out = {"名前": item["名前"], "自動": bool(tickers), "変化率": None,
           "点": 0, "急変": False, "基準日": None, "内訳": [], "見出し": []}
    if not tickers:
        kws = item.get("検索") or []
        if kws and news_conf:
            seen = set()
            for kw in kws:
                for a in fetch_news(kw, news_conf.get("許可ソース", []),
                                    news_conf.get("収集時間", 36),
                                    news_conf.get("最大件数", 5), asof):
                    if a["見出し"] not in seen:
                        seen.add(a["見出し"])
                        out["見出し"].append(a)
            out["見出し"] = out["見出し"][:news_conf.get("最大件数", 5)]
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

    news_conf = (conf.get("共通") or {}).get("ニュース") or {}
    asof = args.asof
    print("=" * 66)
    print(f"【前夜の海外市場シグナル】{'対象: ' + asof + ' の寄り付き向け' if asof else '直近の海外引け'}")
    print("=" * 66)

    results, alerts, hot = {}, [], []
    for gname, g in conf["グループ"].items():
        sc = g.get("朝スコア")
        if not sc:
            continue
        items = [evaluate_item(i, asof, news_conf) for i in sc["項目"]]
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
            elif i["見出し"]:
                print(f"      {i['名前']:<22}    ニュース{len(i['見出し'])}件 → 内容は要判断")
                for a in i["見出し"]:
                    print(f"         ・[{a['日時']}] {a['見出し'][:52]} ({a['発信元']})")
            else:
                print(f"      {i['名前']:<22}    該当ニュースなし / 自動取得不可")
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
