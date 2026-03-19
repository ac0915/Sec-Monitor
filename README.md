# SEC Monitor

一个面向生产化演进的 SEC 披露监控系统。它不再只是“抓 feed 然后发提醒”的单脚本，而是拆成了可持续扩展的后端 API、数据库、采集 worker、现代化监控台，以及专门为后续 LLM 学习、评测、RAG 投喂准备的语料层。

## 现在这个项目是做什么的

目标很明确：

> 持续监控 watchlist 里的公司，一旦出现新的 SEC 披露，就完成采集、分级、入库、分析，并把结果沉淀成可复用的数据资产。

它同时服务三类需求：

- 实时监控：快速发现新的 `8-K`、`10-K`、`10-Q`、`Form 4`、`S-1`、`S-3`
- 研究工作台：在网页里查看事件、分级、摘要、趋势和运行状态
- LLM 数据底座：保存原文、结构化分析和切块结果，方便后续训练、RAG、评测和再加工

## 架构升级

### 1. 采集层

- 轮询 SEC EDGAR Atom feed
- 按公司名和 ticker 匹配 watchlist
- 提取表单类型和 `8-K Item`
- 计算 Tier 1 / 2 / 3
- 抓取主文档 HTML 正文

### 2. 数据层

默认支持两种模式：

- 本地开发：SQLite
- 生产部署：Postgres

数据库会沉淀这些核心数据：

- `filings`
  - SEC 披露基础信息
  - 原始 feed 摘要
  - 主文档正文
  - tier、表单类型、items、通知状态
- `analysis_results`
  - LLM 结构化分析结果
  - `impact`
  - `summary`
  - `key_takeaways`
  - 原始模型响应
- `filing_chunks`
  - 已切块的语料
  - 适合后续 embedding、RAG、训练样本整理
- `ingestion_runs`
  - 每次轮询的运行记录
  - 便于监控匹配率、异常和分析覆盖率
- `telegram_chats`
  - Telegram 会话元数据
  - 保存订阅状态、助手开关、最近收发时间
- `telegram_messages`
  - Telegram 入站 / 出站消息日志
  - 便于后续评测、回放、运营审计和 LLM 数据沉淀
- `telegram_state`
  - Telegram offset 与处理状态
  - 用于长轮询幂等与 webhook 去重

### 3. 分析层

- 支持多 provider 分析：`gemini`、`deepseek`、`grok`、`copilot`、`github`
- `copilot` 走官方 `Copilot SDK + Copilot CLI`
- `github` 走 GitHub Models 官方推理接口
- 输出结构化繁中摘要
- 统一落库，避免只存在 Telegram 或页面里

### 4. 展示层

网页已重做为真正的监控台，而不是 Firebase 读库模板页：

- 总览指标
- SEC Brief
- Signal Index
- Theme Correlations
- 本地 focus watchlist
- 持久化筛选与操作偏好
- Search / Command 入口
- 最近 14 天 tier 趋势
- LLM corpus readiness
- 情绪分布
- 最新 filing dossiers
- ingestion run 历史
- 单条 filing 的 detail drawer
  - AI 摘要
  - chunk 预览
  - 原始文档片段

## 项目结构

```text
.
├── main.py                    # FastAPI API + dashboard entry
├── sec_monitor.py             # Worker / CLI / corpus export entry
├── secmon/
│   ├── config.py              # 环境配置
│   ├── database.py            # SQLAlchemy engine/session
│   ├── models.py              # 数据模型
│   ├── pipeline.py            # 采集、分析、入库、导出
│   ├── watchlist.py           # 监控列表和映射
│   └── services/
│       ├── sec_client.py      # SEC feed + 文档抓取
│       ├── analysis.py        # 多 provider 分析层
│       └── telegram.py        # Telegram alerts + assistant + webhook/polling handling
├── public/
│   ├── index.html             # 新版控制台
│   ├── styles.css
│   ├── app.js
│   └── 404.html
├── Dockerfile
├── package.json
├── docker-compose.yml
├── requirements.txt
├── scripts/
│   └── copilot_analyze.mjs    # Copilot SDK helper
└── .env.example
```

## 核心能力

- 工业化拆分：API、worker、数据库、前端分层
- 配置外置：环境变量取代硬编码密钥
- 数据持久化：不再依赖内存 `seen_filings`
- LLM 语料化：正文自动切块入库
- 数据导出：可直接导出 JSONL 语料包
- 可运维：保存 ingestion runs，页面可见运行历史
- 可升级：SQLite 到 Postgres 只需切换 `DATABASE_URL`
- worldmonitor 风格情报层：brief、signal index、theme correlation、focus watchlist、可保存的工作台偏好

