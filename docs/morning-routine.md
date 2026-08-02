# 平日8:00 の定期実行（Routine 設定）

`python morning.py` を平日朝8:00（JST）に自動実行するための設定。

## 状態

**登録済み。ただし初回実行(2026-08-03 8:00 JST)は失敗した。**

原因はコードではなく Routine の設定。起動したセッションにリポジトリが1つも
紐付いておらず、`git checkout` の前段で止まった。

```
/home/user           空。gitリポジトリではない
GitHub API           "sessions are bound to their configured repositories"（設定済みリポジトリなし）
git認証プロキシ        認証対象がなく clone/ls-remote が全滅
```

**対応: Routine の設定で対象リポジトリを指定すること。**

| 項目 | 値 |
|---|---|
| リポジトリ | `hidekanagawa1106-web/claudecode` |
| ブランチ | `claude/japan-swing-trade-watchlist-bk7bpd` |

保険として、プロンプト側にも `add_repo` での自力復旧手順を入れてある
（`docs/routine-prompt.txt` の手順1）。設定が正しければこの経路は通らない。

`CronCreate` は代用にならない。セッション限定で、セッション終了とともに消える（かつ7日で失効）。
コンテナは非永続なので、日々の定期実行には Routine が必要。

## 設定値

| 項目 | 値 |
|---|---|
| 名前 | 日本株 朝の統合ブリーフィング（平日8:00 JST） |
| cron | `0 23 * * 0-4` |
| 発火モード | 毎回あたらしいセッションを作る（create_new_session_on_fire） |
| 通知 | プッシュ通知あり |

### cron が `0 23 * * 0-4` になる理由

Routine の cron は **UTC で評価される**。JST は UTC+9 なので 8:00 JST は前日の 23:00 UTC。
日付をまたぐため曜日も1つずらす必要がある。

| 日本時間 | UTC |
|---|---|
| 月曜 8:00 | 日曜 23:00 |
| 金曜 8:00 | 木曜 23:00 |

したがって曜日は月〜金（1-5）ではなく **日〜木（0-4）**。

## プロンプト

貼り付け用の全文は **`docs/routine-prompt.txt`** にある。そのままコピーして使う。
新しいセッションは何も知らない状態で始まるため、単体で完結する指示にしてある。

## 登録前に押さえておくこと

**1. ブランチ**

このリポジトリにデフォルトブランチとして設定されているのは
`claude/daily-stock-scoring-schedule-g638sv` で、一連の実装は入っていない。

```
origin/HEAD → claude/daily-stock-scoring-schedule-g638sv   ← デフォルト
              claude/japan-swing-trade-watchlist-bk7bpd    ← 実装はこちら
```

新しいセッションはデフォルトブランチをクローンするので、プロンプト内の `git checkout` は必須。
`claude/japan-swing-trade-watchlist-bk7bpd` をデフォルトに変更するか main に統合すれば、
この手順は不要になる。

**2. picks_log.csv の push**

コンテナは毎回破棄されるため、commit して push しないとその日の候補記録が消える。
`macro_score` や決算フラグを溜めて後から検証する設計になっているので、ここが切れると
検証手段そのものが失われる。

**3. 実行時間**

`morning.py` は74銘柄をJ-Quantsから取得するので5〜10分かかる。
8:00開始なら日本市場の寄り付き（9:00）には十分間に合う。

**4. 夏時間**

8:00 JST は夏時間だと 19:00 ET。冬時間になると 18:00 ET にずれ、米国の時間外取引が
終わるまでの残り時間が1時間増える。cron 自体は UTC 基準なので変更不要だが、
「引け後の決算がまだ動き切っていない」度合いは季節で変わる。

**5. 休場日**

cron は日本の祝日も発火する。`morning.py` 側に `pick_date` の重複チェックがあるため
`picks_log.csv` が二重に記録されることはないが、セッション自体は起動する。
