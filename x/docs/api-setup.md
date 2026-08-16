# X API のセットアップ手順（振り返りの自動化・1日1回）

**目的はひとつだけ。** アナリティクスのスクショを撮って `/x-review` に渡す作業を消すこと。
取れる数字は月次のエクスポートCSVと同じなので、**買っているのは手間ゼロと鮮度だけ**です。

**費用: 月 $1.5 前後**（読み取り1件 $0.005 × 1日10件前後）。月額の最低料金はありません。

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

python x/x_metrics.py --days 3 --dry-run   # まず書き込まずに確認
python x/x_metrics.py --days 3             # posts.csv に反映
```

**`.env` に書く場合は必ず `.gitignore` に入れてください。** キーをコミットすると
X社に自動検知されて即失効します。

出力はこうなります。

```
取得 6件（本文4 / 導線リプ2）  概算コスト $0.030

日付          枠      imp     ♥   返   プロフ   リプimp  click  型
2026-08-14  朝     2,122    17    3      8        —      —  型3/型5 観察
2026-08-15  朝     1,840    22    5      6      210      3  型2 面接官
```

### `--days` の決め方

**既定は3日です。** インプレッションは72時間ほど伸び続けるので、
毎日3日ぶんを取り直して上書きするのが正しい挙動になります。

`--days 3` なら1日あたり6〜10件 = **月$0.9〜1.5**。

---

## 7. 1日1回の自動実行に載せる

**夜（22時ごろ）がおすすめ**です。朝6時台に投稿しているので、当日ぶんが
16時間経った状態で取れます。

cron の例（JST 22:00 = UTC 13:00）:

```
0 13 * * * cd /path/to/repo && python x/x_metrics.py --days 3 >> x/data/metrics.log 2>&1
```

Claude Code の Routine に載せる場合は、実行後に `python x/x_review.py` の集計と
`winners.md` の更新まで続けさせると、**ループが完全に閉じます。**

---

## 動かなかったときは

| 症状 | 原因 |
|---|---|
| `401 Unauthorized` | キーの取り違え。**手順4の前に発行した Access Token** はよくある原因 |
| `non_public_metrics` が空 | **31日以上前の投稿**。この指標は直近30日のみ |
| `403 Forbidden` | 従量課金プランが未登録／App が Project に紐づいていない |
| `429 Too Many Requests` | 実行間隔が短すぎる。1日1回なら起きません |

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
