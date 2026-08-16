"""X API から自分の投稿の実績を取って posts.csv に書き込む（1日1回の想定）。

スクショを撮って `/x-review` に渡す手間を消すためのものです。
**取れる数字はエクスポートCSVと同じ**で、増えるのは鮮度と手間ゼロだけ。

認証は **OAuth 1.0a のユーザーコンテキスト**。開発者ポータルで自分用の
Access Token を発行するだけなので、ブラウザのリダイレクトも
リフレッシュトークンも要りません（OAuth 2.0 PKCE より圧倒的に楽）。

    pip install requests requests-oauthlib
    export X_API_KEY=... X_API_SECRET=... X_ACCESS_TOKEN=... X_ACCESS_SECRET=...
    python x/x_metrics.py            # 3日前の1日ぶんを取る（既定）

手順の全文は `x/docs/api-setup.md`。

**既定は「3日前の1日ぶんだけ」。** 毎朝これを走らせると、各投稿はちょうど
72時間後に**一度だけ**取得されます。インプレッションはそこまでで頭打ちになるので、
同じ投稿を何度も取り直す必要がありません（＝重複課金しない）。

取るもの / 取らないもの:

| | |
|---|---|
| 通常の投稿 | **取る** |
| 引用リポスト | **取る**（通常の投稿と同じ扱い） |
| 自分の投稿への自分のリプ（＝導線リプ） | **取らない**（下記） |
| 他アカウントへの交流リプ | **取らない** |
| リポスト | **取らない** |

**返信は全部API側で除外しています**（`exclude=retweets,replies`）。
交流リプが1日50件あり、捨てるためだけに月$7.5払う形になっていたためです
（2026-08-15にHideさんが判断）。

そのため `reply_impressions` と `link_clicks` は空のままになります。
**導線の実測は月次のエクスポートCSV（`analytics.py`）で拾ってください。**
あちらには全投稿の `URLクリック数` が入っているので、日次で取らなくても
ファネルは測れます（19ケースの分析はエクスポートから出したものです）。

一時的に取りたいときは `--with-replies` を付けます。

注意:
- **non_public_metrics は直近30日の投稿にしか付きません。** 古い投稿は
  公開指標だけになるので、月次のエクスポートCSV（`analytics.py`）と併用します
- 読み取りは1件 $0.005。返信を除外して1日2〜6件なので**月$0.3〜1**
"""

import argparse
import json
import os
import sys
import datetime as dt

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from analytics import classify  # 型の自動分類を使い回す
from x_review import COLUMNS, POSTS_CSV, SLOT_ORDER

API = "https://api.x.com/2"
ME_CACHE = os.path.join(os.path.dirname(__file__), "data", ".user_id")

ENV = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")


def session():
    try:
        from requests_oauthlib import OAuth1Session
    except ImportError:
        raise SystemExit("pip install requests requests-oauthlib が必要です")

    missing = [k for k in ENV if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"環境変数が未設定: {', '.join(missing)}\n"
                         f"発行手順は x/docs/api-setup.md")

    return OAuth1Session(
        os.environ["X_API_KEY"], os.environ["X_API_SECRET"],
        os.environ["X_ACCESS_TOKEN"], os.environ["X_ACCESS_SECRET"],
    )


def get(s, path, **params):
    r = s.get(f"{API}{path}", params=params, timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"APIエラー {r.status_code}: {r.text[:400]}")
    return r.json()


def my_id(s):
    """ユーザーIDは一生変わらないので、引くのは一度だけにする。

    毎回 /users/me を叩くと $0.01/回。GitHub Actions のように毎回まっさらな
    環境で走らせる場合は、環境変数 X_USER_ID に入れておけば呼びません。
    """
    if os.environ.get("X_USER_ID"):
        return os.environ["X_USER_ID"].strip()
    if os.path.exists(ME_CACHE):
        return open(ME_CACHE).read().strip()
    uid = get(s, "/users/me")["data"]["id"]
    os.makedirs(os.path.dirname(ME_CACHE), exist_ok=True)
    open(ME_CACHE, "w").write(uid)
    print(f"ユーザーIDは {uid}。X_USER_ID に入れておくと次回から $0.01 節約できます")
    return uid


JST = dt.timezone(dt.timedelta(hours=9))


