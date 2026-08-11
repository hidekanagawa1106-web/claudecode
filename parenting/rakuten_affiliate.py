"""products.csv の商品リストを楽天市場APIに問い合わせ、アフィリエイトリンク付きの
products_resolved.csv を作る。

楽天アフィリエイトのリンクは手で作らなくていい。楽天市場商品検索APIに
affiliateId を付けてリクエストすると、レスポンスの affiliateUrl に
そのまま貼れるリンクが入って返ってくる。この本文がやっているのはそれだけ。

商品の特定は2通り。
  ・rakuten_url / item_code がある → itemCode で一意に取る（status=ok）
  ・keyword しかない               → 検索して1件目を採用する（status=guess）
    guess は当てずっぽうなので、out/candidates.md に上位候補を書き出す。
    目視で選んで products.csv の rakuten_url を埋め直すと ok に変わる。

--check は既存の products_resolved.csv を作り直して、在庫切れ・価格変動・
リンク切れを差分として出す。公開後の記事を腐らせないための定期実行用。

必要な環境変数（楽天ウェブサービスの管理画面で取得）:
    RAKUTEN_APP_ID        アプリID       https://webservice.rakuten.co.jp/app/list
    RAKUTEN_AFFILIATE_ID  アフィリエイトID https://webservice.rakuten.co.jp/account_affiliate_id/
    RAKUTEN_ACCESS_KEY    アクセスキー    （新バージョンのAPIで必須。アプリ情報の画面に出る）

    python parenting/rakuten_affiliate.py
    python parenting/rakuten_affiliate.py --check
"""
import argparse
import os
import re
import sys
import time
import urllib.parse

import pandas as pd
import requests

API = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "products.csv")
OUT = os.path.join(HERE, "products_resolved.csv")
CANDIDATES = os.path.join(HERE, "out", "candidates.md")

# 同じURLに短時間で叩き込むと一定時間返らなくなる、と楽天のドキュメントにある。
# 商品数はせいぜい数十なので、1秒空けておけば足りる。
INTERVAL = 1.0

ITEM_URL = re.compile(r"item\.rakuten\.co\.jp/([^/]+)/([^/?#]+)")


HELP = {
    "RAKUTEN_APP_ID": "https://webservice.rakuten.co.jp/app/list",
    "RAKUTEN_ACCESS_KEY": "https://webservice.rakuten.co.jp/app/list （アプリ情報の画面）",
    "RAKUTEN_AFFILIATE_ID": "https://webservice.rakuten.co.jp/account_affiliate_id/",
}


def credentials():
    """3つとも必須。accessKey が無いと API は 400 を返す。"""
    vals = {k: os.environ.get(k, "").strip() for k in HELP}
    missing = [k for k, v in vals.items() if not v]
    if missing:
        lines = ["次の環境変数を入れてください。"]
        lines += [f"  export {k}=...   {HELP[k]}" for k in missing]
        sys.exit("\n".join(lines))
    return vals["RAKUTEN_APP_ID"], vals["RAKUTEN_AFFILIATE_ID"], vals["RAKUTEN_ACCESS_KEY"]


def to_item_code(row):
    """rakuten_url または item_code から 店舗コード:商品コード を作る。無ければ None。"""
    code = str(row.get("item_code") or "").strip()
    if code and code.lower() != "nan":
        return code
    url = str(row.get("rakuten_url") or "").strip()
    m = ITEM_URL.search(urllib.parse.unquote(url))
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    return None


def search(params, app_id, affiliate_id, access_key):
    q = {"applicationId": app_id, "affiliateId": affiliate_id, "format": "json", **params}
    headers = {"accessKey": access_key}
    r = requests.get(API, params=q, headers=headers, timeout=20)
    if r.status_code == 429:
        # 叩きすぎ。少し待って1度だけやり直す。
        time.sleep(5)
        r = requests.get(API, params=q, headers=headers, timeout=20)
    if r.status_code != 200:
        return [], f"HTTP {r.status_code}: {r.text[:120]}"
    items = [x["Item"] for x in r.json().get("Items", [])]
    return items, None


def resolve(row, app_id, affiliate_id, access_key):
    """1商品を解決して (結果dict, 候補list) を返す。"""
    item_code = to_item_code(row)
    if item_code:
        items, err = search({"itemCode": item_code, "hits": 1}, app_id, affiliate_id, access_key)
        status = "ok"
        candidates = []
    else:
        keyword = str(row.get("keyword") or row.get("name") or "").strip()
        if not keyword:
            return {"status": "no_key", "note": "rakuten_url / item_code / keyword が全部空"}, []
        params = {"keyword": keyword, "hits": 5, "sort": "-reviewCount", "availability": 1}
        max_price = str(row.get("max_price") or "").strip()
        if max_price and max_price.lower() != "nan":
            params["maxPrice"] = int(float(max_price))
        items, err = search(params, app_id, affiliate_id, access_key)
        status = "guess"
        candidates = items[1:]

    if err:
        return {"status": "error", "note": err}, []
    if not items:
        # itemCode 指定で0件は、その商品ページが消えたということ。
        return {"status": "gone" if item_code else "not_found", "note": item_code or ""}, []

    it = items[0]
    return {
        "status": status,
        "matched_name": it.get("itemName", ""),
        "price": it.get("itemPrice", ""),
        "shop": it.get("shopName", ""),
        "review_avg": it.get("reviewAverage", ""),
        "review_count": it.get("reviewCount", ""),
        "image_url": (it.get("mediumImageUrls") or [{}])[0].get("imageUrl", ""),
        "affiliate_url": it.get("affiliateUrl", ""),
        "item_url": it.get("itemUrl", ""),
        "item_code": it.get("itemCode", ""),
        "note": "",
    }, candidates


