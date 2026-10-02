# 常設ページ（Artifact）

**リンクはずっと同じです。** 同じファイルを publish し直すと、このURLのまま中身が更新されます。

| ページ | URL | 元ファイル | 役割 |
|---|---|---|---|
| **ぜんぶの入口**（ハブ） | https://claude.ai/artifact/M978xiH4iSX8S8gR38UKtf | `x/note/hub.html` | 全ページへのリンク。**スマホのホーム画面に置く用** |
| **X投稿の型カタログ** | https://claude.ai/code/artifact/07cd4541-91f0-495a-8285-8885749228a6 | `x/note/formats-page.html` | 現役7型＋廃止3型。実測の数字・急所・実物サンプル |
| **上司の理不尽** | https://claude.ai/artifact/G1UcHeiY4CfVnwLjWu1KMx | `x/note/absurd-page.html`（`build_absurd_page.py` が `x/absurd.md` から生成） | あるある系の素材92件。型12/型11/型1の印で絞れる |
| **投稿ネタ帳** | https://claude.ai/code/artifact/151d965c-1430-460d-824a-ac9662d3057e | `x/note/claims-page.html` | 主張102件＋議論テーマ14件。検索と分類で絞れる |
| **ねこすけ運用ルール** | https://claude.ai/code/artifact/96284e5f-bf36-4234-82fc-b6d05c450bde | `x/note/rules-page.html` | 規定の索引。守ることの一覧 |

## 開き方

- **Claude Code のターミナル**: `/artifacts` で一覧。`o` で開く、`c` でリンクをコピー
- **ブラウザ**: https://claude.ai/code/artifacts
- このセッションで最後に出したページは **ctrl+]**

**claude.ai アプリの「アーティファクト」一覧とは別の棚です。** アプリ側の一覧
（`nikkei225_action_plan.md` などが並んでいるほう）にこれらのページは出てきません。
上のURLか `/artifacts` から開いてください。

**スマホからは、ハブをホーム画面に追加するのが一番早いです。**
スマホのブラウザ（Safari / Chrome）でハブを開く → 共有 → 「ホーム画面に追加」。
URLは変わらないので、中身を更新してもアイコンは置きっぱなしで大丈夫です。
**ページを増やしたらハブにも1枚足すこと**（足し忘れるとホーム画面から辿れません）。

## 更新のしかた

「型カタログを更新して」「ネタ帳を更新して」と言ってもらえれば、`x/note/*.html` を直して
同じURLに publish し直します。**ネタ帳は `claims.md` / `debates.md` から、理不尽一覧は `x/absurd.md` から生成**しています。
理不尽一覧は `python3 x/note/build_absurd_page.py` を流し直すだけで作り直せます
（スクリプトをリポジトリに置いてあるので、次のセッションでも同じ手順です）。
**新しいURLは作りません**（別リンクが増えると、どれが最新か分からなくなるので）。
