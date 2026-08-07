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

ニュースの扱い
--------------
同じ利上げ報道でも、メガバンクには利ざや改善で追い風、自動車には円高要因で
逆風になる。そこで「事象がどちらに動いたか」と「それが各グループに有利か」を
分けて持つ。

    項目スコア = 事象の方向(+1/0/-1) × グループの感応度(+1/-1)

方向は共通のトピック定義（強まる語/弱まる語）で1回だけ判定し、感応度は
グループ側に持たせる。見出しの単語数え上げなので精度には限界があり、
根拠にした見出しは必ず表示して朝の確認で上書きできるようにしてある。

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


INTRADAY = ("https://query1.finance.yahoo.com/v8/finance/chart/{}"
            "?range=5d&interval=30m&includePrePost=true")


def fetch_afterhours(ticker: str):
    """米国の通常取引の終値と、その後の時間外の最終値を比べる。

    米国企業の決算は引け後(16:00 ET)に出るため、通常取引の終値には反応が載らない。
    日本の寄り付き9:00 JSTは20:00 ETで、米国の時間外取引が終わる時刻にあたる。
    つまり引け後の決算反応は、日本市場が開く前にすべて時間外の値動きとして見える。

    Yahooの分足は直近5営業日しか遡れないため、過去日の再現には使えない。
    """
    import zoneinfo
    try:
        et = zoneinfo.ZoneInfo("America/New_York")
        url = INTRADAY.format(requests.utils.quote(ticker, safe=""))
        j = requests.get(url, headers=UA, timeout=30).json()["chart"]["result"][0]
        bars = [(dt.datetime.fromtimestamp(t, et), c)
                for t, c in zip(j["timestamp"], j["indicators"]["quote"][0]["close"])
                if c is not None]
        if not bars:
            return None
        day = bars[-1][0].date()
        today = [b for b in bars if b[0].date() == day]
        regular = [v for d, v in today if (d.hour, d.minute) <= (16, 0) and d.hour >= 9]
        post = [v for d, v in today if d.hour >= 16]
        if not regular or not post:
            return None
        return {"終値": regular[-1], "時間外": post[-1], "日付": str(day),
                "変化率": round((post[-1] / regular[-1] - 1) * 100, 2)}
    except Exception:
        return None


GNEWS = "https://news.google.com/rss/search?q={}&hl=ja&gl=JP&ceid=JP:ja"


def looks_like_other_company(title: str, name: str) -> bool:
    """社名の部分一致で別会社を拾っていないかを見る。

    「日本電気」で検索すると「日本電気硝子」（5214、別会社）が引っかかる。
    社名の直後が漢字・カタカナなら、より長い社名の一部とみなして落とす。
    「トヨタ自動車が」のように助詞が続く場合は残る。

    完全ではない。子会社（トヨタ自動車東日本など）も落ちるが、
    親会社の株価材料としては別物なので、この用途では落として構わない。
    """
    import re
    for m in re.finditer(re.escape(name), title):
        nxt = title[m.end():m.end() + 1]
        if nxt and re.match(r"[一-龥ァ-ヴー]", nxt) and nxt != "株":
            continue          # 別会社の可能性 → この出現は数えない
        return False          # 社名そのものとして出ている
    return True               # すべての出現が別会社らしい


def fetch_news(keyword: str, allow: list, hours: int, limit: int,
               asof: str = None, company: str = None) -> list:
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
        if company and looks_like_other_company(title, company):
            continue
        out.append({"見出し": title, "発信元": src,
                    "日時": when.strftime("%m-%d %H:%M") if when else "-"})
        if len(out) >= limit:
            break
    return out


def evaluate_topic(name: str, topic: dict, news_conf: dict, asof: str) -> dict:
    """トピックの「事象がどちらに動いたか」だけを判定する。

    見出しに含まれる 強まる語 / 弱まる語 を数え、多い方をそのトピックの方向とする。
    それが各グループにプラスかマイナスかは判定しない。
    グループ側の 感応度 を掛けて初めて点数になる。

    見出しだけの単語数え上げなので精度には限界がある。
    「据え置き」と「利上げ」が同居する見出しは相殺されて中立になる。
    根拠にした見出しは全部表示するので、違うと思ったら朝の確認で上書きしてほしい。
    """
    arts, up, down, hits = [], 0, 0, []
    seen = set()
    for kw in topic.get("検索", []):
        for a in fetch_news(kw, news_conf.get("許可ソース", []),
                            news_conf.get("収集時間", 36),
                            news_conf.get("最大件数", 5), asof):
            if a["見出し"] in seen:
                continue
            seen.add(a["見出し"])
            arts.append(a)
    for a in arts:
        u = [w for w in topic.get("強まる語", []) if w in a["見出し"]]
        d = [w for w in topic.get("弱まる語", []) if w in a["見出し"]]
        up += len(u)
        down += len(d)
        if u or d:
            hits.append((a, u, d))
    direction = 1 if up > down else (-1 if down > up else 0)
    return {"名前": name, "方向": direction, "強": up, "弱": down,
            "見出し": arts[:news_conf.get("最大件数", 5)], "根拠": hits}


