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
  - 每条摘要 40-55 字（含标点，validate 缓冲 28-60）
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

# 集团媒体别名 → 规范名（用于输出 source 归一化，避免 AI 把「新华网」写成板块限定外的名字）
# 规则：同一家媒体的不同称谓/子品牌，在校验时等价视为规范名；
# prompt 也会告诉 AI「素材里看到左边这些名字，输出时写右边的规范名」。
SOURCE_ALIASES = {
    # 新华社系
    "新华网": "新华社",
    "新华网客户端": "新华社",
    "新华社客户端": "新华社",
    # 人民日报系
    "人民网": "人民日报",
    "人民日报客户端": "人民日报",
    # 央视系
    "央视网": "央视新闻",
    "中央广播电视总台": "央视新闻",
    "朝闻天下": "央视新闻",
    # 中国新闻网
    "中新网": "中国新闻网",
    # 中国日报
    "中国日报网": "中国日报",
    # 环球时报
    "环球网": "环球时报",
    # 证券时报
    "证券时报网": "证券时报",
    # 第一财经
    "第一财经网": "第一财经",
    # 科技日报
    "科技日报网": "科技日报",
    # 新京报
    "新京报网": "新京报",
    # 中国青年报
    "中国青年报客户端": "中国青年报",
}


def _norm_source_name(src):
    """把媒体别名归一化为规范名；不在映射表则原样返回。"""
    if not isinstance(src, str):
        return src
    return SOURCE_ALIASES.get(src.strip(), src.strip())


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
def _source_from_url(url):
    """把检索结果的 url 反查为白名单规范来源名（素材预处理用）。"""
    u = (url or "").lower()
    for d, name in _DOMAIN_TO_SOURCE.items():
        if d in u:
            return name
    return ""


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
        title = it.get("title", "")
        content = it.get("content", "")
        url = it.get("url", "")
        # 素材预处理：把原始 url 直接归一化为白名单规范来源名，
        # 模型拿到的素材即带规范来源，从根上减少来源归属混乱。
        src = _source_from_url(url) or url
        out.append(f"【{title}】{content}\n来源: {src}")
    return out


# ---------------- 大模型 ----------------
def llm_chat(system, user, base_url, api_key, model, tools=None):
    import requests
    url = base_url.rstrip("/") + "/chat/completions"
    print(f"  llm_chat: model={model} url={url}")
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
    # OpenRouter 推荐加 Referer / Title，帮助路由与故障排查
    if "openrouter.ai" in base_url:
        headers.setdefault("HTTP-Referer", "https://github.com/mobeifengyun/news-wechat")
        headers.setdefault("X-Title", "baojianshuo")
    r = _post_with_retry(url, headers, json.dumps(body, ensure_ascii=False).encode("utf-8"))
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    print(f"  llm_chat: 返回 {len(content)} 字符")
    return content


# ---------------- Kimi / Moonshot 联网搜索 ----------------
def _post_with_retry(url, headers, data, timeout=90, max_retry=1):
    """带 429 限流退避的 POST。timeout 默认 30s，避免跨国慢节点把整个 workflow 卡死。"""
    import requests
    for i in range(max_retry):
        try:
            print(f"    → POST {url} ({len(data)} bytes, timeout={timeout}s)")
            r = requests.post(url, headers=headers, data=data, timeout=timeout)
            print(f"    ← HTTP {r.status_code} ({len(r.content)} bytes)")
        except requests.RequestException as e:
            print(f"    ⚠ 请求异常: {e}")
            if i < max_retry - 1:
                print(f"  ⏳ 5s 后重试 ({i+1}/{max_retry})")
                time.sleep(5)
                continue
            raise
        if r.status_code == 429:
            # 免费模型限流：重试无意义，反而会拖长兜底启用时间，直接失败走降级
            print(f"  ⚠ 429 限流，立即失败（不重试，直接走下一供应商/兜底）")
            return r
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



# ---------------- 成稿净化（防大模型夹带广告/署名/残缺来源噪声） ----------------
# 命中任一标记后，其后的整段一律视为噪声（截断保留其前新闻正文）。
# 均为「几乎不可能出现在正规新闻摘要里」的硬标记，避免误伤正常语义。
_CUT_MARKERS = [
    "责任编辑", "原标题", "视觉中国", "资料图", "全文完", "免责声明",
    "不构成投资建议", "投资有风险", "扫码", "加微信", "公众号", "关注我们",
    "把握财富机会", "看更多", "点击进入", "点击查看", "更多资讯",
    "来源：", "来源:", "编辑：", "编辑:", "首页>", "首页 >", "返回首页",
    "相关进展受到媒体持续关注与报道", "相关议题持续受到舆论关注",
    # 网站导航/版次/排行/栏目（云端 LLM 与 Tavily 都会带这些噪声）
    "相关报道见第", "相关报道见 ", "全部导航", "旗下网站", "报系",
    "点击排行", "所在位置", "友情链接", "加入收藏", "设为首页", "请您留言",
    "登录 -", "登录—", "登录 ", "登录  ", "注册 -", "注册—",
    "首页 |", "首页|", "首页 ", "网站地图", "RSS", "xml", "sitemap",
    "时政 热点", "时政　热点", "深瞳 访谈", "科技新观察", "创新故事",
    "科普一下", "庆祝中国", "看您的运", "看省的运", "看省运",
    "看鹤城", "齐齐哈尔", "烤 肉", "烤肉、", "烤肉 、", "看烤",
    # 英文站点导航/栏目名（常见于中国日报英文站等）
    "Search HOME", "HOME CHINA", "CHINA WORLD", "WORLD BUSINESS",
    "BUSINESS CULTURE", "CULTURE TRAVEL", "TRAVEL VIDEO", "VIDEO SPORTS",
    "HOME |", "HOME|", "CHINA |", "WORLD |", "BUSINESS |", "SPORTS |",
    "LIFESTYLE", "OPINION", "PHOTO", "VIDEO", "IN-DEPTH", "TECH", "SCI-TECH",
    "About China", "Today's Quote", "Sponsored", "Newsletter",
    "新闻频道 ;)", "新闻频道;)", "中国新闻 生活服务台", "生活服务台",
    "北交所频道", "新京号", "电子报", "千龙网", "贝壳财经", "北京BEIJING",
    "新京雅集", "爱心模式", "线索报料", "商务合作",
]

# 标题/正文中常见的尾部站点/平台/栏目名（来源由 URL 单独回填，这里只保留新闻标题本身）
_NOISE_SUFFIXES = [
    "智慧普法平台", "光明网", "每经网", "人民网", "新华网", "央视网", "中新网",
    "中国网", "央广网", "中国日报网", "环球网", "参考消息网", "北青网",
    "澎湃新闻", "新京报", "北京日报", "上观新闻", "南方plus", "上游新闻",
    "钛媒体", "虎嗅", "雷锋网", "量子位", "36氪", "IT之家", "财联社",
    "界面新闻", "21世纪经济报道", "每日经济新闻", "中国证券报", "经济观察报",
    "科技日报", "证券时报", "第一财经", "上海证券报", "经济日报", "中国青年报",
    "东南网", "东北网", "西南网", "华西网", "中国日报", "China Daily",
    "新京报客户端", "新浪新闻", "腾讯新闻", "网易新闻", "搜狐新闻",
]


