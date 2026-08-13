# -*- coding: utf-8 -*-
"""渲染每日新闻 JSON 为公众号图文 HTML（内联样式，兼容公众号编辑器）。

用法: python render.py [YYYY-MM-DD]
输入: output/news_<date>.json  输出: output/article_<date>.html
"""
import json
import sys
import os
from datetime import date

from audit import audit_data, format_report, interaction_cards

BASE = os.path.dirname(os.path.abspath(__file__))
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate(data, cfg):
    """校验条数、字数、来源白名单，返回警告列表。"""
    warnings = []
    wl = set(cfg["source_whitelist"])
    cmin, cmax = cfg["item_char_min"], cfg["item_char_max"]
    cfg_minmax = {s["name"]: (s.get("min", 3), s.get("max", 6)) for s in cfg["sections"]}
    for sec in data["sections"]:
        items = sec.get("items", [])
        lo, hi = cfg_minmax.get(sec["name"], (3, 6))
        if not (lo <= len(items) <= hi):
            warnings.append(f"[{sec['name']}] 条数 {len(items)} 不在 {lo}-{hi} 范围")
        for i, it in enumerate(items, 1):
            n = len(it["text"])
            if not (cmin <= n <= cmax):
                warnings.append(f"[{sec['name']}] 第{i}条字数 {n}，要求 {cmin}-{cmax}")
            src = it.get("source", "")
            if src and src not in wl:
                warnings.append(f"[{sec['name']}] 第{i}条来源「{src}」不在白名单")
    hot = data.get("hotspot") or {}
    hot_items = hot.get("items", [])
    if hot_items:
        if not (6 <= len(hot_items) <= 12):
            warnings.append(f"[热点榜单] 条数 {len(hot_items)} 不在 6-12 范围")
        sites = set(cfg.get("hotlist_sites", []))
        for i, it in enumerate(hot_items, 1):
            st = it.get("site", "")
            if st and sites and st not in sites:
                warnings.append(f"[热点榜单] 第{i}条来源「{st}」不在热榜站点列表")
    return warnings


def render(data, cfg):
    d = date.fromisoformat(data["date"])
    date_cn = f"{d.year}年{d.month}月{d.day}日 星期{WEEKDAYS[d.weekday()]}"
    lunar = data.get("lunar", "")
    if lunar:
        date_cn += f" 农历{lunar}"
    greeting = data.get("greeting", "工作愉快，生活喜乐！一文速览，众晓天下事。")
    brand = cfg.get("article", {}).get("brand", "报简说")
    color_map = {s["name"]: s["color"] for s in cfg["sections"]}
    parts = []
    parts.append(
        '<section style="margin:0 8px;font-family:-apple-system,BlinkMacSystemFont,'
        '\'Helvetica Neue\',\'PingFang SC\',sans-serif;">'
        f'<section style="text-align:center;padding:18px 0 6px;">'
        f'<p style="font-size:21px;font-weight:bold;color:#222;margin:0;letter-spacing:1px;">'
        f'<span style="color:#C0392B;">{brand}</span>'
        f'<span style="color:#ccc;font-weight:normal;padding:0 6px;">|</span>'
        f'每日消息早知道</p>'
        f'<p style="font-size:13px;color:#888;margin:8px 0 0;">{date_cn}</p>'
        f'<p style="font-size:13px;color:#888;margin:6px 0 0;">{greeting}</p>'
        '</section>'
        '<section style="height:1px;background:#e5e5e5;margin:14px 0 4px;"></section>'
    )
    for sec in data["sections"]:
        color = color_map.get(sec["name"], "#333333")
        parts.append(
            '<section style="margin-top:22px;">'
            f'<section style="display:inline-block;background:{color};color:#fff;'
            'font-size:15px;font-weight:bold;padding:5px 14px;border-radius:4px;">'
            f'{sec["name"]}</section>'
        )
        for i, it in enumerate(sec.get("items", []), 1):
            src = it.get("source", "")
            src_html = (
                f'<span style="color:#999;font-size:12px;">（{src}）</span>' if src else ""
            )
            parts.append(
                f'<p style="font-size:15px;color:#333;line-height:1.75;margin:12px 4px 0;">'
                f'<span style="color:{color};font-weight:bold;">{i}. </span>'
                f'{it["text"]}{src_html}</p>'
            )
        parts.append("</section>")
    hot = data.get("hotspot") or {}
    hot_items = hot.get("items", [])
    if hot_items:
        hcolor = "#D35400"
        parts.append(
            '<section style="margin-top:22px;">'
            f'<section style="display:inline-block;background:{hcolor};color:#fff;'
            'font-size:15px;font-weight:bold;padding:5px 14px;border-radius:4px;">'
            f'{hot.get("name", "热点榜单")}</section>'
        )
        for i, it in enumerate(hot_items, 1):
            site = it.get("site", "")
            site_html = (
                f'<span style="color:#999;font-size:12px;">（{site}）</span>' if site else ""
            )
            parts.append(
                f'<p style="font-size:15px;color:#333;line-height:1.75;margin:12px 4px 0;">'
                f'<span style="color:{hcolor};font-weight:bold;">{i}. </span>'
                f'{it["text"]}{site_html}</p>'
            )
        parts.append("</section>")
    quote = data.get("quote", "")
    if quote:
        parts.append(
            '<section style="margin-top:26px;background:#f7f7f7;border-radius:8px;'
            'padding:16px 18px;">'
            '<p style="font-size:14px;color:#555;line-height:1.8;margin:0;text-align:center;">'
            f'<span style="font-weight:bold;color:#333;">【每日微语】</span>{quote}</p>'
            "</section>"
        )
    parts.append(render_interaction(data))
    parts.append(
        '<section style="margin-top:28px;padding-top:12px;border-top:1px solid #e5e5e5;">'
        '<p style="font-size:12px;color:#aaa;text-align:center;line-height:1.8;margin:0;">'
        f'{brand} · 每日消息早知道<br>'
        '内容综合自新华社、人民日报、央视新闻等权威媒体公开报道<br>仅作信息分享，不构成任何建议'
        "</p></section></section>"
    )
    return "".join(parts)