def evaluate_item(item: dict, asof: str, news_conf: dict = None, topics: dict = None) -> dict:
    """1項目を採点する。tickerが無い項目は採点せず、ニュース見出しを添えて返す。"""
    tickers = item.get("ticker") or []
    out = {"名前": item["名前"], "自動": bool(tickers), "変化率": None,
           "点": 0, "急変": False, "基準日": None, "内訳": [], "見出し": [],
           "方向": None, "根拠": [], "語数": None, "時間外": []}
    if not tickers:
        # トピック参照つきの項目は、事象の方向 × このグループの感応度 で採点する
        tname = item.get("トピック")
        sens = item.get("感応度")
        out["トピック"], out["感応度"] = tname, sens
        if tname and topics and tname in topics:
            t = topics[tname]
            out["自動"] = True
            out["方向"] = t["方向"]
            out["点"] = t["方向"] * (sens if sens is not None else 1)
            out["採点対象"] = item.get("採点", True)
            if not out["採点対象"]:
                out["点"] = 0
            out["見出し"] = t["見出し"]
            out["根拠"] = t["根拠"]
            out["語数"] = (t["強"], t["弱"])
            return out
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
    # 感応度 -1 の項目は、上昇がそのグループにとって逆風であることを表す
    # (自動車にとっての米10年債利回りなど)。既定は +1。
    sens = item.get("感応度", 1)
    out["感応度"] = sens
    out["点"] = (1 if avg > 0 else (-1 if avg < 0 else 0)) * sens
    # 採点: false の項目は、変化率は表示するが合計には入れない。
    # 検証で効果が確認できていない指標を、消さずに材料として残すために使う。
    out["採点対象"] = item.get("採点", True)
    if not out["採点対象"]:
        out["点"] = 0
    th = item.get("急変閾値")
    if th:
        # 平均でも個別でも、どちらかが閾値を超えたら急変とみなす
        out["急変"] = abs(avg) >= th or any(abs(c) >= th for _, c in chgs)

    # 引け後に決算が出ると通常取引の終値には載らないため、時間外も見る
    if item.get("時間外監視") and not asof:
        ah = []
        for t in tickers:
            r = fetch_afterhours(t)
            if r and abs(r["変化率"]) >= 1.0:
                ah.append((t, r))
        out["時間外"] = ah
        if th and any(abs(r["変化率"]) >= th for _, r in ah):
            out["急変"] = True
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
    # トピックは1回だけ取得して全グループで使い回す
    topics = {n: evaluate_topic(n, t, news_conf, asof)
              for n, t in ((conf.get("共通") or {}).get("ニューストピック") or {}).items()}
    print("=" * 66)
    print(f"【前夜の海外市場シグナル】{'対象: ' + asof + ' の寄り付き向け' if asof else '直近の海外引け'}")
    print("=" * 66)

    results, alerts, hot = {}, [], []
    for gname, g in conf["グループ"].items():
        sc = g.get("朝スコア")
        if not sc:
            continue
        items = [evaluate_item(i, asof, news_conf, topics) for i in sc["項目"]]
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
            # そのグループにとって追い風かどうかは点で判断する（感応度を反映）
            arrow = ("追い風" if i["点"] > 0 else
                     "逆風" if i["点"] < 0 else "採点対象外")
            print(f"  [{gname}] {i['名前']} 平均 {i['変化率']:+.2f}%  ← {arrow}")
            print(f"      {detail}")
    else:
        print("\n■ 急変検知: なし")

    print("\n■ グループ別 朝スコア")
    for gname, (items, total, th) in results.items():
        verdict = "→ 場中に条件を探す" if total >= th else "→ 何もしない"
        print(f"  {gname}  {total:+d} / 閾値 +{th}  {verdict}")
        for i in items:
            if i["自動"] and i.get("方向") is not None:
                arrow = {1: "強まる", -1: "弱まる", 0: "中立"}[i["方向"]]
                sens = i.get("感応度")
                sl = "追い風" if sens and sens > 0 else "逆風"
                u, d = i.get("語数") or (0, 0)
                tail = "  ※採点対象外" if not i.get("採点対象", True) else ""
                print(f"      {i['名前']:<22} {arrow}({u}語/{d}語) × {sl}  ({i['点']:+d}){tail}")
                for a, uw, dw in (i.get("根拠") or [])[:3]:
                    tag = "＋" + "".join(uw) if uw else ""
                    tag += ("／−" + "".join(dw)) if dw else ""
                    print(f"         ・{a['見出し'][:46]} [{tag}] ({a['発信元']})")
            elif i["自動"]:
                mark = " ★急変" if i["急変"] else ""
                if not i.get("採点対象", True):
                    mark += "  ※採点対象外（材料として表示のみ）"
                elif (i.get("感応度") or 1) < 0:
                    mark += "  ※上昇が逆風"
                print(f"      {i['名前']:<22} {i['変化率']:+7.2f}%  ({i['点']:+d}){mark}")
                for t, r in (i.get("時間外") or []):
                    print(f"         ・{t} 時間外 {r['変化率']:+.2f}% "
                          f"({r['終値']:.2f} → {r['時間外']:.2f}) ← 引け後の決算反応の可能性")
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
          "エントリーは場中の順張り必須2条件（ORB上抜け・日足MA25の上）で別途判断してください。")


if __name__ == "__main__":
    main()
