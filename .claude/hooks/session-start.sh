#!/bin/bash
# セッション開始時に実行に必要なパッケージを入れる。
#
# 定期実行(平日8:00のRoutine)は毎回まっさらなコンテナで走るため、
# これが無いと morning.py が ModuleNotFoundError: No module named 'pandas' で
# 即座に落ちる。実際に 2026-08-03 の初回実行がこれで失敗した。
#
# 同期実行にしてある。非同期だとセッション起動は速くなるが、
# インストール完了前に morning.py が走り出す競合が起きうる。
# このジョブは寄り付き(9:00)まで1時間の余裕があるので、確実さを優先する。
set -euo pipefail

# ローカル環境には触らない。web/リモートのセッションだけが対象。
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

if [ -f requirements.txt ]; then
  python3 -m pip install --quiet --disable-pip-version-check \
    --root-user-action=ignore -r requirements.txt
fi
