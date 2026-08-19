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
  - 每条摘要 40-55 字（含标点；validate 仍按 28-60 留缓冲）
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
import time
from datetime import date, datetime, timedelta, timezone

# GitHub Actions 等非 TTY 环境下强制行缓冲，确保 print 实时可见
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

BASE = os.path.dirname(os.path.abspath(__file__))
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]

# 白名单来源 -> 检索域名（Tavily include_domains 用，从根上锁死非白名单来源）
# 覆盖范围刻意做宽：央媒（新华社/人民日报/央视/总台/央广/国际在线）+ 财经（一财/证券时报/每经/21世纪/中证报/经观）
# + 科技（科技日报/IT之家/钛媒体/虎嗅/雷锋网/量子位/36氪）+ 社会（澎湃/新京报/北京日报/上观/南方+/上游）
# + 文体（中国体育报/文旅部）+ 民生（健康报/中国教育报/农民日报/教育部）等，减少「新闻遗漏」。
WL_DOMAINS = {
    "新华社": ["xinhuanet.com", "news.cn"],
    "新华网": ["xinhuanet.com", "news.cn"],
    "人民日报": ["people.com.cn"],
    "人民网": ["people.com.cn"],
    "央视新闻": ["cctv.com", "cntv.cn", "cctv.cn"],
    "央视网": ["cctv.com", "cntv.cn", "cctv.cn"],
    "中央广播电视总台": ["cctv.com", "cntv.cn", "cctv.cn"],
    "朝闻天下": ["cctv.com", "cntv.cn", "cctv.cn"],
    "央广网": ["cnr.cn"],
    "国际在线": ["cri.cn"],
    "中国新闻网": ["chinanews.com.cn"],
    "中新网": ["chinanews.com.cn"],
    "中国日报": ["chinadaily.com.cn"],
    "参考消息": ["cankaoxiaoxi.com"],
    "澎湃新闻": ["thepaper.cn"],
    "第一财经": ["yicai.com"],
    "证券时报": ["stcn.com"],
    "上海证券报": ["cnstock.com"],
    "经济日报": ["ce.cn"],
    "科技日报": ["stdaily.com"],
    "光明日报": ["gmw.cn"],
    "中国青年报": ["youth.cn"],
    "环球时报": ["huanqiu.com", "globaltimes.cn"],
    "环球网": ["huanqiu.com", "globaltimes.cn"],
    "IT之家": ["ithome.com"],
    "财联社": ["cailianpress.com"],
    "界面新闻": ["jiemian.com"],
    "中新经纬": ["jwview.com"],
    "21世纪经济报道": ["21jingji.com"],
    "每日经济新闻": ["nbd.com.cn"],
    "新京报": ["bjnews.com.cn"],
    "北京日报": ["beijingdaily.com.cn"],
    "上观新闻": ["jfdaily.com"],
    "南方plus": ["southcn.com"],
    "上游新闻": ["cqcb.com"],
    "钛媒体": ["tmtpost.com"],
    "虎嗅": ["huxiu.com"],
    "雷锋网": ["leiphone.com"],
    "量子位": ["qbitai.com"],
    "36氪": ["36kr.com"],
    "中国证券报": ["cs.com.cn"],
    "经济观察报": ["eeo.com.cn"],
    "国家体育总局": ["sport.gov.cn"],
    "中国体育报": ["sports.cn"],
    "文旅部": ["mct.gov.cn"],
    "教育部": ["moe.gov.cn"],
    "健康报": ["jkb.com.cn"],
    "中国教育报": ["jyb.cn"],
    "农民日报": ["farmer.com.cn"],
    "国家统计局": ["stats.gov.cn"],
    "中国政府网": ["gov.cn"],
}
ALL_WL_DOMAINS = sorted({d for v in WL_DOMAINS.values() for d in v})
WHITELIST = list(WL_DOMAINS.keys())

# ---------------- 节日 / 节气识别（驱动「今日一问」应景出题） ----------------
# 优先用 lunar_python 拿精确节气与农历节日；本地或无该库时回退到内置公历节日表 + 节气近似表。
GREGORIAN_FESTIVALS = {
    (1, 1): "元旦", (2, 14): "情人节", (3, 8): "妇女节", (3, 12): "植树节",
    (3, 15): "消费者权益日", (4, 5): "清明节", (5, 1): "劳动节", (5, 4): "青年节",
    (6, 1): "儿童节", (7, 1): "建党节", (8, 1): "建军节", (9, 3): "抗战胜利日",
    (9, 10): "教师节", (10, 1): "国庆节", (12, 24): "平安夜", (12, 25): "圣诞节",
}
# 24 节气公历近似日期（21 世纪常用值，个别年份 ±1 天；仅作无 lunar_python 时的兜底）
JIEQI_APPROX = {
    "小寒": (1, 6), "大寒": (1, 20), "立春": (2, 4), "雨水": (2, 19),
    "惊蛰": (3, 6), "春分": (3, 21), "清明": (4, 5), "谷雨": (4, 20),
    "立夏": (5, 6), "小满": (5, 21), "芒种": (6, 6), "夏至": (6, 21),
    "小暑": (7, 7), "大暑": (7, 23), "立秋": (8, 8), "处暑": (8, 23),
    "白露": (9, 8), "秋分": (9, 23), "寒露": (10, 8), "霜降": (10, 24),
    "立冬": (11, 8), "小雪": (11, 22), "大雪": (12, 7), "冬至": (12, 22),
}


def festival_of(d):
    """返回当天节日/节气名（可能多个，用「、」连接）；无则返回空串。"""
    names = []
    # 浮动节日（任何情况下都计算）：母亲节(5月第2周日)/父亲节(6月第3周日)/感恩节(11月第4周四)
    if d.month == 5 and d.weekday() == 6 and 8 <= d.day <= 14:
        names.append("母亲节")
    if d.month == 6 and d.weekday() == 6 and 15 <= d.day <= 21:
        names.append("父亲节")
    if d.month == 11 and d.weekday() == 3 and 22 <= d.day <= 28:
        names.append("感恩节")
    try:
        from lunar_python import Lunar, Solar
        solar = Solar.fromYmd(d.year, d.month, d.day)
        l = Lunar.fromSolar(solar)
        try:
            jq = l.getJieQi()
            if jq:
                names.append(jq)
        except Exception:
            pass
        try:
            for f in (l.getFestivals() or []):
                if f:
                    names.append(f)
        except Exception:
            pass
        try:
            for f in (solar.getFestivals() or []):
                if f:
                    names.append(f)
        except Exception:
            pass
    except Exception:
        # 库缺失：回退内置公历节日表 + 近似节气表
        g = GREGORIAN_FESTIVALS.get((d.month, d.day))
        if g:
            names.append(g)
        for nm, (m, dd) in JIEQI_APPROX.items():
            if (m, dd) == (d.month, d.day):
                names.append(nm)
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return "、".join(out)


# 用户手动喂入的「公众号参考素材」目录（仅作选题启发，非信源）
SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seeds")


def load_seed(day):
    """读取当日公众号参考素材。优先级：seeds/<date>.txt > seeds/latest.txt。
    找不到或为空则返回空串（采集流程降级为纯白名单模式，不报错）。"""
    cands = [
        os.path.join(SEED_DIR, f"{day}.txt"),
        os.path.join(SEED_DIR, "latest.txt"),
    ]
    for p in cands:
        if os.path.exists(p):
            try:
                txt = open(p, encoding="utf-8").read().strip()
                if txt:
                    return txt
            except Exception:
                pass
    return ""


DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODEL = "gemini-2.5-flash"


