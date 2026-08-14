# -*- coding: utf-8 -*-
"""内容合规审核模块（微信公众平台运营规范 + 广告法要点）。

用法:
  python audit.py [YYYY-MM-DD]      # 独立审核某天的 news JSON
  render.py 会自动调用 audit_data()

分级:
  BLOCK 明确违规，必须修改后才能发布
  WARN  疑似违规/夸张表述，需人工确认
  NOTE  题材敏感提示，内容本身多为权威媒体报道，注意逐字与通稿一致
"""
import json
import os
import re
import sys
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))

# (级别, 分类, 正则, 处置建议)
RULES = [
    # ---------- BLOCK：微信明令禁止 ----------
    ("BLOCK", "诱导分享点赞",
     r"(点赞|集赞|求赞|点亮在看|点个在看|转发到朋友圈|分享到朋友圈|不转不是|转发本文|"
     r"扫码关注|长按识别关注|关注即送|关注领取|助力砍价|拉票|投票有奖|转发抽奖)",
     "删除诱导语，改为纯邀请留言：欢迎在评论区说说你的看法"),
    ("BLOCK", "医疗功效承诺",
     r"(包治百病|药到病除|彻底根治|根治率|治愈率100|抗癌神药|特效药|壮阳|"
     r"无效退款|包好|一疗程见效)",
     "删除疗效承诺，非医疗资质账号不得出现诊疗、功效表述"),
    ("BLOCK", "荐股保本承诺",
     r"(荐股|牛股|必涨|稳赚|包赚|保本保收益|保证收益|翻倍收益|内幕消息|老师带单|"
     r"跟我买|满仓|抄底良机)",
     "删除投资建议类表述，仅作客观信息陈述"),
    ("BLOCK", "低俗猎奇标题党",
     r"(福利姬|裸聊|一夜情|约炮|不可描述|少儿不宜|尺度太大|细思极恐|"
     r"震惊全国|惊呆了|删前速看|不看后悔|全国都在传|你绝对想不到|我甚至不知道)",
     "替换为客观中性的事实性标题"),
    ("BLOCK", "赌博迷信",
     r"(六合彩|博彩|赌场|开光|转运符|算命|改运|锦鲤转发|风水大师)",
     "删除封建迷信与赌博相关内容"),
    ("BLOCK", "违规营销",
     r"(加微信购买|私信下单|代购|微商|扫码进群领|限时秒杀|点击购买|领券下单)",
     "删除营销导流内容"),
    ("BLOCK", "绝对化广告用语",
     r"(史上最|全网最|100%有效|万能的|绝无仅有|独家秘方|第一品牌|无人能及)",
     "广告法禁用绝对化用语，改为客观描述"),

    # ---------- WARN：疑似违规，人工确认 ----------
    ("WARN", "绝对化/极限词",
     r"(最好|最佳|最优|最强|最先进|唯一|首个|独家|领先全球|世界级|国家级)",
     "若为权威媒体原文客观表述可保留；若属自行加工的评价性用语，请改为中性说法"),
    ("WARN", "夸张情绪化表述",
     r"(暴跌|崩盘|血洗|团灭|一夜蒸发|恐慌|跳崖式|雪崩|沦陷)",
     "财经/社会类避免情绪化渲染，建议改为中性动词（下跌、回调、下降）"),
    ("WARN", "预测性断言",
     r"(即将暴涨|势必|一定会|必然导致|注定)",
     "改为引述来源的判断，如「机构预计」「专家认为」"),

    # ---------- NOTE：题材敏感提示 ----------
    ("NOTE", "时政类内容",
     r"(外交部|国务院|中央政治局|领海|主权|声明|台湾|香港|澳门|军演|建军节)",
     "时政类需与权威通稿逐字一致，不得自行解读、评论或引申"),
    ("NOTE", "涉伤亡灾情",
     r"(死亡|遇难|伤亡|失联|坠毁|事故|爆炸|地震)",
     "客观陈述事实与官方数据，避免渲染细节、避免使用现场血腥描述"),
    ("NOTE", "涉未成年人",
     r"(未成年人|中小学生|幼儿园|留守儿童)",
     "不得披露未成年人身份信息，避免负面细节描写"),
    ("NOTE", "野生动物/食品安全",
     r"(娃娃鱼|野味|穿山甲|野生动物|保护动物|食品安全|超标)",
     "确认取自权威媒体已公开定性的报道，避免传播未经核实的食品安全指控"),
    ("NOTE", "涉宗教民族",
     r"(宗教|清真|穆斯林|寺庙|教堂|民族政策)",
     "严格引述官方表述，避免评论性内容"),
]

