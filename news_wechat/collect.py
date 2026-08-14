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
import time
from datetime import date, datetime, timedelta, timezone

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
        "response_format": {"type": "json_object"},
    }
    if tools:
        body["tools"] = tools
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    r = _post_with_retry(url, headers, json.dumps(body, ensure_ascii=False).encode("utf-8"))
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# ---------------- Kimi / Moonshot 联网搜索 ----------------
def _post_with_retry(url, headers, data, timeout=150, max_retry=5):
    """带 429 限流退避的 POST。Kimi 免费档 RPM 很低，一次采集会连发多轮请求，
    必须遇到 429 就按 Retry-After 等待后重试，否则整轮失败。"""
    import requests
    for i in range(max_retry):
        try:
            r = requests.post(url, headers=headers, data=data, timeout=timeout)
        except requests.RequestException as e:
            if i < max_retry - 1:
                print(f"  ⏳ 网络异常 {e}，5s 后重试 ({i+1}/{max_retry})")
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
            print(f"  ⏳ 429 限流，等 {wait}s 重试 ({i+1}/{max_retry})")
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
        "2. 每条新闻摘要严格 35–55 个汉字（含标点），用客观陈述句，不评论、不引申；宁可稍详勿过简。"
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
    )
    sys_prompt += (
        f"\n10. 时效性铁律（最高优先级）：所有新闻必须是「{day}」当天（或前一日 24–48 小时内）"
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
        '    {"name": "国内要闻", "items": [{"text": "40-60字摘要", "source": "央视新闻"}]},\n'
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
    if not api_key:
        print("ERROR: 未设置 LLM_API_KEY，无法在云端采集。请配置 Secrets.LLM_API_KEY")
        sys.exit(1)
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE).rstrip("/") + "/"
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    search_mode = os.environ.get("SEARCH_MODE", "auto").lower()

    prev_type = prev_card_type(day)
    seed = load_seed(day)
    if seed:
        print(f"检测到公众号参考素材：{len(seed)} 字（仅作选题启发，事实仍以白名单为准）")
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
        print("检索模式：Tavily（限定白名单域名 + 近 2 日新闻）")
        _d = date.fromisoformat(day)
        _dc = f"{_d.year}年{_d.month}月{_d.day}日"
        queries = [
            f"{_dc} 国内要闻 新华社 央视 人民日报", f"{_dc} 国际新闻 新华社 央视 环球",
            f"{_dc} 财经动态 证券时报 第一财经 每日经济新闻", f"{_dc} 科技前沿 AI 芯片 科技日报 量子位",
            f"{_dc} 社会民生 澎湃新闻 新京报", f"{_dc} 文体资讯 影视 体育 音乐",
            f"{_dc} 热搜 微博 抖音 百度",
        ]
        for q in queries:
            try:
                search_ctx += tavily_search(q, tavily_key, max_results=4, days=2)
            except Exception as e:
                print("  Tavily 检索失败:", e)
    else:
        print("检索模式：Gemini 联网搜索（googleSearch grounding）")

    tools = None
    if not use_tavily and not use_kimi and "googleapis.com" in base_url and search_mode != "none":
        tools = [{"googleSearch": {}}]

    errors = []
    data = None
    last_err = ""
    last_raw = ""
    for attempt in range(5):
        sys_p, usr_p = build_prompt(day, cfg, prev_type, search_ctx, errors, seed)
        print(f"第 {attempt+1} 次生成…")
        try:
            if use_kimi:
                raw = kimi_chat(sys_p, usr_p, base_url, api_key, model)
            else:
                raw = llm_chat(sys_p, usr_p, base_url, api_key, model, tools)
            last_raw = raw
            # 容错：去掉可能包裹的 ```json ``` 标记
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                raw = m.group(0)
            cand = json.loads(raw)
        except Exception as e:
            last_err = str(e)
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
        try:
            diag = (
                f"date={day}\nuse_kimi={use_kimi} model={model}\n"
                f"last_err={last_err}\nlast_raw={(last_raw or '')[:1500]}\n"
                f"errors={errors}\n"
            )
            with open(err_path, "w", encoding="utf-8") as f:
                f.write(diag)
            print("已写诊断到", err_path)
        except Exception:
            pass
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
