"""伸びた投稿と伸びなかった投稿を見比べて、改善点を出す。

**チェック項目は推測ではありません。** 公式エクスポート208本を月内相対値
（その月の中央値を1.0）で測り、実際に差が出た特徴だけを使っています。

    特徴          あり  中央値   なし  中央値    差
    年齢あり        25   2.70   183   0.83   3.3倍
    学歴あり        83   1.75   125   0.76   2.3倍
    箇条書き3つ以上   86   1.60   122   0.79   2.0倍
    他人が登場      110   1.29    98   0.68   1.9倍

差が出なかったもの（**チェックしません**）:

    「」セリフ 1.2倍 ／ マジで 0.9倍 ／ 『』決め台詞 0.9倍

**『』の決め台詞は、あってもインプレッションは変わりませんでした。**
formats.md では重視していますが、それは上位投稿だけを見た結論で、
208本で測ると差がありません。引用RTされる箇所ではあるので残していますが、
**「決め台詞が無いから伸びない」とは言えません。**

使い方:

    python x/x_diag.py                # 直近7日
    python x/x_diag.py --date 2026-08-16
    python x/x_diag.py --last 20
"""

import argparse
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from x_review import POSTS_CSV
from analytics import looks_quote

W = 26  # スマホで横スクロールしない幅

# (名前, 判定, 実測の倍率, 直し方)
CHECKS = [
    ("年齢", lambda t: bool(re.search(r"\d\d才", t)), 3.3,
     "「（28才）」のように年齢を入れる"),
    ("学歴", lambda t: bool(re.search(r"早稲田|慶應|青学|東大|明治|同志社|卒", t)), 2.3,
     "「青学卒の」のように学歴を入れる"),
    ("箇条書き3つ以上", lambda t: t.count("・") >= 3, 2.0,
     "具体的な作業を3つ並べる"),
    ("他人が登場", lambda t: bool(re.search(
        r"上司|後輩|新人|部長|候補者|同僚|営業|部下|インターン|先輩|人事", t)), 1.9,
     "上司・後輩・候補者など、誰かの行動を中心にする"),
]


def score(text):
    t = str(text)
    return {name: fn(t) for name, fn, _, _ in CHECKS}


def load(date=None, last=None, days=7):
    df = pd.read_csv(POSTS_CSV)
    df = df[df["text"].notna() & df["impressions"].notna()]
    # 引用RTは imp で測らない（狙いが拡散ではないため）。format が付く前に
    # 取得したぶんもあるので、本文末尾の t.co でも判定する
    df = df[(df["format"].astype(str) != "引用RT")
            & (~df["text"].map(lambda t: looks_quote(str(t))))]
    if date:
        df = df[df["date"].astype(str) == date]
    elif last:
        df = df.tail(last)
    else:
        cut = (pd.to_datetime(df["date"]).max() - pd.Timedelta(days=days))
        df = df[pd.to_datetime(df["date"]) >= cut]
    return df.sort_values("impressions", ascending=False)


def wrap(s, n=W):
    return [s[i:i + n] for i in range(0, len(s), n)] or [""]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--last", type=int)
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    df = load(args.date, args.last, args.days)
    if len(df) < 2:
        print("比べる投稿が足りません（2本以上必要）")
        return

    med = df["impressions"].median()
    hi = df[df["impressions"] >= med]
    lo = df[df["impressions"] < med]

    print("=" * W)
    print(f"対象 {len(df)}本  中央値 {med:,.0f}imp")
    print(f"伸びた {len(hi)}本 / 伸びない {len(lo)}本")
    print("=" * W)

    # 1本ずつの診断
    for _, r in df.iterrows():
        s = score(r["text"])
        miss = [(n, mult, fix) for (n, _, mult, fix) in CHECKS if not s[n]]
        mark = "◎" if r["impressions"] >= med else "△"
        print()
        print(f"{mark} {r['date']} {r.get('time','')}")
        print(f"  imp {int(r['impressions']):,}")
        for line in wrap(re.sub(r"\s+", " ", str(r["text"]))[:52], W - 2):
            print(f"  {line}")
        if miss:
            print("  欠けている要素:")
            for n, mult, fix in miss:
                print(f"   ・{n}（実測{mult}倍）")
                for line in wrap(fix, W - 5):
                    print(f"     → {line}")
        else:
            print("  4要素すべてあり")

    # 伸びた側と伸びない側の差
    print()
    print("=" * W)
    print("伸びた側との差")
    print("=" * W)
    found = False
    for name, fn, mult, fix in CHECKS:
        a = hi["text"].map(lambda t: fn(str(t))).mean()
        b = lo["text"].map(lambda t: fn(str(t))).mean()
        if a - b >= 0.34:  # 3本に1本以上の差があるものだけ
            found = True
            print()
            print(f"{name}")
            print(f"  伸びた {a*100:.0f}% / 伸びない {b*100:.0f}%")
            print(f"  実測 {mult}倍")
            for line in wrap(fix, W - 4):
                print(f"   → {line}")
    if not found:
        print()
        print("この期間では、4要素の有無で")
        print("差は出ていません。")
        print("差は型や要素ではなく、")
        print("テーマや時期の側にあります。")

    print()
    print("=" * W)
    print("注意")
    print("=" * W)
    print("この4項目は208本で差が出た")
    print("ものだけです。ただし型による")
    print("差は2倍程度で、月による差は")
    print("300倍でした。要素を揃えても")
    print("跳ねるかは別の話です。")
    print("本数を出すことが最優先。")


if __name__ == "__main__":
    main()
