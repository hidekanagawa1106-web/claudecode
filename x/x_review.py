#!/usr/bin/env python3
"""投稿実績の記録と集計。

X API を使わない運用なので、数字は X アナリティクスのスクショから読み取って
このスクリプトに渡す。posts.csv が実績台帳（trades.csv 相当）になる。

  # 1件記録する
  python x/x_review.py --add --date 2026-08-11 --slot 朝 \\
      --format 型1 --episode E003 \\
      --hook "新卒の頃、報連相をすっぽかして上司に怒られた" \\
      --impressions 420000 --likes 3800 --reposts 410 --replies 26 \\
      --bookmarks 55 --profile-clicks 2100 --link-clicks 340 --followers-delta 18

  未指定の数値は 0 ではなく「未計測」として扱う（率の集計から外れる）。

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
    "profile_clicks", "reply_impressions", "link_clicks",
    "followers_delta", "note",
]

NUMERIC = [
    "impressions", "likes", "reposts", "replies", "bookmarks",
    "profile_clicks", "reply_impressions", "link_clicks", "followers_delta",
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
    # 列を後から足しても、既存の CSV がそのまま読めるようにしておく。
    df = df.reindex(columns=COLUMNS)
    for col in NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def derive(df):
    """派生指標を足す。

    アフィリの導線は1stリプに置くので、本命は2段のファネルになる。

        本文インプレッション → リプに降りた人 → リンクを踏んだ人
                    drop_rate           reply_ctr
                    └────────── funnel_rate ──────────┘

    funnel_rate（本文impに対するリンククリック率）が最終的な導線効率で、
    drop_rate と reply_ctr は「本文が悪いのかリプが悪いのか」を切り分ける。
    """
    if df.empty:
        return df
    df = df.copy()
    imp = df["impressions"].replace(0, pd.NA)
    reply_imp = df["reply_impressions"].replace(0, pd.NA)

    engagements = df[["likes", "reposts", "replies", "bookmarks"]].sum(axis=1)
    df["engagements"] = engagements
    df["eng_rate"] = (engagements / imp * 100).round(2)

    df["funnel_rate"] = (df["link_clicks"] / imp * 100).round(3)
    df["drop_rate"] = (reply_imp / imp * 100).round(1)
    df["reply_ctr"] = (df["link_clicks"] / reply_imp * 100).round(2)

    # プロフィール経由は副次的な導線（プロフのリンクを踏む人）。
    df["prof_rate"] = (df["profile_clicks"] / imp * 100).round(3)
    return df


def add_record(args):
    df = load()

    dup = df[(df["date"] == args.date) & (df["slot"] == args.slot)]
    if not dup.empty and not args.force:
        print(f"既に {args.date} の「{args.slot}」枠が記録されています。")
        print("上書きするなら --force を付けてください。")
        print(dup.to_string(index=False))
        return 1

    # 枠は違うが同じ日に同じ書き出しの投稿がある = 枠を打ち直そうとしている可能性。
    # そのまま通すと二重計上になるので止める。
    if args.hook:
        same_day = df[(df["date"] == args.date) & (df["slot"] != args.slot)]
        clash = same_day[same_day["hook"].astype(str).str[:20] == args.hook[:20]]
        if not clash.empty:
            print(f"同じ日に、同じ書き出しの投稿が別の枠「{clash['slot'].iloc[0]}」で"
                  "記録されています。")
            print("枠を打ち直すなら、先に古い行を消してください（二重計上になります）。")
            print(clash.to_string(index=False))
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
        "reply_impressions": args.reply_impressions,
        "link_clicks": args.link_clicks,
        "followers_delta": args.followers_delta,
        "note": args.note or "",
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df = sort_by_time(df)
    df.to_csv(POSTS_CSV, index=False)

    d = derive(pd.DataFrame([row]))

    def show(v, suffix=""):
        return "未計測" if pd.isna(v) else f"{v}{suffix}"

    print(f"記録しました（通算 {len(df)} 本目）")
    print(f"  エンゲージ率 {show(d['eng_rate'].iloc[0], '%')}  "
          f"フォロー増 {show(row['followers_delta'])}")
    print(f"  導線: リプ到達 {show(d['drop_rate'].iloc[0], '%')} × "
          f"リプCTR {show(d['reply_ctr'].iloc[0], '%')} = "
          f"総合 {show(d['funnel_rate'].iloc[0], '%')}")
    return 0


def report(args):
    df = load()
    if df.empty:
        print("posts.csv がまだ空です。/x-review で1本目を記録してください。")
        return 0
    # 手で編集された場合に備えて、読み込み側でも並べ直す。
    df = derive(sort_by_time(df))

    print(f"=== 通算 {len(df)} 本（{df['date'].min()} 〜 {df['date'].max()}）===\n")

    def stat(col, how, fmt, suffix=""):
        """全件が未計測なら nan を出さずにそう言う。"""
        v = getattr(df[col], how)()
        return "未計測" if pd.isna(v) else format(v, fmt) + suffix

    print("● 全体")
    print(f"  インプレッション   中央値 {stat('impressions', 'median', ',.0f')} / "
          f"最大 {stat('impressions', 'max', ',.0f')}")
    print(f"  エンゲージ率       中央値 {stat('eng_rate', 'median', '.2f', '%')}")
    if df["followers_delta"].notna().any():
        print(f"  フォロー増         合計 {df['followers_delta'].sum():,.0f} / "
              f"1本あたり {df['followers_delta'].mean():.1f}")
    else:
        print("  フォロー増         未計測")

    print("\n● 導線（1stリプのアフィリリンク）")
    print(f"  リプ到達率   中央値 {stat('drop_rate', 'median', '.1f', '%')}"
          "   ← 本文を見た人のうちリプ欄まで降りた割合")
    print(f"  リプCTR      中央値 {stat('reply_ctr', 'median', '.2f', '%')}"
          "   ← リプを見た人のうちリンクを踏んだ割合")
    print(f"  総合導線率   中央値 {stat('funnel_rate', 'median', '.3f', '%')}"
          "   ← 本文impに対するクリック。これが本命")
    if df["link_clicks"].notna().any():
        print(f"  リンククリック 合計 {df['link_clicks'].sum():,.0f}")
    print(f"  （参考）プロフクリック率 中央値 {stat('prof_rate', 'median', '.3f', '%')}")

    print("\n● 型ごと（サンプル3本以上のみ）")
    grouped = df.groupby("format")
    def cell(series, how, fmt, suffix=""):
        v = getattr(series, how)()
        return "—" if pd.isna(v) else format(v, fmt) + suffix

    # 総合導線率の降順。未計測の型は後ろへ回す。
    # 文字列に整形したあとで並べ替えると "—" が混ざって順序が壊れるので、
    # 整形前の数値でソートしておく。
    def funnel_key(g):
        v = g["funnel_rate"].median()
        return (1, 0.0) if pd.isna(v) else (0, -float(v))

    eligible = [(n, g) for n, g in grouped if len(g) >= MIN_SAMPLES_PER_FORMAT]
    eligible.sort(key=lambda kv: funnel_key(kv[1]))

    rows = []
    for name, g in eligible:
        rows.append({
            "型": name,
            "本数": len(g),
            "imp中央値": cell(g["impressions"], "median", ",.0f"),
            "エンゲ率": cell(g["eng_rate"], "median", ".2f", "%"),
            "リプ到達": cell(g["drop_rate"], "median", ".1f", "%"),
            "総合導線率": cell(g["funnel_rate"], "median", ".3f", "%"),
            "クリック/本": cell(g["link_clicks"], "mean", ".0f"),
        })
    if rows:
        out = pd.DataFrame(rows)
        print(out.to_string(index=False))
    else:
        thin = grouped.size().sort_values(ascending=False)
        print(f"  まだどの型も {MIN_SAMPLES_PER_FORMAT} 本に届いていません。現在の内訳:")
        for name, n in thin.items():
            print(f"    {name}: {n}本")

    # バズったのに導線に効かなかった投稿。ここが一番の学びになる。
    measured = df[df["funnel_rate"].notna()]
    if len(measured) >= 5:
        imp_hi = measured["impressions"] >= measured["impressions"].median()
        fn_lo = measured["funnel_rate"] < measured["funnel_rate"].median()
        leaky = measured[imp_hi & fn_lo]
        if not leaky.empty:
            print(f"\n● 伸びたのに導線が抜けた投稿（{len(leaky)}本）")
            print("  リプ到達が低ければ本文とリプの断絶、")
            print("  リプCTRが低ければ橋渡しの文の問題。")
            for _, r in leaky.sort_values("impressions", ascending=False).head(5).iterrows():
                print(f"    {r['date']} [{r['format']}] imp {r['impressions']:,.0f} / "
                      f"リプ到達 {r['drop_rate']}% / リプCTR {r['reply_ctr']}% / "
                      f"総合 {r['funnel_rate']}%")
                print(f"      {str(r['hook'])[:60]}")

    n = args.last
    print(f"\n● 直近 {n} 本")
    cols = ["date", "slot", "format", "impressions", "eng_rate",
            "drop_rate", "reply_ctr", "funnel_rate", "followers_delta"]
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

    # 未指定は 0 ではなく「未計測」。過去投稿の遡り入力では、プロフィールアクセスや
    # ブックマークが画面に出ておらず、埋めようがない。0 を入れると率が歪む。
    for col in NUMERIC:
        p.add_argument(f"--{col.replace('_', '-')}", type=int, default=None)

    args = p.parse_args()

    if args.add:
        missing = [f for f in ("date", "slot", "format") if not getattr(args, f)]
        if missing:
            p.error("--add には " + " / ".join("--" + m for m in missing) + " が要ります")
        if args.impressions is None:
            print("※ --impressions が未指定です。率の集計からこの投稿は外れます。",
                  file=sys.stderr)
        if args.profile_clicks is None:
            print("※ --profile-clicks が未指定です。第一指標が出せません。"
                  "アナリティクスの「プロフィールへのアクセス」を渡してください。",
                  file=sys.stderr)
        return add_record(args)

    return report(args)


if __name__ == "__main__":
    sys.exit(main())
