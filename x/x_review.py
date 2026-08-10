#!/usr/bin/env python3
"""投稿実績の記録と集計。

X API を使わない運用なので、数字は X アナリティクスのスクショから読み取って
このスクリプトに渡す。posts.csv が実績台帳（trades.csv 相当）になる。

  # 1件記録する
  python x/x_review.py --add --date 2026-08-11 --slot 朝 \\
      --format 暴露 --episode E003 \\
      --hook "上司に『君のためを思って』と言われた日の話" \\
      --impressions 42000 --likes 380 --reposts 41 --replies 26 \\
      --bookmarks 55 --profile-clicks 210 --link-clicks 34 --followers-delta 18

  # 集計レポートを出す
  python x/x_review.py
  python x/x_review.py --last 20
"""

import argparse
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
POSTS_CSV = os.path.join(HERE, "posts.csv")

COLUMNS = [
    "date", "slot", "format", "episode", "hook", "url",
    "impressions", "likes", "reposts", "replies", "bookmarks",
    "profile_clicks", "link_clicks", "followers_delta", "note",
]

NUMERIC = [
    "impressions", "likes", "reposts", "replies", "bookmarks",
    "profile_clicks", "link_clicks", "followers_delta",
]

# 表示上の下限。これ未満のサンプル数では型ごとの比較を出さない。
MIN_SAMPLES_PER_FORMAT = 3

# 文字コード順に並べると 夜→朝 になってしまうので、投稿順を明示する。
SLOT_ORDER = {"朝": 0, "昼": 1, "夜": 2}


def sort_by_time(df):
    key = df["slot"].map(SLOT_ORDER).fillna(99)
    return df.assign(_slot_order=key).sort_values(
        ["date", "_slot_order"]).drop(columns="_slot_order").reset_index(drop=True)


def load():
    if not os.path.exists(POSTS_CSV):
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(POSTS_CSV)
    for col in NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def derive(df):
    """派生指標を足す。

    eng_rate はバズの大きさ、prof_rate は導線に効いたかを見る。
    転職アフィリの目的からすると後者のほうが本命で、
    「伸びたのにプロフィールに誰も来ていない投稿」を見つけるのがこの列の仕事。
    """
    if df.empty:
        return df
    df = df.copy()
    imp = df["impressions"].replace(0, pd.NA)
    engagements = df[["likes", "reposts", "replies", "bookmarks"]].sum(axis=1)
    df["engagements"] = engagements
    df["eng_rate"] = (engagements / imp * 100).round(2)
    df["prof_rate"] = (df["profile_clicks"] / imp * 100).round(3)
    df["ctr"] = (df["link_clicks"] / df["profile_clicks"].replace(0, pd.NA) * 100).round(1)
    return df