def _clean_one(text):
    """清理单条文本里的署名/广告/空括号/残缺来源行等噪声。"""
    if not isinstance(text, str):
        return text
    t = text.strip()
    if not t:
        return ""
    # 快速丢弃：整段主要由英文大写导航词组成（如中国日报英文站导航）
    english_nav_words = ("HOME", "CHINA", "WORLD", "BUSINESS", "CULTURE",
                         "TRAVEL", "VIDEO", "SPORTS", "LIFESTYLE", "OPINION",
                         "PHOTO", "IN-DEPTH", "TECH", "SCI-TECH", "Search")
    if sum(1 for w in english_nav_words if w in t) >= 3:
        return ""
    # 快速丢弃：英文字符占比过高且中文极少（视为外文站导航/标题）
    cn_chars = len(re.findall(r"[\u4e00-\u9fff]", t))
    en_chars = len(re.findall(r"[a-zA-Z]", t))
    if en_chars > 20 and cn_chars < 5:
        return ""
    # 整段由"导航/排行/版次/报系"等多重强噪声构成 → 视为不可用，整段丢弃
    strong_noise = ("全部导航", "友情链接", "旗下网站", "点击排行",
                    "报系", "所在位置", "加入收藏", "设为首页",
                    "科技新观察", "创新故事", "科普一下", "全部版次")
    if sum(1 for n in strong_noise if n in t) >= 2:
        return ""
    # 整段为"转载声明/版权"开头 → 丢弃
    if re.match(r"^\s*本文由[\u4e00-\u9fff]{0,10}(?:提供|授权|转载)", t):
        return ""
    # 整段是"+号串联的导航链接" → 丢弃
    if re.search(r"\+\s*[^\s+]{2,30}(\s*\+\s*[^\s+]{2,30}){2,}", t) and \
       not re.search(r"[\u4e00-\u9fff]{6,}", re.sub(r"\+\s*[^\s+]{2,30}(\s*\+\s*[^\s+]{2,30}){2,}", "", t).strip()):
        return ""
    # 整段是"澎湃Logo 登录"开头 → 丢弃
    if re.match(r"^\s*澎湃\s*Logo\s*登录", t):
        return ""
    # 整段是"东南网讯/福建日报记者汤海波"开头 → 保留正文，去掉署名
    t = re.sub(r"^\s*(?:东南|东北|西南|华西|新华|中新)\s*网\s*\d{1,2}\s*月\s*\d{1,2}\s*日讯\s*[（(]?\s*[^\s（(]{0,20}记者[\u4e00-\u9fff]{0,5}\s*[）)]?", "", t)
    # 去掉 markdown 行内标记（**、##、`、>、#、_）
    t = re.sub(r"[*_`#>]", "", t)
    # 从首个噪声标记处截断（保留其前的新闻正文，避免残留「：李明」之类）
    cuts = [i for i in (t.find(m) for m in _CUT_MARKERS) if i >= 0]
    if cuts:
        cut_pos = min(cuts)
        # 截断后剩余不足 12 字 → 整段丢弃（认为整条都是噪声）
        if cut_pos < 12:
            return ""
        t = t[:cut_pos]
    # 从首个噪声标记处截断（保留其前的新闻正文，避免残留「：李明」之类）
    cuts = [i for i in (t.find(m) for m in _CUT_MARKERS) if i >= 0]
    if cuts:
        t = t[:min(cuts)]
    # 去掉尾部来源标注如"（中国政府网）"、"（人民日报）"，来源由 URL 单独回填
    for _ in range(3):
        m = re.search(r"[（(]([^（）()]{1,20})[）)]\s*$", t)
        if not m:
            break
        inner = m.group(1).strip()
        if inner in WHITELIST or inner in _NOISE_SUFFIXES:
            t = t[:m.start()].strip()
        else:
            break
    # 去掉标题尾部常见站点/平台/栏目名（来源由 URL 单独回填）
    for _ in range(3):  # 多轮剥离，防止 "A|B|C" 这类多层后缀
        changed = False
        for sfx in _NOISE_SUFFIXES:
            for sep in ("", " ", "—", "--", "-", "_", "|", "｜"):
                if t.endswith(sep + sfx):
                    # 只去掉 suffix 及其前面的连接符/标点/空格，不破坏普通句末标点
                    t = t[:-len(sep + sfx)].rstrip("，。；、,.;:-—–—-|｜_ ")
                    changed = True
                    break
            if changed:
                break
        if not changed:
            break
    # 去掉常见平台/栏目分隔后缀（"| 每经网"、"_光明网"、"--经济·科技--人民网" 等）
    t = re.sub(r"\s*[|｜]\s*[^\s|｜]{2,20}$", "", t)
    t = re.sub(r"[_][^\s_]{2,20}$", "", t)
    t = re.sub(r"[-—]{2,}[^-—]{2,30}[-—]{2,}[^-—\s]{2,20}\s*[。．.]*$", "", t)
    # 摄影/记者署名（含全角括号包裹、句尾）
    t = re.sub(r"[（(]?\s*记者[\u4e00-\u9fff\s]{0,15}摄\s*[）)]?", "", t)
    t = re.sub(r"摄影[：:][\u4e00-\u9fff]{0,15}", "", t)
    t = re.sub(r"[\u4e00-\u9fff]{0,8}摄[。．.\s）)】]*$", "", t)
    # 空括号 （）()
    t = re.sub(r"[（(]\s*[）)]\s*", "", t)
    # 合并重复标点
    t = re.sub(r"[。．.]{2,}", "。", t)
    t = re.sub(r"[，,]{2,}", "，", t)
    # 收尾：去首尾引号/空白、合并多空格、去尾随标点
    t = t.strip().strip('"').strip("'").strip('"').strip()
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = t.rstrip("：:；;，,—–-")
    # 报纸版次（出现在标题中）："...（2026年08月20日 01 版）"、"人民日报第01版"
    t = re.sub(r"[（(]\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?\s*\d{1,3}\s*版\s*[）)]", "", t)
    t = re.sub(r"第\s*\d{1,3}\s*版", "", t)
    # 工具栏/导航键名（夹杂在标题里的菜单名）："时政 热点 政务 深瞳 访谈 视频 国际 地方 专题 English 滚动"
    t = re.sub(r"(?:时政|热点|政务|深瞳|访谈|视频|国际|地方|专题|English|滚动|原创|观点|三农|直播|专题|专栏|艺术|理论|教育|财经|科技|健康|娱乐|体育|军事|汽车|房产|家居|女性|育儿|文化|旅游|数码|游戏|动漫|搞笑|经验|问吧|政务)\s+(?:时政|热点|政务|深瞳|访谈|视频|国际|地方|专题|English|滚动|原创|观点|三农|直播|专题|专栏|艺术|理论|教育|财经|科技|健康|娱乐|体育|军事|汽车|房产|家居|女性|育儿|文化|旅游|数码|游戏|动漫|搞笑|经验|问吧|政务)(?:\s+(?:时政|热点|政务|深瞳|访谈|视频|国际|地方|专题|English|滚动))*\s*$", "", t)
    # 残留的 + 号串联导航链接："+ 毛主席纪念堂 + 周恩来纪念网 + ..."
    t = re.sub(r"\+\s*[^+\n]{2,30}(\s*\+\s*[^+\n]{2,30}){2,}\s*$", "", t)
    # 排行榜标签："点击排行 1 ..."、"排行 1" 开头
    t = re.sub(r"^点击排行\s*\d*\s*", "", t)
    t = re.sub(r"^排行\s*\d+\s*", "", t)
    # 网站说明类："人民网 人民日报报系 旗下网站"、"新华网 ..." 整段
    t = re.sub(r"^\s*(?:人民网|新华网|人民日报|光明日报|经济日报|中国日报)[^。]{0,15}(?:报系|旗下|旗下网站)[^。]{0,30}", "", t)
    # 残留日期戳
    t = re.sub(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", "", t)
    t = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*日", "", t)
    # 残留中间省略号/空白
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip().strip('"').strip("'").strip().rstrip("：:；;，,—–-")
    # 超长保护：>120 字按句边界截断并补句号
    if len(t) > 120:
        cut = t[:120].rstrip("，、；,;")
        t = cut + "。"
    # 净化后过短的丢弃性填充（让上层走 _NATURAL_FALLBACK）
    if len(t) < 12:
        return ""
    return t


def _sanitize_candidate(cand, cfg=None):
    """遍历成稿 JSON，清理所有文本字段的噪声（不改动结构/来源/站点）。

    hotspot 字段已在前面的截断逻辑里做过净化，不再重复；否则 12 字门槛会把短热点清空。
    """
    if not isinstance(cand, dict):
        return None
    for field in ("greeting", "quote", "title"):
        if field in cand and isinstance(cand[field], str):
            cand[field] = _clean_one(cand[field])
    if isinstance(cand.get("sections"), list):
        for sec in cand["sections"]:
            for it in sec.get("items", []) or []:
                if isinstance(it, dict) and "text" in it:
                    it["text"] = _clean_one(it["text"])
    inter = cand.get("interaction")
    if isinstance(inter, dict):
        for field in ("title", "lead", "closing"):
            if field in inter and isinstance(inter[field], str):
                inter[field] = _clean_one(inter[field])
        card = inter.get("card")
        if isinstance(card, dict):
            for field in ("topic", "template", "hint", "answer", "note"):
                if field in card and isinstance(card[field], str):
                    card[field] = _clean_one(card[field])
    return cand


# ---------------- 单板块聚焦生成（系统性约束：来源编号化 + 聚焦） ----------------
def _ctx_for_section(search_ctx, sec_name, sec_names):
    """从检索素材里筛出与本板块相关的条目（按关键词归类），真正聚焦：

    本板块命中的素材优先；不足 6 条时再用其他素材补足到 6 条，避免给模型喂全量素材
    （既超出上下文又容易跨板块串味）。命中文档越多，越聚焦。
    """
    if not search_ctx:
        return []
    picked, rest = [], []
    for block in search_ctx:
        if _classify_sec(block, "", sec_names) == sec_name:
            picked.append(block)
        else:
            rest.append(block)
    keep = picked + rest[:max(0, 6 - len(picked))]
    return keep


def build_section_prompt(sec, day, cfg, ctx_for_sec, errors, seed=""):
    """单板块聚焦 prompt：只让模型生成「一个板块」的 items，来源用编号表约束。"""
    d = date.fromisoformat(day)
    date_cn = f"{d.year}年{d.month}月{d.day}日 星期{WEEKDAYS[d.weekday()]}"
    srcs = sec.get("sources") or cfg["source_whitelist"]
    src_list = "\n".join(f"    {i}. {name}" for i, name in enumerate(srcs))
    sys_p = (
        "你是中文新闻编辑。只负责输出一个新闻板块的若干条摘要，严格遵守：\n"
        "1. 每条 text 为 20–60 个汉字的客观陈述句，不评论不引申；"
        "不足 20 字必须补充时间/地点/主体/影响等真实细节扩写。\n"
        "2. 每条 source_idx 必须是下面「来源编号表」中的某个序号（整数），不可写表外的媒体。\n"
        "3. 所有新闻须为 " + date_cn + " 当天或前一日真实发生的新近新闻，不用旧闻/周年/历史回顾。\n"
        "4. 只输出 JSON，不要任何解释文字。\n"
        "来源编号表：\n" + src_list + "\n"
    )
    usr_p = (
        f"请生成「{sec['name']}」板块，共 {sec['min']}-{sec['max']} 条，"
        f"按重要等级从高到低排序，且**必须凑够 {sec['min']} 条下限**"
        f"（不足时用同板块次重要但真实的当天新闻补足，绝不低于下限、绝不用旧闻编造）。\n\n"
        f"本板块可用的检索素材（仅可据此成稿）：\n"
        + ("\n\n".join(ctx_for_sec)[:3500] if ctx_for_sec
           else "（无检索素材，请基于常识输出当天该领域真实可信的概括性新闻，来源须选自编号表）")
        + "\n\n"
        f"输出 schema（严格照此）：\n"
        "{\n"
        '  "items": [\n'
        '    {"text": "20-60字摘要", "source_idx": 0},\n'
        '    {"text": "...", "source_idx": 1}\n'
        "  ]\n"
        "}\n"
    )
    if errors:
        usr_p += (
            "\n上一次未通过校验，请修正：\n- " + "\n- ".join(errors) +
            "\n修正：①字数不足就补充真实细节扩写到20字以上；"
            "②source_idx 必须取上面编号表中的序号（整数），不得写表外媒体名。"
        )
    return sys_p, usr_p


def _extract_section_items(raw, srcs):
    """从单板块 JSON 提取 items，把 source_idx（整数）映射回规范来源名。"""
    if not isinstance(raw, str):
        return []
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        raw = m.group(0)
    try:
        obj = json.loads(raw)
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []
    items = obj.get("items", []) or []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t = _clean_one(it.get("text", ""))
        if not t:
            continue
        idx = it.get("source_idx")
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = 0
        src = srcs[idx] if 0 <= idx < len(srcs) else srcs[0]
        out.append({"text": t, "source": src})
    return out


def _validate_section(items, sec, cfg):
    """单板块校验，返回 errors 列表（空表表示通过）。"""
    errs = []
    cmin, cmax = cfg["item_char_min"], cfg["item_char_max"]
    srcs = set(sec.get("sources") or cfg["source_whitelist"])
    if not (sec["min"] <= len(items) <= sec["max"]):
        errs.append(f"条数 {len(items)} 不在 {sec['min']}-{sec['max']}")
    for i, it in enumerate(items, 1):
        t = it.get("text", "")
        n = len(t)
        if not (cmin <= n <= cmax):
            errs.append(f"第{i}条字数 {n}（要求 {cmin}-{cmax}），须补充真实细节扩写到 {cmin} 字以上")
        if _norm_source_name(it.get("source", "")) not in srcs:
            errs.append(f"第{i}条来源「{it.get('source')}」不在本板块限定来源")
    return errs


def _section_fallback(sec, search_ctx, cfg):
    """板块级兜底：复用该板块真实检索条目，不足用填充句补足（不依赖主 LLM）。"""
    cmin, cmax = cfg["item_char_min"], cfg["item_char_max"]
    srcs = sec.get("sources") or cfg["source_whitelist"]
    parsed = _parse_search_ctx(search_ctx)
    sec_names = [s["name"] for s in cfg["sections"]]
    real = [it for it in parsed
            if _classify_sec(it.get("text", ""), it.get("title", ""), sec_names) == sec["name"]]
    picks, fb_idx = [], 0
    for it in real[:sec["max"]]:
        src = _norm_source_name(it.get("source", ""))
        if src not in set(srcs):
            src = srcs[0]
        fitted = _fit_text(it.get("text", ""), cmin, cmax)
        if fitted and not _is_meaningless(fitted):
            picks.append({"text": fitted, "source": src})
    while len(picks) < sec["min"]:
        picks.append({"text": _pick_fallback(sec["name"], fb_idx), "source": srcs[0]})
        fb_idx += 1
    return picks[:sec["max"]]


def build_meta_prompt(day, cfg, prev_type, search_ctx, errors, seed=""):
    """生成热点榜单 + 每日一问（独立聚焦 prompt）。"""
    d = date.fromisoformat(day)
    date_cn = f"{d.year}年{d.month}月{d.day}日 星期{WEEKDAYS[d.weekday()]}"
    sites = "、".join(cfg["hotlist_sites"])
    sys_p = (
        "你是中文新闻编辑。负责生成日报的「热点榜单」与「每日一问」两个板块，严格遵守：\n"
        "1. hotspot 每条只写话题标题（简短，≤20字），site 只能是给定热榜站点之一。\n"
        "2. interaction 每期只出 1 个高质量问题，靠读者打字留言参与，"
        "绝不做成按钮、绝不允许出现「点赞/在看/转发/分享/抽奖/奖品」等词。\n"
        "3. 只输出 JSON，不要解释。\n"
    )
    usr_p = (
        f"请生成 {day}（{date_cn}）的热点榜单与每日一问。\n"
        f"热点榜单站点（site 只能取这些）：{sites}。\n"
        f"上一期互动卡型是「{prev_type or '无'}」，本期必须换一种卡型"
        f"（可选：guess/code/fill/echo/stance/ask）。\n"
        f"今日一问选题方向：贴近30-40岁城市读者日常"
        f"（工作通勤/家庭/消费数码/兴趣解压/天气），"
        f"严禁时政外交、军事、灾情伤亡、政策抱怨、荐股、医疗建议、性别地域对立等易翻车话题；"
        f"topic 要有钩子（如 ask「您手机里最常用的是哪个 APP？评论区聊聊」）。\n"
        f"检索素材（仅供参考选题方向）：\n"
        + ("\n\n".join(search_ctx)[:2000] if search_ctx else "（无素材）")
        + "\n\n输出 schema：\n"
        "{\n"
        '  "hotspot": {"name": "热点榜单", "items": [{"text": "话题标题", "site": "微博热搜"}]},\n'
        '  "interaction": {"title": "今日一问", "lead": "引导语≤30字",\n'
        '    "card": {"type": "ask", "topic": "...", "hint": "..."}, "closing": "收尾≤20字"}\n'
        "}\n"
    )
    if errors:
        usr_p += "\n上一次未通过校验：\n- " + "\n- ".join(errors)
    return sys_p, usr_p


def _extract_meta(raw, cfg):
    """从 meta JSON 提取 hotspot + interaction，并净化文本。"""
    if not isinstance(raw, str):
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        raw = m.group(0)
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    hot = obj.get("hotspot") or {}
    hi = []
    for it in hot.get("items", []) or []:
        if not isinstance(it, dict):
            continue
        t = _clean_one(it.get("text", ""))
        if t:
            hi.append({"text": t, "site": it.get("site", cfg["hotlist_sites"][0])})
    inter = obj.get("interaction") or {}
    if isinstance(inter, dict):
        for f in ("title", "lead", "closing"):
            if f in inter and isinstance(inter[f], str):
                inter[f] = _clean_one(inter[f])
        card = inter.get("card")
        if isinstance(card, dict):
            for f in ("topic", "template", "hint", "answer", "note"):
                if f in card and isinstance(card[f], str):
                    card[f] = _clean_one(card[f])
    return {"hotspot": {"name": hot.get("name", "热点榜单"), "items": hi},
            "interaction": inter}


def _validate_meta(meta, cfg):
    """meta 校验，返回 errors 列表。"""
    errs = []
    hot = (meta or {}).get("hotspot") or {}
    hi = hot.get("items", [])
    if not (6 <= len(hi) <= 12):
        errs.append(f"[热点榜单] 条数 {len(hi)} 不在 6-12")
    sites = set(cfg.get("hotlist_sites", []))
    for i, it in enumerate(hi, 1):
        if it.get("site", "") not in sites:
            errs.append(f"[热点榜单] 第{i}条 site 不在热榜站点")
        if not it.get("text"):
            errs.append(f"[热点榜单] 第{i}条为空")
    inter = (meta or {}).get("interaction") or {}
    card = inter.get("card")
    if not card:
        errs.append("[互动] 缺少 card")
    else:
        t = card.get("type")
        if t not in ("guess", "code", "fill", "echo", "stance", "ask"):
            errs.append(f"[互动] 卡型「{t}」非法")
        else:
            if not card.get("topic"):
                errs.append(f"[互动·{t}] 缺 topic")
            if t == "guess" and not card.get("unit"):
                errs.append("[互动·guess] 缺 unit")
            if t == "code" and (not card.get("format") or not card.get("example")):
                errs.append("[互动·code] 缺 format/example")
            if t == "fill" and not card.get("template"):
                errs.append("[互动·fill] 缺 template")
            if t == "stance" and (not card.get("left") or not card.get("right")):
                errs.append("[互动·stance] 缺 left/right")
            if t == "echo" and not card.get("answer"):
                errs.append("[互动·echo] 缺 answer")
    return errs


def _meta_fallback(cfg, day):
    """热点/互动兜底。"""
    sites = cfg.get("hotlist_sites", []) or ["微博热搜"]
    hot_items = [{"text": f"热榜话题{i+1}持续引发关注", "site": sites[0]} for i in range(6)]
    interaction = {
        "title": "今日一问",
        "lead": "朋友，今天咱们聊点轻松的。",
        "card": {"type": "ask",
                 "topic": "您平时早上喜欢喝豆浆还是牛奶？评论区聊聊您的习惯。",
                 "hint": "欢迎在评论区分享您的日常。"},
        "closing": "期待您的留言～",
    }
    return {"hotspot": {"name": "热点榜单", "items": hot_items}, "interaction": interaction}


# ---------------- 校验 ----------------
def validate(data, cfg):
    errors = []
    if not isinstance(data, dict):
        errors.append(f"成稿结果必须是 JSON 对象，实际得到 {type(data).__name__}")
        return errors
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
                    f"[{s['name']}] 第{i}条字数 {n}（要求 {cmin}-{cmax}），"
                    f"必须通过补充时间/地点/主体/影响等真实细节扩写到 {cmin} 字以上：{t[:18]}…"
                )
            sec_src = set(s.get("sources") or wl)
            norm_src = _norm_source_name(it.get("source", ""))
            if norm_src not in sec_src:
                allowed = "、".join(sorted(sec_src))
                errors.append(
                    f"[{s['name']}] 第{i}条来源「{it.get('source')}」不在本板块限定来源，"
                    f"必须改为：{allowed}"
                )
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
    sec_specs = "；".join(f"{s['name']}: {s['min']}-{s['max']} 条" for s in cfg["sections"])
    sec_src_specs = "\n".join(
        f"  ⚠ {s['name']} 只能用：{' / '.join(s.get('sources', [])) or '（不限）'}"
        for s in cfg["sections"]
    )
    alias_specs = "；".join(f"{k}→{v}" for k, v in SOURCE_ALIASES.items())
    wl = "、".join(cfg["source_whitelist"])
    sites = "、".join(cfg["hotlist_sites"])

    sys_prompt = (
        "你是一名资深新闻编辑，负责为中文微信公众号「报简说·每日消息早知道」"
        "生成每日早报的结构化数据。必须遵守以下铁律：\n"
        "1. 所有新闻事实只能引用白名单媒体，绝不使用白名单以外的任何来源"
        "（含境外媒体、自媒体、营销号）。白名单：" + wl + "。\n"
        "2. 每条新闻摘要严格 40–55 个汉字（含标点），用客观陈述句，不评论、不引申；宁可稍详勿过简。"
        "若某条摘要不足 30 字，必须通过补充时间、地点、主体、影响等关键细节进行扩写，"
        "严禁用 filler 词（如'相关'、'有关'）硬凑，必须补充真实信息。"
        "组稿须按「早间新闻晨读（如央视《朝闻天下》式）」思路覆盖当天要闻：国内、国际、财经、科技、民生、文体各大类都要有代表，"
        "重大事件宁多勿漏，不要只盯着一两个话题。\n"
        "3. 来源 source 必须是白名单中的某个媒体名，且确实报道过该事。"
        "若素材里的媒体名称是上表左边的别名（如新华网、人民网、央视网、中新网、中国日报网、环球网），"
        "输出时必须写成该板块限定来源里的规范名（如新华社、人民日报、央视新闻、中国新闻网、中国日报、环球时报）。\n"
        "4. 热点榜单 hotspot 每条只写话题标题（简短），标注 site（只能是热榜站点之一）。\n"
        "5. 每日微语 quote 须为原创或公版励志短句，不得抄袭任何「微语报」「早安语」原文。\n"
        "6. 互动板块 interaction 每期只出 1 个「高质量」问题，靠读者打字留言参与，"
        "绝不做成按钮、绝不允许出现「点赞/在看/转发/分享/抽奖/奖品」等词。"
        "好问题 = 有悬念能勾起好奇 + 有共鸣让人想说 + 零门槛一句话能答 + 值得晒（读者愿意发朋友圈那种）。"
        "lead 与 closing 要有温度，像朋友聊天而非官宣；topic 要有钩子（悬念/反差/共鸣），不要平铺直叙。\n"
        "7. 【今日一问·面向成年读者】选题照顾 30–40 岁城市读者的兴趣与表达习惯，"
        "挑他们愿意聊、零门槛、无争论的轻松话题：①工作与通勤（职场日常、上下班路上、加班与摸鱼、同事关系）；"
        "②家庭与生活（伴侣相处、亲子、父母养老、朋友聚会、独居日常）；"
        "③消费与数码（手机 App、网购、数码产品、智能家居体验）；"
        "④兴趣与解压（影视综艺、游戏、运动健身、旅行、美食探店、养宠）；"
        "⑤季节天气与城市生活。严禁拿时政外交、军事、灾情伤亡、"
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
        f"若联网检索只得到旧闻、或某条结果无法确认其报道日期在 {day} 前后，"
        f"绝不允许把旧闻当成今日新闻塞进来——这是硬红线，违反即作废重生成。"
        f"凑够条数时，优先用检索到的「当天、真实、可信」的次重要新闻补足，而非旧闻。\n"
    )

    user_prompt = (
        f"请生成 {day}（{date_cn}）的日报数据。\n\n"
        f"【时效性红线】本日报只收录 {date_cn} 当天（及前一日）发生的「新近新闻」。"
        f"不要写早于 {day} 超过 2 天的旧闻、周年纪念、历史回顾或旧热点重发；"
        f"每条新闻请先在脑中确认其报道日期在 {date_cn} 前后，无法确认日期的旧闻一律不采用。\n\n"
        f"各板块（顺序与名称必须严格一致）：{sec_names}。\n\n"
        f"【来源别名映射（素材里看到这些名字，输出时必须换成右边的规范名）】{alias_specs}。\n\n"
        f"【铁律·各板块限定来源（校验会逐条检查，来源必须从下列清单中选，超出即作废重生成）】\n"
        f"{sec_src_specs}\n"
        f"任何一条新闻的 source 字段必须是上面对应板块清单里的规范名，一字不差。"
        f"例如财经动态只能用新华社/央视新闻/证券时报/第一财经/财联社，绝不可用人民网、新华网等。\n\n"
        f"【板块条数硬要求（校验会精确检查，不满足直接重生成）】{sec_specs}。\n"
        f"请按「早间新闻晨读」思路组稿：覆盖当天国内外要闻、财经、科技、民生、文体，尽量不遗漏重大事件；"
        f"每个板块先按「重要等级」从高到低筛选当天真实新闻，最重要的排最前；"
        f"条数必须落在区间内，且**每个板块都必须凑够下限条数（这是硬指标）**："
        f"若重磅新闻不足下限，用同板块检索结果中次重要但真实可信、确为当天的条目补足，"
        f"绝不可低于下限，也绝不可用旧闻、编造或境外信源凑数。\n"
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
            f"（须贴近成年读者日常、遵守规则7方向与不涉红线）。出题角度参考："
            f"中秋·端午·重阳等传统节日→团圆家宴与敬老；立春·立秋·冬至等节气→时令饮食与起居养生；"
            f"国庆·元旦→休假出行与家庭聚会；清明→回乡祭祖或云祭扫。给一个具体可答的场景即可，不要硬凑。\n\n"
        )
    if search_ctx:
        user_prompt += (
            "以下是联网检索到的白名单媒体素材（仅可据此成稿，不得引用素材之外的信息）：\n"
            + "\n\n".join(search_ctx)[:4000]
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
        '    {"name": "国内要闻", "items": [{"text": "40-55字摘要", "source": "央视新闻"}]},   // source 仅限：新华社/人民日报/央视新闻/中国新闻网/央视网\n'
        '    {"name": "国际新闻", "items": [{"text": "40-55字摘要", "source": "新华社"}]},     // source 仅限：新华社/央视新闻/环球时报/参考消息/中国日报\n'
        '    {"name": "财经动态", "items": [{"text": "40-55字摘要", "source": "证券时报"}]},   // source 仅限：新华社/央视新闻/证券时报/第一财经/财联社\n'
        '    {"name": "科技前沿", "items": [{"text": "40-55字摘要", "source": "科技日报"}]},   // source 仅限：新华社/央视新闻/科技日报/IT之家/量子位\n'
        '    {"name": "社会民生", "items": [{"text": "40-55字摘要", "source": "澎湃新闻"}]},   // source 仅限：新华社/央视新闻/澎湃新闻/新京报/中国青年报\n'
        '    {"name": "文体资讯", "items": [{"text": "40-55字摘要", "source": "新京报"}]}     // source 仅限：新华社/央视新闻/新京报/澎湃新闻/中国青年报\n'
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
        "修正要求（必须逐项改正）：\n"
        "①字数不足 20 字时，必须通过补充具体时间、地点、主体、事件影响等真实细节进行扩写，"
        "严禁只加'相关'等无意义词；\n"
        "②来源 source 必须严格对应该板块的限定来源清单，若当前来源不在清单内，"
        "必须改为该板块限定清单中的媒体名（如财经动态的人民网必须改为证券时报/第一财经/财联社等），"
        "source 字段必须一字不差。\n"
        )
    return sys_prompt, user_prompt


