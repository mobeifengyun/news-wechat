#!/bin/bash
# 本地验证 DeepSeek 原生搜索：从 key.txt 读取 key（避免终端粘贴麻烦）
# 用法：在 Git Bash 中执行  bash run_probe.sh

set -e

# 自动定位 python（优先 WorkBuddy 内置、再系统 PATH）
PYTHON=""
for p in /c/Users/jhon/.workbuddy/binaries/python/versions/*/python.exe /c/Users/jhon/.workbuddy/binaries/python/versions/*/python "$(which python 2>/dev/null)" "$(which python3 2>/dev/null)"; do
  if [ -x "$p" ] && "$p" --version >/dev/null 2>&1; then
    PYTHON="$p"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "找不到 python，请确认已安装 Python"
  exit 1
fi
echo "使用 python: $PYTHON"

if [ -f key.txt ]; then
  export LLM_API_KEY="$(head -n1 key.txt | tr -d '\r')"
elif [ -f key.txt.txt ]; then
  # Windows 隐藏扩展名时容易建成 key.txt.txt
  echo "注意：你创建的是 key.txt.txt，将自动读取它"
  export LLM_API_KEY="$(head -n1 key.txt.txt | tr -d '\r')"
fi
if [ -z "$LLM_API_KEY" ]; then
  echo "请先把 DeepSeek key 写入 key.txt（或 export LLM_API_KEY=...）"
  exit 1
fi
export LLM_BASE_URL="https://api.deepseek.com"
export LLM_MODEL="deepseek-v4-flash"
"$PYTHON" deepseek_probe.py
