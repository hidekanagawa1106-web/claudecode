"""ネタ帳ページ（claims-page.html）を claims.md と debates.md から生成する。

    python x/note/build_claims_page.py

生成したら Artifact に publish し直してください（URLは変わりません）。
    https://claude.ai/code/artifact/151d965c-1430-460d-824a-ac9662d3057e

**印の意味**: STOCKED は承認済みの下書きがあるネタ、DRAFTED は判断待ちのネタ。
どちらも「使ってはいけない印」ではありません（同じネタを別の型で出すのは想定どおり）。
下書きの状態が変わったら、この2つの集合を手で直してください。
"""

import html
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
X = os.path.dirname(HERE)
CLAIMS = os.path.join(X, "docs", "claims.md")
DEBATES = os.path.join(X, "debates.md")
OUT = os.path.join(HERE, "claims-page.html")

STOCKED = {"C002", "C015", "C038", "C051", "C086", "C087", "C088", "C089", "C097"}
DRAFTED = {"C003", "C024", "C027", "C030", "C053", "C061", "C077", "C081", "C082",
           "C096", "C098", "C099", "C100", "C101", "C102", "N1"}


def load_claims():
    text = open(CLAIMS, encoding="utf-8").read()
    text = text[text.index("## キャリア戦略"):]
    cat = sub = None
    items = []
    for line in text.split("\n"):
        line = line.rstrip()
        if line.startswith("## "):
            cat, sub = line[3:].strip(), None
        elif line.startswith("**") and line.endswith("**") and not line.startswith("**全"):
            sub = line.strip("*")
        elif line.startswith("- **C"):
            m = re.match(r"- \*\*(C\d+)\*\* (.+)", line)
            if m:
                body = re.sub(r"（20\d\d-\d\d-\d\d 追加）", "", m.group(2)).strip()
                items.append({"id": m.group(1), "text": body,
                              "cat": cat or "その他", "sub": sub or ""})
    return items


def load_debates():
    """debates.md の在庫テーブルから # / テーマ / 割れ方 / 期限 を拾う。"""
    text = open(DEBATES, encoding="utf-8").read()
    rows = []
    for line in text.split("\n"):
        m = re.match(r"\|\s*([DN]\d+)\s*\|(.+?)\|(.+?)\|(.+?)\|(.+?)\|\s*$", line)
        if m:
            num, theme, split, _why, lim = (x.strip() for x in m.groups())
            theme = re.sub(r"\*\*", "", theme)
            split = re.sub(r"\*\*", "", split)
            lim = re.sub(r"\*\*", "", lim)
            rows.append((num, theme, split, lim))
    return rows


def mark(cid):
    if cid in STOCKED:
        return '<span class="mk stock">在庫</span>'
    if cid in DRAFTED:
        return '<span class="mk draft">提案中</span>'
    return ""


def build():
    items = load_claims()
    debates = load_debates()

    cats = []
    for it in items:
        if it["cat"] not in cats:
            cats.append(it["cat"])

    chips = ['<button class="chip on" data-cat="*">すべて<span class="n">%d</span></button>' % len(items)]
    for c in cats:
        n = sum(1 for it in items if it["cat"] == c)
        chips.append(f'<button class="chip" data-cat="{html.escape(c)}">{html.escape(c)}'
                     f'<span class="n">{n}</span></button>')

    rows = []
    for it in items:
        sub = f'<span class="sub">{html.escape(it["sub"])}</span>' if it["sub"] else ""
        rows.append(
            f'<li class="row" data-cat="{html.escape(it["cat"])}" '
            f'data-q="{html.escape(it["id"] + it["text"] + it["cat"] + it["sub"])}">'
            f'<span class="num">{it["id"]}</span>'
            f'<span class="txt">{html.escape(it["text"])}{sub}</span>{mark(it["id"])}</li>')

    dr = []
    for num, theme, split, lim in debates:
        cls = {"短": "lim-s", "中": "lim-m", "長": "lim-l"}.get(lim, "lim-l")
        dr.append(f'<tr><td class="num">{num}</td><td>{html.escape(theme)}{mark(num)}</td>'
                  f'<td class="split">{html.escape(split)}</td>'
                  f'<td><span class="lim {cls}">{html.escape(lim)}</span></td></tr>')

    tpl = open(os.path.join(HERE, "claims-page.tpl.html"), encoding="utf-8").read()
    out = (tpl.replace("<!--COUNT-->", f"主張{len(items)}件 ＋ 議論テーマ{len(debates)}件")
              .replace("<!--CHIPS-->", "\n".join(chips))
              .replace("<!--ROWS-->", "\n".join(rows))
              .replace("<!--DEBATES-->", "\n".join(dr)))
    open(OUT, "w", encoding="utf-8").write(out)
    print(f"{OUT}: 主張{len(items)}件 / 議論テーマ{len(debates)}件")


if __name__ == "__main__":
    build()