## 这次参考 worldmonitor 加入了什么

我没有照搬它的地图、地缘和多源新闻层，而是抽取了最适合 SEC Monitor 的核心机制：

- `SEC Brief`
  - 参考 worldmonitor 的 intelligence brief / daily brief 思路
  - 把最近真实 filing 聚合成统一概览、行动建议和风险观察
- `Signal Index`
  - 参考它的 composite risk score 思路
  - 把 tier、form、主题、影响、时效性折成 ticker 级别的关注度指数
- `Theme Correlations`
  - 参考它的 cross-stream correlation
  - 把跨 ticker 的披露按融资、业绩、治理、并购、合规、内部人活动等主题聚合
- `Focus Watchlist + Preferences`
  - 参考它的本地 watchlist / settings 持久化
  - 用户可在浏览器里保存自己的 focus ticker、dense mode、auto refresh、focus-only 模式
- `Search / Command`
  - 参考它的 search modal / command workflow
  - 可快速筛选 Tier 1、负面披露、主题簇、ticker 和单条 filing

## 数据原则

这个项目默认只展示真实数据，不注入任何 demo、mock 或伪造样本。

- API 返回的数据只来自已持久化的数据库记录
- 数据源是 SEC EDGAR feed 和实际抓取到的 SEC HTML 文档
- 如果当前没有抓到数据，页面会显示“暂无真实数据”，而不是填充示例卡片
- `0`、空列表、空状态都表示当前数据库里确实没有对应真实记录

## 本地启动

### 1. 安装依赖

建议先建立虚拟环境，避免和现有 Python 环境里的包冲突：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

然后安装依赖：

```bash
pip install -r requirements.txt
npm install
```

### 2. 配置环境变量

复制示例文件：

```bash
cp .env.example .env
```

至少应修改这些项：

- `SEC_USER_AGENT`
  - SEC 要求使用真实可联系的 `User-Agent`
- `DATABASE_URL`
  - 本地可以留空并使用默认 SQLite
- `ANALYSIS_PROVIDER`
  - 可选：`gemini`、`deepseek`、`grok`、`copilot`、`github`
- 对应 provider 的 API key
  - `GEMINI_API_KEY`
  - `DEEPSEEK_API_KEY`
  - `XAI_API_KEY`
  - `GITHUB_MODELS_TOKEN`
- 如果使用 `copilot`
  - 需要本机安装并登录 `Copilot CLI`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
  - 现在是可选项
  - 不填时，系统会通过 Telegram `getUpdates` 自动搜索最近活跃会话并选取最新 `chat_id`
- `TELEGRAM_ASSISTANT_ENABLED`
  - 是否启用 Telegram 双向助手
- `TELEGRAM_ALLOWED_CHAT_IDS`
  - 可选
  - 使用逗号分隔允许访问助手的 `chat_id`
  - 不填则默认允许所有主动与 bot 建立会话的聊天
- `TELEGRAM_WEBHOOK_SECRET`
  - webhook 模式建议配置
  - 用于校验 Telegram webhook secret header

### Provider 选择

默认：

```text
ANALYSIS_PROVIDER=gemini
```

可选 provider：

- `gemini`
  - 使用 `GEMINI_API_KEY`
  - 默认模型：`gemini-2.5-flash`
- `deepseek`
  - 使用 `DEEPSEEK_API_KEY`
  - 默认模型：`deepseek-chat`
- `grok`
  - 使用 `XAI_API_KEY`
  - 默认模型：`grok-4`
- `copilot`
  - 使用官方 `Copilot SDK + Copilot CLI`
  - 默认模型：`gpt-4.1`
- `github`
  - 使用 `GITHUB_MODELS_TOKEN`
  - 默认模型：`openai/gpt-4.1`

如果想统一覆盖模型名，可以直接设置：

```text
ANALYSIS_MODEL=your-model-name
```

否则系统会按 provider 使用各自默认模型或对应的 provider 专属模型变量。

### 如何获取各项凭证

下面只写这个项目真正需要的最小内容。

#### Gemini

```text
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
```

- `GEMINI_API_KEY`
  - 去 Google AI Studio 的 API Keys 页面创建或查看
  - 新用户在接受条款后，Google AI Studio 可能会自动创建默认项目和默认 key
  - 官方文档：
    - https://ai.google.dev/tutorials/setup
    - https://ai.google.dev/aistudio
