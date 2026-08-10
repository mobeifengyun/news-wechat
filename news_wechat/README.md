# 每日新闻自动生成与公众号发布系统

## 目录结构
- `config.json` — 公众号密钥、板块定义、来源白名单、字数限制
- `render.py` — 将新闻 JSON 渲染为公众号图文 HTML（含条数/字数/来源校验）
- `publish.py` — 调用公众号 API：存草稿 → 群发（或仅发布到主页）
- `make_cover.py` — 生成默认封面图 cover.jpg
- `output/news_<日期>.json` — 每日新闻内容数据
- `output/article_<日期>.html` — 渲染后的图文 HTML

## 启用前必须完成（一次性配置）
1. 登录 [公众号后台](https://mp.weixin.qq.com) → 设置与开发 → 基本配置：
   - 复制 **AppID / AppSecret** 填入 `config.json` 的 `wechat` 字段
   - 在 **IP 白名单** 中添加本机公网 IP（否则 token 获取失败）
2. 确认公众号已完成微信认证（群发 API 需要认证）

## 手动运行
```
python render.py 2026-07-29          # 渲染
python publish.py 2026-07-29         # 存草稿并群发
python publish.py 2026-07-29 --draft-only   # 只存草稿，人工确认
```
Python 路径：`C:/Users/jhon/.workbuddy/binaries/python/envs/default/Scripts/python.exe`

## 自动化
已创建每日 07:30 定时任务：搜索热点 → 生成 JSON → 渲染校验 → 群发，8:00 前完成推送。

## 发布模式切换（config.json → wechat.publish_mode）
- `mass_send`：群发，粉丝收到推送（认证订阅号每天限 1 次）
- `freepublish`：仅发布到公众号主页/发表记录，不推送

## 合规提醒
发布时政类新闻采编内容需《互联网新闻信息服务许可证》。无资质请保持“权威媒体转述+注明来源”的形式，并留意平台规则。
