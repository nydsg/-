# 校园创作 · AI 内容共治社区

> 一个面向高校的短视频创作社区：用**多模态审核 Agent**做内容初审，用 **RAG 检索校规与历史判例**让每次判定都有依据，用**人工复核闭环**让误判可申诉、可沉淀。

---

## 一、项目介绍

### 要解决什么问题

校园 UGC 平台面临一个两难：**管得太松**，违规内容（擦边、灰产、侵权）会流出去；**管得太严**，正常的校园创作（成人礼、体育竞技、实验演示）会被误伤，学生会失去表达意愿。

现有做法通常是「关键词过滤 + 人工抽查」：规则生硬、误判高、判定不可解释，学生被拒后也不知道为什么。

### 我们的方案

把「内容审核」从**黑箱判定**变成**可解释、可申诉、可协作**的过程：

| 能力 | 作用 |
|---|---|
| 多模态审核 Agent | 抽帧 → 视觉风险 → 画面文字 → 规范检索 → 综合裁定，**6 步全部可观测** |
| RAG 规范检索 | 每次判定都引用具体条款（如 `R2-2 礼仪性场景豁免`），而不是一句"违规" |
| 人工复核闭环 | AI 判「转人工」的进入审核台；人工结论会**回流成判例**，让下次检索更准 |
| 可解释前端 | Agent 每步耗时、证据、命中条款实时渲染成**推理时间线** |

### 目标用户

高校学生创作社区、校园媒体中心、需要对 UGC 做合规审核的教育机构。

---

## 二、核心功能

1. **投稿与审核（两种方式）**：
   - **切片审核**（推荐）：按时间把视频切成若干段，逐段交给多模态模型判定，**能定位到「第几秒到第几秒」有问题**，前端渲染成彩色时间轴
   - **整片审核**：整条视频给一个结论 + 完整证据链，速度快、消耗少
2. **AI 创作副驾（看视频写文案）**：说不清自己拍了什么？直接上传视频，AI 看完画面后给出 3 个备选标题、简介、标签和风险提醒；点「带着这些去投稿」会把标题 / 简介 / 标签一起预填到投稿表单
3. **人工审核台**：审核员查看待审队列、预览作品，可一键跑**切片分析**把问题定位到具体秒数，再决定通过或拒绝；结论自动写入判例库
4. **AI 创作助手**：流式对话，帮学生起标题、写简介、生成标签，并提示潜在违规表达
5. **规范检索**：8 条校园内容规范 + 历史判例，TF-IDF 检索，返回相关度与条款原文
6. **社区互动**：点赞、收藏（分文件夹）、评论（二级回复）、关注、通知、浏览历史
7. **个人主页**：作品墙、统计数据、签名与头像背景

### 创作副驾是怎么工作的

```
用户上传视频（也可以直接把文件拖进对话框）
   ↓
抽帧 12 帧 → 按清晰度/去重挑 3 帧
   ↓
多模态模型逐帧理解：场景 / 画面文字 / 内容标签
   ↓
结合画面理解写文案，流式输出：
   ├─ 一段口语化解说（边生成边显示）
   └─ 结构化结果：3 个标题（真诚 / 有梗 / 文艺）+ 80 字简介 + 5 个标签
   ↓
用户可一键复制，或点"带着这些去投稿"跳转到投稿页并预填
```

它和审核 Agent **共用同一套视觉工具**，所以成本一致、判断口径也一致：
副驾说"这条适合发"，审核那边大概率也是 pass；如果审核侧发现风险，副驾会同步给出 `⚠️` 提醒。

---

## 三、技术架构

