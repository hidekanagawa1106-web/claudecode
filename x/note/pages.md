# 常設ページ（Artifact）

**リンクはずっと同じです。** 同じファイルを publish し直すと、このURLのまま中身が更新されます。

| ページ | URL | 元ファイル | 役割 |
|---|---|---|---|
| **X投稿の型カタログ** | https://claude.ai/code/artifact/07cd4541-91f0-495a-8285-8885749228a6 | `x/note/formats-page.html` | 型11種＋派生。実測の数字・急所・実物サンプル |
| **投稿ネタ帳** | https://claude.ai/code/artifact/151d965c-1430-460d-824a-ac9662d3057e | `x/note/claims-page.html` | 主張97件＋議論テーマ14件。検索と分類で絞れる |
| **ねこすけ運用ルール** | https://claude.ai/code/artifact/96284e5f-bf36-4234-82fc-b6d05c450bde | `x/note/rules-page.html` | 規定の索引。守ることの一覧 |

## 開き方

- **Claude Code のターミナル**: `/artifacts` で一覧。`o` で開く、`c` でリンクをコピー
- **ブラウザ**: https://claude.ai/code/artifacts
- このセッションで最後に出したページは **ctrl+]**

**claude.ai アプリの「アーティファクト」一覧とは別の棚です。** アプリ側の一覧
（`nikkei225_action_plan.md` などが並んでいるほう）にこの2ページは出てきません。
上のURLか `/artifacts` から開いてください。

## 更新のしかた

「型カタログを更新して」「ネタ帳を更新して」と言ってもらえれば、`x/note/*.html` を直して
同じURLに publish し直します。**ネタ帳は `claims.md` / `debates.md` から生成**しているので、
ネタを足したら再生成が要ります（`/tmp` の生成スクリプトは残らないため、そのとき書き直します）。
**新しいURLは作りません**（別リンクが増えると、どれが最新か分からなくなるので）。
