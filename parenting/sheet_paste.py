"""products_resolved.csv から、スプレッドシートに貼る2列（価格・料率）を出す。

Googleスプレッドシートへの書き込み権限は持っていないので、
貼り付けられる形のTSVを出すところまでをやる。

products.csv の id をシートの行番号に合わせてある（r02 = 2行目）。
このスクリプトは id の数字順に、欠けている行も空行として詰めずに出すので、
出力をそのまま D2 に貼れば全行そろいます。

リンクの無い行（UNIQLO、ガーゼ など商品が特定されていないもの）は
空のままにします。キーワード検索で拾った価格を入れると、
実際に紹介する商品と違う値がシートに残るからです。

    python parenting/sheet_paste.py
    python parenting/sheet_paste.py --start 2
"""
import argparse
import os
import re

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "products_resolved.csv")
OUTDIR = os.path.join(HERE, "out")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2,
                    help="シート側の最初のデータ行。既定は2（1行目がヘッダー）")
    args = ap.parse_args()

    if not os.path.exists(SRC):
        raise SystemExit("products_resolved.csv がありません。先に rakuten_affiliate.py を走らせてください。")

    df = pd.read_csv(SRC, dtype=str).fillna("")
    df["row"] = df["id"].map(lambda s: int(re.sub(r"\D", "", s) or 0))
    df = df.sort_values("row")

    last = int(df["row"].max())
    by_row = {int(r["row"]): r for _, r in df.iterrows()}

    lines, filled, blank = [], 0, []
    for n in range(args.start, last + 1):
        r = by_row.get(n)
        # status=ok（URLで一意に特定できたもの）だけ値を入れる。
        if r is None or r["status"] != "ok":
            lines.append("\t")
            if r is not None:
                blank.append(f"  {n:>3}行 {r['name'][:28]} — {r['status'] or 'リンクなし'}")
            continue
        price = r["price"]
        rate = r["affiliate_rate"]
        lines.append(f"{price}\t{rate}")
        filled += 1

    os.makedirs(OUTDIR, exist_ok=True)
    path = os.path.join(OUTDIR, "sheet_DE.tsv")
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    print("\n".join(lines))
    print(f"\n書き出しました: {path}")
    print(f"D{args.start} を選択して貼り付けてください（{len(lines)}行 / 値あり {filled}件）")
    if blank:
        print(f"\n空のままにした行 {len(blank)}件（商品が特定されていない）:")
        print("\n".join(blank))


if __name__ == "__main__":
    main()