```
┌─────────────────────────────────────────────────────────┐
│  前端（React 18 + TypeScript + Vite + Tailwind）         │
│  ├─ 路由级懒加载 + vendor 分包（14 个 chunk）             │
│  ├─ TanStack Query（服务端状态）+ Zustand（会话状态）     │
│  ├─ SSE 客户端（fetch + ReadableStream，支持中断）        │
│  └─ ErrorBoundary + 骨架屏 + 响应式布局                   │
└──────────────────────┬──────────────────────────────────┘
                       │ REST + SSE (/api/*)
┌──────────────────────▼──────────────────────────────────┐
│  后端（Flask 3，应用工厂 + 蓝图）                          │
│  ├─ api/auth      注册登录会话、审核员申请                 │
│  ├─ api/videos    列表分页、详情、投稿、播放量             │
│  ├─ api/social    点赞收藏评论关注通知历史                 │
│  ├─ api/profile   主页、资料、搜索、推荐流                 │
│  ├─ api/review    审核队列、通过拒绝、判例库               │
│  └─ api/ai        流式对话、审核 Agent、RAG 检索（SSE）    │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│  AI 层（app/ai）                                          │
│  ├─ llm.py    OpenAI 兼容适配器（DeepSeek/智谱/通义/OpenAI）│
│  ├─ agent.py   6 步审核流水线 + 规则兜底 + 安全判定表       │
│  ├─ rag.py     TF-IDF 检索 + 8 条规范 + 判例回流           │
│  └─ tools.py   6 个工具（Function Calling Schema 完备）    │
└──────────────────────┬──────────────────────────────────┘
                       │
              ┌────────▼────────┐        ┌──────────────────┐
              │  MySQL (16 张表) │        │  MCP Server       │
              └─────────────────┘        │  对外暴露 6 个工具 │
                                         └──────────────────┘
```

### 关键设计取舍

| 决策 | 理由 |
|---|---|
| 后端用 Flask 而非 Next.js API Routes | 保留已有 MySQL 业务模型与 Python AI 生态；后端只作 AI 接入层 |
| Agent 手写编排而非 LangChain | 链路仅 6 个工具、状态机线性，手写能精确控制每步耗时与失败降级点 |
| RAG 用 TF-IDF 而非向量库 | 校园规范是短文本强关键词场景，可解释、零外部依赖、可离线；`Retriever` 预留了升级点 |
| 无 AI Key 时走演示流 | 评审现场不会因为没配密钥而白屏；同时暴露"当前是降级模式" |

---

## 四、快速开始

### 环境要求

- Python **3.11**
- Node.js **18+**（仅构建前端时需要）
- MySQL **8.0+**

### 五步启动

```powershell
# 1) 虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) 后端依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3) 建库
mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS userdb CHARSET utf8mb4"

# 4) 配置数据库口令（二选一）
#    a. 环境变量： $env:DB_PASSWORD = "你的口令"
#    b. 项目根目录新建 .db_password 文件，只写口令本身

# 5) 启动
.\.venv\Scripts\python.exe run.py
```

打开 <http://127.0.0.1:8000/>，健康检查 <http://127.0.0.1:8000/api/health>。

**首次启动会自动建表**（16 张，幂等）。

### 前端构建

```powershell
cd web
npm install
npm run dev      # 开发模式：5173，自动代理 /api 到 8000
npm run build    # 生产构建：产物输出到 static/spa，由 Flask 托管
```

### 演示账号（种子数据）

| 用户名 | 密码 | 角色 |
|---|---|---|
| `demo` | `123456` | 审核员（可进审核台） |
| `lin` | `123456` | 普通用户 |
| `chen` | `123456` | 普通用户 |

灌入演示数据：`.\.venv\Scripts\python.exe tools\db_tool.py seed`

---

## 五、环境变量

复制 `.env.example` 并按需填写。**所有敏感信息都通过环境变量或本地未跟踪文件提供，不进仓库。**

