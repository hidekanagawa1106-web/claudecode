"""在庫（stock.md）を「見た目クラス」で数える。

型番で数えると、型2・型3・型5 が別物に見えてしまう。**読者から見た形は同じ**なので、
飽きを避けたいときに数えるべきはこちら（`formats.md` の見た目クラス）。

    python x/stock_looks.py            # 承認済みだけ
    python x/stock_looks.py --all      # 全セクション

判定は `analytics.py` の look_class と同じものを使っています。
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from analytics import look_class  # noqa: E402
from x_match import load_stock  # noqa: E402

# 「箇条書き＋総括」がこの割合を超えたら、次の補充は別の見た目から。
# 実測ではなく方針です（2026-08-21・Hideさんの判断）。飽きは証明できないので
# 前提として扱い、最強の形が増えすぎないように上限だけ置いています。
CAP = 1 / 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="承認済み以外のセクションも数える")
    args = ap.parse_args()

    rows = load_stock()
    if not args.all:
        rows = [r for r in rows if r["section"] == "承認済み"]
    if not rows:
        raise SystemExit("stock.md から下書きを読めませんでした")

    counts = {}
    for r in rows:
        counts.setdefault(look_class(r["text"]), []).append(r["title"])

    total = len(rows)
    print(f"在庫 {total}本（{'全セクション' if args.all else '承認済みのみ'}）\n")
    for look, titles in sorted(counts.items(), key=lambda kv: -len(kv[1])):
        share = len(titles) / total
        mark = "  ← 上限超え" if look == "箇条書き＋総括" and share > CAP else ""
        print(f"{look:<12}{len(titles):>3}本  {share*100:>4.0f}%{mark}")
        for t in titles:
            print(f"    - {t}")
        print()

    n = len(counts.get("箇条書き＋総括", []))
    if n / total > CAP:
        print(f"**次の補充は「箇条書き＋総括」以外から。**（{n}/{total} で上限 {CAP*100:.0f}% を超えています）")
    else:
        room = int(total * CAP) - n
        print(f"箇条書き＋総括はあと{max(room, 0)}本まで（上限 {CAP*100:.0f}%）。")


if __name__ == "__main__":
    main()