GREEN = "#07C160"

# 卡片标签配色：每种互动形式一个色调，避免读者审美疲劳
CARD_LABEL = {
    "echo": ("上期揭晓", "#8E44AD"),
    "guess": ("盲猜", "#E67E22"),
    "code": ("打卡", "#07C160"),
    "stance": ("站队", "#2E86C1"),
    "fill": ("填空", "#16A085"),
    "ask": ("闲聊", "#7F8C8D"),
}


def _card_open(kind):
    label, color = CARD_LABEL.get(kind, CARD_LABEL["ask"])
    return (
        '<section style="background:#fff;border-radius:8px;padding:13px 14px 14px;'
        'margin-bottom:10px;">'
        f'<p style="margin:0 0 8px;"><span style="display:inline-block;background:{color};'
        'color:#fff;font-size:11px;font-weight:bold;letter-spacing:1px;'
        f'border-radius:3px;padding:2px 7px;">{label}</span></p>'
    )


def _topic(text):
    return (
        '<p style="font-size:14px;color:#333;line-height:1.75;margin:0;'
        f'font-weight:bold;">{text}</p>'
    )


def _hint(text):
    return (
        '<p style="font-size:12px;color:#999;line-height:1.7;margin:8px 0 0;">'
        f'{text}</p>'
    )


def _blank_box(inner):
    """虚线框：视觉上是「待填写」，不是可点击按钮。"""
    return (
        '<section style="border:1px dashed #c9c9c9;border-radius:6px;background:#fafafa;'
        'padding:10px 12px;margin:10px 0 0;">'
        f'{inner}</section>'
    )