| 变量 | 默认 | 说明 |
|---|---|---|
| `SECRET_KEY` | 自动生成 | 会话签名密钥，缺失时写入 `.secret_key` |
| `DB_HOST` / `DB_PORT` | `localhost` / `3306` | 数据库地址 |
| `DB_USER` / `DB_PASSWORD` | `root` / 空 | 口令也可放 `.db_password` 文件 |
| `DB_NAME` | `userdb` | 数据库名 |
| `AI_API_KEY` | 空 | **留空则 AI 走演示模式** |
| `AI_BASE_URL` | `https://api.deepseek.com/v1` | 任意 OpenAI 兼容端点 |
| `AI_MODEL` | `deepseek-chat` | 对话模型 |
| `AI_VISION_MODEL` | 空 | 多模态模型（留空复用 `AI_MODEL`） |
| `HOST` / `PORT` | `127.0.0.1` / `8000` | 监听地址 |
| `DEBUG` | `1` | 生产请设 `0` |

---

### 可选：启用真实的多模态审核

不装也能跑，但**审核会退化为"零证据 → 一律转人工"**（这是刻意的 fail-closed 设计）。

```powershell
# 1) CPU 版 torch（不要用默认源，那是 2GB+ 的 CUDA 版）
.\.venv\Scripts\python.exe -m pip install torch torchvision `
  --index-url https://download.pytorch.org/whl/cpu

# 2) OCR（会尝试装 opencv-python-headless 与本项目的 opencv-python 冲突；
#    若报 WinError 5 文件占用，先停服务，再按下面的方式装）
.\.venv\Scripts\python.exe -m pip install --no-deps easyocr
.\.venv\Scripts\python.exe -m pip install scikit-image ninja imageio
```

首次调用 OCR 会自动下载约 100MB 模型到 `~/.EasyOCR`（CPU 上首帧约 5~8 秒，之后快）。

验证装好了没有：

```powershell
.\.venv\Scripts\python.exe tests\test_vision_tools.py    # 9 项，含中文路径回归
.\.venv\Scripts\python.exe tools\compare_ocr.py          # 两个对照样本的审核结论差异
```

`compare_ocr.py` 的预期输出（这正是 OCR 的价值证明）：

```
正常样本  case_normal.mp4 → pass   score=100  证据强度=1
可疑样本  case_spam.mp4   → manual score=76   命中「违法违规, 广告营销」 证据强度=2
```

> 想再加视觉侧（CLIP），需要本机有 git：`pip install git+https://github.com/openai/CLIP.git`。
> 本机默认没装 git，所以上面只走了 OCR 这条路。

---

## 六、目录结构

```
.
├── run.py                  # 后端入口
├── app/                    # Flask 应用（工厂 + 蓝图 + AI 层）
│   ├── factory.py          #   应用工厂、错误处理、SPA 托管、启动自检
│   ├── config.py           #   配置（环境变量优先）
│   ├── db.py               #   pymysql 封装 + 16 张表建表
│   ├── security.py         #   鉴权装饰器、文件名清洗、分页
│   ├── api/                #   6 个蓝图
│   └── ai/                 #   LLM / Agent / RAG / 工具
├── web/                    # 前端工程（React + TS + Vite）
│   └── src/
│       ├── pages/          #   9 个页面
│       ├── components/     #   Header / ErrorBoundary / UI 组件
│       ├── lib/            #   api 客户端、SSE 解析、类型定义
│       └── store/          #   Zustand 会话状态
├── mcp_server.py           # MCP Server（对外暴露审核工具）
├── tools/                  # 运维与验证脚本
│   ├── db_tool.py          #   查看/灌数据/清库
│   └── e2e_check.py        #   端到端联调
├── tests/                  # 自动化测试
├── static/spa/             # 前端构建产物（由 Flask 托管）
├── RUN.md                  # 运行说明（更详细）
└── requirements*.txt       # 依赖清单
```

---

## 七、AI 能力说明

### 视觉层：三级降级

审核的"看懂画面"能力不是单点方案，而是**三级降级链**，保证任何环境下都有输出：