# 互动板块专用红线：不得出现诱导分享/点赞/抽奖
INTERACTION_FORBIDDEN = re.compile(
    r"(点赞|在看|转发|分享到|集赞|抽奖|送礼|红包|奖品|前\d+名|中奖)"
)

# ---------------- 互动选题风险闸门 ----------------
# 只作用于 interaction 板块，不影响新闻正文。
# 原理：新闻正文是引述权威通稿，可控；而互动是把话筒交给读者，
#       评论区由运营者承担管理责任。敏感题材一旦放开让人自由发言，
#       容易出现违规留言被判「导向问题」，删评往往来不及。
# 结论：新闻可以报，但绝不拿来当互动问题。
INTERACTION_TOPIC_RISK = [
    ("BLOCK", "互动涉时政外交",
     r"(外交部|国务院|中央|政策|领海|主权|台湾|香港|澳门|领土|"
     r"制裁|关税|总统|大选|议会|政府|官员|抗议|示威|战争|冲突)",
     "时政外交题材只报道、不提问。换成文体、天气、生活消费类话题"),
    ("BLOCK", "互动涉军事",
     r"(建军节|入伍|退伍|当过兵|服役|军种|部队|军演|阅兵|战士|军属|国防)",
     "涉军话题即使是正能量也不设互动，评论区易出现涉军敏感信息。可改为致敬式陈述句，不提问"),
    ("BLOCK", "互动涉灾情伤亡",
     r"(死亡|遇难|伤亡|事故|坠毁|爆炸|地震|洪灾|疫情|失联|遇害|自杀|殉职)",
     "灾情伤亡题材不做互动，避免消费苦难与情绪失控"),
    ("BLOCK", "互动涉民生政策抱怨",
     r"(油价|房价|房贷|个税|税负|社保|养老金|延迟退休|学区|中考|高考|"
     r"医保|看病贵|失业|裁员|降薪|物价|收费|罚款)",
     "此类提问会把评论区变成政策吐槽场，风险高于收益。换成无立场对抗的生活话题"),
    ("BLOCK", "互动涉投资荐股",
     r"(股票|个股|基金|买入|加仓|抄底|涨停|跌停|金价|币价|楼市|理财|收益率)",
     "不得引导读者讨论具体投资标的，无金融资质账号风险极高"),
    ("BLOCK", "互动涉医疗健康建议",
     r"(偏方|疗效|治病|保健品|降压|降糖|减肥|吃什么能|中药|处方)",
     "不得征集或讨论医疗健康建议，非医疗资质账号禁止"),
    ("BLOCK", "互动点名个人是非",
     r"(网红|明星|艺人|塌房|翻车|出轨|离婚|被举报|判刑|被抓|索赔|维权)",
     "不针对具体个人的争议做提问，避免侵权与网络暴力"),
    ("BLOCK", "互动涉群体对立",
     r"(男人都|女人都|男生|女生|彩礼|婆媳|重男轻女|地域|外地人|"
     r"东北人|河南人|上海人|北方人|南方人|穷人|富人)",
     "严禁制造性别、地域、阶层对立话题，这是评论区翻车最常见的来源"),
    ("WARN", "互动涉宗教民族",
     r"(宗教|信仰|清真|穆斯林|寺庙|教堂|民族)",
     "宗教民族话题不设互动"),
]