def window(offset=None, days=None):
    """取得する時間帯を UTC で返す。

    既定（offset指定）は**JSTの1日ぶんだけ**を切り出す。毎朝走らせれば
    各投稿は72時間後に一度だけ取得され、二度と取りに行きません。
    """
    today = dt.datetime.now(JST).date()
    if days:  # 過去ぶんの取りこぼしを埋めるとき用
        end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)
        return end - dt.timedelta(days=days), end, None
    target = today - dt.timedelta(days=offset)
    start = dt.datetime.combine(target, dt.time.min, tzinfo=JST)
    return start.astimezone(dt.timezone.utc), (start + dt.timedelta(days=1)).astimezone(dt.timezone.utc), target


def fetch(s, uid, start, end, with_replies=False):
    """指定期間の自分の投稿を取る。

    **既定で返信を全部除外します**（`exclude=retweets,replies`）。
    APIには「自分への返信だけ残す」指定が無く、残すと他アカウントへの
    交流リプまで取ってしまいます。実測で1日50件あり、捨てるためだけに
    月$7.5かかっていました。

    副作用として自分の導線リプも取れなくなるので、`link_clicks` と
    `reply_impressions` は空になります。**導線は月次のエクスポート
    （analytics.py）で測る**方針です。

    **引用リポストは除外されません。** 引用は referenced_tweets の type が
    quoted であって replied_to ではないため、通常の投稿として返ってきます。
    """
    data = get(
        s, f"/users/{uid}/tweets",
        max_results=100,
        start_time=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end_time=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        exclude="retweets" if with_replies else "retweets,replies",
        **{
            "tweet.fields": "created_at,text,public_metrics,non_public_metrics,referenced_tweets",
        },
    ).get("data", [])
    for t in data:
        t["jst"] = dt.datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")).astimezone(JST).replace(tzinfo=None)
    return data


def split(tweets):
    """本文 / 自分への導線リプ / 他人への交流リプ に振り分ける。

    - replied_to が**自分の投稿**を指す → 導線リプ（本文に紐づける）
    - replied_to が**他人の投稿**を指す → 交流リプ（捨てる）
    - replied_to が無い → 本文。**引用リポストもここに入る**
      （引用は referenced_tweets の type が quoted なので replied_to にならない）
    """
    ids = {t["id"] for t in tweets}
    mains, replies, social = [], {}, []
    for t in tweets:
        parent = next((r["id"] for r in t.get("referenced_tweets") or []
                       if r["type"] == "replied_to"), None)
        if parent is None:
            mains.append(t)
        elif parent in ids:
            # 同じ親に複数ぶら下がっている場合、クリックが多いほうを採用
            cur = replies.get(parent)
            if cur is None or npm(t, "url_link_clicks") > npm(cur, "url_link_clicks"):
                replies[parent] = t
        else:
            social.append(t)
    return mains, replies, social


def already_done(target):
    """その日ぶんを取得済みなら True。**二重に課金しないための番人。**"""
    if target is None or not os.path.exists(POSTS_CSV):
        return False
    df = pd.read_csv(POSTS_CSV)
    if "note" not in df or "date" not in df:
        return False
    hit = df[(df["date"].astype(str) == target.strftime("%Y-%m-%d"))
             & (df["note"].astype(str).str.contains("x_metrics", na=False))]
    return len(hit) > 0


def npm(t, key):
    return (t.get("non_public_metrics") or {}).get(key, 0)


def slot_of(jst):
    h = jst.hour
    return "朝" if h < 11 else ("昼" if h < 17 else "夜")


def is_quote(t):
    """引用リポストか。referenced_tweets の type が quoted なら確実に分かる。"""
    return any(r["type"] == "quoted" for r in t.get("referenced_tweets") or [])


def to_row(t, reply):
    pm = t.get("public_metrics") or {}
    imp = npm(t, "impression_count") or pm.get("impression_count")
    # 引用RTは引用元が見えないと意味が取れないので、型の自動分類にかけない。
    # かけると全部「断言・その他」に落ちて、分類そのものが濁る
    fmt = "引用RT" if is_quote(t) else classify(t["text"])
    return {
        "date": t["jst"].strftime("%Y-%m-%d"),
        "time": t["jst"].strftime("%H:%M"),
        "slot": slot_of(t["jst"]),
        "format": fmt,
        "episode": "",
        "hook": t["text"].split("\n")[0][:40],
        "url": f"https://x.com/i/status/{t['id']}",
        "impressions": imp,
        "likes": pm.get("like_count"),
        "reposts": pm.get("retweet_count"),
        "replies": pm.get("reply_count"),
        "bookmarks": pm.get("bookmark_count"),
        "profile_clicks": npm(t, "user_profile_clicks"),
        "reply_impressions": npm(reply, "impression_count") if reply else None,
        "link_clicks": npm(reply, "url_link_clicks") if reply else None,
        "followers_delta": None,
        # 本文をまるごと残す。在庫（stock.md）との照合と、Hideさんが直した
        # 差分の学習に使うので、hook（1行目40字）だけでは足りない
        "text": t["text"],
        "note": "x_metrics.py",
    }