def load_cfg():
    with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def lunar_of(d):
    try:
        from lunar_python import Lunar, Solar
        l = Lunar.fromSolar(Solar.fromYmd(d.year, d.month, d.day))
        m, day = l.getMonth(), l.getDay()
        leap = ""
        try:
            if l.getLeapMonth() == m:
                leap = "闰"
        except Exception:
            pass
        return f"{leap}{m}月{day}日"
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
def tavily_search(query, api_key, max_results=5, days=2):
    import requests
    r = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "include_domains": ALL_WL_DOMAINS,
            "search_depth": "advanced",
            "topic": "news",
            "days": days,
            "time_range": "day",
            "include_raw_content": True,
        },
        timeout=30,
    )
    r.raise_for_status()
    res = r.json().get("results", [])
    out = []
    for it in res:
        # content（摘要）通常比 raw_content（全文）干净，优先使用；
        # 仅当摘要过短（<80 字）时才补充 raw_content 前段，避免把整页导航/UI 垃圾倒进来。
        content = (it.get("content") or "").strip()
        raw = (it.get("raw_content") or "").strip()
        body = content
        if len(body) < 80 and raw and raw != content:
            body += "\n" + raw[:800]
        body = body.strip()
        # 太长则截断，避免上下文爆炸
        if len(body) > 1200:
            body = body[:1200]
        out.append(f"【{it.get('title','')}】{body}\n来源: {it.get('url','')}")
    return out


# ---------------- 大模型 ----------------
def _normalize_llm_url(base_url, model):
    """把用户可能写错的 base_url 自动补全为正确的 OpenAI 兼容地址。"""
    import re
    base = base_url.rstrip("/")
    # 如果用户把完整 /chat/completions 都填进来了，先剥掉
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")].rstrip("/")
    # DeepSeek
    if "deepseek" in base:
        if not base.endswith("/v1"):
            base = re.sub(r"/v1/?$", "", base).rstrip("/") + "/v1"
        return base
    # Kimi/Moonshot
    if "moonshot" in base:
        if not base.endswith("/v1"):
            base = re.sub(r"/v1/?$", "", base).rstrip("/") + "/v1"
        return base
    # Gemini OpenAI 兼容接口
    if "googleapis" in base or "generativelanguage" in base:
        if not base.endswith("/openai"):
            base = re.sub(r"/v1beta/?$", "", base).rstrip("/") + "/v1beta/openai"
        return base
    return base


class LLMAuthError(Exception):
    """鉴权或额度类错误（401/402/403）：账号不可用，应立即降级、无需重试。"""


def llm_chat(system, user, base_url, api_key, model, tools=None):
    import requests
    base_url = _normalize_llm_url(base_url, model)
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    # DeepSeek reasoner / 思考类模型通常不支持 response_format，避免 400
    # OpenRouter 免费层(:free) 对 json_object 支持不稳定，也跳过，靠 prompt+正则提取 JSON
    is_openrouter = "openrouter" in base_url.lower()
    if not any(k in model.lower() for k in ("reasoner", "-think", "thinking", "r1")) and not is_openrouter:
        body["response_format"] = {"type": "json_object"}
    if tools:
        body["tools"] = tools
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # OpenRouter 推荐提供这两个 header；HTTP 头必须用 ASCII，不能含中文
        "HTTP-Referer": "https://github.com/mobeifengyun/news-wechat",
        "X-Title": "news-wechat-daily",
    }
    try:
        r = _post_with_retry(url, headers, json.dumps(body, ensure_ascii=False).encode("utf-8"))
    except requests.HTTPError as e:
        # 诊断：打印实际请求地址（脱敏）与状态码，方便排查 404/401/402
        safe = url.replace(api_key, "***") if api_key else url
        code = getattr(e.response, "status_code", None)
        if code in (401, 402, 403):
            # 鉴权/额度失败：账号不可用，抛专属异常让主流程立即降级为规则拼装（不重试）
            print(f"  LLM 鉴权/额度失败（{code}），将直接降级为 Tavily 规则拼装：{safe}", flush=True)
            raise LLMAuthError(f"LLM 返回 {code}（{getattr(e.response, 'text', '')[:160]}）")
        print(f"  LLM 请求失败: {code} {safe}", flush=True)
        raise
    resp = r.json()
    # 增强诊断：若返回的不是标准 OpenAI 格式，打印原始响应帮助排查 key/余额/模型错误
    if "choices" not in resp:
        raw_preview = json.dumps(resp, ensure_ascii=False)[:800]
        print(f"  LLM 返回无 choices 字段（状态 {r.status_code}）：{raw_preview}", flush=True)
        raise KeyError(f"choices (response keys: {list(resp.keys())})")
    return resp["choices"][0]["message"]["content"]


# ---------------- Kimi / Moonshot 联网搜索 ----------------
def _post_with_retry(url, headers, data, timeout=45, max_retry=2):
    """带 429 限流退避的 POST。Kimi 免费档 RPM 很低，一次采集会连发多轮请求，
    必须遇到 429 就按 Retry-After 等待后重试，否则整轮失败。"""
    import requests
    for i in range(max_retry):
        try:
            r = requests.post(url, headers=headers, data=data, timeout=timeout)
        except requests.RequestException as e:
            if i < max_retry - 1:
                print(f"  ⏳ 网络异常 {e}，5s 后重试 ({i+1}/{max_retry})", flush=True)
                time.sleep(5)
                continue
            raise
        if r.status_code == 429:
            wait = 20
            try:
                wait = int(r.headers.get("Retry-After", 20))
            except Exception:
                pass
            wait = min(max(wait, 15), 90)
            print(f"  ⏳ 429 限流，等 {wait}s 重试 ({i+1}/{max_retry})", flush=True)
            time.sleep(wait)
            continue
        return r
    return r


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
            "tools": tools,
            "thinking": {"type": "disabled"},  # $web_search 必须禁用 thinking
        }
        # Kimi K2.6/K2.5 对 temperature/top_p 等采样参数有固定约束，非思考模式固定 0.6，
        # 传其他值会 400。这里不传，让平台使用默认值。
        r = _post_with_retry(url, headers, json.dumps(body, ensure_ascii=False).encode("utf-8"))
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
        time.sleep(6)  # 多轮之间留间隔，避免触发 Kimi 免费档 RPM 限流
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
        if t not in ("guess", "code", "fill", "echo", "stance", "ask"):
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


# ---------------- 时效性 / 真实性兜底校验 ----------------
import re as _re

def check_freshness(data, day):
    """扫描生成文本，命中明显旧闻/编造信号则判错，强制重生成，杜绝失真新闻。"""
    from datetime import date as _date
    yr = _date.fromisoformat(day).year
    errors = []
    # 往年年份（早 2 年及以上）硬错：如 2026 年写成 2024 年
    for s in data.get("sections", []):
        for i, it in enumerate(s.get("items", []), 1):
            t = it.get("text", "")
            for ystr in _re.findall(r"(?:19|20)\d{2}", t):
                y = int(ystr)
                if y != yr and y <= yr - 2:
                    errors.append(f"[{s['name']}] 第{i}条出现往年年份 {ystr}（今年 {yr}）：{t[:18]}…")
                    break
            # 旧闻 / 未发生信号（防"杭州亚运会倒计时""即将开幕"等硬错）
            for mk in ("倒计时", "即将开幕", "即将举行", "即将召开", "即将开赛",
                       "即将上映", "即将开播", "定于近日", "拟于本月", "计划于下月"):
                if mk in t:
                    errors.append(f"[{s['name']}] 第{i}条含旧闻/未发生信号「{mk}」：{t[:18]}…")
                    break
    return errors


def domain_to_source(url):
    """保底降级用：从 Tavily 返回的文章 url 反查白名单媒体名。"""
    if not url:
        return "新华社"
    for name, doms in WL_DOMAINS.items():
        for d in doms:
            if d in url:
                return name
    return "新华社"


# 保底降级时优先采用的核心权威媒体域名（避免低质量商业站返回导航垃圾）
CORE_MEDIA_DOMAINS = [
    "news.cn", "xinhuanet.com",            # 新华社
    "cctv.com", "cntv.cn",                 # 央视新闻
    "people.com.cn",                       # 人民日报
    "chinanews.com.cn",                    # 中国新闻网
    "stdaily.com",                         # 科技日报
    "globaltimes.cn",                      # 环球时报
    "cyol.com",                            # 中国青年报
    "ce.cn",                               # 经济日报
    "gmw.cn",                              # 光明日报
    "stcn.com",                            # 证券时报
    "thepaper.cn",                         # 澎湃新闻
]