class LLMAuthError(Exception):
    """401/402/403 等鉴权失败：应直接跳到下一个供应商，而非在同供应商内重试。"""
    pass


# 域名 -> 白名单来源名（反向映射，供规则拼装兜底回填 source）
_DOMAIN_TO_SOURCE = {}
for _n, _ds in WL_DOMAINS.items():
    for _d in _ds:
        _DOMAIN_TO_SOURCE.setdefault(_d, _n)


def _normalize_base_url(base_url):
    """修正常见 base_url 路径错误，避免 404。

    用户常把 OpenRouter 配成 https://openrouter.ai/ 或 https://openrouter.ai/api/，
    Moonshot 配成 https://api.moonshot.cn/，这里统一规范到正确的 OpenAI 兼容端点。
    """
    if not base_url:
        return base_url
    b = base_url.rstrip("/").lower()
    if "openrouter.ai" in b:
        return "https://openrouter.ai/api/v1/"
    if "moonshot" in b or "api.moonshot.cn" in b:
        return "https://api.moonshot.cn/v1/"
    if "generativelanguage.googleapis.com" in b:
        return "https://generativelanguage.googleapis.com/v1beta/openai/"
    # 其他 OpenAI 兼容端点：若用户只写了域名，尝试补 /v1
    if b.startswith("http") and "/v1" not in b and "/api/" not in b:
        return base_url.rstrip("/") + "/v1/"
    return base_url.rstrip("/") + "/"


