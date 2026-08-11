# -*- coding: utf-8 -*-
"""云端采集 + 成稿：调用 OpenAI 兼容大模型生成当日 news_<date>.json。

替代「人工 AI 联网搜索」这一步，使整条流水线可在 GitHub Actions 等云端全自动跑，
不再依赖本地 WorkBuddy 客户端与本机网络。

用法:
  python collect.py [YYYY-MM-DD]

环境变量:
  LLM_API_KEY    必填，大模型 API Key（Gemini / Kimi / DeepSeek 等皆可）
  LLM_BASE_URL   可选，兼容 /chat/completions 的基址
                 Gemini 默认 https://generativelanguage.googleapis.com/v1beta/openai/
                 Kimi 国内可达：https://api.moonshot.cn/v1（自带联网搜索，无需 Google）
  LLM_MODEL      可选，模型名（Gemini 默认 gemini-2.5-flash；Kimi 默认 kimi-k2.6）
  TAVILY_API_KEY 可选，配置了就改用 Tavily 限定白名单域名检索（最稳，免费 1000次/月）
  SEARCH_MODE    auto|tavily|kimi|none
                 auto：有 Tavily 用 Tavily，否则按 BASE_URL 自动判断（Gemini 走 googleSearch，Kimi 走 $web_search）
                 kimi：强制用 Kimi/Moonshot 内置联网搜索（国内用户首选，免 Google）
                 none：不联网（不推荐，新闻会失真）

约束（与本地流程完全一致）:
  - 来源必须是 config.json 的 source_whitelist 白名单
  - 每条摘要 40-60 字（含标点）
  - 5 个板块各 3-6 条
  - hotspot 6-12 条，site 在 hotlist_sites
  - interaction 仅 1 个 card，且卡型与上一期不同
  - 今日一问不得涉敏感题材，不得出现点赞/在看/转发/分享/抽奖/奖品
依赖: requests；lunar_python（可选，用于农历，缺失则留空）
"""
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]

# 白名单来源 -> 检索域名（Tavily include_domains 用，从根上锁死非白名单来源）
WL_DOMAINS = {
    "新华社": ["xinhuanet.com", "news.cn"],
    "新华网": ["xinhuanet.com", "news.cn"],
    "人民日报": ["people.com.cn"],
    "人民网": ["people.com.cn"],
    "央视新闻": ["cctv.com", "cntv.cn"],
    "央视网": ["cctv.com", "cntv.cn"],
    "中国新闻网": ["chinanews.com.cn"],
    "中新网": ["chinanews.com.cn"],
    "澎湃新闻": ["thepaper.cn"],
    "第一财经": ["yicai.com"],
    "证券时报": ["stcn.com"],
    "上海证券报": ["cnstock.com"],
    "经济日报": ["ce.cn"],
    "科技日报": ["stdaily.com"],
    "光明日报": ["gmw.cn"],
    "中国青年报": ["youth.cn"],
    "环球时报": ["huanqiu.com", "globaltimes.cn"],
    "IT之家": ["ithome.com"],
    "财联社": ["cailianpress.com"],
    "界面新闻": ["jiemian.com"],
    "国家统计局": ["stats.gov.cn"],
    "中国政府网": ["gov.cn"],
}
ALL_WL_DOMAINS = sorted({d for v in WL_DOMAINS.values() for d in v})
WHITELIST = list(WL_DOMAINS.keys())

DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-2.5-flash"


def load_cfg():
    with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def lunar_of(d):
    try:
        from lunar_python import Converter
        c = Converter.Lunar.fromSolar(d.year, d.month, d.day)
        return f"{'闰' if c.isLeap else ''}{c.month}月{c.day}日"
    except Exception:
        return ""


def prev_card_type(day):
    """读 output/ 下上一期 JSON，返回上期互动卡型（用于避免连续重复）。"""
    out = os.path.join(BASE, "output")
    if not os.path.isdir(out):
        return None
    days = []
    for fn in os.listdir(out):
        m = re.match(r"news_(\d{4}-\d{2}-\d{2})\.json$", fn)
        if m and m.group(1) < day:
            days.append(m.group(1))
    if not days:
        return None
    prev = max(days)
    try:
        d = json.load(open(os.path.join(out, f"news_{prev}.json"),
                           encoding="utf-8"))
    except Exception:
        return None
    card = (d.get("interaction") or {}).get("card") or {}
    return card.get("type")


