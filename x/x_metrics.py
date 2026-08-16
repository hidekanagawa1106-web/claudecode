"""X API から自分の投稿の実績を取って posts.csv に書き込む（1日1回の想定）。

スクショを撮って `/x-review` に渡す手間を消すためのものです。
**取れる数字はエクスポートCSVと同じ**で、増えるのは鮮度と手間ゼロだけ。

認証は **OAuth 1.0a のユーザーコンテキスト**。開発者ポータルで自分用の
Access Token を発行するだけなので、ブラウザのリダイレクトも
リフレッシュトークンも要りません（OAuth 2.0 PKCE より圧倒的に楽）。

    pip install requests requests-oauthlib
    export X_API_KEY=... X_API_SECRET=... X_ACCESS_TOKEN=... X_ACCESS_SECRET=...
    python x/x_metrics.py --days 3

手順の全文は `x/docs/api-setup.md`。

注意:
- **non_public_metrics は直近30日の投稿にしか付きません。** 古い投稿は
  公開指標だけになるので、月次のエクスポートCSV（`analytics.py`）と併用します
- 読み取りは1件 $0.005。`--days 3` なら1日あたり10件前後 = 月$1.5ほど
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


def fetch(s, uid, days):
    """直近 days 日ぶんの自分の投稿を取る。リツイートは除外。"""
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days))
    data = get(
        s, f"/users/{uid}/tweets",
        max_results=100,
        start_time=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        exclude="retweets",
        **{
            "tweet.fields": "created_at,text,public_metrics,non_public_metrics,referenced_tweets",
        },
    ).get("data", [])
    for t in data:
        t["jst"] = (dt.datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))
                    + dt.timedelta(hours=9)).replace(tzinfo=None)
    return data


def split(tweets):
    """本文と、その本文にぶら下げた自分のリプ（＝導線リプ）に分ける。

    referenced_tweets の replied_to が自分の投稿を指していれば1stリプ扱い。
    これで本文とリプが自動で紐づくので、リプ到達率まで計算できる。
    """
    ids = {t["id"] for t in tweets}
    mains, replies = [], {}
    for t in tweets:
        parent = next((r["id"] for r in t.get("referenced_tweets") or []
                       if r["type"] == "replied_to"), None)
        if parent and parent in ids:
            # 同じ親に複数ぶら下がっている場合、クリックが多いほうを採用
            cur = replies.get(parent)
            if cur is None or npm(t, "url_link_clicks") > npm(cur, "url_link_clicks"):
                replies[parent] = t
        elif parent is None:
            mains.append(t)
    return mains, replies


def npm(t, key):
    return (t.get("non_public_metrics") or {}).get(key, 0)


def slot_of(jst):
    h = jst.hour
    return "朝" if h < 11 else ("昼" if h < 17 else "夜")


def to_row(t, reply):
    pm = t.get("public_metrics") or {}
    imp = npm(t, "impression_count") or pm.get("impression_count")
    return {
        "date": t["jst"].strftime("%Y-%m-%d"),
        "slot": slot_of(t["jst"]),
        "format": classify(t["text"]),
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
    old = old.sort_values(["date", "_s"]).drop(columns="_s")
    old.to_csv(POSTS_CSV, index=False)
    return added, updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3,
                    help="何日ぶん遡るか（既定3。数字は72時間ほど伸び続けるため）")
    ap.add_argument("--dry-run", action="store_true", help="posts.csv に書かずに表示だけ")
    args = ap.parse_args()

    s = session()
    tweets = fetch(s, my_id(s), args.days)
    mains, replies = split(tweets)
    if not mains:
        print(f"直近{args.days}日に自分の投稿はありませんでした")
        return

    rows = [to_row(t, replies.get(t["id"])) for t in sorted(mains, key=lambda t: t["jst"])]

    print(f"取得 {len(tweets)}件（本文{len(mains)} / 導線リプ{len(replies)}）"
          f"  概算コスト ${len(tweets) * 0.005:.3f}\n")
    print(f"{'日付':<11}{'枠':<3}{'imp':>9}{'♥':>6}{'返':>4}{'プロフ':>7}{'リプimp':>9}{'click':>7}  型")
    for r in rows:
        ri = r["reply_impressions"]
        print(f"{r['date']:<11}{r['slot']:<3}{r['impressions'] or 0:>9,}{r['likes'] or 0:>6,}"
              f"{r['replies'] or 0:>4}{r['profile_clicks'] or 0:>7,}"
              f"{ri if ri is not None else '—':>9}{r['link_clicks'] if r['link_clicks'] is not None else '—':>7}"
              f"  {r['format']}")

    if args.dry_run:
        print("\n--dry-run のため posts.csv は更新していません")
        return
    a, u = upsert(rows)
    print(f"\nposts.csv: 新規{a}件 / 更新{u}件")
    print("型（format）は自動分類なので、狙いと違っていれば手で直してください")


if __name__ == "__main__":
    main()
