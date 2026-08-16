"""伸びた投稿と伸びなかった投稿を見比べて、改善点を出す。

**型ごとに別の物差しを当てます。** 全投稿に同じチェックをかけると、
型4や型8のように年齢も箇条書きも使わない型が、毎回「要素が欠けている」と
言われ続けます。それはバリエーションを潰す方向に働くので、やりません
（2026-08-16にHideさんの指摘で作り直し）。

チェック項目は推測ではなく、エクスポート208本を**型ごとに・月内相対値で**
測って差が出たものだけです。型が違えば効く要素も違いました。

    型3/型5 観察（58本）
      年齢あり        5.7倍
      学歴あり        4.1倍
      他人が登場       2.2倍
      箇条書き4つ以上   0.3倍  ← **多いほど悪い**

    断言・その他（62本）
      他人が登場       3.8倍
      年齢あり        3.5倍
      学歴あり        3.2倍
      セリフ「」       1.9倍
      『』の決め台詞    0.5倍  ← **入れると下がる**

    型1 上司の一言（57本）
      決定的な特徴なし（学歴1.6倍が最大）

**「他人が登場」すら普遍ではありません。** 型4では0.1倍で、
入れるとむしろ下がりました。全型共通のルールは作れません。

本数が30本に満たない型（型2・型4・型7・型8）は**診断しません**。
同じ型の中での順位だけ出します。

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

AGE = lambda t: bool(re.search(r"\d\d才", t))
EDU = lambda t: bool(re.search(r"早稲田|慶應|青学|東大|明治|同志社|卒", t))
PERSON = lambda t: bool(re.search(
    r"上司|後輩|新人|部長|候補者|同僚|営業|部下|インターン|先輩|人事", t))

# 型ごとのチェック。(名前, 判定, 望ましいか, 倍率, 直し方)
# 望ましいか=False は「あると下がる」項目
BY_FORMAT = {
    "型3/型5 観察": [
        ("年齢", AGE, True, 5.7, "「（28才）」を入れる"),
        ("学歴", EDU, True, 4.1, "「青学卒の」を入れる"),
        ("他人が登場", PERSON, True, 2.2, "誰かの行動を中心にする"),
        ("箇条書きが4つ以上", lambda t: t.count("・") >= 4, False, 0.3,
         "3つに減らす"),
    ],
    "断言・その他": [
        ("他人が登場", PERSON, True, 3.8, "誰かの行動を中心にする"),
        ("年齢", AGE, True, 3.5, "「（28才）」を入れる"),
        ("学歴", EDU, True, 3.2, "「青学卒の」を入れる"),
        ("セリフ「」", lambda t: "「" in t, True, 1.9, "実際の発言を引く"),
        ("『』の決め台詞", lambda t: "『" in t, False, 0.5,
         "断言型では外す"),
    ],
}

# 本数が足りず診断できない型（30本未満）
TOO_FEW = {"型2 面接官": 15, "型4 対比リスト": 12, "型7 会話だけ": 4, "引用RT": 0}


def wrap(s, n=W):
    return [s[i:i + n] for i in range(0, len(s), n)] or [""]


def load(date=None, last=None, days=7):
    df = pd.read_csv(POSTS_CSV)
    df = df[df["text"].notna() & df["impressions"].notna()]
    # 引用RTは imp で測らない（狙いが拡散ではないため）
    df = df[(df["format"].astype(str) != "引用RT")
            & (~df["text"].map(lambda t: looks_quote(str(t))))]
    if date:
        df = df[df["date"].astype(str) == date]
    elif last:
        df = df.tail(last)
    else:
        cut = pd.to_datetime(df["date"]).max() - pd.Timedelta(days=days)
        df = df[pd.to_datetime(df["date"]) >= cut]
    return df.sort_values("impressions", ascending=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--last", type=int)
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    df = load(args.date, args.last, args.days)
    if df.empty:
        print("対象の投稿がありません")
        return

    med = df["impressions"].median()
    print("=" * W)
    print(f"対象 {len(df)}本")
    print(f"この期間の中央値 {med:,.0f}imp")
    print("=" * W)

    for _, r in df.iterrows():
        fmt = str(r["format"])
        t = str(r["text"])
        mark = "◎" if r["impressions"] >= med else "△"
        print()
        print(f"{mark} {r['date']} {r.get('time', '')}")
        print(f"  imp {int(r['impressions']):,}  {fmt}")
        for line in wrap(re.sub(r"\s+", " ", t)[:52], W - 2):
            print(f"  {line}")

        checks = BY_FORMAT.get(fmt)
        if checks is None:
            n = TOO_FEW.get(fmt)
            print(f"  この型は実測{n}本しかなく、")
            print("  診断できません。数字だけ")
            print("  記録して様子を見ます。")
            continue

        issues = []
        for name, fn, want, mult, fix in checks:
            has = fn(t)
            if want and not has:
                issues.append((f"{name}が無い", mult, fix, "倍損"))
            elif not want and has:
                issues.append((name, mult, fix, "倍に下がる"))
        if not issues:
            print("  この型で効く要素は揃って")
            print("  います。")
        else:
            print("  この型で直せる点:")
            for name, mult, fix, unit in issues:
                print(f"   ・{name}")
                print(f"     実測 {mult}{unit}")
                for line in wrap(fix, W - 7):
                    print(f"     → {line}")

    print()
    print("=" * W)
    print("読み方")
    print("=" * W)
    print("チェックは型ごとに違います。")
    print("型4や型8で年齢や箇条書きが")
    print("無いのは欠陥ではありません。")
    print("その型ではそもそも使わない")
    print("要素なので、診断もしません。")
    print()
    print("型による差は2倍程度で、")
    print("月による差は300倍でした。")
    print("要素は下限を上げるだけです。")


if __name__ == "__main__":
    main()
