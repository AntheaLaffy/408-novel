#!/usr/bin/env bash
# 重建站点与 EPUB，提交 docs/ 并推送。用法：tools/publish.sh "更新第二卷"
set -euo pipefail
cd "$(dirname "$0")/.."

message="${1:-更新正文}"

# 文档与正文对不上就先停下（章号、卷区间、生成块）
python3 tools/status.py --check
python3 tools/build_site.py \
  --out docs \
  --base /408-novel/ \
  --site-url https://anthealaffy.github.io/408-novel/

git add -A
if git diff --cached --quiet; then
  echo "没有需要发布的变化。"
  exit 0
fi
git commit -m "$message"
git push
