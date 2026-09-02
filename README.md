# 面试靶场 (Interview Range)

**可自定义方向的 AI 面试练习场** —— 本地部署、零依赖：刷题、AI 模拟面试、终面官录用判定、逐题复盘追问、联网找新题。方向完全由你定义（运维、网络安全、前端、算法……都行），新建方向支持 AI 智能拆解。

纯 Python 标准库 + 原生前端，**零第三方依赖**，所有数据落在本地 JSON 文件。

## 前置要求

- **Python 3.8+**（仅标准库，无第三方依赖）
- **一个大模型 API**：兼容 OpenAI 的 Chat Completions 接口即可。优先读取 `~/.config/opencode/opencode.json`（取 baseURL 为 `https://token.sensenova.cn/v1` 的 provider），也可用环境变量直接指定（见「AI 模型配置」）
- **（可选）联网找新题**：需要本机的 `anysearch` CLI 用于联网搜索；没有它也不影响刷题、模拟面试、复盘等其他功能

## 快速开始

```bash
git clone <仓库地址> interview-range
cd interview-range
python3 server.py
# 打开 http://localhost:8787
```

> **仓库不包含题库数据**（含自定义方向内容），clone 后是空库。首次使用：新建方向（支持 AI 智能拆解），建好方向后用「联网找新题」导入题目，或手动录入。你的刷题记录、追问会话、面试历史等运行数据也全部留在本地 `data/`，不会随仓库提交。

## 功能

| 功能 | 说明 |
|------|------|
| 题库刷题 | 左侧方向栏分类（**可拖动排序**、删除进**回收站**可还原），题目卡片点击展开参考答案要点 + 常见追问；按状态（全部/未掌握/已掌握/收藏）与重要度（必考/高频/中频）筛选、关键词搜索 |
| 常见追问 | 点追问即新开一个**独立对话弹窗**（同题目多个追问可叠开多个、互不干扰），AI 以面试场景口吻回答；支持**流式输出**、**最小化悬浮条**（可暂时去干别的）、**重置**（清空记录重新开始）；每个追问的对话按「题目+追问」分文件持久化，下次打开不重复消费 |
| 联网找新题 | 右上角全局入口：多选方向、每方向搜题数（3/6/10/15 快捷或 1–30 自定义），AI 联网搜**真实面经**提炼候选题 → 勾选导入题库（不凭空编）。*需要本机 anysearch CLI* |
| 新建方向 | 输入一个领域名（如「网络安全」），**智能拆解**自动拆出多个子方向一键创建；也可直接创建单个方向作为兜底 |
| 模拟面试 | 聊天式一问一答：AI 面试官**针对你的回答追问**、答偏当场指出，满意后自动下一题；**综合混考**（全方向均匀抽题，可排除指定方向）或按方向单考；全程**流式输出**（思考过程折叠可见 + 正文打字机） |
| 断点续面 | 面试过程中每步落盘到 `data/live/`，页面刷新或重启服务都不丢；未完成面试在顶部「模拟面试」tab 显示橙色角标，可一键继续 |
| 现场出题 | 面试中随时点，AI 按已考知识点出**不重复**的新题（不入库、替换当前未答题） |
| AI 录用判定 | 答完由 AI 当**终面官**综合整场判定：录用 / 待定 / 不录用，附把握度、决策理由、亮点、硬伤、补救建议（不设固定分数线） |
| 完整报告 | 每场面试生成独立报告页：得分环、逐题得分/点评/采分点/遗漏、亮点与弱点 |
| 弱点报告 | 面试历史沉淀在此：按方向筛选、可搜索；每场点开逐题复盘（答得差的题优先展示） |
| 复盘追问 / 反驳 | 每道题可「追问这题」：AI 以导师身份深挖知识点，或你质疑评分它跟你掰扯；左右布局（回答记录 / 对话 / 弱点遗漏），支持最小化悬浮条 |

## 模拟面试的评分逻辑

**逐题**（AI 一次调用评完全场，无需分批，max_tokens 已放宽）：
- `score`：0–100（60 及格 / 80 良好 / 90 优秀 / 95 顶级）
- `strengths` / `weaknesses`：具体优缺点，不许空泛表扬
- `key_points` / `missed_points`：该题核心采分点 vs 你遗漏的点
- `followup`：考官式追问

评分看的是**完整对话**——被追问后答出来的点算你掌握，不算遗漏。

**整场**（`final_decision`）：AI 终面官综合技术深度、实战经验、思路清晰度、被追问后的表现、是否有硬伤、岗位匹配度直接判定，分数只作参考。

## 架构

```
interview-range/
├── server.py            # Python 标准库 HTTP 服务 + AI 调用（流式 / 指数退避重试）
│                        #   方向管理 / 会话 / 评分 / 终面官 / 联网找题 / 追问
├── store.py             # 数据存储层（分拆 JSON + 轮转日志 + 学习状态 + 追问会话）
├── data/                # 运行数据，全部被 .gitignore 忽略，不随仓库分发
│   ├── questions/       # 题库：_meta.json（元数据 + 方向）+ 每方向一个 json（首次使用自行创建）
│   ├── history/         # 面试历史：index.json 索引 + sessions/<id>.json 单场明细
│   ├── followups/       # 追问会话缓存：{qid}.json / {qid}__{sha1(topic)[:8]}.json
│   ├── live/            # 进行中面试的阶段性落盘（断点续面）
│   ├── state.json       # 学习状态（已掌握/收藏/上次方向/综合混考排除方向）
│   └── logs/            # 轮转应用日志（512KB × 3）
└── web/
    ├── index.html       # 单页结构（题库 / 模拟面试 / 弱点报告 / 完整报告 + 各模态框）
    ├── style.css        # 全站样式
    └── app.js           # 全部前端逻辑（分模块：刷题 / 面试 / 报告 / 追问 / 找题 …）
```