# 低风险互动题材（供选题参考，命中任一即视为安全区）
INTERACTION_SAFE_HINT = (
    "低风险选题方向：影视综艺票房收视、体育赛事结果竞猜、天气季节体感、"
    "饮食口味偏好、出行见闻、老物件怀旧、科技产品使用体验、方言俗语"
)


def interaction_cards(inter):
    """统一取出互动卡片。新结构 card(单个) > cards(列表) > 旧 vote/questions。"""
    if not inter:
        return []
    if inter.get("card"):
        return [inter["card"]]
    if inter.get("cards"):
        return list(inter["cards"])
    cards = []
    vote = inter.get("vote") or {}
    if vote.get("topic"):
        cards.append({"type": "stance", "topic": vote["topic"],
                      "left": vote.get("a", ""), "right": vote.get("b", "")})
    for q in inter.get("questions", []):
        cards.append({"type": "ask", "topic": q})
    return cards


def prev_card_type(day):
    """找上一期用过的互动卡型，用于避免连续重复。返回 (日期, 卡型) 或 None。"""
    if not day:
        return None
    out_dir = os.path.join(BASE, "output")
    if not os.path.isdir(out_dir):
        return None
    days = []
    for fn in os.listdir(out_dir):
        m = re.match(r"news_(\d{4}-\d{2}-\d{2})\.json$", fn)
        if m and m.group(1) < day:
            days.append(m.group(1))
    if not days:
        return None
    prev = max(days)
    try:
        with open(os.path.join(out_dir, f"news_{prev}.json"), "r",
                  encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    cards = interaction_cards(d.get("interaction") or {})
    if not cards:
        return None
    return prev, cards[0].get("type", "ask")


def _iter_texts(data):
    """遍历所有需要审核的文本，返回 (位置, 文本) 列表。"""
    out = []
    for k in ("greeting", "quote"):
        if data.get(k):
            out.append((k, data[k]))
    for sec in data.get("sections", []):
        for i, it in enumerate(sec.get("items", []), 1):
            out.append((f"{sec['name']}·第{i}条", it.get("text", "")))
    hot = data.get("hotspot") or {}
    for i, it in enumerate(hot.get("items", []), 1):
        out.append((f"热点榜单·第{i}条", it.get("text", "")))
    inter = data.get("interaction") or {}
    if inter.get("lead"):
        out.append(("互动·引导语", inter["lead"]))
    for i, q in enumerate(inter.get("questions", []), 1):
        out.append((f"互动·问题{i}", q))
    vote = inter.get("vote") or {}
    for k in ("topic", "a", "b"):
        if vote.get(k):
            out.append((f"互动·投票{k}", vote[k]))
    for i, c in enumerate(interaction_cards(inter), 1):
        tag = f"互动·问题{i}({c.get('type', '?')})"
        for k in ("topic", "format", "example", "hint", "template",
                  "left", "right", "answer", "note", "unit"):
            if c.get(k):
                out.append((f"{tag}·{k}", c[k]))
    if inter.get("closing"):
        out.append(("互动·结语", inter["closing"]))
    return out


def audit_data(data, day=None):
    """审核整份 JSON，返回 (findings, counts)。

    findings: [(level, category, where, hit, text, advice), ...]
    day 用于跨期检查互动卡型是否与上一期重复。
    """
    findings = []
    texts = _iter_texts(data)
    for where, text in texts:
        if not text:
            continue
        for level, cat, pat, advice in RULES:
            m = re.search(pat, text)
            if m:
                findings.append((level, cat, where, m.group(0), text, advice))

    # 时效性兜底：新闻正文出现明显早于目标年份的年份字样 → 疑似旧闻
    if day:
        try:
            target_year = int(day[:4])
        except Exception:
            target_year = None
        if target_year:
            for where, text in texts:
                if not text or where.startswith("互动"):
                    continue
                for ystr in re.findall(r"((?:19|20)\d{2})年", text):
                    yv = int(ystr)
                    if yv <= target_year - 1:
                        findings.append((
                            "WARN", "疑似旧闻", where, f"{ystr}年", text,
                            f"条文中出现 {ystr}年，早于目标日期 {day} 的年份，疑似旧闻/周年回顾，请核实报道时间",
                        ))

    # 互动板块红线单独强校验
    inter = data.get("interaction") or {}
    for where, text in texts:
        if where.startswith("互动"):
            m = INTERACTION_FORBIDDEN.search(text)
            if m:
                findings.append((
                    "BLOCK", "互动区诱导", where, m.group(0), text,
                    "互动引导只能邀请留言评论，不得涉及点赞/在看/转发/抽奖",
                ))

    # 互动选题风险闸门：敏感题材可以报道，但绝不做成提问
    for where, text in texts:
        if not where.startswith("互动") or not text:
            continue
        for level, cat, pat, advice in INTERACTION_TOPIC_RISK:
            m = re.search(pat, text)
            if m:
                findings.append((level, cat, where, m.group(0), text, advice))

    # 结构性检查：每期只出一个问题
    cards = interaction_cards(inter)
    if not cards:
        findings.append((
            "WARN", "缺少互动", "interaction", "-", "",
            "建议补充结尾互动板块，提升评论率",
        ))
    if len(cards) > 1:
        findings.append((
            "WARN", "互动问题过多", "interaction", f"{len(cards)}个", "",
            "每期只保留 1 个互动问题：问题一多读者反而不知道回哪个，留言率下降",
        ))
    if cards and inter.get("cards") and inter.get("card"):
        findings.append((
            "WARN", "互动结构冲突", "interaction", "card+cards", "",
            "同时存在 card 与 cards，请只保留单个 card 字段",
        ))
    if cards:
        kind = cards[0].get("type", "ask")
        if kind == "stance":
            findings.append((
                "NOTE", "站队式提问", "interaction", "stance", "",
                "站队天然制造两方对立，务必确认话题不含性别/地域/立场冲突属性",
            ))
        prev = prev_card_type(day)
        if prev and prev[1] == kind:
            findings.append((
                "WARN", "互动形式与上期重复", "interaction", kind, "",
                f"上一期（{prev[0]}）已用过 {kind}，建议换一种玩法保持新鲜感",
            ))

    counts = {"BLOCK": 0, "WARN": 0, "NOTE": 0}
    for f in findings:
        counts[f[0]] = counts.get(f[0], 0) + 1
    return findings, counts


def format_report(findings, counts):
    lines = []
    order = {"BLOCK": 0, "WARN": 1, "NOTE": 2}
    for f in sorted(findings, key=lambda x: order.get(x[0], 9)):
        level, cat, where, hit, text, advice = f
        lines.append(f"{level}: [{cat}] {where} 命中「{hit}」")
        if level in ("BLOCK", "WARN"):
            lines.append(f"      建议: {advice}")
    if any(f[1].startswith("互动涉") for f in findings):
        lines.append(f"      {INTERACTION_SAFE_HINT}")
    lines.append(
        f"AUDIT: BLOCK={counts.get('BLOCK', 0)} "
        f"WARN={counts.get('WARN', 0)} NOTE={counts.get('NOTE', 0)}"
    )
    return "\n".join(lines)


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    src = os.path.join(BASE, "output", f"news_{day}.json")
    if not os.path.exists(src):
        print(f"ERROR: 未找到 {src}")
        sys.exit(1)
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    findings, counts = audit_data(data, day)
    print(format_report(findings, counts))
    if counts.get("BLOCK", 0):
        sys.exit(3)


if __name__ == "__main__":
    main()