| 层级 | 实现 | 依赖 | 输出 | 说明 |
|---|---|---|---|---|
| 1（主） | **多模态大模型逐帧理解** | `AI_VISION_MODEL`（如 GLM-4V） | 场景描述 + 风险证据 + 内容标签 | 能说出"第 2 帧：食堂后厨明火旁堆纸箱" |
| 2（兜底） | CLIP 相似度打分 | `clip` + `torch` | 5 类风险分数 | 本地免费，无可解释文字 |
| 3（最后） | 必转人工 | 无 | — | 零证据时 fail-closed |

视觉层前置还有一步 **帧选择**：从 15 帧里按"清晰度 + 去重"挑出 3~4 帧再送模型，
token 成本降到 1/3，也避免重复帧稀释判断。

### 审核 Agent 的 6 步流水线（整片模式）

| 步骤 | 做什么 | 失败时 |
|---|---|---|
| 检查元信息 | 文件名可疑信号（18+/破解等） | 跳过 |
| 抽取关键帧 | 等间隔抽 15 帧（OpenCV） | 标记 error，后续步骤跳过 |
| **画面理解** | 多模态模型逐帧解读；不可用则 CLIP | 降级到 CLIP → 再降级到无证据 |
| 画面文字识别 | EasyOCR 提取画面文字 | 降级为无文字 |
| 检索规范与判例 | RAG 命中条款 | 按通用规范 |
| 综合裁定 | LLM 裁定；不可用时规则引擎兜底 | 规则兜底 |

每一步都通过 SSE 推给前端，渲染成带耗时的推理时间线。

### 切片审核（segment 模式，推荐）

把视频按时长切成 N 段（默认 4 秒 / 最多 6 段），每段抽 **1 张代表帧**再单独判定：

```
视频 20s
 ├─ 0–4s   图书馆      → pass   95
 ├─ 4–8s   食堂窗口    → pass   95
 ├─ 8–12s  广告营销    → manual 55   ← 违规定位到这里
 ├─ 12–16s 操场夜跑    → pass   95
 └─ 16–20s 宿舍        → pass   95
整体：manual（原因：第 8.0–12.0 秒需要人工确认）
```

**代表帧不等于中点帧**：沿用旧版 `vitalframe` 的思路——段内逐帧算相邻帧灰度差之和，
取**运动量最大**的那一帧。静止画面会被跳过，转场与动作段才抓得住。

聚合策略**从严**：

- 任一段 `high` → 整体 `reject`，分数取最严那段
- 任一段 `medium` → 整体 `manual`
- 全部通过 → `pass`
- 所有段都没拿到证据 → `manual`（fail-closed，绝不自动放行）

配置：`AUDIT_SEGMENT_SECONDS`（每段秒数，默认 4）、`AUDIT_SEGMENT_MAX`（最多几段，默认 6，用于控成本）。

**两种模式并存**：`/api/ai/audit/stream`（整片）与 `/api/ai/audit/segment/stream`（切片）互不影响，
原有接口与调用方无需改动。前端两处入口：

- **投稿页**：上传前选择「切片审核 / 整片审核」，切片模式实时渲染彩色时间轴
- **审核台**：每条待审作品旁有「🔍 切片分析」按钮，按 `pending_id` 现场跑一次切片审核，
  审核员直接看到「第 8–12 秒是广告营销」，据此判断该整条拒绝还是要求作者剪掉那几秒

时间轴组件在 `web/src/components/SegmentTimeline.tsx`，两个页面共用。

### 成本与稳定性设计

- **结果缓存**：同一视频重复审核命中缓存（实测 9.7s → 0ms），避免重复烧 token；
- **QPS 退避**：命中 429 / 并发限制时自动退避重试一次；
- **超时保护**：单次视觉调用有超时，失败不阻塞整条链路；
- **无 key 可演示**：所有 AI 调用在无密钥时走降级，界面不会白屏。

### 安全兜底（fail-closed）