## API

### GET

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/api/questions` | 题库（合并所有方向文件） |
| GET | `/api/directions` | 方向列表 |
| GET | `/api/directions/trash` | 回收站中的方向 |
| GET | `/api/state` | 学习状态（已掌握/收藏/上次方向/排除方向） |
| GET | `/api/health` | 健康检查 + AI 配置可用性 |
| GET | `/api/history` | 面试历史索引（轻量摘要） |
| GET | `/api/history/<session_id>` | 单场完整明细（懒加载） |
| GET | `/api/followup/<qid>?topic=` | 某题某追问的对话会话 |
| GET | `/api/session/live` | 进行中的面试列表（断点续面） |
| GET | `/api/session/<session_id>` | 会话详情 |

### POST

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/api/session/new` | 开始面试（direction 或 `all` 综合混考，支持 `exclude_dirs`） |
| POST | `/api/session/answer/stream` | **流式**答题 → SSE 推送 reasoning / content / meta |
| POST | `/api/session/retry` | 重发当前题最后一条回答 |
| POST | `/api/session/skip` | 跳过当前题 |
| POST | `/api/session/generate` | AI 现场出题（不入库） |
| POST | `/api/session/end` | 结束面试：评分 + 终面官判定 + 落盘（未作答直接返回，不生成空报告） |
| POST | `/api/review/chat/stream` | **流式**复盘追问/反驳 → SSE |
| POST | `/api/followup/ask/stream` | **流式**常见追问 → SSE |
| POST | `/api/followup/reset` | 清空某追问的会话记录 |
| POST | `/api/find-questions` | 联网找题 → 返回候选（多方向） |
| POST | `/api/import-questions` | 确认导入题库 |
| POST | `/api/directions/expand` | AI 智能拆解领域名 → 候选子方向 |
| POST | `/api/directions/batch-add` | 批量创建方向 |
| POST | `/api/directions` | 新增 / 删除 / 重排方向（action=add\|delete\|reorder） |
| POST | `/api/directions/restore` | 回收站还原方向 |
| POST | `/api/directions/purge` | 彻底删除回收站中某方向 |
| POST | `/api/directions/purge-all` | 清空回收站 |
| POST | `/api/state/mastered` | 切换题目「已掌握」标记 |
| POST | `/api/state/bookmarks` | 切换题目收藏 |
| POST | `/api/state` | 更新学习状态 |
| POST | `/api/history/delete` | 删除一场面试记录 |

## AI 模型配置

支持任何**兼容 OpenAI Chat Completions** 的大模型服务，配置优先级：环境变量 > opencode 配置。

- 若本机装有 OpenCode：默认从 `~/.config/opencode/opencode.json` 读取，**遍历所有 provider，取第一个 `baseURL` 为 `https://token.sensenova.cn/v1` 的 provider**（不写死 provider 名），默认模型 `glm-5.2`
- 没有 opencode 配置时，直接用环境变量接入任意兼容服务（见下表）

已内置**盲目的指数退避自动重试**（默认最多 5 次，退避 1s→2s→4s→8s→16s）应对限流；评分接口要求输出合法 JSON（`response_format=json_object`），max_tokens 不设上限。

可用环境变量覆盖（优先级高于 opencode.json）：

```bash
INTERVIEW_RANGE_API_KEY=sk-xxx INTERVIEW_RANGE_BASE_URL=https://xxx INTERVIEW_RANGE_MODEL=glm-5.2 python3 server.py
```

| 变量 | 默认 | 说明 |
|------|------|------|
| `INTERVIEW_RANGE_PORT` | 8787 | 服务端口 |
| `INTERVIEW_RANGE_MODEL` | glm-5.2 | 面试官/评分/终面官模型 |
| `INTERVIEW_RANGE_MAX_RETRIES` | 5 | 429/5xx 最大重试次数 |
| `INTERVIEW_RANGE_BACKOFF` | 1.0 | 退避基数（秒），指数 = base × 2^(n-1) |
| `INTERVIEW_RANGE_TIMEOUT` | 120 | 单次请求超时秒数 |
| `INTERVIEW_RANGE_API_KEY` | 空(读opencode) | 覆盖 apiKey |
| `INTERVIEW_RANGE_BASE_URL` | 空(读opencode) | 覆盖 baseURL |
| `ANYSEARCH_CLI` | 见上方默认值 | anysearch CLI 的绝对路径（联网找题用；换成你自己的 anysearch 安装路径即可） |
| `INTERVIEW_RANGE_SEARCH_MAX` | 5 | 联网找题每方向搜索结果条数 |

## 用法建议

1. **先刷题**：题库 → 选方向 → 展开答案理解采分点（重点「必考/高频」），不会的随手点开常见追问问 AI
2. **再模拟**：综合混考或按方向开考，被追问别慌，答上来就算你会
3. **看判定**：结束后看终面官给的录用结论和硬伤清单
4. **要复盘**：弱点报告点开那场逐题看细节；不服评分就「追问这题」跟导师抠
5. **循环**：直到 AI 稳定给出「录用」

## 数据不丢

- 每场面试的逐题明细和录用判定都写进 `data/history/sessions/`，索引在 `data/history/index.json`
- 进行中的面试实时落盘 `data/live/`，刷新/重启后可续面
- 追问会话按题目+追问分别持久化，已问过的不重复消费 token