# ---------------- 检索 ----------------
def tavily_search(query, api_key, max_results=5):
    import requests
    r = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "include_domains": ALL_WL_DOMAINS,
            "search_depth": "basic",
        },
        timeout=30,
    )
    r.raise_for_status()
    res = r.json().get("results", [])
    out = []
    for it in res:
        out.append(f"【{it.get('title','')}】{it.get('content','')}\n来源: {it.get('url','')}")
    return out


# ---------------- 大模型 ----------------
def llm_chat(system, user, base_url, api_key, model, tools=None):
    import requests
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    if tools:
        body["tools"] = tools
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        timeout=150,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# ---------------- Kimi / Moonshot 联网搜索 ----------------
def kimi_chat(system, user, base_url, api_key, model):
    """Kimi/Moonshot 内置 $web_search 工具联网搜索。

    与 Gemini googleSearch 不同，Kimi 的搜索需要多轮 tool_call 回传：
    模型先返回 tool_calls（含查询参数），客户端原样回传为 tool 消息，
    平台在服务端执行搜索后给出最终答复。使用 $web_search 必须禁用 thinking。
    """
    import requests
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    tools = [{"type": "builtin_function", "function": {"name": "$web_search"}}]
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    for _ in range(6):
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
            "tools": tools,
            "thinking": {"type": "disabled"},  # $web_search 必须禁用 thinking
        }
        r = requests.post(
            url,
            headers=headers,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            timeout=150,
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        if not msg.get("tool_calls"):
            return msg.get("content", "")
        # 回传 assistant(tool_calls) + 每个 tool 的结果（Kimi 要求原样回传 arguments）
        messages.append({
            "role": "assistant",
            "content": msg.get("content"),
            "tool_calls": msg.get("tool_calls"),
        })
        for tc in msg.get("tool_calls") or []:
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": tc["function"]["arguments"],
            })
    return None  # 超过轮次上限仍未出最终答案



# ---------------- 校验 ----------------
def validate(data, cfg):
    errors = []
    wl = set(cfg["source_whitelist"])
    cmin, cmax = cfg["item_char_min"], cfg["item_char_max"]
    names = {s["name"] for s in cfg["sections"]}

    for s in cfg["sections"]:
        items = next(
            (x.get("items", []) for x in data.get("sections", [])
             if x.get("name") == s["name"]),
            [],
        )
        if not (s["min"] <= len(items) <= s["max"]):
            errors.append(f"[{s['name']}] 条数 {len(items)} 不在 {s['min']}-{s['max']}")
        for i, it in enumerate(items, 1):
            t = it.get("text", "")
            n = len(t)
            if not (cmin <= n <= cmax):
                errors.append(
                    f"[{s['name']}] 第{i}条字数 {n}（要求 {cmin}-{cmax}）：{t[:18]}…"
                )
            if it.get("source", "") not in wl:
                errors.append(f"[{s['name']}] 第{i}条来源「{it.get('source')}」不在白名单")
        if s["name"] not in names:
            errors.append(f"板块名「{s['name']}」与配置不符")

    hot = data.get("hotspot") or {}
    hi = hot.get("items", [])
    if not (6 <= len(hi) <= 12):
        errors.append(f"[热点榜单] 条数 {len(hi)} 不在 6-12")
    sites = set(cfg.get("hotlist_sites", []))
    for i, it in enumerate(hi, 1):
        if it.get("site", "") not in sites:
            errors.append(f"[热点榜单] 第{i}条 site「{it.get('site')}」不在热榜站点")
        if not it.get("text"):
            errors.append(f"[热点榜单] 第{i}条为空")

    inter = data.get("interaction") or {}
    card = inter.get("card")
    if not card:
        errors.append("[互动] 缺少 card")
    else:
        if inter.get("cards"):
            errors.append("[互动] 同时存在 card 与 cards，请只保留单个 card")
        t = card.get("type")
        if t not in ("guess", "code", "fill", "echo", "stance"):
            errors.append(f"[互动] 卡型「{t}」非法")
        else:
            if not card.get("topic"):
                errors.append(f"[互动·{t}] 缺 topic")
            if t == "guess" and not card.get("unit"):
                errors.append("[互动·guess] 缺 unit")
            if t == "code" and (not card.get("format") or not card.get("example")):
                errors.append("[互动·code] 缺 format/example")
            if t == "fill" and not card.get("template"):
                errors.append("[互动·fill] 缺 template")
            if t == "stance" and (not card.get("left") or not card.get("right")):
                errors.append("[互动·stance] 缺 left/right")
            if t == "echo" and not card.get("answer"):
                errors.append("[互动·echo] 缺 answer")
    return errors