- `GEMINI_MODEL`
  - 这是模型名，不是密钥
  - 默认的 `gemini-2.5-flash` 可以直接用，通常不需要改
  - 如果你要换模型，再到 Google AI Studio / Gemini 文档里确认当前可用模型

#### DeepSeek

```text
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

- `DEEPSEEK_API_KEY`
  - 先注册 DeepSeek Open Platform 账号，然后创建 API key
  - 官方文档明确写了：使用 DeepSeek API 前需要先创建 API key
  - 官方文档：
    - https://api-docs.deepseek.com/api/deepseek-api
    - https://api-docs.deepseek.com/
- `DEEPSEEK_MODEL`
  - 默认 `deepseek-chat`
  - 官方文档当前说明 `deepseek-chat` 是标准对话模型
- `DEEPSEEK_BASE_URL`
  - 默认保持 `https://api.deepseek.com`
  - 官方文档也提到可兼容使用 `https://api.deepseek.com/v1`，但本项目默认值不用改

#### Grok / xAI

```text
XAI_API_KEY=
XAI_MODEL=grok-4
XAI_BASE_URL=https://api.x.ai/v1
```

- `XAI_API_KEY`
  - 去 xAI Console 的 API Keys 页面创建
  - 官方入门文档明确写了：先在 xAI Console 创建 API key
  - 官方文档：
    - https://docs.x.ai/docs/tutorial
    - https://docs.x.ai/docs/key-information/usage-explorer
- `XAI_MODEL`
  - 这是 Grok 模型名，不是密钥
  - 默认 `grok-4`
  - 如果你要切到更快或推理版本，去 xAI docs / console 确认当前模型名
- `XAI_BASE_URL`
  - 默认保持 `https://api.x.ai/v1`
  - 官方 REST 文档的 chat completions 就挂在这个 base URL 下

#### GitHub Copilot

```text
COPILOT_MODEL=gpt-4.1
COPILOT_NODE_BINARY=node
```

- `COPILOT_MODEL`
  - 这是真正的 GitHub Copilot provider 使用的模型名
  - 本项目通过官方 `Copilot SDK` 调用，并依赖本机 `Copilot CLI` 的登录状态
  - 官方文档：
    - https://docs.github.com/en/copilot/how-tos/copilot-sdk/sdk-getting-started
    - https://docs.github.com/en/copilot/managing-copilot/configure-personal-settings/installing-github-copilot-in-the-cli
- `COPILOT_NODE_BINARY`
  - Node.js 可执行文件名
  - 默认 `node`
  - 一般不用改

#### GitHub Models

```text
GITHUB_MODELS_TOKEN=
GITHUB_MODELS_MODEL=openai/gpt-4.1
GITHUB_MODELS_BASE_URL=https://models.github.ai
GITHUB_MODELS_API_VERSION=2026-03-10
GITHUB_MODELS_ORG=
```

- `GITHUB_MODELS_TOKEN`
  - 你需要创建 GitHub Personal Access Token
  - 官方 quickstart 写的是带 `models` scope 的 PAT
  - 如果你走 fine-grained PAT / GitHub App，REST inference 文档要求有 `models: read`
  - 官方文档：
    - https://docs.github.com/en/github-models/quickstart
    - https://docs.github.com/en/rest/models/inference
- `GITHUB_MODELS_MODEL`
  - 这是模型 ID，不是 token
  - 默认 `openai/gpt-4.1`
  - GitHub Models 的模型 ID 形如 `publisher/model_name`
  - 你也可以改成 GitHub Models 目录里可用的别的模型
- `GITHUB_MODELS_BASE_URL`
  - 默认保持 `https://models.github.ai`
- `GITHUB_MODELS_API_VERSION`
  - 默认保持项目里的版本即可
  - 如果 GitHub 后续升级版本，再按官方文档同步
- `GITHUB_MODELS_ORG`
  - 可选
  - 只有你想把推理请求归属到某个 GitHub Organization 时才需要填
  - 不填就走通用 endpoint

#### Telegram

```text
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_ASSISTANT_ENABLED=true
TELEGRAM_ALLOWED_CHAT_IDS=
TELEGRAM_WEBHOOK_SECRET=
```

- `TELEGRAM_BOT_TOKEN`
  - 在 Telegram 里找 `@BotFather`
  - 使用 `/newbot` 创建机器人
  - BotFather 会返回 bot token
  - 官方文档：
    - https://core.telegram.org/bots
    - https://core.telegram.org/bots/features
