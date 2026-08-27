"""投稿された本文と、在庫（stock.md）の下書きを突き合わせる。

Hideさんは**下書きをそのまま使わず、手を入れてから投稿します。** 実際、
これまでの2本はどちらも書き換えられていました（14行→10行、締めの全面差し替え）。
なので文字列が完全一致することは、まず期待できません。

そこで**類似度で「これは在庫の◯◯を直したものだ」と判定**し、
差分を出します。差分の理由を言語化して `voice.md` に貯めることが本題で、
「使用済み」への移動はそのついでです。

    python x/x_match.py              # 未処理の投稿を在庫と突き合わせる
    python x/x_match.py --date 2026-08-13
    python x/x_match.py --all        # 使用済み判定が付いたものも含めて全部見る

判定は**提案まで**です。`stock.md` の書き換えは Claude が手順を踏んで行います
（近いだけで別物、ということがあるため）。
"""

import argparse
import difflib
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from x_review import POSTS_CSV

HERE = os.path.dirname(os.path.abspath(__file__))
STOCK = os.path.join(HERE, "stock.md")

# これ以上似ていれば「同じ下書きを直したもの」とみなす。
# 0.55 は、これまでの2本（14行→10行の削り、締めの全面差し替え）が
# どちらも 0.6 台だったことから決めた。上げると取りこぼす。
THRESHOLD = 0.55
NEARLY_SAME = 0.92


def norm(s):
    """比較用に正規化する。改行と空白の違いは差分として見たくない。"""
    s = re.sub(r"[　\s]+", "", str(s))
    return s.replace("＂", '"').replace("“", '"').replace("”", '"')


def load_stock():
    """stock.md の見出しとコードブロックを拾う。

    見出し（### で始まる行）ごとに、直後のコードブロックを本文とみなす。
    どのセクション（承認済み/予約済み/使用済み）にあるかも持つ。
    """
    if not os.path.exists(STOCK):
        return []
    section, title, out = "", "", []
    lines = open(STOCK).read().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            section = line[3:].split("（")[0].strip()
        elif line.startswith("### "):
            title = line[4:].strip()
        elif line.strip() == "```" and title:
            body, i = [], i + 1
            while i < len(lines) and lines[i].strip() != "```":
                body.append(lines[i])
                i += 1
            text = "\n".join(body).strip()
            if text and len(text) > 30:  # 1stリプの短い引用は拾わない
                out.append({"section": section, "title": title, "text": text})
            title = ""  # 1見出しにつき最初のブロックだけ
        i += 1
    return out


def load_posts(date=None, include_done=False):
    if not os.path.exists(POSTS_CSV):
        return pd.DataFrame()
    df = pd.read_csv(POSTS_CSV)
    if "text" not in df:
        return pd.DataFrame()
    df = df[df["text"].notna() & (df["text"].astype(str).str.len() > 30)]
    if date:
        df = df[df["date"].astype(str) == date]
    if not include_done:
        df = df[~df["note"].astype(str).str.contains("照合済み", na=False)]
    return df


def diff_lines(draft, posted):
    """行単位の差分。Claude が理由を言語化するための材料。"""
    return list(difflib.unified_diff(
        draft.split("\n"), posted.split("\n"),
        fromfile="下書き", tofile="投稿", lineterm="", n=1,
    ))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD で絞る")
    ap.add_argument("--all", action="store_true", help="照合済みも含める")
    args = ap.parse_args()

    stock, posts = load_stock(), load_posts(args.date, args.all)
    if posts.empty:
        print("突き合わせる投稿がありません（本文が入った未照合の行が無い）")
        return
    if not stock:
        print("stock.md に下書きが見つかりません")
        return

    print(f"投稿 {len(posts)}件 × 在庫 {len(stock)}件 を突き合わせます\n")

    for _, p in posts.iterrows():
        scored = sorted(
            ((difflib.SequenceMatcher(None, norm(d["text"]), norm(p["text"])).ratio(), d)
             for d in stock),
            key=lambda x: -x[0],
        )
        ratio, best = scored[0]
        head = re.sub(r"\s+", " ", str(p["text"]))[:46]

        print("=" * 74)
        print(f"{p['date']} {p.get('time', '')}  imp{int(p['impressions'] or 0):,}  {head}")

        if ratio < THRESHOLD:
            print(f"→ **在庫に該当なし**（最も近くて {ratio:.0%}: {best['title']}）")
            print("   Hideさんが自分で思いついて投稿したものと思われます。")
            print("   在庫は触らず、内容が新しいテーマなら content.md に追記してください。\n")
            continue

        print(f"→ **{best['title']}**（{best['section']}）と {ratio:.0%} 一致")
        if ratio >= NEARLY_SAME:
            print("   ほぼそのまま投稿されています。差分の学習は不要。")
        else:
            print("   **手を入れて投稿されています。以下の差分の理由を言語化してください。**")
            print()
            for line in diff_lines(best["text"], p["text"]):
                if line.startswith(("---", "+++")):
                    continue
                print(f"   {line}")
        print()

    print("=" * 74)
    print("""
次にやること:

1. 一致した下書きを `stock.md` の「使用済み」へ移す
   （日付・imp・「Hideさんが◯箇所修正」を添える）
2. **差分の理由を `voice.md` の「削り方」に追記する。** ここが本題です
   - 既に書いてある項目に当てはまるなら、実例を1つ足すだけでよい
   - 新しい傾向なら番号を振って追加する
   - **1回の差分で一般化しないこと。** 2回同じ直され方をしてから書く
3. 該当なしだった投稿にテーマの新規性があれば `content.md` へ
""")


if __name__ == "__main__":
    main()