def upsert(rows):
    """同じURLの行は上書きする。数字は24〜72時間伸び続けるので、
    毎日走らせて最新値で塗り替えるのが正しい挙動。"""
    old = pd.read_csv(POSTS_CSV) if os.path.exists(POSTS_CSV) else pd.DataFrame(columns=COLUMNS)
    new = pd.DataFrame(rows, columns=COLUMNS)
    added = updated = 0
    for _, r in new.iterrows():
        hit = old.index[old["url"] == r["url"]] if "url" in old else []
        if len(hit):
            # 手で入れた format / episode / note は保持する
            for c in COLUMNS:
                if c in ("format", "episode", "note") and str(old.at[hit[0], c]) not in ("", "nan"):
                    continue
                old.at[hit[0], c] = r[c]
            updated += 1
        else:
            old = pd.concat([old, r.to_frame().T], ignore_index=True)
            added += 1
    old["_s"] = old["slot"].map(SLOT_ORDER).fillna(9)
    old = old.sort_values(["date", "time", "_s"], na_position="first").drop(columns="_s")
    old.to_csv(POSTS_CSV, index=False)
    return added, updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offset", type=int, default=3,
                    help="何日前の1日ぶんを取るか（既定3。72時間で数字が頭打ちになるため）")
    ap.add_argument("--days", type=int,
                    help="直近N日ぶんをまとめて取る（取りこぼしを埋めるとき用）")
    ap.add_argument("--force", action="store_true", help="取得済みでも取り直す")
    ap.add_argument("--with-replies", action="store_true",
                    help="返信も取る（導線リプの数字が要るとき。交流リプも来るので割高）")
    ap.add_argument("--dry-run", action="store_true", help="posts.csv に書かずに表示だけ")
    args = ap.parse_args()

    start, end, target = window(args.offset, args.days)
    if target and already_done(target) and not args.force:
        print(f"{target} ぶんは取得済みです。取り直すなら --force")
        return

    label = f"{target}（JSTの1日ぶん）" if target else f"直近{args.days}日"
    s = session()
    tweets = fetch(s, my_id(s), start, end, args.with_replies)
    mains, replies, social = split(tweets)
    if not mains:
        print(f"{label}に自分の投稿はありませんでした（取得{len(tweets)}件）")
        return

    rows = [to_row(t, replies.get(t["id"])) for t in sorted(mains, key=lambda t: t["jst"])]

    print(f"対象: {label}")
    detail = f"本文{len(mains)}"
    if args.with_replies:
        detail += f" / 導線リプ{len(replies)} / 交流リプ{len(social)}＝捨てる"
    print(f"取得 {len(tweets)}件（{detail}）  概算コスト ${len(tweets) * 0.005:.3f}")
    if not args.with_replies:
        print("返信はAPI側で除外しています。導線の数字は月次エクスポートで測ります\n")
    else:
        print()
    for r in rows:
        ri = r["reply_impressions"]
        print("=" * 72)
        print(f"{r['date']} {r['time']}（{r['slot']}）  {r['url']}")
        print(f"imp {r['impressions'] or 0:,}  ♥{r['likes'] or 0:,}  RT{r['reposts'] or 0}  "
              f"返{r['replies'] or 0}  BM{r['bookmarks'] or 0}  プロフ{r['profile_clicks'] or 0:,}  "
              f"リプimp {ri if ri is not None else '—'}  click {r['link_clicks'] if r['link_clicks'] is not None else '—'}")
        print(f"型（自動分類）: {r['format']}")
        print("-" * 72)
        print(r["text"])
        print()

    if args.dry_run:
        print("\n--dry-run のため posts.csv は更新していません")
        return
    a, u = upsert(rows)
    print(f"\nposts.csv: 新規{a}件 / 更新{u}件")
    print("型（format）は自動分類なので、狙いと違っていれば手で直してください")


if __name__ == "__main__":
    main()