def is_core_media(url):
    return any(d in url for d in CORE_MEDIA_DOMAINS) if url else False


# 常见媒体名（用于标题兜底时过滤以媒体名为开头的碎片）
MEDIA_NAMES = set(WL_DOMAINS.keys()) | {
    "光明网", "新华网", "央视网", "中新网", "人民网", "央广网", "环球网",
    "中国日报网", "参考消息网", "澎湃新闻", "证券时报网", "中国经济网",
}


def _strip_inline_markup(line):
    """去掉一行内的 markdown/HTML/URL/UI 占位，保留可读中文。"""
    import re as _re
    if not line:
        return ""
    # 强 UI 头行：只要出现“字号/点击播报/Image/Logo/小字号”等强网页控件，且行首是日期或来源，整行丢弃
    strong_ui = ["字号", "小字号", "点击播报", "Image", "Logo"]
    has_strong_ui = any(m in line for m in strong_ui)
    is_header = (
        _re.match(r"^20\d{2}年\d{1,2}月\d{1,2}日", line) or
        _re.search(r"(?:来源|编辑|记者|作者)[:：]", line)
    )
    if has_strong_ui and is_header:
        return ""
    # 如果整行几乎全是 UI 控件/导航词，直接丢弃
    ui_markers = ["订阅", "收藏", "字号", "点击播报", "Image", "Logo", "复制地址", "QQ空间",
                  "全部导航", "查看大图", "【大 中 小】", "【大中小】", "回到顶部", "返回首页"]
    has_ui = sum(line.count(m) for m in ui_markers)
    cn = len(_re.findall(r"[\u4e00-\u9fa5]", line))
    if has_ui >= 2 and cn <= has_ui * 8:
        return ""

    # markdown 图片/链接
    line = _re.sub(r"!?\[.*?\]\(.*?\)", "", line, flags=_re.S)
    line = _re.sub(r"!\(.*?\)", "", line, flags=_re.S)
    line = _re.sub(r"\[(.*?)\]\(.*?\)", r"\1", line, flags=_re.S)
    # URL / 邮箱 / 脚本
    line = _re.sub(r"https?://\S+|ftps?://\S+|mailto:\S+|javascript:\S+", "", line, flags=_re.I)
    line = _re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "", line)
    # HTML 标签
    line = _re.sub(r"<[^>]+>", "", line, flags=_re.S)
    # markdown 标题/列表/引用/代码
    line = _re.sub(r"^\s*#{1,6}\s+", "", line)
    line = _re.sub(r"^\s*[\*\-\+]\s+", "", line)
    line = _re.sub(r"^\s*>\s+", "", line)
    line = _re.sub(r"```[\s\S]*?```", "", line)
    line = _re.sub(r"`[^`]+`", "", line)
    # 残留 #、*、_、|、--
    line = _re.sub(r"#{2,}", "", line)
    line = _re.sub(r"\*{2,}", "", line)
    line = _re.sub(r"_{2,}", "", line)
    line = _re.sub(r"[\|_]{2,}", "", line)
    line = _re.sub(r"\s*--\s*", " ", line)
    line = _re.sub(r"\s*#\s*", " ", line)
    # 日期时间前缀：2026年08月18日07: |、2026年08月18日 - 等
    line = _re.sub(r"^20\d{2}年\d{1,2}月\d{1,2}日\s*(?:\d{1,2}[：:]\d{0,2})?\s*[\|\-]\s*", "", line)
    # 典型页面路径前缀：## 首页 >> 正文 -- 来源：XXX、首页 >> 正文 等
    line = _re.sub(r"^\s*#*\s*首页\s*>>\s*正文\s*(--\s*)?来源[:：][^\s]+\s*", "", line)
    line = _re.sub(r"^\s*#*\s*来源[:：][^\s]+\s*", "", line)
    line = _re.sub(r"^\s*--\s*来源[:：][^\s]+\s*", "", line)
    # （详情见...原文）、[详情见...]
    line = _re.sub(r"[（(]\s*详情见.*?原文\s*[）)]", "", line, flags=_re.I)
    # 来源/编辑/记者等前缀（去掉“来源：”后紧跟的媒体名也一并去掉）
    line = _re.sub(r"^(?:来源|编辑|记者|作者|审核|校对|发布时间|更新时间|阅读|浏览量)[:：]\s*", "", line)
    # 去掉摄影/记者署名行："活动现场 澎湃新闻记者 邓玲玮 摄"、"XXX 记者 XXX 摄"、"记者 XXX 摄影"
    # 注意：中文正文里 摄/图 后常紧跟标点（。，；等）而非空白，因此结尾允许标点
    _tail = r"(?:\s|[\u3002\uff0c\uff0e\uff1b\uff01\uff1f\u3001]|$)"
    line = _re.sub(r"(?:^|\s)\S*?记者\s+\S+\s*(?:摄|摄影|图)" + _tail, " ", line)
    line = _re.sub(r"(?:^|\s)\S+\s+摄" + _tail, " ", line)
    # 如果行首残留常见媒体名 + 数字/空格/UI 控件，也清理掉
    for name in MEDIA_NAMES:
        line = _re.sub(r"^" + _re.escape(name) + r"\s*\d*\s*", "", line)
    # 处理“澎湃 Logo/登录/#”这类行首媒体名+UI 控件的残留
    line = _re.sub(r"^(?:澎湃|界面|一财|新华|央视|人民|光明|经济|科技|环球)\s*(?:Logo|登录|#|>>)\s*", "", line, flags=_re.I)
    # 去掉“活动现场”这类无意义场景前缀
    line = _re.sub(r"^活动现场\s+", "", line)
    # 导航/UI 词（截图中实际出现）。注意：用一次性正则避免“已订阅”被拆成“已”。
    line = _re.sub(r"\d*_?\s*(?:订阅|收藏)|已订阅|已收藏|分享|评论|点赞|转发|登录|注册", "", line, flags=_re.I)
    ui_words = [
        "Image", "Logo", "全部导航", "网站地图", "回到顶部", "返回首页", "更多>>", "更多>", "相关阅读",
        "延伸阅读", "推荐阅读", "热图推荐", "图集", "查看大图", "小字号", "字号", "打印",
        "复制地址", "QQ空间", "点击播报", "【大 中 小】", "【大中小】",
        "打开 央视", "查看更多精彩评论", "热门推荐", "滚动新闻",
    ]
    for w in ui_words:
        line = _re.sub(_re.escape(w), "", line, flags=_re.I)
    # >> 路径：首页 >> 正文、财经 >> 正文 等
    line = _re.sub(r"(?:首页|频道|栏目|正文)\s*>>\s*[^\s，。！？；]*", "", line)
    # 广告/营销话术（截图中出现过的）
    ad_patterns = [
        r"下载[“\"']?.*?官方?APP[“\"']?.*?(?:，|。|！|？|$)",
        r"关注[“\"']?.*?官方?微信公众?号[“\"']?.*?(?:，|。|！|？|$)",
        r"(?:随时|及时)了解.*?动态.*?(?:，|。|！|？|$)",
        r"洞察政策信息.*?(?:，|。|！|？|$)",
        r"把握财富机会.*?(?:，|。|！|？|$)",
        r"即可.*?了解.*?把握.*?机会.*?(?:，|。|！|？|$)",
        r"打开.*?APP.*?(?:，|。|！|？|$)",
        r"扫描.*?二维码.*?(?:，|。|！|？|$)",
        r"点击.*?(?:领取|下载|关注|报名).*(?:，|。|！|？|$)",
        r"注册即送.*?(?:，|。|！|？|$)",
    ]
    for p in ad_patterns:
        line = _re.sub(p, "", line, flags=_re.I)
    # 孤立英文（前后都不是英文/数字/中文的纯英文单词）
    line = _re.sub(r"(?<![A-Za-z0-9\u4e00-\u9fa5])[A-Za-z]{1,20}(?![A-Za-z0-9\u4e00-\u9fa5])", "", line)
    # 孤立数字：只删真正孤立的纯数字，保留“7月10日”“100%股权”等带单位的
    line = _re.sub(r"(?<![A-Za-z0-9\u4e00-\u9fa5%‰℃￥$€£])\d{1,20}(?![A-Za-z0-9\u4e00-\u9fa5%‰℃￥$€£])", "", line)
    # 去掉英文/数字被删后留下的空括号（中英文）
    line = _re.sub(r"\(\s*\)", "", line)
    line = _re.sub(r"（\s*）", "", line)
    line = _re.sub(r"\[\s*\]", "", line)
    line = _re.sub(r"【\s*】", "", line)
    # 归一化空白
    line = _re.sub(r"\s+", " ", line).strip()
    return line