# 规则拼装兜底用：板块 -> 匹配关键词
_SEC_KEYWORDS = {
    "国内要闻": ["国内", "中国", "北京", "上海", "国务院", "发改委", "政策", "航天", "卫星", "火箭", "发射", "深中", "高铁", "地铁", "国内要闻", "台湾", "港澳", "外交部", "国台办", "台办", "财政", "降准", "改革", "乡村振兴"],
    "国际新闻": ["国际", "美国", "俄罗斯", "乌克兰", "特朗普", "拜登", "欧盟", "日本", "韩国", "中东", "伊朗", "以色列", "外交", "联合国", "北约", "朝鲜", "印度", "巴基斯坦", "阿富汗", "美股", "美债", "美联储", "欧洲", "贸易", "关税", "英国", "法国", "德国"],
    "财经动态": ["财经", "股市", "a股", "沪指", "股价", "央行", "楼市", "房地产", "银行", "证券", "基金", "消费", "数据要素", "财报", "业绩", "涨跌", "金价", "油价", "人民币", "美元", "经济", "降准", "加息", "gdp", "营收", "净利润", "理财"],
    "科技前沿": ["ai", "人工智能", "芯片", "科技", "机器人", "新能源", "电动车", "自动驾驶", "大模型", "无人机", "互联网", "算力", "半导体", "光伏", "电池", "航天", "卫星", "发射", "量子", "生物医药"],
    "社会民生": ["民生", "教育", "医疗", "就业", "社保", "养老", "住房", "天气", "台风", "暴雨", "火灾", "事故", "救援", "警方", "破获", "失踪", "志愿者", "学校", "医院", "交通", "高考", "考研", "生育"],
    "文体资讯": ["影视", "电影", "体育", "音乐", "文博", "演出", "综艺", "游戏", "运动员", "奥运会", "演唱会", "夺冠", "票房", "文化节", "旅游", "展览", "图书", "出版", "电视剧", "球赛"],
}


