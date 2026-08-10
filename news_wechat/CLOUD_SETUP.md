# 报简说 · 云端全自动部署说明（GitHub Actions）

目标：**彻底不依赖本机 WorkBuddy 客户端与本机网络**，每天北京时间 07:00 自动完成
采集 → 渲染审核 →（可选）公众号群发 → 手机页 → 公网部署 → 微信推送。

---

## 一、要花多少钱

| 项目 | 费用 | 说明 |
|---|---|---|
| GitHub Actions 计算 | **免费** | Free 私有库 2000 分钟/月，本流程约 2–3 分钟/天，约 90 分钟/月 |
| GitHub Pages 托管手机页 | **免费** | |
| 大模型 + 联网搜索 | **≈ 免费** | 默认用 Gemini 免费额度（需免费 API Key，无需绑卡）；或 DeepSeek + Tavily，约 ¥0.1/月 |
| ServerChan 推送 | **免费** | 已有 SCKEY |
| 公众号群发 | **免费** | 需你自有公众号的 AppID/AppSecret（一直没填的那个卡点） |

> 比 Coze 的插件 API 收费方案省，且完全可控、源码在你手里。

---

## 二、准备工作（一次性）

### 1. 把项目推到 GitHub
```bash
cd news_wechat 的上级目录
git init
git add -A
git commit -m "init: 报简说云端流水线"
gh repo create baojianshuo-news --private   # 或用 GitHub 网页建库后 git remote add
git branch -M main
git push -u origin main
```

### 2. 配置 Secrets（仓库 Settings → Secrets and variables → Actions → New repository secret）
至少填下面两个，流水线即可跑通（手机页 + 微信推送）：

| Secret 名 | 必填 | 说明 |
|---|---|---|
| `LLM_API_KEY` | ✅ | 大模型 Key。默认 Gemini：`https://aistudio.google.com/apikey` 免费申请 |
| `LLM_BASE_URL` | 可选 | 默认 `https://generativelanguage.googleapis.com/v1beta/openai/`（Gemini OpenAI 兼容） |
| `LLM_MODEL` | 可选 | 默认 `gemini-2.5-flash` |
| `TAVILY_API_KEY` | 可选 | 填了就改用 Tavily 限定白名单域名检索（最稳，免费 1000 次/月）|
| `SEARCH_MODE` | 可选 | `auto`（默认）/ `tavily` / `none` |
| `SCKEY` | 可选 | ServerChan Key（config.json 里已写，可不重复填）|
| `WX_APPID` / `WX_APPSECRET` | 可选 | 填了才群发公众号；并把本机公网 IP 加进公众号后台 IP 白名单 |

> 想用 DeepSeek 替代 Gemini：把 `LLM_BASE_URL` 设为 `https://api.deepseek.com/v1/`，
> `LLM_MODEL` 设为 `deepseek-chat`，并**必须**再填 `TAVILY_API_KEY`（DeepSeek 无联网搜索）。

### 3. 开启 GitHub Pages
Settings → Pages → Build and deployment → Source 选 **Deploy from a branch**，
Branch 选 **gh-pages** → Save。约 1 分钟后手机页地址为：
`https://<你的用户名>.github.io/<仓库名>/`

---

## 三、怎么验证跑通

1. 进仓库 **Actions** 标签 → 选 `报简说·每日生成与发布（云端全自动）` → **Run workflow** 手动触发一次。
2. 看日志：采集是否成功生成 `output/news_<今天>.json`、渲染是否 `BLOCK=0`、手机页是否部署、微信是否收到推送。
3. 收到 ServerChan 推送即说明端到端通了。以后每天 07:00 自动跑。

---

## 四、与本地流程的关系

- **本地流程（WorkBuddy 定时/手动）保持不变**，照常可用。两者写同一个 `output/news_<date>.json`，
  且 `collect.py` 和本地都做了「当日已存在则跳过」的幂等保护，**不会重复生成或重复推送**。
- 云端产出会 `git commit` 回仓库，本地 `git pull` 即可同步历史，跨期互动查重也照常工作。
- 手机页地址：云端版在 GitHub Pages；本地版仍可部署到原 CloudStudio 链接。
  推送文案里的链接以触发方为准（云端推 GitHub Pages 链接，本地推 CloudStudio 链接）。

---

## 五、已知限制 / 注意

- **农历**由 `lunar_python` 计算，未装则留空，不影响发布。
- **公众号群发**目前因 AppID/AppSecret 未填而跳过；这是唯一还需你操作的卡点，本地云端都一样。
- GitHub Actions 若有 **60 天无任何提交**会自动停用定时任务；本流程每天提交产出，不会触发此限制。
- 大模型成稿偶有偏差，`collect.py` 内置校验+重试（最多 3 次），仍不过则任务失败并推送/留日志，不会发出违规内容。
- 国家/地区网络对 `api.tavily.com`、`generativelanguage.googleapis.com` 的可达性请自测；
  若 Gemini 不通，改用 DeepSeek+Tavily 组合。
