"""X公式アナリティクスのエクスポートCSVを読んで、ベースラインを出す。

`x/posts.csv`（手動記録の台帳）とは別物。あちらは「レビューを通した投稿」だけで、
ハイライトから拾った過去11本は**アカウントの最高記録**しか入っていない。
こちらは**全投稿**が入るので、平常値が分かる。型の良し悪しは平常値と比べないと
判定できない（最高記録どうしを比べても、運の大きさを比べているだけになる）。

使い方:
    x/data/raw/ に X からエクスポートしたCSVを置いて、
    python x/analytics.py
    python x/analytics.py --month 2025-12
    python x/analytics.py --formats      # 型の判定ルールと分類結果
    python x/analytics.py --looks        # 見た目クラス別の平常値と、連投したときの落ち方
"""

import argparse
import glob
import os
import re

import pandas as pd

RAW_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")

COLMAP = {
    "ツイートID": "id",
    "ツイートの固定リンク": "url_permalink",
    "ツイート本文": "text",
    "時間": "t",
    "インプレッション": "imp",
    "エンゲージメント": "eng",
    "エンゲージメント率": "er",
    "リツイート": "rt",
    "返信": "rep",
    "いいね": "fav",
    "ユーザープロフィールクリック": "prof",
    "URLクリック数": "url",
    "詳細クリック": "det",
    "メディアのエンゲージメント数": "media",
}


