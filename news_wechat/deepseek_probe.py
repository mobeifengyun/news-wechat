#!/usr/bin/env python3
"""本地诊断：验证 DeepSeek 原生 web_search（/responses 端点）能否返回中文新闻。

用法（PowerShell）：
  $env:LLM_API_KEY = "sk-xxx"
  $env:LLM_BASE_URL = "https://api.deepseek.com"
  $env:LLM_MODEL = "deepseek-v4-flash"
  python deepseek_probe.py

用途：先确认 DeepSeek 服务端搜索能返回中文新闻素材，再跑 GitHub Actions 全链路，
避免盲目跑全链路白扣费。只需要 DeepSeek key，不依赖 Tavily。
"""
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("请先安装依赖：python -m pip install requests")

API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
if not API_KEY:
    sys.exit("未设置 LLM_API_KEY / DEEPSEEK_API_KEY 环境变量")

BASE = (os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com").rstrip("/")
if BASE.endswith("/v1"):
    BASE = BASE[:-3]
URL = BASE + "/responses"
MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-flash")

body = {
    "model": MODEL,
    "input": [
        {"role": "system", "content": "你是新闻编辑。必须使用联网搜索获取真实新闻，不要凭记忆编造。"},
        {"role": "user", "content": "搜索 2026年8月25日 中国国内重要新闻（新华社/央视/人民日报），列出3条，每条含标题、来源、一句话摘要。"},
    ],
    "tools": [{"type": "web_search"}],
    "tool_choice": {"type": "web_search"},
    "max_tokens": 1024,
}

print("=" * 60)
print(f"DeepSeek 原生 web_search 诊断")
print(f"  URL   : {URL}")
print(f"  MODEL : {MODEL}")
print("=" * 60)

try:
    r = requests.post(
        URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    print(f"\nHTTP {r.status_code}")
    if r.status_code != 200:
        print("响应:", r.text[:500])
        sys.exit(1)
    resp = r.json()
    text = resp.get("output_text") or ""
    if not text:
        for item in resp.get("output", []) or []:
            for c in item.get("content", []) or []:
                if c.get("type") == "output_text" and c.get("text"):
                    text += c.get("text", "")
    print(f"返回 {len(text)} 字符")
    print("-" * 60)
    print(text[:800])
    print("-" * 60)
    if len(text.strip()) > 30:
        print("✅ DeepSeek 原生搜索可用：可切 SEARCH_MODE=deepseek 跑全链路")
    else:
        print("❌ 返回内容过短，可能搜索未生效，请检查模型/账户")
except Exception as e:
    print(f"异常: {e}")
    sys.exit(1)
