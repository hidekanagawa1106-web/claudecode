# X API のセットアップ手順（振り返りの自動化・1日1回）

**目的はひとつだけ。** アナリティクスのスクショを撮って `/x-review` に渡す作業を消すこと。
取れる数字は月次のエクスポートCSVと同じなので、**買っているのは手間ゼロと鮮度だけ**です。

**費用: 月 $0.5〜2**（読み取り1件 $0.005 × 1日3〜13件）。月額の最低料金はありません。

所要時間は**30分〜1時間**。難所は開発者アカウントの審査だけです。

---

## 全体像

```
1. 開発者アカウント（審査あり）
2. 従量課金プランに登録（クレカ）
3. Project と App を作る
4. User authentication を有効化 ← ここを飛ばすとトークンが発行できません
5. キー4つを発行
6. 環境変数に入れて実行
7. 1日1回の自動実行に載せる
```

---

## 1. 開発者アカウントを作る

[developer.x.com](https://developer.x.com/) → Sign up。

**利用目的の記入欄があります。** 審査に通る書き方の要点:

- **自分のアカウントの分析であることを明記する。** 「自分の投稿のインプレッションと
  リンククリックを取得し、どの投稿形式が効果的かを分析する」で十分
- **他人のデータを扱わないと書く。** ここが一番見られます
- **X上に何も投稿しないと書く。** 読み取り専用だと明示すると通りやすい

英語で200〜300語。審査は数分〜数日です。

---

## 2. 従量課金プランに登録する

ポータルの Products → **Pay-per-use** を選んでクレジットカードを登録。

- 2026年2月から、**新規開発者はこれが唯一の選択肢**です（無料枠は廃止、
  Basic $200/月・Pro $5,000/月は既存契約者のみ）
- **月額の最低料金はありません。** 使った分だけ
- 読み取りは月200万件が上限（今回の用途では到達しません）

**上限アラートを設定しておいてください。** 想定は月$2なので、$10あたりで
通知が来るようにしておけば、実装ミスで暴走しても気づけます。

---

## 3. Project と App を作る

Projects & Apps → **Add App**。名前は何でも構いません（`nekosukexx-metrics` など）。

---

## 4. User authentication settings を有効化する ← **重要**

App の設定画面で **Set up** を押して、以下を入力します。

| 項目 | 値 |
|---|---|
| App permissions | **Read**（書き込みは不要。事故防止のため権限を最小に） |
| Type of App | Web App / Automated App or Bot |
| Callback URI | `http://localhost/` （使いませんが必須項目） |
| Website URL | 自分のXプロフィールURLで可 |

**この設定をしないと、次の手順で Access Token が発行できません。** ここが
一番よくある詰まりどころです。

---

## 5. キーを4つ発行する

**Keys and tokens** タブで、次の4つを取得します。

| キー | どこで | 備考 |
|---|---|---|
| API Key | Consumer Keys | |
| API Key Secret | Consumer Keys | |
| Access Token | Authentication Tokens → **Generate** | **手順4のあとに発行すること** |
| Access Token Secret | 同上 | |

**Access Token は手順4より前に発行すると Read-only の古い形式になります。**
先に発行してしまった場合は Regenerate してください。

**Secret は一度しか表示されません。** その場で控えてください。

> なぜ OAuth 2.0 ではなく 1.0a か: `non_public_metrics` は両方で取れますが、
> OAuth 2.0 (PKCE) はブラウザのリダイレクトとリフレッシュトークンの管理が要ります。
> **自分1人が自分のデータを取るだけなら 1.0a のほうが圧倒的に楽**です。

---

## 6. 動かす

```bash
pip install requests requests-oauthlib

export X_API_KEY='...'
export X_API_SECRET='...'
export X_ACCESS_TOKEN='...'
export X_ACCESS_SECRET='...'

python x/x_metrics.py --dry-run   # まず書き込まずに確認
python x/x_metrics.py             # posts.csv に反映
```

**`.env` に書く場合は必ず `.gitignore` に入れてください。** キーをコミットすると
X社に自動検知されて即失効します。

出力はこうなります。

```
対象: 2026-08-13（JSTの1日ぶん）
取得 5件（本文2 / 導線リプ1 / 交流リプ2＝捨てる）  概算コスト $0.025

日付          枠      imp     ♥   返   プロフ   リプimp  click  型
2026-08-13  朝     2,122    17    3      8        —      —  型3/型5 観察
2026-08-13  夜     1,840    22    5      6      210      3  型2 面接官
```

### 何を、いつ取るか

**既定は「3日前の、JSTの1日ぶんだけ」です。**

```
毎朝6:00に実行 → 3日前（例: 8/13）の 00:00〜24:00 JST に投稿したものを取る
```

**各投稿はちょうど72時間後に一度だけ取得されます。** インプレッションは
そこで頭打ちになるので、同じ投稿を取り直す必要がありません（＝重複課金しない）。

`posts.csv` にその日の行が既にあれば**API自体を呼びません**。二重実行しても
課金は発生しません（取り直したいときは `--force`）。

| | 扱い |
|---|---|
| 通常の投稿 | **取る** |
| **引用リポスト** | **取る**（APIは通常の投稿として返します） |
| 自分の投稿への自分のリプ＝導線リプ | **取る**。本文に紐づけて `link_clicks` に入れる |
| **他アカウントへの交流リプ** | **捨てる** |
| **リポスト** | **取らない**（`exclude=retweets` でAPI側で除外） |

**交流リプだけは、捨てるけれど課金は発生します。** APIには「自分への返信だけ」を
指定する方法がなく、返ってきたものを手元で振り分けるしかないためです。
実行ログに「交流リプ◯件＝捨てる」と出るので、いくら払っているかは見えます。

### コスト

| 1日の交流リプ | 取得件数 | 月額 |
|---|---|---|
| 2件 | 5件前後 | **$0.75** |
| 10件 | 13件前後 | **$2.0** |

### 取りこぼしを埋めるとき

```bash
python x/x_metrics.py --days 7      # 直近7日をまとめて取る
python x/x_metrics.py --offset 5    # 5日前の1日ぶん
python x/x_metrics.py --force       # 取得済みでも取り直す
```

## 7. GitHub Actions（予備・手動のみ）

**定期実行は下の Routine 側です。** ここは予備として手動実行だけ残してあります
（Routineが動かないときの切り分け、過去ぶんの取りこぼし埋め）。
使う場合だけ Secrets の登録が要ります。

### Secrets を4つ登録する

リポジトリの **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `X_API_KEY` | コンシューマーキー |
| `X_API_SECRET` | コンシューマーシークレット |
| `X_ACCESS_TOKEN` | アクセストークン |
| `X_ACCESS_SECRET` | アクセストークンシークレット |

**名前は完全に一致させてください**（大文字・アンダースコア）。

### 任意: ユーザーIDを変数に入れる

同じ画面の **Variables** タブで `X_USER_ID` を登録しておくと、毎回の
`/users/me`（$0.01/回 = 月$0.3）を節約できます。IDは初回実行のログに出ます。

### 初回は手動で回す

**Actions タブ → 「X実績の自動取得」→ Run workflow。**
スケジュールを待たずにテストできます。ログに取得件数と概算コストが出ます。

### 動いているかの確認

- 成功していれば `x/posts.csv` に自動コミットが入ります
- 変更が無い日はコミットされません（正常）
- 失敗するとGitHubからメールが届きます

## 代わりの置き場所

| 置き場所 | 向き |
|---|---|
| **Claude Code の Routine** | **採用。** 取得から winners.md の更新まで一気に通せる |
| GitHub Actions | Secretsは暗号化されるが、分析まで続けられない。手動用に残置 |
| ご自身のMac | `crontab -e` で `0 6 * * *`。Macが起動している必要あり |

## Claude Code の Routine に一本化する（採用した構成）

**取得から分析まで1つのRoutineで通します。** GitHub Actions は使いません。

### 前提: 環境変数はシークレットストアではない

公式ドキュメントには、こう書かれています。

> Anyone who uses the environment can read the values, and cloud environments
> have no dedicated secrets store, so don't add API keys or other credentials.
> — [Configure cloud environments](https://code.claude.com/docs/en/cloud-environments)

**それを承知のうえで環境変数に置く判断をしています**（2026-08-15・Hideさん）。
根拠は2つ:

- **個人アカウントで、環境を使うのは本人だけ**
- **キーの権限が Read のみ。** できるのは自分の投稿の数字を読むことだけで、
  投稿・削除・DMは一切できない。上限も月$2程度

**ただし、セッションを Public で共有しないでください。** Pro/Maxの共有設定には
Public があり、共有したセッションのログにキーが出ていると外から読めます。

### 環境変数を登録する

**claude.ai/code** を開き、**メッセージ入力欄の上の行にある雲アイコン**
（現在の環境名が出ているところ）を選びます。**設定ページや直リンクはありません。**

環境を編集して、環境変数の欄に `.env` 形式で1行ずつ貼ります。

```
X_API_KEY=（コンシューマーキー）
X_API_SECRET=（コンシューマーシークレット）
X_ACCESS_TOKEN=（アクセストークン）
X_ACCESS_SECRET=（アクセストークンシークレット）
```

- **クォートは不要**です（付けても外されます）
- **`#` を含む値だけはクォートで囲んでください**。囲まないとそこから
  後ろがコメント扱いで消えます
- `X_USER_ID` は初回実行のログに出るので、後から追記すれば `/users/me` の
  $0.01/回 が消えます

**値はセッション開始時に一度だけコピーされます。** 実行中のセッションには
反映されないので、**登録後は新しいセッションを開いてください。**

### Routine を作る

| 項目 | 値 |
|---|---|
| 名前 | X実績の記録と振り返り（毎朝6:00 JST） |
| cron | `0 21 * * *` （JST 6:00 = UTC 21:00） |
| 発火モード | 毎回あたらしいセッションを作る |
| 環境 | **環境変数を登録した環境**を選ぶ |
| プロンプト | **`x/docs/routine-metrics.txt` の全文** |

**cronはUTCで評価されます。** JST 6:00 は前日の 21:00 UTC。

## 動かなかったときは

| 症状 | 原因 |
|---|---|
| `401 Unauthorized` | キーの取り違え。**手順4の前に発行した Access Token** はよくある原因 |
| `non_public_metrics` が空 | **31日以上前の投稿**。この指標は直近30日のみ |
| `403 Forbidden` | 従量課金プランが未登録／App が Project に紐づいていない |
| `429 Too Many Requests` | 実行間隔が短すぎる。1日1回なら起きません |
| Actionsが `Permission denied` | ワークフローの `permissions: contents: write` を確認 |
| Secretsを入れたのに未設定エラー | 名前の綴り。`X_ACCESS_SECRET` を `X_ACCESS_TOKEN_SECRET` にしがち |

---

## 分かっておくべき制約

- **`non_public_metrics` は直近30日の投稿のみ。** それより古い実績は
  月次のエクスポートCSV（`analytics.py`）で拾います。**両方必要です**
- **他人の投稿の非公開指標は取れません。** 競合分析には使えない
- **自動リプ・自動投稿はしません。** 権限を Read にしてあるのはそのためです

---

## 出典

- [X API pay-per-usage pricing — X Docs](https://docs.x.com/x-api/getting-started/pricing)
- [X API Metrics — X Docs](https://docs.x.com/x-api/fundamentals/metrics)
- [X API Pricing 2026 — Postproxy](https://postproxy.dev/blog/x-api-pricing-2026/)
