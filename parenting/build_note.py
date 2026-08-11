"""products_resolved.csv から、note に貼り付ける下書きの骨組みを組む。

このスクリプトは文章を書かない。書けるのは機械的に決まる部分だけ——
PR表記、セクションの並び、商品ごとの見出し、買い時、リンク、価格の注記。
本文（読ませる部分）は comment を種にして Claude が書きます。
分担をこう切っているのは、スクリプトが書いた紹介文は例外なくスペック紹介になり、
それは読まれないからです。

出るもの:
  out/note_draft.md  note の編集画面に貼る下書き。<<< >>> が Claude の書くところ
  out/links.md       商品名とURLの対応表。note で Cmd+K を使って貼るとき用

    python parenting/build_note.py
    python parenting/build_note.py --section 寝かしつけ
"""
import argparse
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "products_resolved.csv")
OUTDIR = os.path.join(HERE, "out")

PR = "※この記事にはアフィリエイト広告（楽天アフィリエイト）が含まれます。"

# 記事内のセクションの並び。products.csv の section をここに合わせておくと、
# 書いた順ではなく読者が必要になる順で並びます。
# ここに無い section は末尾に回ります。
ORDER = ["妊娠中に買う", "退院直後", "授乳・ミルク", "寝かしつけ",
         "おむつ", "外出", "家事削減", "自分のための道具"]

MUST_LABEL = {"◎": "◎ これは要る", "○": "○ あると相当ラク", "△": "△ 人による"}


def section_key(name):
    return (ORDER.index(name), name) if name in ORDER else (len(ORDER), name)


def product_block(r):
    must = MUST_LABEL.get(str(r["must"]).strip(), str(r["must"]).strip())
    price = f"{int(float(r['price'])):,}円" if str(r.get("price") or "").strip() else "価格未取得"
    review = ""
    if str(r.get("review_count") or "").strip() not in ("", "0", "nan"):
        review = f" / ★{r['review_avg']}（{r['review_count']}件）"

    lines = [
        f"### {r['name']}",
        "",
        f"**{must}｜買い時：{r['timing']}｜{price}{review}**",
        "",
        f"<<< ここを書く。種にする実体験: {r['comment']} >>>",
        "",
    ]
    url = str(r.get("affiliate_url") or "").strip()
    if url:
        lines += [str(r.get("matched_name", ""))[:60], url, ""]
    else:
        lines += [f"（リンク未解決: status={r.get('status')}）", ""]
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", help="このセクションだけ出す（別記事に切り出すとき）")
    ap.add_argument("--force", action="store_true",
                    help="本文を書き込み済みの下書きでも上書きする")
    args = ap.parse_args()

    if not os.path.exists(SRC):
        raise SystemExit("products_resolved.csv がありません。先に rakuten_affiliate.py を走らせてください。")

    df = pd.read_csv(SRC, dtype=str).fillna("")
    if args.section:
        df = df[df["section"] == args.section]
    if df.empty:
        raise SystemExit("対象の商品がありません。")

    broken = df[~df["status"].isin(["ok", "guess"])]
    df = df[df["status"].isin(["ok", "guess"])]

    os.makedirs(OUTDIR, exist_ok=True)
    sections = sorted(df["section"].unique(), key=section_key)

    # --- 下書き本体 -----------------------------------------------------
    # 並びは docs/outline.md「全体の並び」と同じ。変えるときは両方直すこと。
    # 商品より先に「買い物じゃない話」を置くのが要点。ここを入れ替えると
    # 量産記事と同じ顔になります。
    doc = [
        "<<< タイトル: docs/outline.md の候補から選ぶ >>>",
        "",
        PR,
        "",
        "<<< 導入。育休を取った事実・期間・立場を1〜2行で。",
        "    そのあと「この記事で何がわかるか」を1行。長くしない >>>",
        "",
        "---",
        "",
        "## 育休前にやったこと（買い物じゃない話）",
        "",
        "<<< 商品を1つも出さないセクション。ここが差別化そのものです。",
        "    上司への伝え方 / 引き継ぎ / 育児休業給付金の手続きと入金までの",
        "    タイムラグ / 社会保険料の免除 / 復帰時期の決め方。",
        "    金の話を具体的に書く —— 若手の読者が一番不安なのは収入です >>>",
        "",
        "---",
        "",
    ]
    for sec in sections:
        doc += [f"## {sec}", "", "<<< このセクションの入り（2〜3行）。"
                "この時期に何がしんどいのかを先に書く >>>", ""]
        rows = df[df["section"] == sec].sort_values("must")
        for _, r in rows.iterrows():
            doc += product_block(r)
    doc += [
        "---",
        "",
        "## 買わなくてよかったもの",
        "",
        "<<< 3つ、理由付きで。リンクは貼らない。",
        "    ここが記事で一番読まれます。ここに正直さがあるから上のリンクが押される。",
        "    逆にここが弱いと記事全体が売り込みに見えます >>>",
        "",
        "## やってみて分かったこと",
        "",
        "<<< 商品に戻らない。読後感を作るところ。",
        "    しておいてよかった判断 / しておけばよかった判断 /",
        "    育休が仕事にどう返ってきたか / これから取る人へ一つだけ >>>",
        "",
        PR,
        "",
    ]
    # セクションを切り出すときは別ファイルにする。本記事の下書きを潰さないため。
    stem = f"note_draft_{args.section}" if args.section else "note_draft"
    draft = os.path.join(OUTDIR, f"{stem}.md")

    # 一度本文を書き込んだ下書きは作り直せない。<<< >>> が消えていたら書き済みとみなす。
    if os.path.exists(draft) and not args.force:
        old = open(draft, encoding="utf-8").read()
        if "<<<" not in old:
            raise SystemExit(
                f"{draft} は本文が書き込まれています。上書きすると消えます。\n"
                "作り直すなら --force を付けるか、先に別名で退避してください。"
            )
    open(draft, "w", encoding="utf-8").write("\n".join(doc))

    # --- リンク対応表 ---------------------------------------------------
    sheet = ["# リンク対応表", "",
             "note の編集画面では、テキストを選択して Cmd+K でリンクを貼ります。",
             "下の URL をコピーして使ってください。", ""]
    for sec in sections:
        sheet.append(f"## {sec}")
        for _, r in df[df["section"] == sec].iterrows():
            sheet += [f"- **{r['name']}**", f"  {r['affiliate_url']}"]
        sheet.append("")
    if len(broken):
        sheet += ["## 未解決（リンクなし）", ""]
        for _, r in broken.iterrows():
            sheet.append(f"- {r['id']} {r['name']} — status={r['status']} {r.get('note','')}")
    links = os.path.join(OUTDIR, f"{stem.replace('note_draft', 'links')}.md")
    open(links, "w", encoding="utf-8").write("\n".join(sheet) + "\n")

    print(f"書き出しました:\n  {draft}\n  {links}")
    print(f"商品 {len(df)}件 / セクション {len(sections)}個"
          + (f" / 未解決 {len(broken)}件" if len(broken) else ""))
    print(f"\n下書きの <<< >>> が {sum(l.count('<<<') for l in doc)}箇所あります。ここを埋めれば公開できます。")


if __name__ == "__main__":
    main()
