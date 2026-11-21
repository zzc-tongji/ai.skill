#!/usr/bin/env bash
# 新建一个 skill 的脚手架：生成 src/<name>/ 骨架 + 各平台空 head + 空资产目录。
# 用法：bash scripts/new-skill.sh <skill-name>
set -euo pipefail

NAME="${1:-}"
if [ -z "$NAME" ]; then
  echo "用法: bash scripts/new-skill.sh <skill-name>（kebab-case，小写无空格）" >&2
  exit 1
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SKILL="$REPO/src/$NAME"

if [ -e "$SKILL" ]; then
  echo "已存在: $SKILL" >&2
  exit 1
fi

mkdir -p "$SKILL/assets" "$SKILL/scripts"

cat > "$SKILL/SKILL.md" <<'EOF'
# <skill 标题>

（在这里写通用正文：用途、输入输出、步骤、示例。不要写 frontmatter，头放 variants/。）
EOF

for agent in qwen workbuddy claude codex; do
  H="$REPO/variants/$agent/$NAME.head.md"
  [ -d "$REPO/variants/$agent" ] && cat > "$H" <<EOF
---
name: $NAME
description: <一句话描述，含触发词>
---
EOF
done

echo "已创建 $SKILL"
echo "下一步：写正文 → 填各平台 head → git commit → python build.py"