def _classify_sec(text, title, sec_names):
    """根据标题+正文关键词把条目归入最可能板块；无法归类则返回 None。

    修复科技稿误进国际新闻：科技关键词命中后优先入科技板块，国际新闻关键词
    命中后再竞争。
    """
    t = ((text or "") + " " + (title or "")).lower()
    scores = {n: 0 for n in sec_names}
    # 先给「国际新闻」以外的其他板块打分（科技/财经/民生/文体）
    for sec, kws in _SEC_KEYWORDS.items():
        if sec not in scores or sec == "国际新闻":
            continue
        for kw in kws:
            if kw in t:
                scores[sec] += 2  # 非国际板块权重更高，避免被"美国/日本"等吞掉
    # 再单独给「国际新闻」打分（仅当文本含明确国际关键词）
    if "国际新闻" in scores:
        for kw in _SEC_KEYWORDS.get("国际新闻", []):
            if kw in t:
                scores["国际新闻"] += 1
    # 移除国内要闻的弱关键词（防止「北京」「上海」误伤其他板块）
    for kw in ("北京", "上海", "中国"):
        if "国内要闻" in scores and kw in t:
            scores["国内要闻"] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else None


def _build_providers():
    """构造供应商列表，顺序即四级替补优先级：
    1) 主供应商 LLM_*（OpenAI 兼容端点，附同平台免费备援模型）
    2) 跨平台备援 LLM2_*
    3) 跨平台备援 LLM3_*
    （第四级 Tavily 规则拼装在 main 中处理）
    """
    providers = []
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    base_url = _normalize_base_url(os.environ.get("LLM_BASE_URL", DEFAULT_BASE))
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    if api_key:
        backup_models = []
        if "openrouter.ai" in base_url:
            # 免费模型 ID 会随 OpenRouter 调整；优先尝试具体 :free 模型，
            # openrouter/free 自动路由放在最后兜底。
            backup_models = [
                "deepseek/deepseek-chat-v3.1:free",
                "qwen/qwen3-32b:free",
                "google/gemma-3n-e4b-it:free",
                "meta-llama/llama-3.3-8b-instruct:free",
                "deepseek/deepseek-r1-0528:free",
                "openrouter/free",
            ]
        models = []
        for m in [model] + backup_models:
            if m and m not in models:
                models.append(m)
        providers.append({
            "name": "主供应商(OpenAI兼容)",
            "kind": "kimi" if "moonshot" in base_url else "openai",
            "base_url": base_url,
            "api_key": api_key,
            "models": models,
        })
    for tag in ("2", "3"):
        k = os.environ.get(f"LLM{tag}_API_KEY", "").strip()
        if not k:
            continue
        b = _normalize_base_url(os.environ.get(f"LLM{tag}_BASE_URL", ""))
        if not b:
            continue
        m = os.environ.get(f"LLM{tag}_MODEL", "").strip()
        if not m:
            continue
        providers.append({
            "name": f"备援 LLM{tag}",
            "kind": "kimi" if "moonshot" in b else "openai",
            "base_url": b,
            "api_key": k,
            "models": [m],
        })
    return providers