def audit_block(data, day):
    try:
        from audit import audit_data
        _, counts = audit_data(data, day)
        return counts.get("BLOCK", 0)
    except Exception:
        return 0


def build_prompt(day, cfg, prev_type, search_ctx, errors):
    d = date.fromisoformat(day)
    date_cn = f"{d.year}年{d.month}月{d.day}日 星期{WEEKDAYS[d.weekday()]}"
    sec_names = "、".join(s["name"] for s in cfg["sections"])
    wl = "、".join(cfg["source_whitelist"])
    sites = "、".join(cfg["hotlist_sites"])

    sys_prompt = (
        "你是一名资深新闻编辑，负责为中文微信公众号「报简说·每日消息早知道」"
        "生成每日早报的结构化数据。必须遵守以下铁律：\n"
        "1. 所有新闻事实只能引用白名单媒体，绝不使用白名单以外的任何来源"
        "（含境外媒体、自媒体、营销号）。白名单：" + wl + "。\n"
        "2. 每条新闻摘要严格 40–60 个汉字（含标点），用客观陈述句，不评论、不引申。\n"
        "3. 来源 source 必须是白名单中的某个媒体名，且确实报道过该事。\n"
        "4. 热点榜单 hotspot 每条只写话题标题（简短），标注 site（只能是热榜站点之一）。\n"
        "5. 每日微语 quote 须为原创或公版励志短句，不得抄袭任何「微语报」「早安语」原文。\n"
        "6. 互动板块 interaction 每期只出 1 个问题，靠读者打字参与，"
        "禁止出现「点赞/在看/转发/分享/抽奖/奖品」等词，绝不做成按钮。\n"
        "7. 今日一问的安全选题区：影视综艺票房收视、体育赛事竞猜、天气季节体感、"
        "饮食口味、出行见闻、老物件怀旧、科技产品体验、方言俗语。"
        "严禁拿时政外交、军事、灾情伤亡、民生政策抱怨（油价房价社保养老医保裁员物价）、"
        "投资荐股、医疗健康建议、点名个人是非、性别地域彩礼等群体对立话题来提问。\n"
        "8. 只输出符合指定 schema 的 JSON，不要任何解释文字。"
    )

    user_prompt = (
        f"请生成 {day}（{date_cn}）的日报数据。\n\n"
        f"五个板块（顺序与名称必须严格一致，每板块 3–6 条）：{sec_names}。\n"
        f"各板块检索关键词建议：国际新闻=国际 外交部 环球；财经动态=财经 股市 央行；"
        f"科技前沿=科技 AI 芯片；社会民生=社会 民生 政策；文体资讯=文体 影视 体育。\n\n"
        f"热点榜单站点（site 只能取这些）：{sites}。\n\n"
        f"上一期互动卡型是「{prev_type or '无'}」，本期必须换一种卡型"
        f"（可选：guess 盲猜 / code 打卡 / fill 填空 / echo 上期揭晓 / stance 站队）。\n\n"
    )
    if search_ctx:
        user_prompt += (
            "以下是联网检索到的白名单媒体素材（仅可据此成稿，不得引用素材之外的信息）：\n"
            + "\n\n".join(search_ctx)[:6000]
            + "\n\n"
        )
    user_prompt += (
        "输出 JSON schema（严格照此，字段名勿改）：\n"
        "{\n"
        '  "greeting": "一句早安问候（≤20字）",\n'
        '  "quote": "原创/公版励志微语（≤30字）",\n'
        '  "sections": [\n'
        '    {"name": "国际新闻", "items": [{"text": "40-60字摘要", "source": "央视新闻"}]},\n'
        '    {"name": "财经动态", "items": [...]},\n'
        '    {"name": "科技前沿", "items": [...]},\n'
        '    {"name": "社会民生", "items": [...]},\n'
        '    {"name": "文体资讯", "items": [...]}\n'
        "  ],\n"
        '  "hotspot": {"name": "热点榜单", "items": [{"text": "话题标题", "site": "微博热搜"}]},\n'
        '  "interaction": {\n'
        '    "title": "今日一问",\n'
        '    "lead": "引出问题的引导语（≤30字）",\n'
        '    "card": {"type": "fill", "topic": "问题主题", "template": "填空模板含下划线空", "hint": "参与提示"},\n'
        '    "closing": "收尾语（≤20字）"\n'
        "  }\n"
        "}\n\n"
        "卡型字段完整示例：\n"
        '- guess: {"type":"guess","topic":"…","unit":"亿元","hint":"…"}\n'
        '- code:  {"type":"code","topic":"…","format":"打卡格式","example":"示例一行","hint":"…"}\n'
        '- fill:  {"type":"fill","topic":"…","template":"我家乡在__，今天__度。","hint":"…"}\n'
        '- echo:  {"type":"echo","topic":"上期答案揭晓","answer":"上期正确答案是…","note":"…"}\n'
        '- stance:{"type":"stance","topic":"…","left":"甲","right":"乙","hint":"…"}\n'
    )
    if errors:
        user_prompt += (
            "\n上一次生成未通过校验，请修正以下问题后重新输出：\n- "
            + "\n- ".join(errors)
            + "\n"
        )
    return sys_prompt, user_prompt


