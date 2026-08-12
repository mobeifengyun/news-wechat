# -*- coding: utf-8 -*-
"""ServerChan 推送（必须用 requests + UTF-8，Windows 下用 curl 会因 GBK 编码报 30001）。

用法:
  python push_sc.py <手机页链接> [YYYY-MM-DD]
若省略日期，默认今天。
审核计数与发布状态均动态计算，不写死。
"""
import json
import os
import sys
from datetime import date

import requests

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) < 2:
        print("用法: python push_sc.py <手机页链接> [YYYY-MM-DD]")
        sys.exit(1)
    url = sys.argv[1]
    day = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
    # 每天 URL 加日期参数，避免微信内置浏览器直接复用前一天缓存
    if "?" in url:
        url = f"{url}&d={day.replace('-', '')}"
    else:
        url = f"{url}?d={day.replace('-', '')}"

    with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 优先用环境变量 SCKEY（云端 Secrets 注入），缺失时回退 config.json
    key = os.environ.get("SCKEY", "") or (cfg.get("wechat") or {}).get("sckey", "")
    if not key or key.startswith("请填入"):
        print("SKIP: 未配置 sckey")
        return

    with open(os.path.join(BASE, "output", f"news_{day}.json"), "r",
              encoding="utf-8") as f:
        data = json.load(f)

    # 动态审核计数
    try:
        from audit import audit_data, format_report
        findings, counts = audit_data(data, day)
        audit_str = f"BLOCK={counts.get('BLOCK',0)} WARN={counts.get('WARN',0)} NOTE={counts.get('NOTE',0)}"
    except Exception:
        audit_str = "（审核模块不可用）"

    # 发布状态
    appid = (cfg.get("wechat") or {}).get("appid", "")
    if not appid or "请填入" in appid:
        pub_str = "跳过（config.json 的 AppID/AppSecret 仍为占位符，未配置公众号群发）"
    else:
        pub_str = "已尝试群发（见 Actions 日志）；若失败多为 IP 白名单未加"

    counts_txt = "，".join(
        f"{s['name']}{len(s['items'])}条" for s in data["sections"]
    )
    hot = len((data.get("hotspot") or {}).get("items", []))
    card = (data.get("interaction") or {}).get("card", {})

    desp = "\n\n".join([
        f"**{day} 农历{data.get('lunar','')}**",
        f"[点这里打开手机端发布助手页]({url})",
        f"**板块**：{counts_txt}；热点榜单 {hot} 条",
        f"**今日一问**（{card.get('type','')} 卡）：{card.get('topic','')}",
        f"**审核**：{audit_str}",
        f"**公众号 API 发布**：{pub_str}",
    ])
    r = requests.post(
        f"https://sctapi.ftqq.com/{key}.send",
        data={"title": f"报简说 | 每日消息早知道（{day}）", "desp": desp},
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        timeout=20,
    )
    print(r.status_code, r.text[:300])


if __name__ == "__main__":
    main()
