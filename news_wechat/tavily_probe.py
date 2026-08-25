#!/usr/bin/env python3
"""本地诊断：验证 Tavily 中文新闻召回是否可用（不消耗 DeepSeek 费用）。

用法（Windows PowerShell）：
  $env:TAVILY_API_KEY = "tvly-xxx"
  python tavily_probe.py

用法（bash）：
  TAVILY_API_KEY=tvly-xxx python tavily_probe.py

目的：先确认 Tavily 能在「最近24h + 中文」条件下返回真实新闻，
再跑 GitHub Actions 全链路，避免 DeepSeek 因素材为空而白扣费。
"""
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("请先安装依赖：python -m pip install requests")

API_KEY = os.environ.get("TAVILY_API_KEY")
if not API_KEY:
    sys.exit("未设置 TAVILY_API_KEY 环境变量")

QUERIES = [
    "2026年8月25日 国内要闻",
    "2026年8月25日 国际新闻",
    "2026年8月25日 财经 经济",
    "2026年8月25日 科技",
]

# 与 collect.py 完全一致的检索配置（铁律：24h + 中文定向）
BASE = {
    "api_key": API_KEY,
    "max_results": 3,
    "search_depth": "advanced",
    "topic": "news",
    "time_range": "day",
    "country": "china",
    "language": "zh-cn",
    "filter_by_language": True,
}

WL_HINT = ["xinhuanet.com", "people.com.cn", "cctv.com", "chinanews.com.cn",
           "thepaper.cn", "yicai.com", "stcn.com", "huanqiu.com", "gov.cn"]


def probe(payload):
    r = requests.post("https://api.tavily.com/search", json=payload, timeout=30)
    return r


print("=" * 60)
print("Tavily 中文新闻召回诊断")
print("=" * 60)

all_ok = True
for q in QUERIES:
    payload = {**BASE, "query": q}
    print(f"\n▶ 查询: {q}")
    try:
        r = probe(payload)
        print(f"  HTTP {r.status_code}")
        if r.status_code != 200:
            all_ok = False
            print("  响应:", r.text[:300])
            continue
        res = r.json().get("results", [])
        print(f"  返回 {len(res)} 条")
        for it in res[:3]:
            print(f"   - {it.get('title', '')[:36]} | {it.get('url', '')[:50]}")
        if not res:
            all_ok = False
    except Exception as e:
        all_ok = False
        print(f"  异常: {e}")

print("\n" + "=" * 60)
if all_ok:
    print("✅ 全绿：Tavily 中文 24h 召回升，可跑 GitHub Actions 全链路")
else:
    print("❌ 仍有 0 条：说明 Tavily 免费档中文召回不可靠")
    print("   建议升级方案：改用 DeepSeek 原生 web_search（去掉 Tavily 依赖）")
    print("   或换 Brave/SearXNG 等检索后端")
print("=" * 60)