def _is_junk_line(line):
    """判断一整行是否只是导航/来源/UI 垃圾，应丢弃。"""
    import re as _re
    if not line:
        return True
    # 整行都是特殊符号或纯英文/数字
    if _re.fullmatch(r"[\W\sA-Za-z0-9]+", line) and len(_re.findall(r"[\u4e00-\u9fa5]", line)) < 3:
        return True
    # 典型垃圾整行模式
    junk_patterns = [
        r"^\s*#{1,6}\s*$",
        r"^\s*[\*\-\+]\s*$",
        r"^\s*来源[:：]\s*[^\s]{1,20}\s*$",
        r"^\s*编辑[:：]\s*",
        r"^\s*记者[:：]\s*",
        r"^\s*免责声明\s*$",
        r"^\s*版权声明\s*$",
        r"^\s*相关新闻\s*$",
        r"^\s*网友评论\s*$",
        r"^\s*图集\s*$",
        r"^\s*登录\s*$",
        r"^\s*注册\s*$",
    ]
    for p in junk_patterns:
        if _re.search(p, line, _re.I):
            return True
    return False


def _extract_best_paragraph(text):
    """从多段文本中挑出最长、中文密度最高、最少导航词的正文段落。"""
    import re as _re
    paragraphs = _re.split(r"\n\s*\n|\r\n\s*\r\n", text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    nav_words = ["导航", "订阅", "收藏", "首页", "滚动", "版权声明", "免责声明",
                 "相关新闻", "图集", "查看更多", "分享", "登录", "注册", "来源", "编辑", "记者"]
    best, best_score = "", -1
    for p in paragraphs:
        cn = len(_re.findall(r"[\u4e00-\u9fa5]", p))
        nav = sum(p.count(w) for w in nav_words)
        score = cn - nav * 10
        if score > best_score and cn >= 15:
            best_score = score
            best = p
    return best


def _clean_text(text):
    """深度清洗 Tavily 返回的网页文本：按行丢弃导航/UI/来源垃圾，
    再去掉行内 markdown/HTML/URL 等，最后取最像正文的那段。"""
    import re as _re
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    kept = []
    for raw in lines:
        line = _strip_inline_markup(raw)
        if _is_junk_line(line):
            continue
        # 去掉后如果只剩很短，也丢弃
        if len(line) < 8:
            continue
        cn = len(_re.findall(r"[\u4e00-\u9fa5]", line))
        if cn < 5:
            continue
        # 如果整行导航词密度过高，丢弃
        nav_words = ["导航", "订阅", "收藏", "首页", "滚动", "版权声明", "免责声明",
                     "相关新闻", "网友评论", "图集", "查看更多", "登录", "注册"]
        nav = sum(line.count(w) for w in nav_words)
        if nav * 6 > cn:
            continue
        kept.append(line)
    if not kept:
        return ""
    joined = " ".join(kept)
    best = _extract_best_paragraph(joined)
    return best if best else joined


def _is_good_sentence(s):
    """判断一个句子片段是否可用作新闻摘要。"""
    import re as _re
    if not (12 <= len(s) <= 120):
        return False
    cn = len(_re.findall(r"[\u4e00-\u9fa5]", s))
    if cn < 8:
        return False
    # 不以禁用词开头
    bad_starts = ["记者", "编辑", "来源", "免责声明", "版权声明", "相关新闻", "订阅",
                  "导航", "首页", "滚动新闻", "图集", "第", "打开", "查看", "点击", "登录", "注册",
                  "据报道", "据悉", "据了解", "据介绍", "消息称", "活动现场"]
    if any(s.startswith(w) for w in bad_starts):
        return False
    # 不包含禁用词/符号
    bad_inside = ["Image", "Logo", "订阅", "收藏", "全部导航", "https", "mailto",
                  "javascript", "复制地址", "QQ空间", "####", "详情见", "点击播报",
                  "小字号", "【大 中 小】", "【大中小】", ">>", "来源:", "编辑:", "记者:",
                  "下载", "官方APP", "微信公众号", "把握财富机会", "洞察政策信息"]
    if any(w in s for w in bad_inside):
        return False
    # 不包含只剩空括号
    if _re.search(r"[（(]\s*[）)]", s):
        return False
    # 必须以中文或数字（日期/年份/百分比，如“8月18日”“2026年”）开头，
    # 拒绝英文/URL/符号开头；具体 junk 词已由 bad_starts 拦截（第/首页/导航等）
    if not _re.match(r"[\u4e00-\u9fa5\d]", s):
        return False
    return True


def _clean_first_sentence(text):
    """从正文中提取第一句（或前几句合并）完整、通顺、不含垃圾的陈述句。
    接受以 。！？； 结尾的完整句，拒绝半截句；若首句过短则尝试与下句合并。"""
    import re as _re
    text = _clean_text(text)
    if not text:
        return ""
    text = text.strip('"').strip("'")
    # 按完整句末标点分割，保留标点
    parts = _re.split(r"([。！？；])", text)
    sentences = []
    current = ""
    for part in parts:
        if part in "。！？；":
            sentence = current.strip()
            if _is_good_sentence(sentence):
                sentences.append(sentence + part)
            current = ""
        else:
            current += part
    if not sentences:
        return ""
    # 取第一个句子；若过短（<20字），尝试与后续完整句合并，但不超过60字
    result = sentences[0]
    for s in sentences[1:]:
        if len(result) >= 20:
            break
        if len(result) + len(s) > 60:
            break
        result += s
    return result


def _smart_truncate(text, max_len=60, min_len=12):
    """智能截断：只在完整句末（。！？；）截断，绝不在逗号处砍出半截句。
    若找不到完整句末，宁可取更短的第一个完整句，也不要半截词。"""
    import re as _re
    text = text.strip()
    if len(text) <= max_len:
        return text if len(text) >= min_len else ""
    # 1) 在 max_len 内找最后一个完整句末
    cut = text[:max_len]
    idx = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"), cut.rfind("；"))
    if idx >= min_len - 5:
        return text[:idx + 1]
    # 2) 没有的话，取 text 中第一个满足条件的完整句（可能较短，但语义完整）
    parts = _re.split(r"([。！？；])", text)
    current = ""
    for part in parts:
        if part in "。！？；":
            sentence = current.strip()
            full = sentence + part
            if _is_good_sentence(sentence) and min_len <= len(full) <= max_len:
                return full
            current = ""
        else:
            current += part
    # 3) 退化兜底：整句超长且内部无句末时，在 max_len 内最后一个「分号>逗号」处断句并补句号，
    #    宁可略带半截也绝不整条丢弃（兜底拼装以“不丢条、填满版面”为优先）
    cut = text[:max_len]
    for sep in ("；", "，", ",", ";"):
        j = cut.rfind(sep)
        if j >= min_len - 1:
            return cut[:j] + "。"
    # 4) 连逗号都没有，直接截断补句号（极端兜底）
    return text[:max_len] + "。"


def rule_assemble(sec_ctx_map, hot_ctx, day, cfg, prev_type):
    """保底降级：不调用任何大模型，直接把 Tavily 白名单检索结果按板块拼装成稿。
    仅用于 LLM 完全不可用（无 key / 超时 / 限流）时，保证当天绝不中断、绝不编造。
    来源真实（均来自白名单域名检索）、合规、可读；质量略机械，属可接受底线。"""
    import re as _re
    d = date.fromisoformat(day)
    hs0 = cfg.get("hotlist_sites", ["微博热搜"])[0]
    data = {
        "greeting": "早安，新的一天",
        "quote": "日子慢些过，才看得见光。",
        "sections": [],
        "hotspot": {"name": "热点榜单", "items": []},
        "interaction": {},
    }
    # 极限词过滤：兜底拼装避免触发审核 WARN
    absolute_words = ["独家", "首家", "第一", "唯一", "绝对", "百分百", "必将", "注定"]
    # 先统一解析候选，并标记是否核心权威媒体，优先用核心媒体结果
    def _parse_candidate(c):
        m = _re.match(r"【(.*?)】(.*?)(?:\n来源:\s*(\S+))?$", c, _re.S)
        if m:
            title = m.group(1).strip()
            content = m.group(2).strip()
            url = m.group(3).strip() if m.group(3) else ""
        else:
            title = ""
            content = c
            url = ""
        return title, content, url, is_core_media(url)

    for s in cfg["sections"]:
        candidates = [_parse_candidate(c) for c in sec_ctx_map.get(s["name"], [])]
        # 核心媒体优先；同质量按原顺序
        candidates.sort(key=lambda x: (not x[3], 0))
        items = []
        for title, content, url, _ in candidates:
            if len(items) >= s["max"]:
                break
            src = domain_to_source(url) if url else "新华社"
            # 优先用正文首句；正文没有可用句时回退到清洗后的标题
            body = _clean_first_sentence(content)
            if len(body) < 12:
                body = _clean_text(title)
                # 标题兜底：要求 ≥20 字、不以媒体名开头、不以废话开头、不含导航/UI 词
                bad_title_starts = ["据报道", "据悉", "据了解", "据介绍", "消息称"]
                if (len(body) < 20 or
                    any(body.startswith(m) for m in MEDIA_NAMES) or
                    any(body.startswith(m) for m in bad_title_starts) or
                    _re.search(r"Image|Logo|订阅|收藏|全部导航|https|mailto|javascript|复制地址|QQ空间|####|详情见|点击播报|小字号|>>", body, _re.I)):
                    continue
                # 无句末标点的干净标题，去掉末尾逗号/分号/冒号后补句号
                if body and body[-1] not in "。！？；":
                    body = body.rstrip("，,；;:：") + "。"
                # 补完句号后仍是以废话词结尾的，不要
                if body.endswith(("据报道。", "据悉。", "据了解。", "据介绍。", "消息称。")):
                    continue
            if len(body) < 12:
                continue
            text = body
            # 去掉极限词
            for w in absolute_words:
                text = text.replace(w, "")
            text = _re.sub(r"\s+", " ", text).strip()
            # 字数控制：优先 28–66；只在完整句/分句处截断，绝不在半截词处截断
            text = _smart_truncate(text, max_len=66, min_len=12)
            if not text:
                continue
            # 最终过滤：来源合规、无垃圾、字数合规、必须以完整句末结尾
            cn_chars = len(_re.findall(r"[\u4e00-\u9fa5]", text))
            if (cn_chars < 10 or
                text[-1] not in "。！？；" or
                _re.search(r"https?|mailto|javascript|!\[|\[\]|Image|Logo|订阅|收藏|全部导航|复制地址|QQ空间|####|详情见", text, _re.I) or
                _re.search(r"^(第\d+页|要闻|首页|订阅|导航|滚动新闻|202\d年\d+月\d+日)", text)):
                continue
            if src not in WHITELIST:
                src = "新华社"
            if text and text not in [it["text"] for it in items]:
                items.append({"text": text, "source": src})
        if items:
            data["sections"].append({"name": s["name"], "items": items})

    # 热点：解析、核心媒体优先、清洗
    hot_candidates = [_parse_candidate(c) for c in hot_ctx]
    hot_candidates.sort(key=lambda x: (not x[3], 0))
    for title, content, url, _ in hot_candidates[:12]:
        # 热榜优先用完整正文第一句，没有可用正文才回退标题
        t = _clean_first_sentence(content)
        if len(t) < 12:
            t = _clean_text(title)
            # 标题兜底同样过滤
            bad_title_starts = ["据报道", "据悉", "据了解", "据介绍", "消息称"]
            if (len(t) < 20 or
                any(t.startswith(m) for m in MEDIA_NAMES) or
                any(t.startswith(m) for m in bad_title_starts) or
                _re.search(r"Image|Logo|订阅|收藏|全部导航|https|mailto|javascript|复制地址|QQ空间|####|详情见|点击播报|小字号|>>", t, _re.I)):
                continue
            if t and t[-1] not in "。！？；":
                t = t.rstrip("，,；;:：") + "。"
            if t.endswith(("据报道。", "据悉。", "据了解。", "据介绍。", "消息称。")):
                continue
        t = t.strip()
        # 热点要实质性中文句子；过滤 URL/markdown/图片/UI 垃圾/无意义片段
        cn_chars = len(_re.findall(r"[\u4e00-\u9fa5]", t))
        if not (t and len(t) >= 12 and cn_chars >= 8):
            continue
        if _re.search(r"https?|mailto|javascript|!\[|\[\]|Image|Logo|订阅|收藏|全部导航|复制地址|QQ空间|####|详情见", t, _re.I):
            continue
        # 截断到 30 字以内，只在完整句末截断，拒绝半截句
        t = _smart_truncate(t, max_len=30, min_len=12)
        if not t or t[-1] not in "。！？；":
            continue
        if t not in [x["text"] for x in data["hotspot"]["items"]]:
            data["hotspot"]["items"].append({"text": t, "site": hs0})
    # 若热搜检索结果不足 6 条，用各板块首条新闻兜底，保证版面完整且字数合规
    if len(data["hotspot"]["items"]) < 6:
        pad = []
        for sec in data["sections"]:
            for it in sec.get("items", []):
                # 用智能截断保证热点兜底也是完整句（绝不半截、绝不丢条）
                txt = _smart_truncate(it["text"], max_len=30, min_len=12)
                if txt and txt not in [x["text"] for x in data["hotspot"]["items"]]:
                    pad.append({"text": txt, "site": hs0})
        for p in pad:
            if len(data["hotspot"]["items"]) >= 12:
                break
            data["hotspot"]["items"].append(p)
    top = data["hotspot"]["items"][0]["text"] if data["hotspot"]["items"] else "今天"
    data["interaction"] = {
        "title": "今日一问",
        "lead": "来聊聊你今天的小日子",
        "card": {
            "type": "code",
            "topic": f"关于「{top[:12]}」的今日打卡",
            "format": "我今天__，感觉__",
            "example": "我今天散步3000步，感觉挺舒服",
            "hint": "评论区打个卡，记录平凡一天",
        },
        "closing": "每天进步一点点",
    }
    data["date"] = day
    data["lunar"] = lunar_of(d)
    return data


# ---------------- AI 成稿硬性净化（兜底，防 LLM 误抄脏内容） ----------------

def _sanitize_ai_text(text, max_len=58, min_len=28):
    """对 LLM 生成的单条 text 做硬性净化：
    - 去除广告话术 / 摄影署名 / 空括号 / UI 残留（复用 _strip_inline_markup）
    - 兜底剥离从 Tavily 碎片误抄的「下载…APP」「关注…公众号」等营销话术
    - 补全句末标点（AI 偶尔漏标）
    - 超长按完整句截断兜底
    不重写语义，只去脏 + 控字数，保证 AI 成稿也达标。"""
    import re as _re
    if not text or not isinstance(text, str):
        return text
    t = _strip_inline_markup(text.strip())
    # 去空括号（英文/数字删除残留）：() （） [] 【】
    t = _re.sub(r"[\(（\[【]\s*[\)）\]】]", "", t)
    # 兜底广告/营销话术（AI 偶从 Tavily 碎片误抄，且 _strip_inline_markup 仅匹配「官方APP」会漏）：
    # 下载…APP、关注…公众号、把握财富机会、洞察政策信息、扫描…二维码、点击领取/下载、注册即送
    ad_re = [
        r"下载[^，。！？；]{0,20}?APP",
        r"关注[^，。！？；]{0,20}?公众号[^，。！？；]{0,12}",
        r"把握财富机会",
        r"洞察政策信息",
        r"扫描[^，。！？；]{0,20}?二维码",
        r"点击[^，。！？；]{0,20}?(?:领取|下载|关注|报名|进入|了解|查看|阅读|更多)",
        r"注册即送[^，。！？；]{0,20}",
        r"看更多", "了解更多", "阅读全文", "查看更多", "详情点击", "点击进入",
        r"原标题[:：][^，。！？；]{0,30}",
        r"责任编辑[:：][^，。！？；]{0,20}",
        r"责编[:：][^，。！？；]{0,20}",
        r"扫码[^，。！？；]{0,20}?(?:加微信|关注|领取)?",
        r"加微信[^，。！？；]{0,20}",
        r"微信号[:：]?[^\s，。！？；]{0,20}",
        r"后台回复[^，。！？；]{0,20}",
        r"全文完", "（图）", "（图文）", "图源[:：][^，。！？；]{0,20}",
        r"资料图[^，。！？；]{0,10}", "视觉中国", r"IC\s*photo",
        r"转载请[^，。！？；]{0,20}", "版权声明", "免责声明",
        r"点击[^，。！？；]{0,10}?领取",
    ]
    for p in ad_re:
        t = _re.sub(p, "", t, flags=_re.I)
    t = t.strip(" ，。、；")
    t = t.strip()
    if not t:
        return t
    # 半截句：无句末标点则补句号
    if t[-1] not in "。！？；":
        t += "。"
    # 超长：优先按完整句截断，失败则硬截断补句号
    if len(t) > max_len:
        cut = _smart_truncate(t, max_len=max_len, min_len=min_len)
        t = cut if cut else t[:max_len] + "。"
    return t


def _sanitize_ai_candidate(cand, cfg):
    """对 LLM 返回的整份 JSON 做净化：逐条清洗 section/hotspot 文本，
    保证即使 AI 误抄了 Tavily 碎片中的广告/署名/空括号，也能被兜掉。"""
    if not isinstance(cand, dict):
        return cand
    for sec in cand.get("sections", []) or []:
        for it in sec.get("items", []) or []:
            if isinstance(it, dict) and it.get("text"):
                it["text"] = _sanitize_ai_text(it["text"])
    hs = cand.get("hotspot") or {}
    for it in hs.get("items", []) or []:
        if isinstance(it, dict) and it.get("text"):
            it["text"] = _sanitize_ai_text(it["text"], max_len=30, min_len=8)
    return cand


def build_prompt(day, cfg, prev_type, search_ctx, errors, seed=""):
    d = date.fromisoformat(day)
    theme = festival_of(d)
    date_cn = f"{d.year}年{d.month}月{d.day}日 星期{WEEKDAYS[d.weekday()]}"
    sec_names = "、".join(s["name"] for s in cfg["sections"])
    wl = "、".join(cfg["source_whitelist"])
    sites = "、".join(cfg["hotlist_sites"])

    sys_prompt = (
        "你是一名资深新闻编辑，负责为中文微信公众号「报简说·每日消息早知道」"
        "生成每日早报的结构化数据。必须遵守以下铁律：\n"
        "1. 所有新闻事实只能引用白名单媒体，绝不使用白名单以外的任何来源"
        "（含境外媒体、自媒体、营销号）。白名单：" + wl + "。\n"
        "2. 每条新闻摘要严格 40–55 个汉字（含标点），用客观陈述句，不评论、不引申；宁可稍详勿过简。"
        "组稿须按「早间新闻晨读（如央视《朝闻天下》式）」思路覆盖当天要闻：国内、国际、财经、科技、民生、文体各大类都要有代表，"
        "重大事件宁多勿漏，不要只盯着一两个话题。\n"
        "3. 来源 source 必须是白名单中的某个媒体名，且确实报道过该事。\n"
        "4. 热点榜单 hotspot 每条只写话题标题（简短），标注 site（只能是热榜站点之一）。\n"
        "5. 每日微语 quote 须为原创或公版励志短句，不得抄袭任何「微语报」「早安语」原文。\n"
        "6. 互动板块 interaction 每期只出 1 个「高质量」问题，靠读者打字留言参与，"
        "绝不做成按钮、绝不允许出现「点赞/在看/转发/分享/抽奖/奖品」等词。"
        "好问题 = 有悬念能勾起好奇 + 有共鸣让人想说 + 零门槛一句话能答 + 值得晒（读者愿意发朋友圈那种）。"
        "lead 与 closing 要有温度，像朋友聊天而非官宣；topic 要有钩子（悬念/反差/共鸣），不要平铺直叙。\n"
        "7. 【今日一问·面向中老年读者】选题优先照顾银发群体的兴趣与表达习惯，"
        "挑他们愿意聊、零门槛、无争论的轻松话题：①生活作息与轻养生（散步走路、睡眠质量、喝水、"
        "节气起居，只聊习惯、绝不聊具体治病偏方）；②家庭与隔代（带孙辈、老伴、亲子、邻里家常）；"
        "③怀旧记忆（老物件、老歌老电影老剧、青春年代、家乡味、童年零食）；"
        "④银发日常（智能手机使用困惑与防骗小妙招、广场舞/太极/书法、买菜做饭、棋牌、养花养宠、旅居/周边游）；"
        "⑤季节天气与衣食住行。严禁拿时政外交、军事、灾情伤亡、"
        "民生政策抱怨（油价房价社保养老医保裁员物价）、投资荐股、医疗健康建议（偏方疗效治病保健品降压降糖减肥中药处方）、"
        "点名个人是非、性别地域阶层对立等易翻车话题来提问。\n"
        "8. 只输出符合指定 schema 的 JSON，不要任何解释文字。\n"
        "9. 用户可能提供一份「公众号参考素材」（仅供选题方向与表述启发）。"
        "你只能用它发现哪些话题值得今日报道、借鉴其栏目编排思路；"
        "最终每一条新闻的事实仍须来自上面的白名单媒体检索结果，"
        "source 必须是白名单媒体名，绝不可把公众号或其素材当作信源、"
        "绝不可照搬其原文原句。"
        "10. 联网检索素材常夹带网页噪声：下载APP/关注公众号等广告话术、"
        "「记者 XXX 摄」等摄影署名、以及「兆易创新()」这类因删英文/数字残留的空括号。"
        "成稿时必须彻底剔除这些噪声，用自己的话重写干净摘要，不得原样照搬上述噪声。"
        "11. 时效性等其余铁律见下一条补充指令。"
    )
    sys_prompt += (
        f"\n11. 时效性铁律（最高优先级）：所有新闻必须是「{day}」当天（或前一日 24–48 小时内）"
        f"由白名单媒体新近发布/滚动报道的事件，报道日期不得早于 {day} 超过 2 天。"
        f"严禁采用周年纪念、历史回顾、旧闻重发、科普百科、以及在 {day} 之前就已发生且已被广泛报道过的「旧热点」。"
        f"若联网检索只得到旧闻、或某条结果无法确认其报道日期在 {day} 前后，宁可该板块少写几条，"
        f"也绝不允许把旧闻当成今日新闻塞进来——这是硬红线，违反即作废重生成。"
    )

    user_prompt = (
        f"请生成 {day}（{date_cn}）的日报数据。\n\n"
        f"【时效性红线】本日报只收录 {date_cn} 当天（及前一日）发生的「新近新闻」。"
        f"不要写早于 {day} 超过 2 天的旧闻、周年纪念、历史回顾或旧热点重发；"
        f"每条新闻请先在脑中确认其报道日期在 {date_cn} 前后，无法确认日期的旧闻一律不采用。\n\n"
        f"各板块（顺序与名称必须严格一致，每板块 4–6 条）：{sec_names}。\n"
        f"请按「早间新闻晨读」思路组稿：覆盖当天国内外要闻、财经、科技、民生、文体，尽量不遗漏重大事件；"
        f"每个板块挑当天最具关注度、最值得读者知道的 4–6 条。\n"
        f"各板块检索/选题方向：国内要闻=今日国内大事·政策发布·重大工程·航天科技成就；"
        f"国际新闻=国际 外交部 环球；财经动态=财经 股市 央行 楼市；科技前沿=AI 芯片 航天 新能源；"
        f"社会民生=民生 教育 医疗 就业 暖新闻；文体资讯=影视 体育 音乐 文博。\n\n"
        f"热点榜单站点（site 只能取这些）：{sites}。\n\n"
        f"上一期互动卡型是「{prev_type or '无'}」，本期必须换一种卡型"
        f"（可选：guess 盲猜 / code 打卡 / fill 填空 / echo 上期揭晓 / stance 站队 / ask 开放式闲聊）。\n"
        f"互动卡质量红线：①给一个让人想立刻回答的具体场景；②给模板或格式降低参与门槛；"
        f"③topic 要有钩子（例：fill「我退休后每天走__步，您平时走多少？」远比「您运动吗」好；"
        f"ask「您手机里最常用的是哪个 APP？评论区聊聊」远比「您会用智能手机吗」好；"
        f"guess「您家今年中秋回老家还是就地过节？」远比「中秋怎么过」好）；④lead/closing 有温度像聊天。\n\n"
    )
    if theme:
        user_prompt += (
            f"【今日应景】今天是「{theme}」，今日一问请优先结合这个节日/节气来设计"
            f"（须贴近中老年生活、遵守规则7方向与不涉红线）。出题角度参考："
            f"中秋·端午·重阳等传统节日→团圆家宴与敬老；立春·立秋·冬至等节气→时令饮食与起居养生；"
            f"国庆·元旦→休假出行与家庭聚会；清明→回乡祭祖或云祭扫。给一个具体可答的场景即可，不要硬凑。\n\n"
        )
    if search_ctx:
        user_prompt += (
            "以下是联网检索到的白名单媒体素材（仅可据此成稿，不得引用素材之外的信息）：\n"
            + "\n\n".join(search_ctx)[:6000]
            + "\n\n"
        )
    if seed:
        user_prompt += (
            "【用户提供的公众号参考素材（仅供选题方向与表述启发，"
            "不得作为信源、不得照搬原文原句）】\n"
            + seed[:4000]
            + ("\n（素材已截断）" if len(seed) > 4000 else "")
            + "\n\n"
        )
    user_prompt += (
        "输出 JSON schema（严格照此，字段名勿改）：\n"
        "{\n"
        '  "greeting": "一句早安问候（≤20字）",\n'
        '  "quote": "原创/公版励志微语（≤30字）",\n'
        '  "sections": [\n'
        '    {"name": "国内要闻", "items": [{"text": "40-55字摘要", "source": "央视新闻"}]},\n'
        '    {"name": "国际新闻", "items": [...]},\n'
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
        '- ask:   {"type":"ask","topic":"一个中老年读者想聊的开放问题，如「您年轻时最拿手的一道菜是啥？」","hint":"评论区聊聊您的故事…"}\n'
    )
    if errors:
        user_prompt += (
            "\n上一次生成未通过校验，请修正以下问题后重新输出：\n- "
            + "\n- ".join(errors)
            + "\n"
        )
    return sys_prompt, user_prompt


# ---------- 多供应商替补链（主 → 同平台备用模型 → 跨平台备援 → 规则拼装） ----------

def _default_model_for(base_url):
    """按 base_url 推断默认模型，覆盖常见 OpenAI 兼容平台。"""
    if "deepseek.com" in base_url:
        return "deepseek-chat"
    if "moonshot" in base_url:
        return "kimi-k2.6"
    if "openrouter" in base_url:
        return "deepseek/deepseek-chat:free"
    if "siliconflow" in base_url or "silicon" in base_url:
        return "deepseek-ai/DeepSeek-V3"
    if "dashscope" in base_url:
        return "qwen-plus"
    if "bigmodel" in base_url or "zhipu" in base_url:
        return "glm-4-air"
    return DEFAULT_MODEL


def _resolve_provider(n=""):
    """从环境变量读取一个 LLM 供应商配置。
    n 为空=主供应商（LLM_API_KEY/LLM_BASE_URL/LLM_MODEL）；
    n='2'/'3'=跨平台备援供应商（LLM2_API_KEY/LLM2_BASE_URL/LLM2_MODEL 等）。
    未配置 key 时返回 None。
    返回: {"base_url","api_key","models","is_kimi"}
      - models 为待尝试模型列表（主模型 + 同平台免费备援模型）
    """
    api_key = (os.environ.get(f"LLM{n}_API_KEY", "") or "").strip()
    if not api_key:
        return None
    base_url_env = (os.environ.get(f"LLM{n}_BASE_URL", "") or "").strip()
    if not base_url_env:
        base_url_env = DEFAULT_BASE
    base_url = _normalize_llm_url(base_url_env, "")
    is_kimi = "moonshot" in base_url
    main_model = (os.environ.get(f"LLM{n}_MODEL", "") or "").strip() or _default_model_for(base_url)

    # 同平台免费备援模型：主模型失败（额度/限流/下架）时自动换一个试试
    fallbacks = []
    if "openrouter" in base_url:
        fb = [
            "qwen/qwen-2.5-72b-instruct:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemma-2-9b-it:free",
        ]
        fallbacks = [m for m in fb if m != main_model]
    models = [main_model] + fallbacks
    return {
        "base_url": base_url,
        "api_key": api_key,
        "models": models,
        "is_kimi": is_kimi,
    }


def _generate_with_provider(prov, day, cfg, prev_type, search_ctx, seed, tools):
    """对单个供应商尝试成稿：先跑主模型，失败再换同平台备用模型，仍失败返回 None。
    返回 (data, last_err, last_raw)。"""
    last_err = ""
    last_raw = ""
    for model in prov["models"]:
        # 每个模型最多 3 次重试
        errors = []
        for attempt in range(3):
            sys_p, usr_p = build_prompt(day, cfg, prev_type, search_ctx, errors, seed)
            print(f"    模型 {model} 第 {attempt+1}/3 次生成…", flush=True)
            try:
                if prov["is_kimi"]:
                    raw = kimi_chat(sys_p, usr_p, prov["base_url"], prov["api_key"], model)
                else:
                    raw = llm_chat(sys_p, usr_p, prov["base_url"], prov["api_key"], model, tools)
                last_raw = raw
                m = re.search(r"\{.*\}", raw, re.S)
                if m:
                    raw = m.group(0)
                cand = json.loads(raw)
                # AI 成稿硬性净化：兜底去除误抄的广告/摄影署名/空括号/UI 残留，补句末标点
                cand = _sanitize_ai_candidate(cand, cfg)
            except Exception as e:
                if isinstance(e, LLMAuthError):
                    # 鉴权/额度类：该供应商整体不可用，跳出模型循环
                    last_err = str(e)
                    print(f"    ⚠️ {model} 鉴权/额度失败：{str(e)[:160]}", flush=True)
                    return None, last_err, last_raw
                last_err = str(e)
                print("    解析失败:", e, flush=True)
                errors = [f"模型返回无法解析为 JSON：{str(e)[:80]}"]
                continue
            errors = validate(cand, cfg)
            if errors:
                print("    校验未过：", errors[0], flush=True)
                continue
            fresh = check_freshness(cand, day)
            if fresh:
                print("    时效性校验未过：", fresh, flush=True)
                errors = [f"时效性校验未过：{fresh}"]
                last_err = errors[-1]
                continue
            if audit_block(cand, day) > 0:
                errors = ["内容触发合规 BLOCK，请改用更中性的表述后重试"]
                print("    合规 BLOCK，重试", flush=True)
                continue
            return cand, "", last_raw
        # 当前模型 3 次都失败，log 后换下一个同平台模型
        print(f"    ⚠️ 模型 {model} 3 次未成稿（{last_err[:120]}），尝试同平台备用模型…", flush=True)
    return None, last_err, last_raw


def main():
    tz = timezone(timedelta(hours=8))
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(tz).strftime("%Y-%m-%d")
    cfg = load_cfg()
    out_path = os.path.join(BASE, "output", f"news_{day}.json")
    err_path = os.path.join(BASE, "output", "_collect_error.txt")
    if os.path.exists(err_path):
        try:
            os.remove(err_path)
        except Exception:
            pass

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
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    search_mode = os.environ.get("SEARCH_MODE", "auto").lower()

    # 构建供应商替补链：主供应商(LLM_*) → 同平台备用免费模型 →
    # 跨平台备援(LLM2_* / LLM3_：硅基流动/阿里百炼/智谱…)。
    # 任一供应商出错自动切下一个；主供应商未配则链为空，直接走规则拼装保底。
    providers = []
    _p1 = _resolve_provider("")
    if _p1:
        providers.append(_p1)
    for _n in ("2", "3"):
        _p = _resolve_provider(_n)
        if _p:
            providers.append(_p)
    primary = providers[0] if providers else None
    api_key = primary["api_key"] if primary else ""
    base_url = primary["base_url"] if primary else DEFAULT_BASE
    model = primary["models"][0] if primary else DEFAULT_MODEL
    is_kimi_primary = primary["is_kimi"] if primary else False

    use_tavily = (search_mode == "tavily") or (tavily_key and search_mode != "none")
    use_kimi = (search_mode == "kimi") or (
        is_kimi_primary and not use_tavily and search_mode != "none"
    )
    if not use_tavily and not api_key:
        print("ERROR: 未设置 LLM_API_KEY，且未启用 Tavily 检索，无法在云端采集。")
        sys.exit(1)
    if use_tavily and not tavily_key:
        print("ERROR: SEARCH_MODE=tavily 但未设置 TAVILY_API_KEY")
        sys.exit(1)

    prev_type = prev_card_type(day)
    seed = load_seed(day)
    if seed:
        print(f"检测到公众号参考素材：{len(seed)} 字（仅作选题启发，事实仍以白名单为准）", flush=True)
    nprov = len(providers)
    print(f"采集 {day}（上期卡型={prev_type or '无'}）供应商链={nprov}个"
          + (f"，主模型={model}" if primary else "，无 LLM，直接走规则拼装"))

    search_ctx = []
    sec_ctx_map = {}  # Tavily 按板块归属的检索结果（降级拼装用）
    hot_ctx = []

    if use_kimi:
        if model == DEFAULT_MODEL:  # 用户未显式指定模型时给 Kimi 默认
            model = "kimi-k2.6"
        print(f"检索模式：Kimi/Moonshot 内置联网搜索（$web_search）模型={model}", flush=True)
    elif use_tavily:
        print("检索模式：Tavily（限定白名单域名 + 近 2 日新闻）", flush=True)
        _d = date.fromisoformat(day)
        _dc = f"{_d.year}年{_d.month}月{_d.day}日"
        # 各板块检索词（与 config.json 的 sections 顺序对应），拓宽白名单域名覆盖
        sec_query_words = {
            "国内要闻": "新华社 央视新闻 人民日报 央广网",
            "国际新闻": "新华社 央视新闻 环球时报 国际",
            "财经动态": "证券时报 第一财经 每日经济新闻 财经",
            "科技前沿": "科技日报 量子位 IT之家 AI 芯片 新能源",
            "社会民生": "澎湃新闻 新京报 社会 民生 教育",
            "文体资讯": "影视 体育 音乐 文博 文旅",
        }
        queries = []
        sec_order = []
        for s in cfg["sections"]:
            w = sec_query_words.get(s["name"], s["name"])
            queries.append(f"{_dc} {s['name']} {w}")
            sec_order.append(s["name"])
        queries.append(f"{_dc} 今日热点 要闻 新华社 央视")
        sec_ctx_map = {name: [] for name in sec_order}
        hot_ctx = []
        for idx, q in enumerate(queries, 1):
            print(f"  Tavily 查询 {idx}/{len(queries)}: {q}", flush=True)
            try:
                res = tavily_search(q, tavily_key, max_results=8, days=2)
            except Exception as e:
                print(f"    Tavily 检索失败: {e}", flush=True)
                res = []
            if idx <= len(sec_order):
                sec_ctx_map[sec_order[idx - 1]] = res
                print(f"    返回 {len(res)} 条 -> {sec_order[idx - 1]}", flush=True)
            else:
                hot_ctx = res
                print(f"    返回 {len(res)} 条 -> 热点", flush=True)
            search_ctx += res
        print(f"Tavily 总计检索到 {len(search_ctx)} 条上下文", flush=True)
    else:
        print("检索模式：Gemini 联网搜索（googleSearch grounding）", flush=True)

    tools = None
    if not use_tavily and not use_kimi and "googleapis.com" in base_url and search_mode != "none":
        tools = [{"googleSearch": {}}]

    # 空检索硬熔断：既非 Tavily、也非 Kimi 内置搜索、又无 Google grounding 时，
    # 才认定“没有任何真实检索手段”，绝不允许大模型凭知识编造新闻。
    # 注意：Kimi/Moonshot 走模型内置 $web_search，search_ctx 本身为空、也不挂 googleSearch，
    # 因此必须放行 use_kimi，否则会被误杀（这正是此前云端一直产出失败的直接原因之一）。
    if not use_tavily and not use_kimi and not tools:
        msg = (
            "ERROR: 未配置任何真实联网检索（TAVILY_API_KEY 未设且非 Kimi/Google 搜索模式），"
            "为避免编造虚假新闻，已拒绝生成。\n"
            "→ 主模型若为 OpenRouter / DeepSeek 等「无内置搜索」供应商，必须配置 TAVILY_API_KEY"
            "（建议同时把 SEARCH_MODE 设为 tavily），否则云端必定硬熔断、当天不出报。\n"
            "→ 若用 Kimi/Moonshot，请将 SEARCH_MODE 设为 kimi；若用 Gemini，请确认 LLM_BASE_URL 指向 googleapis.com。"
        )
        print(msg, flush=True)
        try:
            with open(os.path.join(BASE, "output", "_collect_error.txt"), "w", encoding="utf-8") as f:
                f.write(msg + f"\ndate={day}\n")
        except Exception:
            pass
        sys.exit(1)

    errors = []
    data = None
    last_err = ""
    last_raw = ""
    # 未配置任何成稿模型（供应商链为空且非 Kimi 模式）→ 跳过 LLM，直接走规则拼装保底
    no_llm = (len(providers) == 0) and not use_kimi
    if no_llm:
        print("未配置任何成稿模型 key，直接走 Tavily 规则拼装保底…", flush=True)
    else:
        for pi, prov in enumerate(providers, 1):
            tag = "主供应商" if pi == 1 else f"备援供应商{pi-1}"
            print(f"▶ 尝试 {tag}：{prov['models'][0]} @ {prov['base_url']}", flush=True)
            cand, err, raw = _generate_with_provider(prov, day, cfg, prev_type, search_ctx, seed, tools)
            if cand:
                data = cand
                print(f"✅ {tag} 成稿成功", flush=True)
                break
            last_err = err
            last_raw = raw
            print(f"⚠️ {tag} 失败（{(err or '多次重试未成稿')[:140]}），自动切换下一个供应商…", flush=True)

    if not data:
        # 所有供应商（主+备援+同平台模型）均失败 → 降级为 Tavily 规则拼装（无模型依赖，保证当天出稿、绝不编造）
        if use_tavily and sec_ctx_map:
            print("⚠️ 所有 LLM 供应商均失败，降级为 Tavily 规则拼装保底…", flush=True)
            ruled = rule_assemble(sec_ctx_map, hot_ctx, day, cfg, prev_type)
            if ruled:
                data = ruled
                print("✅ 规则拼装成功，已生成当日真实新闻（无大模型依赖）", flush=True)
        if not data:
            try:
                diag = (
                    f"date={day}\nuse_kimi={use_kimi} model={model}\n"
                    f"last_err={last_err}\nlast_raw={(last_raw or '')[:1500]}\n"
                    f"errors={errors}\n"
                )
                with open(err_path, "w", encoding="utf-8") as f:
                    f.write(diag)
                print("已写诊断到", err_path, flush=True)
            except Exception:
                pass
            print("ERROR: 所有 LLM 供应商及规则拼装均已失败，未生成合规内容：", errors, flush=True)
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