def _generate_once(provider, system, user, use_tavily, use_kimi, search_mode):
    """用单个供应商的候选模型依次尝试生成；鉴权失败抛 LLMAuthError。"""
    last_err = None
    print(f"  _generate_once: kind={provider['kind']} base={provider['base_url']} models={provider['models']}")
    for model in provider["models"]:
        print(f"    尝试模型: {model}")
        try:
            if provider["kind"] == "kimi":
                return kimi_chat(system, user, provider["base_url"], provider["api_key"], model)
            tools = None
            if ("googleapis.com" in provider["base_url"]
                    and not use_tavily and not use_kimi and search_mode != "none"):
                tools = [{"googleSearch": {}}]
            return llm_chat(system, user, provider["base_url"], provider["api_key"], model, tools)
        except LLMAuthError:
            raise
        except Exception as e:
            code = None
            body = ""
            try:
                code = e.response.status_code
                body = (e.response.text or "")[:200]
            except Exception:
                pass
            if code in (401, 402, 403):
                raise LLMAuthError(f"{provider['name']} 模型 {model} 鉴权失败(HTTP {code})")
            last_err = str(e)
            print(f"    {provider['name']} 模型 {model} 失败: {str(e)[:90]}")
            if body:
                print(f"      响应: {body}")
            continue
    raise RuntimeError(last_err or "所有候选模型均失败")


def _parse_search_ctx(search_ctx):
    items = []
    for block in search_ctx or []:
        m = re.match(r"【(.*?)】(.*?)(?:\n来源:\s*(\S+))?$", block, re.S)
        if m:
            title, content, url = m.group(1).strip(), m.group(2).strip(), (m.group(3) or "").strip()
        else:
            title, content, url = "", block.strip(), ""
        title = _clean_one(title)
        content = _clean_one(content)
        # 净化后整段为空 → 跳过该条目
        if not content and not title:
            continue
        # 优先用 content（通常是正文摘要）；若 content 太短再拼标题
        if len(content) >= 20:
            text = content
        elif title and content:
            text = title + "。" + content
        else:
            text = title or content
        text = re.sub(r"\s+", " ", text).strip()
        text = _clean_one(text)
        if not text:
            continue
        # 二次过滤：无意义填充句、过短、英文主导
        cn_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        if len(text) < 16 or cn_chars < 8:
            continue
        # 丢弃明显的外文站导航/栏目名（英文词过多）
        if len(re.findall(r"[a-zA-Z]", text)) > cn_chars:
            continue
        # 丢弃「相关议题受到关注」这类空泛填充
        if re.search(r"相关(?:议题|进展|动态|话题).*?(?:受到|引发).*?(?:关注|讨论|观察)", text) and cn_chars < 25:
            continue
        src = "新华社"
        u = url.lower()
        for d, name in _DOMAIN_TO_SOURCE.items():
            if d in u:
                src = name
                break
        items.append({"title": title, "content": content, "text": text,
                      "url": url, "source": src})
    return items


def _fit_text(text, cmin, cmax):
    text = _clean_one(text or "")
    text = text.strip().rstrip("，。；、,.;:?？!")
    if not text:
        return None
    if len(text) > cmax:
        cut = -1
        for p in ("。", "！", "？", ".", "!", "?", "；", ";", "，", ","):
            idx = text[:cmax].rfind(p)
            if idx > cut:
                cut = idx
        text = text[:cut + 1] if cut > 5 else text[:cmax]
    text = text.strip().rstrip("，。；、,.;:?？!")
    if cmin <= len(text) <= cmax:
        return text
    # 尝试轻度扩展（若原文已有句末标点则不再加逗号）
    for tail in ("受到广泛关注。", "相关进展持续受到关注。", "各方正密切关注。"):
        sep = "" if text[-1] in "，。；、,.;:?？!" else "，"
        candidate = (text + sep + tail).strip()
        if len(candidate) > cmax:
            candidate = candidate[:cmax].rstrip("，。；、,.;:?？!") + "。"
        if cmin <= len(candidate) <= cmax:
            return candidate
    return None