- **零证据不自动通过**：视觉与 OCR 都不可用时，强制转人工，分数封顶 60。
  这道闸**对 LLM 裁定和规则裁定同时生效**（LLM 给出的 `pass` 也会被压回 `manual`），
  不是只在规则路径生效
- **高危硬否决**：多模态判定 `high` 或色情相似度 ≥0.8 直接拒绝
- **误判可申诉**：人工结论回流判例库，同类内容下次能被正确检索

验证脚本：`tools/check_failclosed.py`（构造一个无文字视频 + 关闭视觉模型，
断言结果必须是 `manual` 且 `evidence_strength = 0`）

### 可调阈值

```
AUDIT_PASS_THRESHOLD=75     # >= 自动通过
AUDIT_MANUAL_THRESHOLD=45   # 中间区间转人工
```

### 接入模型（已验证 GLM）

`.env` 中配置即可，`app/config.py` 会自动加载：

```bash
AI_API_KEY=<你的密钥>
AI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
AI_MODEL=glm-4-flash          # 文本裁定
AI_VISION_MODEL=glm-4v-flash  # 看图
```

任意 OpenAI 兼容端点都可替换（DeepSeek / 通义 / Moonshot / 本地 Ollama）。

---

## 八、MCP 集成

审核能力以 [MCP](https://modelcontextprotocol.io) 协议对外暴露，**任何支持 MCP 的客户端**（Claude Desktop / Cursor / Windsurf）都能直接调用，无需启动 Web 服务、无需登录。

```powershell
# 安装
.\.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt

# 自检（验证工具链可用）
.\.venv\Scripts\python.exe mcp_server.py --selftest

# 协议级验证：真的走一遍 initialize → tools/list → tools/call
.\.venv\Scripts\python.exe tools\mcp_probe.py

# 启动（stdio，由客户端拉起）
.\.venv\Scripts\python.exe mcp_server.py
```

> 注意：stdio 服务器在**没有客户端**时会因为 stdin EOF 立刻退出，这是正常行为。
> 想验证它是否可用，请用 `tools/mcp_probe.py`（它会真的握手并调用一次工具），
> 而不是看进程"活没活"。

客户端配置见 [`mcp.json.example`](./mcp.json.example)，合并到你的 MCP 配置即可。

暴露的工具：`extract_frames` / `clip_risk` / `ocr_text` / `rule_lookup` / `keyword_risk` / `meta_risk`。

---

## 九、测试

```powershell
# API 契约（打桩，无需数据库）—— 29 项
.\.venv\Scripts\python.exe tests\test_api_contract.py

# 审核判定规则 —— 11 项
.\.venv\Scripts\python.exe tests\test_audit_rules.py

# 后端安全回归 —— 47 项
.\.venv\Scripts\python.exe tests\test_security.py

# 端到端（真实数据库 + HTTP）—— 24 项
.\.venv\Scripts\python.exe tools\e2e_check.py
```

合计 **111 项自动化测试**。

---

## 十、开发阶段说明

| 阶段 | 时间 | 内容 |
|---|---|---|
| 原型阶段 | 2026-07 | Flask 单体：模板渲染 + 基础社区功能 |
| 架构重写 | 2026-10 | 前后端分离（React/TS），AI 层重构为 LLM + Agent + RAG + MCP |

原 `zhanshizhan.py`（单体版本）保留在仓库中作为演进记录，新架构入口为 `run.py`。

---

## 十一、团队信息

<!-- 请按实际情况填写 -->
| 项 | 内容 |
|---|---|
| 学校 | _待填_ |
| 组别 | _待填（研究生组 / A 组 / B 组 / C 组）_ |
| 团队成员 | _待填_ |
| 指导教师 | _待填_ |
| 联系方式 | _待填_ |

---

## 十二、许可

<!-- 如需开源许可，取消下一行注释 -->
<!-- 本项目采用 MIT License，详见 [LICENSE](./LICENSE)。 -->