- `TELEGRAM_CHAT_ID`
  - 现在是可选
  - 如果不填，本项目会自动调用 `getUpdates` 搜索最近活跃会话，并自动使用最新的 `chat.id`
  - 自动发现要生效，目标私聊 / 群组 / 频道里必须至少出现过一条发给 bot 的更新
  - 如果你想固定到某个会话，也可以手动填写
  - 手动获取方式仍然是：

```text
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

  - 在返回 JSON 里找到目标会话的 `chat.id`
  - 如果 bot 配置了 webhook，Telegram 不允许同时使用 `getUpdates`，这时需要手动填 `TELEGRAM_CHAT_ID`
  - 官方文档：
    - https://core.telegram.org/bots/api
- `TELEGRAM_ASSISTANT_ENABLED`
  - 默认 `true`
  - 设为 `false` 时保留 alert 能力，但不处理用户消息和指令
- `TELEGRAM_ALLOWED_CHAT_IDS`
  - 可选访问控制
  - 如果你只希望少数私聊 / 群组能使用助手，把允许的 `chat_id` 用逗号填进去
- `TELEGRAM_WEBHOOK_SECRET`
  - 用于 Telegram webhook header 校验
  - 线上部署建议配置，降低伪造请求风险

### 这些值哪些通常不用改

大多数情况下，下面这些值直接保留默认即可：

- `GEMINI_MODEL=gemini-2.5-flash`
- `DEEPSEEK_MODEL=deepseek-chat`
- `DEEPSEEK_BASE_URL=https://api.deepseek.com`
- `XAI_BASE_URL=https://api.x.ai/v1`
- `COPILOT_MODEL=gpt-4.1`
- `COPILOT_NODE_BINARY=node`
- `GITHUB_MODELS_BASE_URL=https://models.github.ai`
- `GITHUB_MODELS_API_VERSION=2026-03-10`

真正必须自己准备的，通常只有：

- `GEMINI_API_KEY`
- `DEEPSEEK_API_KEY`
- `XAI_API_KEY`
- Copilot CLI 登录态
- `GITHUB_MODELS_TOKEN`
- `TELEGRAM_BOT_TOKEN`

### 3. 启动 API

```bash
uvicorn main:app --reload
```

默认地址：

```text
http://127.0.0.1:8000
```

### 4. 启动 worker

持续轮询：

```bash
python sec_monitor.py run-worker
```

只跑一次：

```bash
python sec_monitor.py ingest-once
```

### 5. 启动 Telegram 双向助手

现在 Telegram 已经不是单向通知器，而是完整的会话入口。用户可以直接发送命令或自然语言问题，系统会基于数据库里的真实 SEC 数据检索，并调用你当前配置的 LLM provider 回答。

长轮询模式：

```bash
python sec_monitor.py run-telegram-worker
```

只同步一次更新：

```bash
python sec_monitor.py telegram-sync-once
```

可用命令：

- `/start`
- `/stop`
- `/help`
- `/status`
- `/subscribe`
- `/unsubscribe`
- `/latest [数量]`
- `/ticker <代码> [数量]`
- `/search <关键词>`
- `/ask <问题>`

也支持直接发送自然语言，例如：

- `总结一下 NVDA 最近的 SEC 披露`
- `找出最近涉及融资或稀释风险的文件`
- `AMD 最近有没有高优先级 8-K`

回答原则：

- 只使用本地数据库里已经持久化的真实 SEC 数据
- 不使用 demo、mock 或虚构样本
- 如果证据不足，会明确说数据库信息不够
- 默认附上 `filing_id / ticker / form / date` 作为资料来源

### 6. Telegram Webhook 部署

线上环境推荐 webhook，而不是持续长轮询。项目已提供：

```text
POST /api/integrations/telegram/webhook
```

建议在 `.env` 中设置：

```text
TELEGRAM_WEBHOOK_SECRET=your-secret
```

然后在 Telegram webhook 配置里使用同一个 secret，让 Telegram 通过 `X-Telegram-Bot-Api-Secret-Token` 发送到服务端。

注意：

- webhook 与 `getUpdates` 不能同时使用
- 如果你启用了 webhook，就不要再运行 `run-telegram-worker`
- 本地调试更适合长轮询，生产部署更适合 webhook

### Copilot Provider 官方认证方式

如果你要用真正的 `copilot` provider，而不是 GitHub Models：

1. 安装 Node 依赖
2. 安装 Copilot CLI
3. 完成 Copilot CLI 登录

最少步骤：

```bash
npm install
npm install -g @github/copilot
copilot
```

然后在 `.env` 里设置：