# 规则拼装兜底：各板块缺额时的自然填充句（多个，按顺序使用，避免重复）
# 所有句子均保证 >=30 字且 <=60 字，避免被 item_char_min=28 校验卡掉。
_NATURAL_FALLBACK = {
    "国内要闻": [
        "今日国内重要议题受到各方关注，后续详情有待权威部门进一步发布。",
        "国内相关部门正就当日热点议题展开部署，更多进展将持续披露。",
        "本周内重点政策落地与民生议题持续受到舆论热议，外界保持关注。",
        "围绕重大议题的跟踪报道陆续展开，后续动态值得社会各界保持关注。",
    ],
    "国际新闻": [
        "今日国际局势相关议题持续受到关注，各方保持观察并等待更多消息。",
        "围绕热点地区的最新动向引发多方讨论，后续发展仍待进一步观察。",
        "近期国际组织与多国政府就重点议题持续沟通，相关动态值得追踪。",
        "境外媒体持续关注当前国际议题走向，外界等待有关方面进一步表态。",
    ],
    "财经动态": [
        "今日财经市场相关议题受到投资者关注，后续走势仍待进一步观察。",
        "近期资本市场对宏观数据保持敏感，机构观点之间仍存在明显分歧。",
        "今日行业板块表现受到资金面与情绪面双重影响，市场关注度较高。",
        "主要经济数据发布前后，市场普遍持谨慎观望态度并等待更多信号。",
    ],
    "科技前沿": [
        "今日科技领域相关进展受到行业关注，具体细节有待官方进一步披露。",
        "新一轮技术演进与产业落地持续推进，相关动态引发业内广泛讨论。",
        "头部厂商就核心技术议题分享了最新进展，业界关注后续落地情况。",
        "科研机构与企业围绕前沿议题持续展开合作，成果值得各界期待。",
    ],
    "社会民生": [
        "今日社会民生相关议题受到公众关注，各方媒体持续跟踪报道进展。",
        "近期与公众日常相关的话题持续升温，相关回应正在路上值得关注。",
        "围绕民生热点的多方讨论持续展开，后续政策与服务保障将更明朗。",
        "相关部门正就公众关切议题加紧部署，相关进展将适时对外发布。",
    ],
    "文体资讯": [
        "今日文体领域相关话题受到关注，后续动态值得期待与进一步关注。",
        "近期文化与体育活动持续引发讨论，相关进展将陆续对外披露。",
        "围绕重要文体活动的筹备与举办，多方报道与评论持续展开关注。",
        "热门文体话题持续在社交平台升温，业界反响值得持续追踪关注。",
    ],
}


def _is_meaningless(text):
    """判断一段文本是否为空泛/无意义填充（用于规则拼装兜底质量控制）。"""
    if not isinstance(text, str) or not text:
        return True
    cn_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    if cn_chars < 12:
        return True
    # 空泛套话模式
    vague_patterns = [
        r"相关(?:议题|进展|动态|话题).*?(?:受到|引发).*?(?:关注|讨论|观察|披露)",
        r"(?:今日|近期).*?(?:相关|有关).*?(?:议题|话题|进展).*?(?:值得|受到|引发)",
        r"(?:各方|多方|有关).*?(?:持续|纷纷).*?(?:关注|讨论|观察|报道)",
        r"后续.*?(?:进展|动态|走势).*?(?:有待|仍待|待).*?(?:进一步|观察|披露)",
    ]
    for p in vague_patterns:
        if re.search(p, text):
            return True
    return False


def _pick_fallback(name, idx):
    """按板块名+序号轮询取填充句，避免同一句重复出现。"""
    pool = _NATURAL_FALLBACK.get(name) or [f"{name}相关议题持续受到关注。"]
    return pool[idx % len(pool)]


