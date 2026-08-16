# -*- coding: utf-8 -*-
"""漏跑检测与自愈补跑。

设计前提：整条链路分两段抗灾能力不同——
  1) 采集成稿（写 news_<date>.json）依赖 AI 联网搜索，脚本无法代劳；
  2) 渲染/手机页/推送是纯 Python，只要有网就能自动补。
本脚本负责：扫描缺期 -> 能自愈的直接修 -> 不能自愈的登记待办并推送提醒。

用法:
  python catchup.py            # 检测最近 7 天并自愈
  python catchup.py --days 14  # 指定回溯天数
  python catchup.py --check    # 只报告不动手
  python catchup.py --no-push  # 不发 ServerChan 提醒
"""
import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(BASE, "output")
TODO = os.path.join(BASE, "output", "_pending_catchup.json")
PY = sys.executable


def log(msg):
    print(f"[catchup] {msg}", flush=True)


def load_cfg():
    with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def net_ready(timeout=5):
    """检测公网连通性。开机瞬间网卡未就绪是已知故障源（2026-08-04）。"""
    for host, port in (("sctapi.ftqq.com", 443), ("www.baidu.com", 443)):
        try:
            socket.create_connection((host, port), timeout=timeout).close()
            return True
        except OSError:
            continue
    return False


def wait_for_net(max_wait=180, interval=10):
    """开机自启场景下等待网络就绪，最多等 max_wait 秒。"""
    waited = 0
    while waited < max_wait:
        if net_ready():
            if waited:
                log(f"网络就绪（等待 {waited}s）")
            return True
        log(f"网络未就绪，{interval}s 后重试…（已等 {waited}s）")
        import time
        time.sleep(interval)
        waited += interval
    return net_ready()


def scan(days):
    """返回 (missing_json, missing_html)：缺稿日 / 有稿但没渲染的日期。"""
    today = date.today()
    missing_json, missing_html = [], []
    for i in range(days):
        d = today - timedelta(days=i)
        ds = d.isoformat()
        has_json = os.path.exists(os.path.join(OUTPUT, f"news_{ds}.json"))
        has_html = os.path.exists(os.path.join(OUTPUT, f"article_{ds}.html"))
        if not has_json:
            missing_json.append(ds)
        elif not has_html:
            missing_html.append(ds)
    return sorted(missing_json), sorted(missing_html)


def run(cmd):
    log("$ " + " ".join(cmd[1:] if cmd[0] == PY else cmd))
    p = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    if p.stdout:
        print(p.stdout.strip()[-1500:])
    if p.returncode != 0 and p.stderr:
        print(p.stderr.strip()[-800:])
    return p.returncode


def heal(days_html):
    """自愈：JSON 在但 HTML 缺失 —— 纯脚本可修复，直接重渲染。"""
    fixed, failed = [], []
    for ds in days_html:
        code = run([PY, os.path.join(BASE, "render.py"), ds])
        (fixed if code == 0 else failed).append(ds)
    if fixed:
        run([PY, os.path.join(BASE, "mobile.py")])
    return fixed, failed