def load():
    """x/data/raw/*.csv を全部読んで1本にまとめる。空ファイルは飛ばす。"""
    frames, empty = [], []
    for path in sorted(glob.glob(os.path.join(RAW_DIR, "*.csv"))):
        df = pd.read_csv(path)
        if df.empty:
            empty.append(os.path.basename(path))
            continue
        df = df.rename(columns={k: v for k, v in COLMAP.items() if k in df.columns})
        df["source"] = os.path.basename(path)
        frames.append(df)

    if not frames:
        raise SystemExit(f"{RAW_DIR} に中身のあるCSVがありません")

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset="id")

    # エクスポートの時刻は UTC。この人は朝6時台に投稿するので、
    # JST に直さないと「前日の21時」に見えて時間帯の分析が崩れる。
    df["jst"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)
    df["month"] = df["jst"].dt.strftime("%Y-%m")

    for c in ("imp", "eng", "er", "rt", "rep", "fav", "prof", "url", "det", "media"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # 「@」で始まるものは他人への返信。自分のタイムラインに出る投稿とは
    # 配信のされ方がまったく違うので、混ぜて平均を取ると平常値が壊れる。
    df["is_reply"] = df["text"].astype(str).str.startswith("@")

    # 導線ポストの判定は**本文にURLがあるか**で行う。URLクリック数で判定すると、
    # 長文が省略されたときに付く t.co を踏んだぶんが数クリック入るせいで、
    # リンクを貼っていない本編（1272万impの型4など）まで導線扱いになる。
    df["has_link"] = df["text"].astype(str).str.contains("http", na=False)

    # 引用リポストも本文末尾に t.co が付くので、そのままだと導線ポストと
    # 区別できない。導線ポストは「pr」表記か「↓▼」の誘導記号を持つので、
    # **末尾にt.coが1つだけで、誘導記号もpr表記も無いもの**を引用RTとみなす。
    df["is_quote"] = df["has_link"] & df["text"].map(looks_quote)
    return df.sort_values("jst"), empty


def looks_quote(t):
    """引用リポストらしいか。エクスポートCSVには引用元の情報が無いので推定。

    日次の `x_metrics.py` 側は referenced_tweets を見られるので正確に判定できる。
    こちらは過去ぶんを分類するための近似。
    """
    t = str(t)
    if len(re.findall(r"https://t\.co/", t)) != 1:
        return False
    if "↓" in t or "▼" in t or re.search(r"\bpr\b", t.lower()):
        return False
    return bool(re.search(r"https://t\.co/\S+\s*$", t))


# 型の判定。本文の形から機械的に付ける。あくまで一次分類で、
# 微妙なものは `formats.md` を見て手で直す前提。
FORMAT_RULES = [
    # 助詞切り（型9）は本文が助詞のまま終わる。他のどの型よりも先に判定する
    ("型9 助詞切り", lambda t: bool(re.search(r"(は|とは|が|には|のは)、\s*$", str(t).rstrip()))),
    ("型4 対比リスト", lambda t: "←" in t or "→" in t),
    ("型7 会話だけ", lambda t: len(re.findall(r"^.{0,8}「", t, re.M)) >= 3),
    ("型1 上司の一言", lambda t: bool(re.search(r"上司|部長|先輩", t)) and bool(re.search(r"[「『]", t))),
    ("型2 面接官", lambda t: "面接官" in t or "面接して" in t),
    # 呼びかけ版（型5の派生）は宛先を1行目に置く。観察版と数字を分けて見る
    ("型5-呼びかけ", lambda t: t.count("・") >= 3 and bool(re.match(r"^\s*\S{1,6}へ\s*$", str(t).split("\n")[0]))),
    ("型3/型5 観察", lambda t: t.count("・") >= 3),
]


def classify(text):
    text = str(text)
    for name, rule in FORMAT_RULES:
        if rule(text):
            return name
    return "断言・その他"


def head(text, n=44):
    return re.sub(r"\s+", " ", str(text))[:n]


def report(df, empty):
    own = df[~df["is_reply"]]
    organic = own[~own["has_link"]]
    quotes = own[own["is_quote"]]

    print("=" * 74)
    print(f"読み込み {len(df):,}件 / 期間 {df['jst'].min():%Y-%m-%d} 〜 {df['jst'].max():%Y-%m-%d}")
    if empty:
        print(f"投稿ゼロの月: {len(empty)}ファイル（{', '.join(empty)}）")
    print("=" * 74)

    print("\n## 月次")
    print(f"{'月':<9}{'自発':>5}{'リプ':>5}{'中央値':>9}{'最大':>10}{'ER中央':>8}")
    for m, g in df.groupby("month"):
        o = g[(~g["is_reply"]) & (~g["has_link"])]
        if o.empty:
            continue
        print(f"{m:<9}{len(g[~g['is_reply']]):>5}{len(g[g['is_reply']]):>5}"
              f"{o['imp'].median():>9,.0f}{o['imp'].max():>10,.0f}{o['er'].median()*100:>7.2f}%")

    print(f"\n## ベースライン（リンク無しの自発ポスト {len(organic)}本）")
    print("**型の良し悪しはこの中央値と比べる。** 最高記録どうしの比較は運の比較にしかならない。")
    q = organic["imp"].quantile([0.25, 0.5, 0.75, 0.9])
    print(f"  中央値 {organic['imp'].median():,.0f}  /  平均 {organic['imp'].mean():,.0f}"
          f"  /  最小 {organic['imp'].min():,.0f}  /  最大 {organic['imp'].max():,.0f}")
    print(f"  25% {q[0.25]:,.0f}  50% {q[0.5]:,.0f}  75% {q[0.75]:,.0f}  90% {q[0.9]:,.0f}")
    print(f"  エンゲージ率 中央値 {organic['er'].median()*100:.2f}%")

    print("\n## 型ごと（自動分類。件数が少ないうちは参考値）")
    organic = organic.assign(fmt=organic["text"].map(classify))
    g = organic.groupby("fmt").agg(本数=("imp", "size"), 中央値=("imp", "median"),
                                   最大=("imp", "max"), ER=("er", "median"),
                                   返信=("rep", "median")).sort_values("中央値", ascending=False)
    g["ER"] = (g["ER"] * 100).round(2)
    g["中央値"] = g["中央値"].round(0).astype(int)
    g["最大"] = g["最大"].round(0).astype(int)
    print(g.to_string())

    print("\n## 上位10本")
    for _, x in organic.nlargest(10, "imp").iterrows():
        print(f"{x['jst']:%m/%d %H:%M} {x['imp']:>7,.0f} ER{x['er']*100:5.2f}% "
              f"返{x['rep']:>2.0f} ♥{x['fav']:>3.0f} プ{x['prof']:>3.0f} "
              f"[{classify(x['text'])}] {head(x['text'])}")

    print("\n## 下位5本")
    for _, x in organic.nsmallest(5, "imp").iterrows():
        print(f"{x['jst']:%m/%d %H:%M} {x['imp']:>7,.0f} ER{x['er']*100:5.2f}% "
              f"返{x['rep']:>2.0f} ♥{x['fav']:>3.0f} プ{x['prof']:>3.0f} "
              f"[{classify(x['text'])}] {head(x['text'])}")

    links = own[own["has_link"]]
    if not links.empty:
        print(f"\n## 導線（リンク付き {len(links)}本）—— **ここが本命の指標**")
        print(f"{'日時':<12}{'imp':>9}{'URLクリック':>11}{'CTR':>9}{'プロフ':>7}  本文")
        for _, x in links.sort_values("jst").iterrows():
            print(f"{x['jst']:%m/%d %H:%M}{x['imp']:>9,.0f}{x['url']:>11,.0f}"
                  f"{x['url']/x['imp']*100:>8.3f}%{x['prof']:>7,.0f}  {head(x['text'], 30)}")

    print("\n## 投稿時刻（JST・自発ポスト）")
    vc = own["jst"].dt.hour.value_counts().sort_index()
    for h, n in vc.items():
        print(f"  {h:>2}時 {'█' * n} {n}")

    if not quotes.empty:
        print(f"\n## 引用リポスト {len(quotes)}本（オーガニックの集計からは外してあります）")
        med = organic["imp"].median()
        print(f"  中央値 {quotes['imp'].median():,.0f}  最大 {quotes['imp'].max():,.0f}"
              f"  →  オーガニック中央値({med:,.0f})の {quotes['imp'].median()/med:.2f}倍")
        print("  **平均より下です。** 引用RTは他人の投稿に乗るぶんネタ切れしませんが、")
        print("  伸びやすい形ではありません")

    replies = df[df["is_reply"]]
    if not replies.empty:
        print(f"\n## 他者へのリプ {len(replies)}件")
        print(f"  中央値 {replies['imp'].median():,.0f} imp  /  合計 {replies['imp'].sum():,.0f} imp")
        print("  **リプ単体の配信量はほぼゼロ。** 交流の価値は配信量では測れない")


def look_class(text):
    """型番ではなく「読者から見た見た目」で分ける。

    型2・型3・型5 は型としては別でも、画面上は同じ形に見える。飽きを測るには
    型ではなくこちらで数える必要がある（2026-08-21）。
    """
    t = str(text)
    if "←" in t or "→" in t:
        return "対比リスト"
    bullets = len(re.findall(r"^[・･]", t, re.M))
    if bullets == 0:
        return "物語・散文"
    # 箇条書きを受けて1つに束ねる接続。言い回しは毎回変わるので広めに取る
    # （「など気をつけるべきで、総じていうと、」で取りこぼしていた・2026-08-21）
    if re.search(r"(などあるが|など基本だが|これだけでも|など[^。\n]{0,12}(総じて|とくに|特に|実は))", t):
        return "箇条書き＋総括"
    return "箇条書きのみ"


def looks(df):
    """見た目クラス別の平常値と、同じ見た目を連投したときの落ち方。"""
    own = df[(~df["is_reply"]) & (~df["has_link"]) & (~df["is_quote"])].copy()
    own = own.sort_values("jst")
    own["rel"] = own["imp"] / own.groupby("month")["imp"].transform("median")
    own["look"] = own["text"].map(look_class)

    print(f"\n## 見た目クラス（オーガニック {len(own)}本）")
    print(f"{'見た目':<12}{'本数':>6}{'相対中央値':>11}{'最大':>13}")
    for k, g in own.groupby("look"):
        print(f"{k:<12}{len(g):>6}{g['rel'].median():>11.2f}{g['imp'].max():>13,.0f}")

    print("\n## 連投したときに落ちるか（72時間以内に前の投稿があるものだけ）")
    own["prev"] = own["look"].shift(1)
    own["gap_h"] = own["jst"].diff().dt.total_seconds() / 3600
    sub = own[(own["gap_h"] <= 72) & own["prev"].notna()]
    same = sub[sub["look"] == sub["prev"]]
    diff = sub[sub["look"] != sub["prev"]]
    print(f"  同じ見た目が続いた  n={len(same):>3}  相対中央値 {same['rel'].median():.2f}")
    print(f"  見た目が変わった    n={len(diff):>3}  相対中央値 {diff['rel'].median():.2f}")
    own["p2"] = own["look"].shift(2)
    run3 = own[(own["look"] == own["prev"]) & (own["look"] == own["p2"])]
    if len(run3):
        print(f"  3本連続で同じ      n={len(run3):>3}  相対中央値 {run3['rel'].median():.2f}")
    print("  **2026-08-21 時点では差がありません。** ただしこのデータは月26本ペース。")
    print("  1日2本の水準で飽きが出るかは、これから貯まる数字で見ること。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="YYYY-MM で絞る")
    ap.add_argument("--formats", action="store_true", help="型の自動分類の結果を全件出す")
    ap.add_argument("--looks", action="store_true", help="見た目クラス別の平常値と連投の影響")
    args = ap.parse_args()

    df, empty = load()
    if args.month:
        df = df[df["month"] == args.month]
        if df.empty:
            raise SystemExit(f"{args.month} のデータがありません")

    if args.looks:
        looks(df)
        return

    if args.formats:
        own = df[(~df["is_reply"]) & (~df["has_link"])]
        for _, x in own.sort_values("jst").iterrows():
            print(f"{x['jst']:%Y-%m-%d %H:%M} {x['imp']:>8,.0f} "
                  f"[{classify(x['text']):<12}] {head(x['text'], 50)}")
        return

    report(df, empty)


if __name__ == "__main__":
    main()