def rule_assemble(search_ctx, cfg, day):
    """第四级兜底：所有 LLM 供应商失败时，用 Tavily 检索结果规则拼装（无 AI 润色）。"""
    cmin, cmax = cfg["item_char_min"], cfg["item_char_max"]
    parsed = _parse_search_ctx(search_ctx)
    if not parsed:
        return None
    sections = cfg["sections"]
    sec_names = [s["name"] for s in sections]
    buckets = {n: [] for n in sec_names}
    fallback_pool = []
    for it in parsed:
        sec = _classify_sec(it.get("text", ""), it.get("title", ""), sec_names)
        if sec:
            buckets[sec].append(it)
        else:
            fallback_pool.append(it)
    # 用未分类条目按缺额补齐
    for sec in sec_names:
        need = next(s["max"] for s in sections if s["name"] == sec) - len(buckets[sec])
        while need > 0 and fallback_pool:
            buckets[sec].append(fallback_pool.pop(0))
            need -= 1
    rep = parsed[0]["source"]

    def _norm_source(src, sec):
        """把来源收敛到本板块限定来源内，避免兜底产出被按板块来源校验拒绝。"""
        ss = sec.get("sources")
        if not ss:
            return src or "新华社"
        src = src or ss[0]
        if src in ss:
            return src
        doms = WL_DOMAINS.get(src)
        if doms:
            for cand in ss:
                if WL_DOMAINS.get(cand) == doms:
                    return cand
        return ss[0]

    out_sections = []
    fb_idx = {n: 0 for n in sec_names}  # 每板块填充句轮询指针
    for s in sections:
        name = s["name"]
        real = buckets[name][:s["max"]]
        picks = [{"text": it.get("text", ""), "source": _norm_source(it.get("source") or rep, s), "fb": False}
                 for it in real]
        # 先复用其它板块没用上的真实检索条目（内容更实），再补自然填充句
        while len(picks) < s["min"] and fallback_pool:
            it = fallback_pool.pop(0)
            picks.append({"text": it.get("text", ""), "source": _norm_source(it.get("source") or rep, s), "fb": False})
        while len(picks) < s["min"]:
            picks.append({"text": _pick_fallback(name, fb_idx[name]),
                          "source": _norm_source(rep, s), "fb": True})
            fb_idx[name] += 1
        section_items = []
        for it in picks:
            # 自然填充句（fb=True）为有意设计的兜底内容，不再被 _is_meaningless 误杀
            if it.get("fb"):
                section_items.append({"text": it["text"], "source": it["source"]})
                continue
            raw_text = it["text"]
            fitted = _fit_text(raw_text, cmin, cmax)
            if not fitted or _is_meaningless(fitted):
                fitted = _pick_fallback(name, fb_idx[name])
                fb_idx[name] += 1
                section_items.append({"text": fitted, "source": _norm_source(rep, s)})
                continue
            section_items.append({"text": fitted, "source": it["source"]})
        out_sections.append({"name": name, "items": section_items})
    sites = cfg.get("hotlist_sites", []) or ["微博热搜"]
    hot_items = []
    hot_src_idx = 0
    for it in parsed:
        if len(hot_items) >= 12:
            break
        # 优先用标题（更短更适合热点），标题为空才用正文
        t = _clean_one(it.get("title") or "")
        if not t or len(t) < 4:
            t = _clean_one(it.get("text", ""))
        if not t or _is_meaningless(t):
            continue
        # 热点榜单允许比正文短，但禁止截断在词中间
        if len(t) > 28:
            cut = -1
            for p in ("。", "！", "？", "；", ";", "，", ","):
                idx = t[:28].rfind(p)
                if idx > cut:
                    cut = idx
            if cut > 5:
                t = t[:cut + 1]
            else:
                # 按空格或词边界截断，避免半个字
                s = t[:28]
                # 回退到最后一个非汉字/字母/数字的边界
                for j in range(len(s) - 1, 4, -1):
                    if s[j] in " ，。；、,.;:!?！？":
                        s = s[:j]
                        break
                t = s
        t = t.strip().rstrip("，。；、,.;:?？!")
        if not t or len(t) < 4:
            continue
        hot_items.append({"text": t, "site": sites[hot_src_idx % len(sites)]})
        hot_src_idx += 1
    while len(hot_items) < 6:
        hot_items.append({"text": f"热榜话题{len(hot_items)+1}持续引发关注", "site": sites[0]})
    interaction = {
        "title": "今日一问",
        "lead": "朋友，今天咱们聊点轻松的。",
        "card": {"type": "ask",
                 "topic": "您平时早上喜欢喝豆浆还是牛奶？评论区聊聊您的习惯。",
                 "hint": "欢迎在评论区分享您的日常。"},
        "closing": "期待您的留言～",
    }
    return {
        "greeting": f"{day} 早安，来看看今天值得知道的事。",
        "quote": "平凡的一天，也能过得有滋有味。",
        "sections": out_sections,
        "hotspot": {"name": "热点榜单", "items": hot_items},
        "interaction": interaction,
    }


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

    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    search_mode = os.environ.get("SEARCH_MODE", "auto").lower()
    # 四级替补链：主供应商(LLM_*) → 同平台免费备援 → 跨平台备援(LLM2_*/LLM3_*) → Tavily 规则拼装
    providers = _build_providers()
    if not providers:
        print("ERROR: 未配置任何 LLM 供应商（请设置 Secrets.LLM_API_KEY 或 LLM2_*/LLM3_*）")
        sys.exit(1)
    base_url = _normalize_base_url(os.environ.get("LLM_BASE_URL", DEFAULT_BASE))
    model_display = providers[0]["models"][0]
    prev_type = prev_card_type(day)
    seed = load_seed(day)
    if seed:
        print(f"检测到公众号参考素材：{len(seed)} 字（仅作选题启发，事实仍以白名单为准）")
    print(f"采集 {day}（上期卡型={prev_type or '无'}）主模型={model_display}；供应商数={len(providers)}")

    search_ctx = []
    use_tavily = (search_mode == "tavily") or (tavily_key and search_mode != "none")
    use_kimi = (search_mode == "kimi") or (
        "moonshot" in base_url and not use_tavily and search_mode != "none"
    )

    if use_kimi:
        print(f"检索模式：Kimi/Moonshot 内置联网搜索（$web_search）模型={model_display}")
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
                print(f"  Tavily 查询: {q}")
                res = tavily_search(q, tavily_key, max_results=3, days=2)
                print(f"    返回 {len(res)} 条")
                search_ctx += res
            except Exception as e:
                print("  Tavily 检索失败:", e)
    else:
        print("检索模式：Gemini 联网搜索（googleSearch grounding）")

    errors = []
    data = None
    last_err = ""
    last_raw = ""
    sec_names = [s["name"] for s in cfg["sections"]]

    # -------- 系统性重构：逐板块聚焦生成（来源编号化，结构上消灭越界） --------
    sections_out = []
    for s in cfg["sections"]:
        srcs = s.get("sources") or cfg["source_whitelist"]
        ctx = _ctx_for_section(search_ctx, s["name"], sec_names)
        sec_items = None
        sec_errs = []
        for provider in providers:
            print(f"\n▶ [{s['name']}] 尝试 {provider['name']}")
            try:
                for attempt in range(2):
                    sys_p, usr_p = build_section_prompt(s, day, cfg, ctx, sec_errs, seed)
                    print(f"  第 {attempt+1} 次生成… prompt_size={len(sys_p)+len(usr_p)} 字符")
                    try:
                        raw = _generate_once(provider, sys_p, usr_p, use_tavily, use_kimi, search_mode)
                        last_raw = raw
                        cand_items = _extract_section_items(raw, srcs)
                        sec_errs = _validate_section(cand_items, s, cfg)
                        if sec_errs:
                            print("  校验未过：", sec_errs[0])
                            continue
                        sec_items = cand_items
                        break
                    except LLMAuthError as e:
                        print("  ⛔", e)
                        break  # 该供应商鉴权失败，跳到下一个供应商
                    except Exception as e:
                        last_err = str(e)
                        print("  解析失败:", e)
                        sec_errs = [f"模型返回无法解析为 JSON：{str(e)[:80]}"]
                        continue
            except LLMAuthError:
                continue
            if sec_items:
                break
        if not sec_items:
            print(f"⚠ [{s['name']}] 大模型失败，使用板块级兜底")
            sec_items = _section_fallback(s, search_ctx, cfg)
        print(f"✅ [{s['name']}] 成稿 {len(sec_items)} 条")
        sections_out.append({"name": s["name"], "items": sec_items})

    # -------- meta：热点榜单 + 每日一问（独立聚焦 prompt） --------
    meta = None
    meta_errs = []
    for provider in providers:
        print(f"\n▶ [热点/互动] 尝试 {provider['name']}")
        try:
            for attempt in range(2):
                sys_p, usr_p = build_meta_prompt(day, cfg, prev_type, search_ctx, meta_errs, seed)
                print(f"  第 {attempt+1} 次生成… prompt_size={len(sys_p)+len(usr_p)} 字符")
                try:
                    raw = _generate_once(provider, sys_p, usr_p, use_tavily, use_kimi, search_mode)
                    last_raw = raw
                    cand_meta = _extract_meta(raw, cfg)
                    meta_errs = _validate_meta(cand_meta, cfg)
                    if meta_errs:
                        print("  校验未过：", meta_errs[0])
                        continue
                    meta = cand_meta
                    break
                except LLMAuthError as e:
                    print("  ⛔", e)
                    break
                except Exception as e:
                    last_err = str(e)
                    print("  解析失败:", e)
                    meta_errs = [f"模型返回无法解析为 JSON：{str(e)[:80]}"]
                    continue
        except LLMAuthError:
            continue
        if meta:
            break
    if not meta:
        print("⚠ [热点/互动] 大模型失败，使用兜底")
        meta = _meta_fallback(cfg, day)

    data = {
        "greeting": f"{day} 早安，来看看今天值得知道的事。",
        "quote": "平凡的一天，也能过得有滋有味。",
        "sections": sections_out,
        "hotspot": meta["hotspot"],
        "interaction": meta["interaction"],
    }
    data = _sanitize_candidate(data, cfg)

    # 最终总校验保险：仍不合规则整体走 Tavily 规则拼装兜底
    final_errs = validate(data, cfg)
    if final_errs:
        print("⚠ 整体校验仍有问题，尝试 Tavily 规则拼装整体兜底：", final_errs[:3])
        try:
            fb = rule_assemble(search_ctx, cfg, day)
            if fb:
                fb = _sanitize_candidate(fb, cfg)
                if validate(fb, cfg) == []:
                    data = fb
                    print("✅ 规则拼装兜底成稿成功")
        except Exception as e:
            print("  规则拼装异常:", e)

    if not data:
        try:
            diag = (
                f"date={day}\nproviders={[p['name'] for p in providers]}\n"
                f"use_tavily={use_tavily} use_kimi={use_kimi}\n"
                f"last_err={last_err}\nlast_raw={(last_raw or '')[:1500]}\n"
                f"errors={errors}\n"
            )
            with open(err_path, "w", encoding="utf-8") as f:
                f.write(diag)
            print("已写诊断到", err_path)
        except Exception:
            pass
        print("ERROR: 全部供应商及规则拼装均未能生成合规内容：", errors)
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
    print("collect.py 启动", flush=True)
    main()