def add_record(args):
    df = load()

    dup = df[(df["date"] == args.date) & (df["slot"] == args.slot)]
    if not dup.empty and not args.force:
        print(f"既に {args.date} の「{args.slot}」枠が記録されています。")
        print("上書きするなら --force を付けてください。")
        print(dup.to_string(index=False))
        return 1

    if not dup.empty:
        df = df.drop(dup.index)

    row = {
        "date": args.date,
        "slot": args.slot,
        "format": args.format,
        "episode": args.episode or "",
        "hook": args.hook or "",
        "url": args.url or "",
        "impressions": args.impressions,
        "likes": args.likes,
        "reposts": args.reposts,
        "replies": args.replies,
        "bookmarks": args.bookmarks,
        "profile_clicks": args.profile_clicks,
        "link_clicks": args.link_clicks,
        "followers_delta": args.followers_delta,
        "note": args.note or "",
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df = sort_by_time(df)
    df.to_csv(POSTS_CSV, index=False)

    d = derive(pd.DataFrame([row]))
    print(f"記録しました（通算 {len(df)} 本目）")
    print(f"  エンゲージ率 {d['eng_rate'].iloc[0]}%  "
          f"プロフクリック率 {d['prof_rate'].iloc[0]}%  "
          f"フォロー増 {row['followers_delta']}")
    return 0


def report(args):
    df = load()
    if df.empty:
        print("posts.csv がまだ空です。/x-review で1本目を記録してください。")
        return 0
    # 手で編集された場合に備えて、読み込み側でも並べ直す。
    df = derive(sort_by_time(df))

    print(f"=== 通算 {len(df)} 本（{df['date'].min()} 〜 {df['date'].max()}）===\n")

    print("● 全体")
    print(f"  インプレッション   中央値 {df['impressions'].median():,.0f} / "
          f"最大 {df['impressions'].max():,.0f}")
    print(f"  エンゲージ率       中央値 {df['eng_rate'].median():.2f}%")
    print(f"  プロフクリック率   中央値 {df['prof_rate'].median():.3f}%")
    print(f"  フォロー増         合計 {df['followers_delta'].sum():,.0f} / "
          f"1本あたり {df['followers_delta'].mean():.1f}")

    print("\n● 型ごと（サンプル3本以上のみ）")
    grouped = df.groupby("format")
    rows = []
    for name, g in grouped:
        if len(g) < MIN_SAMPLES_PER_FORMAT:
            continue
        rows.append({
            "型": name,
            "本数": len(g),
            "imp中央値": f"{g['impressions'].median():,.0f}",
            "エンゲ率": f"{g['eng_rate'].median():.2f}%",
            "プロフ率": f"{g['prof_rate'].median():.3f}%",
            "フォロー/本": f"{g['followers_delta'].mean():.1f}",
        })
    if rows:
        out = pd.DataFrame(rows).sort_values("プロフ率", ascending=False)
        print(out.to_string(index=False))
    else:
        thin = grouped.size().sort_values(ascending=False)
        print(f"  まだどの型も {MIN_SAMPLES_PER_FORMAT} 本に届いていません。現在の内訳:")
        for name, n in thin.items():
            print(f"    {name}: {n}本")

    # バズったのに導線に効かなかった投稿。ここが一番の学びになる。
    if len(df) >= 5:
        imp_hi = df["impressions"] >= df["impressions"].median()
        prof_lo = df["prof_rate"] < df["prof_rate"].median()
        leaky = df[imp_hi & prof_lo]
        if not leaky.empty:
            print(f"\n● 伸びたのにプロフィールに来ていない投稿（{len(leaky)}本）")
            print("  ＝ 内容は刺さったが「この人を知りたい」に繋がっていない。")
            for _, r in leaky.sort_values("impressions", ascending=False).head(5).iterrows():
                print(f"    {r['date']} [{r['format']}] imp {r['impressions']:,.0f} / "
                      f"プロフ率 {r['prof_rate']:.3f}%")
                print(f"      {str(r['hook'])[:60]}")

    n = args.last
    print(f"\n● 直近 {n} 本")
    cols = ["date", "slot", "format", "impressions", "eng_rate", "prof_rate",
            "link_clicks", "followers_delta"]
    print(df.tail(n)[cols].to_string(index=False))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--add", action="store_true", help="1件記録する")
    p.add_argument("--force", action="store_true", help="同じ日・同じ枠を上書きする")
    p.add_argument("--last", type=int, default=10, help="レポートに出す直近本数")

    p.add_argument("--date")
    p.add_argument("--slot", help="朝 / 昼 / 夜")
    p.add_argument("--format", help="formats.md の型名")
    p.add_argument("--episode", help="episodes.md の素材ID（E001 など）")
    p.add_argument("--hook", help="冒頭1行")
    p.add_argument("--url")
    p.add_argument("--note")

    for col in NUMERIC:
        p.add_argument(f"--{col.replace('_', '-')}", type=int, default=0)

    args = p.parse_args()

    if args.add:
        missing = [f for f in ("date", "slot", "format") if not getattr(args, f)]
        if missing:
            p.error("--add には " + " / ".join("--" + m for m in missing) + " が要ります")
        if args.impressions == 0:
            p.error("--impressions が 0 です。アナリティクスの数字を渡してください")
        return add_record(args)

    return report(args)


if __name__ == "__main__":
    sys.exit(main())