def render_card(c):
    kind = c.get("type", "ask")
    p = [_card_open(kind)]

    if kind == "echo":
        p.append(_topic(c.get("topic", "上期答案揭晓")))
        if c.get("answer"):
            p.append(
                _blank_box(
                    '<p style="font-size:14px;color:#333;line-height:1.75;margin:0;">'
                    f'{c["answer"]}</p>'
                )
            )
        if c.get("note"):
            p.append(_hint(c["note"]))

    elif kind == "guess":
        p.append(_topic(c.get("topic", "")))
        unit = c.get("unit", "")
        p.append(
            _blank_box(
                '<p style="font-size:15px;color:#333;line-height:1.9;margin:0;'
                'text-align:center;letter-spacing:1px;">'
                '我猜 <span style="color:#E67E22;font-weight:bold;">＿＿＿＿</span>'
                f' {unit}</p>'
            )
        )
        p.append(_hint(c.get("hint", "评论区写下你的数字，答案公布时回来对一对。")))

    elif kind == "code":
        p.append(_topic(c.get("topic", "")))
        p.append(
            _blank_box(
                '<p style="font-size:13px;color:#666;line-height:1.9;margin:0;">'
                f'格式：<span style="color:#333;font-weight:bold;">{c.get("format", "")}</span>'
                "<br>"
                f'示例：<span style="color:{GREEN};">{c.get("example", "")}</span></p>'
            )
        )
        p.append(_hint(c.get("hint", "照着格式抄一行就行，几秒钟的事。")))

    elif kind == "stance":
        p.append(_topic(c.get("topic", "")))
        left, right = c.get("left", ""), c.get("right", "")
        p.append(
            _blank_box(
                '<p style="font-size:13px;color:#666;line-height:1.9;margin:0;'
                'text-align:center;">'
                '在评论区打这两个字<br>'
                f'<span style="font-size:16px;color:#2E86C1;font-weight:bold;">「{left}」</span>'
                '<span style="font-size:13px;color:#bbb;">　或　</span>'
                f'<span style="font-size:16px;color:#C0392B;font-weight:bold;">「{right}」</span>'
                "</p>"
            )
        )
        p.append(_hint(c.get("hint", "只打两个字也算数，明天公布两边人数。")))

    elif kind == "fill":
        p.append(_topic(c.get("topic", "")))
        p.append(
            _blank_box(
                '<p style="font-size:15px;color:#333;line-height:1.9;margin:0;'
                f'text-align:center;">{c.get("template", "")}</p>'
            )
        )
        p.append(_hint(c.get("hint", "把空补上，一句话就够。")))

    else:  # ask
        p.append(_topic(c.get("topic", "")))
        if c.get("hint"):
            p.append(_hint(c["hint"]))

    p.append("</section>")
    return "".join(p)


def render_interaction(data):
    """结尾互动板块：每期只出一个问题，靠打字参与，不含任何诱导分享/点赞内容。"""
    inter = data.get("interaction") or {}
    cards = interaction_cards(inter)
    if not cards:
        return ""
    card = cards[0]  # 一天只问一个问题，多余的忽略（audit 会报 WARN）
    p = ['<section style="margin-top:26px;border:1px solid #d9f0e2;border-radius:10px;'
         'background:#f4fbf7;padding:16px 14px 18px;">']
    p.append(
        f'<p style="font-size:16px;font-weight:bold;color:{GREEN};margin:0 0 4px;">'
        f'{inter.get("title", "今日一问")}</p>'
    )
    if inter.get("lead"):
        p.append(
            f'<p style="font-size:13px;color:#666;line-height:1.7;margin:0 0 12px;">'
            f'{inter["lead"]}</p>'
        )
    p.append(render_card(card))
    closing = inter.get("closing", "评论区打一行字就行，明天这一栏公布结果。")
    p.append(
        f'<p style="font-size:13px;color:#666;line-height:1.7;margin:10px 0 0;'
        f'text-align:center;">{closing}</p>'
    )
    p.append("</section>")
    return "".join(p)


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    cfg = load(os.path.join(BASE, "config.json"))
    src = os.path.join(BASE, "output", f"news_{day}.json")
    if not os.path.exists(src):
        print(f"ERROR: 未找到 {src}")
        sys.exit(1)
    data = load(src)
    warnings = validate(data, cfg)
    for w in warnings:
        print("WARN:", w)

    findings, counts = audit_data(data, day)
    print(format_report(findings, counts))

    html = render(data, cfg)
    out = os.path.join(BASE, "output", f"article_{day}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("OK:", out)

    if counts.get("BLOCK", 0):
        print("ERROR: 存在 BLOCK 级违规内容，禁止发布，请修正后重新渲染")
        sys.exit(3)
    if warnings:
        sys.exit(2)


if __name__ == "__main__":
    main()