def main():
    tz = timezone(timedelta(hours=8))
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(tz).strftime("%Y-%m-%d")
    cfg = load_cfg()
    out_path = os.path.join(BASE, "output", f"news_{day}.json")

    # 幂等：当日已存在且通过审核则跳过，避免重复生成/重复推送
    if os.path.exists(out_path):
        try:
            existing = json.load(open(out_path, encoding="utf-8"))
            if validate(existing, cfg) == [] and audit_block(existing, day) == 0:
                print(f"SKIP: {out_path} 已存在且合规，跳过采集")
                return
        except Exception:
            pass

    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key:
        print("ERROR: 未设置 LLM_API_KEY，无法在云端采集。请配置 Secrets.LLM_API_KEY")
        sys.exit(1)
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE).rstrip("/") + "/"
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    search_mode = os.environ.get("SEARCH_MODE", "auto").lower()

    prev_type = prev_card_type(day)
    print(f"采集 {day}（上期卡型={prev_type or '无'}）模型={model}")

    search_ctx = []
    use_tavily = (search_mode == "tavily") or (tavily_key and search_mode != "none")
    use_kimi = (search_mode == "kimi") or (
        "moonshot" in base_url and not use_tavily and search_mode != "none"
    )

    if use_kimi:
        if model == DEFAULT_MODEL:  # 用户未显式指定模型时给 Kimi 默认
            model = "kimi-k2.6"
        print(f"检索模式：Kimi/Moonshot 内置联网搜索（$web_search）模型={model}")
    elif use_tavily:
        print("检索模式：Tavily（限定白名单域名）")
        queries = [
            "今日国际新闻 新华社 央视", "今日财经动态 证券时报 第一财经",
            "今日科技前沿 AI 芯片 科技日报", "今日社会民生 澎湃新闻",
            "今日文体资讯 影视 体育", "今日热搜 微博 抖音 百度",
        ]
        for q in queries:
            try:
                search_ctx += tavily_search(q, tavily_key, max_results=4)
            except Exception as e:
                print("  Tavily 检索失败:", e)
    else:
        print("检索模式：Gemini 联网搜索（googleSearch grounding）")

    tools = None
    if not use_tavily and not use_kimi and "googleapis.com" in base_url and search_mode != "none":
        tools = [{"googleSearch": {}}]

    errors = []
    data = None
    for attempt in range(3):
        sys_p, usr_p = build_prompt(day, cfg, prev_type, search_ctx, errors)
        print(f"第 {attempt+1} 次生成…")
        try:
            if use_kimi:
                raw = kimi_chat(sys_p, usr_p, base_url, api_key, model)
            else:
                raw = llm_chat(sys_p, usr_p, base_url, api_key, model, tools)
            # 容错：去掉可能包裹的 ```json ``` 标记
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                raw = m.group(0)
            cand = json.loads(raw)
        except Exception as e:
            print("  解析失败:", e)
            errors = [f"模型返回无法解析为 JSON：{str(e)[:80]}"]
            continue

        errors = validate(cand, cfg)
        if errors:
            print("  校验未过：", errors[0])
            continue
        if audit_block(cand, day) > 0:
            errors = ["内容触发合规 BLOCK，请改用更中性的表述后重试"]
            print("  合规 BLOCK，重试")
            continue
        data = cand
        break

    if not data:
        print("ERROR: 3 次尝试仍未生成合规内容：", errors)
        sys.exit(1)

    # 农历由代码计算，避免模型误差
    d = date.fromisoformat(day)
    data["date"] = day
    data["lunar"] = lunar_of(d)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"OK: 已生成 {out_path}")
    print(f"     板块: " + "，".join(
        f"{s['name']}{len(s['items'])}条" for s in data["sections"]))
    print(f"     热点: {len(data['hotspot']['items'])} 条；"
          f"今日一问卡型: {data['interaction']['card']['type']}")


if __name__ == "__main__":
    main()
