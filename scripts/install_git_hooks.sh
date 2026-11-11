#!/bin/sh
# 启用仓库内提交钩子（每个克隆执行一次）。
set -e
cd "$(git rev-parse --show-toplevel)"
chmod +x .githooks/pre-commit
git config core.hooksPath .githooks
echo "已启用 .githooks/pre-commit（提交前校验 9 处版本面）"
