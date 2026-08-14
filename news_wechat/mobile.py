# -*- coding: utf-8 -*-
"""生成手机端「一键复制」页面，用于把日报正文搬进公众号编辑器。

用法:
  python mobile.py            # 收录 output/ 下全部日期
  python mobile.py 2026-07-31 # 只收录指定日期

产物: news_wechat/mobile_dist/index.html
可用 CloudStudio 部署为公网页面，手机浏览器打开 -> 点「复制全文」-> 粘贴到订阅号助手。
"""
import glob
import json
import os
import re
import sys
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "output")
DIST = os.path.join(BASE, "mobile_dist")


def load_cfg():
    with open(os.path.join(BASE, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def collect(day=None):
    cfg = load_cfg()
    art = cfg.get("article", {})
    title_tpl = art.get("title_template", "报简说 | 每日消息早知道（{date}）")
    digest_tpl = art.get("digest_template", "")
    pattern = f"article_{day}.html" if day else "article_*.html"
    files = sorted(glob.glob(os.path.join(OUT, pattern)), reverse=True)
    items = []
    for fp in files:
        m = re.search(r"article_(\d{4}-\d{2}-\d{2})\.html$", fp.replace("\\", "/"))
        if not m:
            continue
        d = m.group(1)
        with open(fp, "r", encoding="utf-8") as f:
            html = f.read()
        date_cn = f"{int(d[5:7])}月{int(d[8:10])}日"
        title = title_tpl.format(date=date_cn)
        digest = digest_tpl.format(date=date_cn) if digest_tpl else ""
        jp = os.path.join(OUT, f"news_{d}.json")
        if os.path.exists(jp):
            with open(jp, "r", encoding="utf-8") as f:
                data = json.load(f)
            digest = data.get("greeting", "") or digest
        items.append({"date": d, "title": title, "digest": digest, "html": html})
    return items


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>报简说 · 手机发布助手</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#f2f3f5;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif;color:#1a1a1a;padding-bottom:96px}
.bar{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid #e8e8e8;padding:10px 12px}
.bar h1{margin:0 0 8px;font-size:15px;font-weight:500}
.tabs{display:flex;gap:8px;overflow-x:auto;-webkit-overflow-scrolling:touch}
.tabs button{flex:0 0 auto;border:1px solid #ddd;background:#fff;color:#555;border-radius:16px;padding:5px 14px;font-size:13px}
.tabs button.on{background:#07c160;border-color:#07c160;color:#fff}
.tip{margin:12px 12px 0;background:#fff;border-radius:10px;padding:12px 14px;font-size:13px;line-height:1.7;color:#555}
.tip b{font-weight:500;color:#1a1a1a}
.tip ol{margin:8px 0 0;padding-left:20px}
.meta{margin:12px 12px 0;background:#fff;border-radius:10px;padding:12px 14px}
.meta .lbl{font-size:12px;color:#999;margin-bottom:4px}
.meta .val{font-size:14px;line-height:1.6;word-break:break-all}
.meta .row{margin-bottom:12px}
.meta .row:last-child{margin-bottom:0}
.copy-s{border:1px solid #07c160;background:#fff;color:#07c160;border-radius:6px;padding:4px 12px;font-size:12px;margin-top:6px}
.paper{margin:12px 12px 0;background:#fff;border-radius:10px;padding:14px 6px;overflow:hidden}
.dock{position:absolute;left:0;right:0;bottom:0;height:0}
.actions{position:fixed;left:0;right:0;bottom:0;z-index:30;background:#fff;border-top:1px solid #e8e8e8;padding:10px 12px calc(10px + env(safe-area-inset-bottom));display:flex;gap:10px}
.actions button{flex:1;border:0;border-radius:8px;padding:13px 0;font-size:15px;font-weight:500}
#btnRich{background:#07c160;color:#fff}
#btnCode{background:#f2f3f5;color:#444}
.toast{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);background:rgba(0,0,0,.82);color:#fff;padding:12px 20px;border-radius:8px;font-size:14px;z-index:99;opacity:0;transition:opacity .2s;pointer-events:none;max-width:80%;text-align:center;line-height:1.5}
.toast.on{opacity:1}
textarea{position:fixed;left:-9999px;top:0}
</style>
</head>
<body>
<div class="bar">
  <h1>报简说 · 手机发布助手</h1>
  <div class="tabs" id="tabs"></div>
</div>

<div class="tip">
  <b>怎么用</b>
  <ol>
    <li>点底部「复制全文（带排版）」</li>
    <li>打开「订阅号助手」App → 新建图文</li>
    <li>正文区长按 → 粘贴，排版会保留</li>
    <li>标题和摘要在下方单独复制</li>
  </ol>
</div>

<div class="meta">
  <div class="row">
    <div class="lbl">标题</div>
    <div class="val" id="mTitle"></div>
    <button class="copy-s" onclick="copyText(document.getElementById('mTitle').innerText,'标题已复制')">复制标题</button>
  </div>
  <div class="row">
    <div class="lbl">摘要</div>
    <div class="val" id="mDigest"></div>
    <button class="copy-s" onclick="copyText(document.getElementById('mDigest').innerText,'摘要已复制')">复制摘要</button>
  </div>
</div>

<div class="paper"><div id="paper"></div></div>

<div class="actions">
  <button id="btnCode" onclick="copyCode()">复制源码</button>
  <button id="btnRich" onclick="copyRich()">复制全文（带排版）</button>
</div>

<div class="toast" id="toast"></div>
<textarea id="sink"></textarea>

<script>
var DATA = __DATA__;
var cur = 0;

function toast(msg){
  var t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('on');
  clearTimeout(t._h); t._h = setTimeout(function(){ t.classList.remove('on'); }, 1900);
}

function render(i){
  cur = i;
  var d = DATA[i];
  document.getElementById('paper').innerHTML = d.html;
  document.getElementById('mTitle').textContent = d.title;
  document.getElementById('mDigest').textContent = d.digest || '';
  Array.prototype.forEach.call(document.querySelectorAll('#tabs button'), function(b, k){
    b.className = (k === i) ? 'on' : '';
  });
  window.scrollTo(0, 0);
}

function copyRich(){
  var node = document.getElementById('paper');
  var sel = window.getSelection();
  var range = document.createRange();
  range.selectNodeContents(node);
  sel.removeAllRanges();
  sel.addRange(range);
  var ok = false;
  try { ok = document.execCommand('copy'); } catch(e) { ok = false; }
  sel.removeAllRanges();
  if (ok) { toast('已复制，去订阅号助手粘贴'); return; }
  if (navigator.clipboard && window.ClipboardItem) {
    var html = node.innerHTML;
    navigator.clipboard.write([new ClipboardItem({
      'text/html': new Blob([html], {type:'text/html'}),
      'text/plain': new Blob([node.innerText], {type:'text/plain'})
    })]).then(function(){ toast('已复制，去订阅号助手粘贴'); })
      .catch(function(){ toast('自动复制失败，请长按正文手动全选'); });
  } else {
    toast('自动复制失败，请长按正文手动全选');
  }
}

function copyCode(){ copyText(DATA[cur].html, '源码已复制'); }

function copyText(txt, msg){
  var s = document.getElementById('sink');
  s.value = txt; s.select(); s.setSelectionRange(0, txt.length);
  var ok = false;
  try { ok = document.execCommand('copy'); } catch(e) {}
  if (!ok && navigator.clipboard) {
    navigator.clipboard.writeText(txt).then(function(){ toast(msg); });
    return;
  }
  toast(ok ? msg : '复制失败');
}

(function init(){
  var tabs = document.getElementById('tabs');
  DATA.forEach(function(d, i){
    var b = document.createElement('button');
    b.textContent = d.date.slice(5);
    b.onclick = function(){ render(i); };
    tabs.appendChild(b);
  });
  // 支持 ?d=YYYYMMDD / ?d=YYYY-MM-DD：方糖推送链接带该参数时，确定性打开当天，避免看到过期构建
  var idx = 0;
  try {
    var q = new URLSearchParams(location.search).get('d');
    if (q) {
      var want = q.length === 8 ? (q.slice(0,4)+'-'+q.slice(4,6)+'-'+q.slice(6,8)) : q;
      DATA.forEach(function(d, i){ if (d.date === want) idx = i; });
    }
  } catch(e) {}
  render(idx);
})();
</script>
</body>
</html>
"""


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else None
    items = collect(day)
    if not items:
        print("ERROR: output/ 下没有找到 article_*.html，请先运行 render.py")
        sys.exit(1)
    os.makedirs(DIST, exist_ok=True)
    page = PAGE.replace("__DATA__", json.dumps(items, ensure_ascii=False))
    target = os.path.join(DIST, "index.html")
    with open(target, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"OK: 已生成 {target}")
    print(f"     收录 {len(items)} 期：{', '.join(i['date'] for i in items)}")


if __name__ == "__main__":
    main()
