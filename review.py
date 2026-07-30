"""
定期レビュー・レポート生成スクリプト
======================================

picks_log.csv に一定件数(デフォルト20件)の記録がたまったら、
「大引け坊主スクリーニングの成績」を集計してレポートする。

重要: このスクリプトはフィルタ条件やロジックを自動で書き換えません。
あくまで集計結果を提示するだけです。screen_daily.py の条件を変えるかどうかは、
このレポートを見てHideさんご自身が判断してください
(禁止事項6「エントリー後に理由を後付けする」と同じ発想で、
 日々の結果を見て毎日ロジックを変えるのは避ける設計にしています)。

使い方:
    python review.py --log picks_log.csv --min-samples 20
"""

import argparse
import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="picks_log.csv")
    parser.add_argument("--min-samples", type=int, default=20)
    args = parser.parse_args()

    df = pd.read_csv(args.log, dtype={"code": str})
    completed = df[df["outcome_recorded"] == True].copy()

    if len(completed) < args.min_samples:
        print(f"記録済みサンプルは{len(completed)}件です。"
              f"{args.min_samples}件たまってからレビューすることをおすすめします。")
        return

    completed["gap_up"] = completed["gap_pct"] > 0
    completed["day_up"] = completed["day_change_pct"] > 0

    print(f"=== 大引け坊主スクリーニング レビュー（サンプル数: {len(completed)}） ===\n")

    print("[全体]")
    print(f"  翌日始値が前日終値より高かった割合: {completed['gap_up'].mean() * 100:.1f}%")
    print(f"  平均ギャップ率: {completed['gap_pct'].mean():.2f}%")
    print(f"  始値から引けにかけてさらに上昇した割合: {completed['day_up'].mean() * 100:.1f}%")
    print(f"  平均日中変化率: {completed['day_change_pct'].mean():.2f}%\n")

    print("[quant_all_pass別の比較]")
    for val in [True, False]:
        sub = completed[completed["quant_all_pass"] == val]
        if len(sub) == 0:
            continue
        print(f"  quant_all_pass={val} (n={len(sub)}): "
              f"平均ギャップ率={sub['gap_pct'].mean():.2f}%, "
              f"平均日中変化率={sub['day_change_pct'].mean():.2f}%")

    print("\n[パターン別の比較]")
    for pattern, sub in completed.groupby("pattern"):
        print(f"  {pattern} (n={len(sub)}): "
              f"平均ギャップ率={sub['gap_pct'].mean():.2f}%, "
              f"平均日中変化率={sub['day_change_pct'].mean():.2f}%")

    print("\n[ドライバー別の比較]")
    for driver, sub in completed.groupby("driver"):
        print(f"  {driver} (n={len(sub)}): "
              f"平均ギャップ率={sub['gap_pct'].mean():.2f}%, "
              f"平均日中変化率={sub['day_change_pct'].mean():.2f}%")

    print("\n※ このレポートはロジックを自動変更しません。"
          "傾向を見て、フィルタ条件を変えるかどうかはご自身で判断してください。")
    print("※ サンプル数が少ないうちの傾向は誤差(ノイズ)の可能性が高い点にご留意ください。")


if __name__ == "__main__":
    main()