def write_todo(missing_json, failed_html):
    """把 AI 才能处理的缺口登记成待办，供下次 WorkBuddy 启动时读取补跑。"""
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "need_ai_collect": missing_json,
        "render_failed": failed_html,
        "note": "need_ai_collect 需要 AI 联网采集重新成稿，脚本无法自动完成",
    }
    os.makedirs(OUTPUT, exist_ok=True)
    with open(TODO, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def clear_todo():
    if os.path.exists(TODO):
        os.remove(TODO)


# 记录已告警过的漏跑日期，避免每次开机/登录都重复推送、浪费方糖额度。
STATE = os.path.join(BASE, "output", "_catchup_state.json")


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {"alerted": [], "ts": ""}


def save_state(alerted):
    os.makedirs(OUTPUT, exist_ok=True)
    json.dump(
        {"alerted": sorted(set(alerted)), "ts": datetime.now().isoformat(timespec="seconds")},
        open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2,
    )


def due_days(miss):
    """只把『已过云端计划运行时间(北京 07:00)』的日期视为真正的漏跑候选。

    当天 07:00 之前云端尚未触发，不算漏跑，避免一早重启误报。
    """
    now_bj = datetime.now(timezone(timedelta(hours=8)))
    out = []
    for ds in miss:
        d = date.fromisoformat(ds)
        if d < now_bj.date() or (d == now_bj.date() and now_bj.hour >= 7):
            out.append(ds)
    return out


def push(cfg, title, desp):
    key = (cfg.get("wechat") or {}).get("sckey", "")
    if not key or str(key).startswith("请填入"):
        log("SKIP 推送：未配置 sckey")
        return False
    try:
        import requests
        r = requests.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": desp},
            headers={"Content-Type":
                     "application/x-www-form-urlencoded; charset=utf-8"},
            timeout=20,
        )
        log(f"推送 {r.status_code} {r.text[:160]}")
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001
        log(f"推送失败: {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--check", action="store_true", help="只报告，不修复")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--wait-net", type=int, default=0,
                    help="先等待网络就绪的秒数（开机自启建议 180）")
    args = ap.parse_args()

    if args.wait_net:
        if not wait_for_net(args.wait_net):
            log("网络始终不可用，退出（下次开机会再检测）")
            return 2

    online = net_ready()
    log(f"网络状态: {'在线' if online else '离线'}")

    miss_json, miss_html = scan(args.days)
    log(f"回溯 {args.days} 天 -> 缺稿 {len(miss_json)} 天，缺渲染 {len(miss_html)} 天")
    if miss_json:
        log("  需 AI 采集: " + ", ".join(miss_json))
    if miss_html:
        log("  可自愈渲染: " + ", ".join(miss_html))

    if not miss_json and not miss_html:
        log("无缺口，一切正常")
        clear_todo()
        save_state([])  # 清零已告警记忆，下次真漏跑会重新提醒
        return 0

    if args.check:
        return 1

    fixed, failed = ([], [])
    if miss_html and online:
        fixed, failed = heal(miss_html)
        log(f"自愈完成: 成功 {len(fixed)}，失败 {len(failed)}")
    elif miss_html:
        log("离线，跳过渲染自愈")
        failed = miss_html

    pending = write_todo(miss_json, failed)

    # 只有『已到计划运行时间』且『此前未告警过』的漏跑才推送，避免每次开机重复扣方糖次数
    due = due_days(miss_json)
    prev = set(load_state().get("alerted", []))
    new = [d for d in due if d not in prev]
    if due:
        save_state(due)  # 标记这些日期已处理，后续开机不再重复推送
    if new and online and not args.no_push:
        lines = [
            f"**检测时间**：{pending['updated_at']}",
            f"**新增漏跑**：{len(new)} 天 —— " + "、".join(new),
            f"**此前已提醒(本次不再重复推送)**："
            + ("、".join(sorted(due - set(new))) or "无"),
            "",
            "这些日期需要重新联网采集成稿，脚本补不了。",
            "打开 WorkBuddy 说一句「补跑 news_wechat 缺失期次」即可。",
        ]
        if fixed:
            lines.append(f"\n（另有 {len(fixed)} 天已自动重渲染修复）")
        push(load_cfg(), "报简说 · 检测到漏跑", "\n\n".join(lines))
    elif new:
        log(f"有新增漏跑 {len(new)} 天（{', '.join(new)}），但本次因 --no-push 或离线未推送")
    elif due:
        log(f"漏跑日期 {len(due)} 天均已在既往开机时告警过，本次静默跳过，不重复推送")
    else:
        log("缺失日期均未到云端计划运行时间(北京07:00)，暂不视为漏跑")

    return 1


if __name__ == "__main__":
    sys.exit(main())