def write_candidates(rows):
    """keyword 検索になった商品の候補を、目視で選べる形で書き出す。"""
    if not rows:
        return
    os.makedirs(os.path.dirname(CANDIDATES), exist_ok=True)
    out = [
        "# 候補（目視で選ぶ）\n",
        "keyword 検索で解決した商品です。1件目を自動採用していますが、",
        "違うと思ったら下の候補から選んで `products.csv` の `rakuten_url` に",
        "商品ページURLを貼り直してください。貼れば status が ok になります。\n",
    ]
    for pid, name, chosen, cands in rows:
        out.append(f"\n## {pid} {name}\n")
        out.append(f"- **採用中**: {chosen.get('matched_name','')[:80]}")
        out.append(f"  - {chosen.get('price','')}円 / {chosen.get('shop','')} / "
                   f"★{chosen.get('review_avg','')}({chosen.get('review_count','')}件)")
        out.append(f"  - {chosen.get('item_url','')}")
        for c in cands:
            out.append(f"- {c.get('itemName','')[:80]}")
            out.append(f"  - {c.get('itemPrice','')}円 / {c.get('shopName','')} / "
                       f"★{c.get('reviewAverage','')}({c.get('reviewCount','')}件)")
            out.append(f"  - {c.get('itemUrl','')}")
    open(CANDIDATES, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print(f"候補を書き出しました: {CANDIDATES}")


def diff_report(before, after):
    """--check 用。前回との差分だけを出す。"""
    if before is None:
        return
    b = before.set_index("id")
    changed = []
    for _, r in after.iterrows():
        pid = r["id"]
        if pid not in b.index:
            continue
        old = b.loc[pid]
        if r["status"] in ("gone", "not_found", "error"):
            changed.append(f"  [切れ] {pid} {r['name']} — status={r['status']} {r.get('note','')}")
            continue
        try:
            op, np_ = float(old.get("price") or 0), float(r.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if op and np_ and abs(np_ - op) / op >= 0.10:
            changed.append(f"  [価格] {pid} {r['name']} — {int(op)}円 → {int(np_)}円 "
                           f"({(np_-op)/op*100:+.0f}%)")
    if changed:
        print("\n前回からの変化:")
        print("\n".join(changed))
        print("\n価格を本文に書いている場合は、記事側も直してください。")
    else:
        print("\n前回からの変化: なし")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="既存の products_resolved.csv と比べて、在庫切れ・価格変動を報告する")
    args = ap.parse_args()

    app_id, affiliate_id, access_key = credentials()
    src = pd.read_csv(SRC, comment="#", dtype=str).fillna("")
    src = src[src["id"].str.strip() != ""]

    before = None
    if args.check and os.path.exists(OUT):
        before = pd.read_csv(OUT, dtype=str)

    rows, cand_rows = [], []
    for i, (_, row) in enumerate(src.iterrows()):
        if i:
            time.sleep(INTERVAL)
        res, cands = resolve(row, app_id, affiliate_id, access_key)
        print(f"{row['id']:>4} {row['name'][:24]:24} {res['status']}")
        if res["status"] == "guess":
            cand_rows.append((row["id"], row["name"], res, cands))
        rows.append({**row.to_dict(), **res})

    after = pd.DataFrame(rows)
    cols = ["id", "section", "name", "timing", "must", "comment", "status",
            "matched_name", "price", "shop", "review_avg", "review_count",
            "image_url", "affiliate_url", "item_url", "item_code", "note"]
    # 列は常に同じ並び・同じ数で出す。全件エラーの回でも build_note.py が読めるように。
    after = after.reindex(columns=cols)
    after.to_csv(OUT, index=False)
    print(f"\n書き出しました: {OUT}")

    counts = after["status"].value_counts().to_dict()
    print("内訳: " + " / ".join(f"{k}={v}" for k, v in counts.items()))
    write_candidates(cand_rows)
    diff_report(before, after)

    if counts.get("guess"):
        print(f"\nguess が {counts['guess']}件あります。候補を見て products.csv を埋めると確実になります。")


if __name__ == "__main__":
    main()