```text
ANALYSIS_PROVIDER=copilot
COPILOT_MODEL=gpt-4.1
```

说明：

- 本项目没有照抄 OpenClaw 的私有 token 交换链路
- 这里走的是 GitHub 官方文档里的 `Copilot SDK -> Copilot CLI` 认证路径
- 首次运行 `copilot` 时，按官方提示输入 `/login` 完成登录
- 也可以改用带 `Copilot Requests` 权限的 fine-grained PAT，并通过 `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN` 提供给 CLI
- 登录完成后，SDK 会通过本机 CLI 使用你的 GitHub / Copilot 授权
- 如果你只是想用 GitHub 官方推理 API，而不是 Copilot CLI 登录态，请使用 `ANALYSIS_PROVIDER=github`

## Docker / Compose

项目已经补上容器化入口，推荐生产或准生产环境直接用 Compose 启动：

```bash
cp .env.example .env
docker compose up --build
```

默认会启动：

- `postgres`
- `api`
- `worker`

## 为 LLM 学习和投喂准备的数据层

这是这次升级最关键的部分之一。

系统现在不是“生成一段摘要就结束”，而是把每条披露拆成后续可复用的数据资产：

- 原始文档正文
- 元数据
  - ticker
  - company
  - form type
  - tier
  - SEC items
  - 时间戳
- 结构化分析
  - impact
  - summary
  - key takeaways
- 语料 chunk
  - chunk index
  - char count
  - token estimate

这几层数据可以直接用于：

- RAG 知识库
- prompt evaluation dataset
- 监督微调样本整理
- 人工标注平台的基础数据源
- 事件驱动研究库

### 导出 JSONL 语料包

```bash
python sec_monitor.py export-llm-dataset --output data/llm_corpus.jsonl
```

导出的每一条记录都包含：

- filing 元信息
- 原始正文
- 分析结果
- 切块结果

## API

### `GET /api/health`

返回服务状态和最后一次 ingestion run。

### `GET /api/dashboard`

返回控制台所需总览数据，包括：

- 总 filing 数
- analysis 数
- chunk 数
- `SEC Brief`
- `Signal Index`
- `Theme Correlations`
- feed facets
- tier 分布
- sentiment 分布
- 最近 filings
- 最近 runs
- Telegram bot / assistant 可用状态
- Telegram 订阅会话数量

### `GET /api/feed`

返回可筛选的 filing feed，支持：

- `q`
- `tier`
- `impact`
- `form_type`
- `theme`
- `tickers`
- `limit`

### `GET /api/filings/{id}`

返回单条 filing 明细：

- AI 摘要
- raw document text
- chunk 列表
- 链接和元信息

### `POST /api/integrations/telegram/webhook`

供 Telegram webhook 调用的入站消息处理接口。

## 当前默认策略

- 所有匹配到的 filing 都可以抓主文档正文并入库
- Tier 1 / Tier 2 默认尝试做多 provider LLM 分析
- Tier 1 / Tier 2 默认尝试发 Telegram
- 数据块默认按 `1800` 字符切块，`250` 字符 overlap

这些都可以通过 `.env` 调整。

## 运行命令

```bash
# 启动 API
uvicorn main:app --reload

# 启动持续 worker
python sec_monitor.py run-worker

# 单次抓取
python sec_monitor.py ingest-once

# 导出 LLM 语料
python sec_monitor.py export-llm-dataset --output data/llm_corpus.jsonl
```

### 切换到 DeepSeek / Grok / Copilot / GitHub

```bash
# DeepSeek
ANALYSIS_PROVIDER=deepseek
DEEPSEEK_API_KEY=...

# Grok
ANALYSIS_PROVIDER=grok
XAI_API_KEY=...

# GitHub Copilot
ANALYSIS_PROVIDER=copilot
COPILOT_MODEL=gpt-4.1

# GitHub Models
ANALYSIS_PROVIDER=github
GITHUB_MODELS_TOKEN=...
GITHUB_MODELS_MODEL=openai/gpt-4.1
```

## 仍然保留但不再是主架构的部分

仓库里仍有 [`firebase.json`](/Users/lihiko/repo/Sec-Monitor/firebase.json)，但现在它已经不是主方案。

当前主路线是：

- FastAPI 提供 API 和页面
- SQL 数据库存储主数据
- worker 持续采集

如果后面要继续升级，建议优先做：

1. 加 Alembic migration
2. 增加认证和 admin 操作权限控制
3. 增加价格采集服务并统一入库
4. 对 chunk 增加 embedding 表
5. 接入标注流和模型评测流
