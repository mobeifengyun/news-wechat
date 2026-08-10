# -*- coding: utf-8 -*-
"""公众号发布脚本：新增草稿 -> 群发（或发布到主页）。

用法:
  python publish.py [YYYY-MM-DD] [--draft-only]
流程:
  1. 获取 access_token（缓存到 output/token.json）
  2. 首次运行上传 cover.jpg 为永久素材，thumb_media_id 写回 config.json
  3. 新增草稿（draft/add）
  4. publish_mode = mass_send  -> 群发接口 message/mass/sendall（粉丝收到推送）
     publish_mode = freepublish -> 发布接口 freepublish/submit（仅进入发表记录/主页）
     --draft-only              -> 只存草稿，人工确认后发布
"""
import json
import os
import sys
import time
from datetime import date

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.weixin.qq.com/cgi-bin"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_token(cfg):
    cache_path = os.path.join(BASE, "output", "token.json")
    if os.path.exists(cache_path):
        c = load_json(cache_path)
        if c.get("expire_at", 0) > time.time() + 120:
            return c["access_token"]
    w = cfg["wechat"]
    r = requests.get(
        f"{API}/token",
        params={"grant_type": "client_credential", "appid": w["appid"], "secret": w["appsecret"]},
        timeout=15,
    ).json()
    if "access_token" not in r:
        raise RuntimeError(f"获取token失败: {r}（请检查AppID/AppSecret和IP白名单）")
    save_json(cache_path, {"access_token": r["access_token"], "expire_at": time.time() + r["expires_in"]})
    return r["access_token"]


def ensure_thumb(cfg, token, cfg_path):
    """确保有封面素材 thumb_media_id，没有则上传 cover.jpg。"""
    if cfg["wechat"].get("thumb_media_id"):
        return cfg["wechat"]["thumb_media_id"]
    cover = os.path.join(BASE, "cover.jpg")
    if not os.path.exists(cover):
        raise RuntimeError("缺少封面图 cover.jpg，请先生成或放置封面图")
    with open(cover, "rb") as f:
        r = requests.post(
            f"{API}/material/add_material",
            params={"access_token": token, "type": "image"},
            files={"media": ("cover.jpg", f, "image/jpeg")},
            timeout=30,
        ).json()
    if "media_id" not in r:
        raise RuntimeError(f"上传封面失败: {r}")
    cfg["wechat"]["thumb_media_id"] = r["media_id"]
    save_json(cfg_path, cfg)
    return r["media_id"]


def add_draft(cfg, token, day, html):
    d = date.fromisoformat(day)
    title = cfg["article"]["title_template"].format(date=f"{d.month}月{d.day}日")
    digest = cfg["article"]["digest_template"].format(date=f"{d.month}月{d.day}日")
    article = {
        "title": title,
        "author": cfg["wechat"]["author"],
        "digest": digest,
        "content": html,
        "thumb_media_id": cfg["wechat"]["thumb_media_id"],
        # 开启评论区，读者才能就结尾互动板块留言
        "need_open_comment": int(cfg["wechat"].get("open_comment", 1)),
        "only_fans_can_comment": int(cfg["wechat"].get("only_fans_can_comment", 0)),
    }
    payload = json.dumps({"articles": [article]}, ensure_ascii=False).encode("utf-8")
    r = requests.post(
        f"{API}/draft/add",
        params={"access_token": token},
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=30,
    ).json()
    if "media_id" not in r:
        raise RuntimeError(f"新增草稿失败: {r}")
    return r["media_id"]


def mass_send(token, media_id):
    payload = {
        "filter": {"is_to_all": True},
        "mpnews": {"media_id": media_id},
        "msgtype": "mpnews",
        "send_ignore_reprint": 1,
    }
    r = requests.post(
        f"{API}/message/mass/sendall",
        params={"access_token": token},
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=30,
    ).json()
    if r.get("errcode", 0) != 0:
        raise RuntimeError(f"群发失败: {r}")
    return r


def freepublish(token, media_id):
    r = requests.post(
        f"{API}/freepublish/submit",
        params={"access_token": token},
        json={"media_id": media_id},
        timeout=30,
    ).json()
    if r.get("errcode", 0) != 0:
        raise RuntimeError(f"发布失败: {r}")
    return r


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    draft_only = "--draft-only" in sys.argv
    day = args[0] if args else date.today().isoformat()

    cfg_path = os.path.join(BASE, "config.json")
    cfg = load_json(cfg_path)
    if "请填入" in cfg["wechat"]["appid"]:
        print("ERROR: 请先在 config.json 填入 AppID / AppSecret")
        sys.exit(1)

    # 发布前强制合规审核，存在 BLOCK 级违规则拒绝发布
    news_path = os.path.join(BASE, "output", f"news_{day}.json")
    if os.path.exists(news_path):
        from audit import audit_data, format_report

        findings, counts = audit_data(load_json(news_path))
        if counts.get("BLOCK", 0):
            print(format_report(findings, counts))
            print("ERROR: 内容审核未通过（存在 BLOCK 级违规），已终止发布")
            sys.exit(3)
        print(f"审核通过：BLOCK=0 WARN={counts.get('WARN', 0)} NOTE={counts.get('NOTE', 0)}")

    html_path = os.path.join(BASE, "output", f"article_{day}.html")
    if not os.path.exists(html_path):
        print(f"ERROR: 未找到 {html_path}，请先运行 render.py")
        sys.exit(1)
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    token = get_token(cfg)
    ensure_thumb(cfg, token, cfg_path)
    media_id = add_draft(cfg, token, day, html)
    print(f"草稿已创建 media_id={media_id}")

    if draft_only:
        print("OK: 仅存草稿模式，请到公众号后台确认发布")
        return
    mode = cfg["wechat"].get("publish_mode", "mass_send")
    if mode == "mass_send":
        r = mass_send(token, media_id)
        print(f"OK: 群发任务已提交 msg_id={r.get('msg_id')}")
    else:
        r = freepublish(token, media_id)
        print(f"OK: 发布任务已提交 publish_id={r.get('publish_id')}")


if __name__ == "__main__":
    main()
