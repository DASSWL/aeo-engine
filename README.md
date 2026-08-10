# aeo-engine

Vivu AEO Engine 的执行仓库。当前落到 **Phase 2（A1 感知 v0.1）**。

| Phase | 内容 | 状态 |
|---|---|---|
| Phase 0 | A6 记忆层（Notion 五库） | 已建成（迁移未执行，见 Phase 0 实现结果页） |
| Phase 1 | A7 中枢（度量脚本 + OpenClaw 两个定时任务） | 已上线，观察期中 |
| Phase 2 | A1 感知（扫描 playbook + 接入脚本 + 调度试验） | **已开跑**（2026-08-04 起），见下方进度 |

### Phase 2 开跑进度（2026-08-04）

| 前置 | 状态 |
|---|---|
| 三个 Claude scheduled task | ✅ 已创建（probe_daily 09:00 / linkedin_daily 09:30 / linkedin_reddit_weekly 周一 10:00） |
| Claude in Chrome 五站点授权与登录 | ✅ 已实测通过（Browser 2：LinkedIn / Reddit / ChatGPT / Perplexity / Gemini 全部已登录） |
| 探测问题入 Query 库 | ✅ 10 条已写入并逐字段回读核对（5 任务式 + 5 评估式） |
| 首轮周批扫真人盯跑 | ⏳ 待下周一 10:00 |
| `APOLLO_API_KEY` | ❌ 仍缺，Apollo 链路未通 |
| `SERPAPI_KEY` / Google Ads 凭据 | ❌ 仍缺（均有降级路径或列为可选，不阻塞） |

**五库当前状态**：Query 库 10 行（探测问题），其余四库为空。

依据文档在 Notion，本仓库不复制其内容，只引用：
Build Spec Phase 0 / 1 / 2 与对应的实现结果页。

---

## 目录

```
~/aeo-engine/
├── .env                    # 凭据与库 ID（gitignore，绝不入 git）
├── config/
│   ├── gates.yaml          # 闸门（Phase 1）
│   ├── thresholds.yaml     # 阈值与 SLA、周窗口（Phase 1）
│   ├── segments.yaml       # 5 个 segment 的扫描与名单配置（✅ Shawn 已审核定稿）
│   └── scan.yaml           # Phase 2 扫描规则与数字（⚠️ 未审核，见下）
├── prompts/
│   ├── friday_review.md                      # 周五复盘包模板（Phase 1）
│   ├── scan_linkedin_weekly.md               # ↓ 四份扫描 playbook
│   ├── scan_linkedin_daily.md
│   ├── scan_reddit_weekly.md
│   ├── probe_ai_engines_daily.md
│   ├── probe_questions_v1.md                 # 探测问题草稿【待审，未入库】
│   ├── scheduled_task_probe_daily.md         # ↓ 三个 scheduled task 的粘贴文本
│   ├── scheduled_task_linkedin_daily.md
│   └── scheduled_task_linkedin_reddit_weekly.md
├── scripts/
│   ├── aeo_common.py       # Notion 客户端、周窗口、日期口径（Phase 1 底座）
│   ├── aeo_scan.py         # Phase 2 底座：dry-run 开关、property 构造、去重
│   ├── metrics.py          # 周度量（Phase 1）
│   ├── sla_check.py        # 时限检查（Phase 1）
│   ├── keyword_volume.py   # ↓ Phase 2 三个接入脚本
│   ├── serp_scan.py
│   ├── apollo_poll.py
│   ├── scan_log_append.py  # 扫描日志合并（playbook 收尾用）
│   ├── run_daily_sla.sh    # OpenClaw 执行体（Phase 1）
│   └── run_friday_review.sh
├── data/                   # 运行期数据（gitignore）
│   ├── kw/                 # ← Keyword Planner 手动导出的 CSV 放这里
│   └── apollo_backfill.csv # ← LinkedIn 周批扫写入的在招公司，apollo_poll 反查用
├── logs/                   # 运行原始输出与扫描日志（gitignore）
└── outbox/                 # 待推送内容（gitignore）
```

---

## ⚠️ 真人要做的三件事（Phase 2 开跑前的全部前置）

按顺序做。做完之前，Phase 2 一条数据都不会产生。

### 一、创建三个 Claude scheduled task

在 Claude 桌面端创建，**prompt 文本直接从对应文件全文复制粘贴**——
那三个文件里没有需要删改的说明文字，整份就是 prompt。

| 任务名 | 时间（America/Los_Angeles） | prompt 取自 |
|---|---|---|
| `probe_daily` | 每日 09:00 | `prompts/scheduled_task_probe_daily.md` |
| `linkedin_daily` | 每日 09:30 | `prompts/scheduled_task_linkedin_daily.md` |
| `linkedin_reddit_weekly` | 每周一 10:00 | `prompts/scheduled_task_linkedin_reddit_weekly.md` |

任务所在的 Claude 会话必须同时挂上 **Claude in Chrome（默认 Browser 2）** 与 **Notion 连接**，
两者缺一 playbook 的第一步自检就会停机——这是设计如此，不是故障。

> **不要**把这三个任务建成 OpenClaw 定时任务。Phase 1 的 daily_sla 与 friday_review
> 是 OpenClaw 的，别混。也**不要动**那两个已有任务。
>
> 如果将来改用 OpenClaw 派生这三个任务、且执行体会调用 `claude` 命令，
> **必须套用 Phase 1 的 wrapper 模式**：在进程范围内
> `unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN`。
> 不剥离会静默转成 API 按量计费（Phase 1 实现结果页 §八①）。
> 参照 `scripts/run_friday_review.sh` 的写法。

### 二、给 Claude in Chrome 授站点权限

四份 playbook 会访问这些站点，**逐个授权**，否则扫描会在半路卡权限弹窗：

- `linkedin.com`（信息流、内容搜索、职位搜索、自己的动态页、profile 访客分析页）
- `reddit.com`
- `chatgpt.com`
- `perplexity.ai`
- `gemini.google.com`

并确认这五个站点**都已登录**。

### 三、首轮周批扫真人盯跑

spec §二 明文要求。第一个周一 10:00 的 `linkedin_reddit_weekly` **人在旁边看着跑完**。

盯跑时重点看四件事（这四件也是校准 `segments.yaml` 三处疑点的数据来源，见下）：

1. 有没有触发确认弹窗。卡确认就接受**半自动形态**——每周真人一句话启动（spec §二 允许）。
2. 招聘命中有没有同时写进水箱**和** `data/apollo_backfill.csv`。断了整条名字链就断了。
3. 「职位变动」那组的产出量。基本无产出就该砍掉这组，别每周白跑。
4. 扫描日志有没有真的落到 `logs/scan_YYYY-MM-DD.json`。
   落不了（会话没有本机文件系统权限）就走上报里贴 JSON、真人手动合并的路径。

---

## 三个接入脚本

**全部默认 dry-run。不带 `--commit` 就绝不写 Notion。**
（Phase 0 的六条测试记录清理折腾过一轮，不再允许任何「顺手写进真库」的测试数据。）

Python 用系统 `python3`（3.9.6），只依赖 `requests` / `PyYAML` / 标准库。
本机 Homebrew python 损坏，**不要**建 venv（Phase 1 实现结果页 §七）。

### keyword_volume.py —— 痛点级 query 的月搜索量 → Query 库

```bash
python3 scripts/keyword_volume.py --print-seeds   # 先看该往 Keyword Planner 里贴哪些词
python3 scripts/keyword_volume.py                 # dry-run，解析 data/kw/*.csv
python3 scripts/keyword_volume.py --commit        # 确认无误后写 Query 库
```

降级路径（当前唯一可用路径）：真人在 Keyword Planner 里跑 `--print-seeds` 给出的词，
导出 CSV 放 `data/kw/`，再跑脚本。认得的 CSV 形态：

- 编码 UTF-16（带 BOM）/ UTF-8 / UTF-8-BOM；分隔符 TAB 或逗号
- 表头前可以有若干说明行，脚本自己找表头
- 关键词列名认 `Keyword` / `Keyword (by relevance)` / `Search term` / `关键字`
- 搜索量列名认 `Avg. monthly searches` 等（见脚本顶部常量）
- 搜索量是区间（如 `10 – 100`）时 **`月搜索量` 留空**，不折算中值——
  Phase 0 的口径是「未知留空」，折算等于伪造精度

API 路径（Google Ads）接口留着但**未接**：跑 `--source api` 会如实报缺凭据并以退出码 2 退出。

### serp_scan.py —— 目标 query 的 SERP 占位情况

```bash
python3 scripts/serp_scan.py --queries "video search tool" "search inside video footage"
python3 scripts/serp_scan.py            # 不指定则取 Query 库月搜索量前 20
python3 scripts/serp_scan.py --commit
```

需要 `.env` 里的 `SERPAPI_KEY`（当前**没有**，跑起来会报缺凭据退出码 2，并打印已解析的执行计划）。
免费层每月 100 次，脚本会从历史日志累加本月用量并在超额前拒跑。

> **已知 schema 缺口**：spec 要求把「占位者与评测站页 URL」写进 Query 库，
> 但 Query 库只有 7 个字段，没有任何字段能装这些。
> 当前处置：占位者与评测站页只落 `logs/serp_scan_*.json`；
> Notion 侧仅在 `数据来源` 为空时写入「SERP 观察」。需要拍板（见下）。

### apollo_poll.py —— 按 segment 拉名单，同公司双角色配对入水箱

```bash
python3 scripts/apollo_poll.py --segments A --limit-companies 5   # dry-run
python3 scripts/apollo_poll.py --backfill                         # 只跑招聘公司反查
python3 scripts/apollo_poll.py --commit
```

需要 `.env` 里的 `APOLLO_API_KEY`（当前**没有**，跑起来会报缺凭据退出码 2，
并打印由 `segments.yaml` + `scan.yaml` 解析出的**完整请求体**供逐字复核）。

行为要点：
- 读 `segments.yaml` 的 `apollo` 条件与 `titles` 映射，按公司分组配对
- 单边角色降级入箱，在 `下一步动作` 标注缺失的那一侧
- `配对` 是**单向** relation（Phase 0 差异①），脚本**两边各写一次**，不假设反向可达
- 另读 `data/apollo_backfill.csv` 对 LinkedIn 扫到的在招公司做定向反查
- **不做邮件回流轮询**（sequence 在 J4 才建）

### scan_log_append.py —— 扫描日志合并（四份 playbook 收尾调用）

```bash
echo '{"date":"2026-08-10","playbook":"scan_linkedin_weekly","records":[
       {"segment":"A","hits":12,"inboxed":8,"skipped_dupe":4}]}' \
  | python3 scripts/scan_log_append.py

python3 scripts/scan_log_append.py --show 2026-08-10    # 只看不写
```

存在的理由：spec 定的记录结构是 `{date, playbook, segment, hits, inboxed, skipped_dupe}`，
而 Phase 1 的 `metrics.py` 只认 `{"by_segment": {...}}`，两边对不上扫描日志就白写。
本脚本让文件**同时满足两边**并保证一致，不需要也不允许改 Phase 1 的任何一行。
同一天多个 playbook 共写一个文件，**不要手写这个 JSON**，会互相覆盖。

---

## 配置文件的可信度不一样，用之前先看清楚

| 文件 | 状态 |
|---|---|
| `gates.yaml` / `thresholds.yaml` | Phase 1 交付，数字均标了出处；三个阈值是「v0.1 拍的数」，两周后校准 |
| `segments.yaml` | ✅ 2026-08-04 Shawn 审核通过定稿。但**定稿 ≠ 有出处**：五类扫描内容仍是推演的（见该文件顶部注释与 Phase 1 §九②） |
| `scan.yaml` | ⚠️ **未经审核**。约一半内容标着【推演待校准】，尤其 `signal_intent`、`job_change`、`query.type_markers` 三节 |

`scan.yaml` 与 `segments.yaml` 分开放，是为了不让未审核的内容混进已审核的文件——
审核通过后可以合并，也可以就这么放着，由 Shawn 定。

---

## 遗留问题（需要拍板，不是 bug）

1. **探测问题第 2 条空着**：spec 要求「具体竞品 alternative」，但竞替名单未收敛
   （`gates.yaml: competitor_list_converged: false`，且明文禁止脚本自动翻转）。
   需要 Shawn 给 1–3 个真实竞品名。见 `prompts/probe_questions_v1.md`。
2. **Query 库装不下 SERP 结果**：见上文 serp_scan 的 schema 缺口。
   加字段 / 另建库 / 就留在日志，三选一（改 schema 需解冻 Phase 0 字段表）。
3. **探测记录标识会重名**：`探测标识` 格式是 Phase 0 冻结的「日期空格引擎」，
   而一天里同一引擎要写 10 条，标题完全相同，靠 `具体问题` 区分。
   要改成「日期 引擎 序号」同样需解冻字段表。
4. **`metrics.py` 的扫描日志分母是累计的**：它把 `logs/scan_*.json` **所有历史文件**
   的 `by_segment` 全部相加，不按周窗口过滤。跑满几周后，信号命中率的分母会是
   累计扫描数、分子是本周入箱数，两者不同期。这是 Phase 1 既有实现，
   本次按「不动 Phase 1」的约束原样保留，但它会让那个指标越来越小。
   要不要改，请拍板。
5. **Apollo 行业筛选是降级的**：Apollo 要 `organization_industry_tag_ids`（内部 ID），
   `segments.yaml` 写的是行业名文本，脚本把行业名并进了关键词标签。
   命中面更宽也更糊，拿到 API key 后首轮要专门看这条。

---

# Phase 3 运行手册（J0–J4）

依据：[Build Spec · Phase 3](https://app.notion.com/p/3b2059d9693381e988a4f460945a6bc7)。
落成日期 2026-08-05。本节只讲 Phase 3 新增的东西；Phase 1/2 的两个定时任务与脚本一行未动。

## 零发送红线

Phase 3 是发信基础设施第一次进场，所以三条红线写死在 `config/outreach.yaml` 的
`zero_send` 节，三个值必须全为 `true`，任一为 `false` 时 `draft_runner` 直接拒绝运行：

| 红线 | 实现 |
|---|---|
| 草稿只推群 | 脚本只写 `outbox/*.md`，收件人永远是 Telegram group `-5261250225` |
| sequence 暂停建 | `apollo_sequence.py` 以 `active=false` 建，且不提供任何激活参数 |
| 不调发送类 API | 脚本不碰 Apollo `send_now`、不直连 SMTP、不直连 Telegram Bot API |

**sequence 的启动键永远在 Apollo 界面由真人按。**

## 新增脚本

| 脚本 | 干什么 | 默认行为 |
|---|---|---|
| `skill_check.py` | 解析真 skill 并暂存进 `.claude/skills/` 供 `claude -p` 加载 | 只报告；`--stage` 才暂存 |
| `draft_runner.py` | J4 待触达队列 → 草稿请求包 → Telegram 消息 | dry-run |
| `receipt_apply.py` | 「sent 行ID」回执 → 水箱行状态改「触达中」 | dry-run |
| `apollo_sequence.py` | 建 `seq_A_v1`（暂停态） | dry-run |
| `reply_poll.py` | Apollo sequence 回流轮询 | dry-run |
| `j0_market_definition.py` | J0 骨架：win/loss 提及计数 → 竞替名单 diff | dry-run |
| `j1_evidence.py` | J1 骨架：五道硬校验闸门 | dry-run |
| `j3_channel_presence.py` | J3 骨架：痛点帖回答草稿 | dry-run |

纪律与 Phase 2 完全一致：**不带参数跑 = 只算不写；写库必须显式 `--commit`。**

## 定时任务（Phase 3 新增两个）

| 任务 | cron | agent | delivery |
|---|---|---|---|
| `j4_draft_runner` | `30 8 * * *` @ America/Los_Angeles | vivu-sales | `mode: none` → telegram `-5261250225` |
| `j4_reply_poll` | `45 8 * * *` @ America/Los_Angeles | vivu-sales | `mode: none` → 同上 |

排在 Phase 1 的 `daily_sla`（08:00）之后，符合 spec「回流轮询随 daily_sla 之后运行」。

`delivery.mode` 必须是 `none`。`openclaw cron create` 会默认给 `announce`，
建完要用 `openclaw cron edit <id> --no-deliver` 改掉——`announce` 会把 agent 每次
最终回复都推到群里，没有草稿的日子也会推一条，违反「无内容不打扰」。

## unset wrapper（必须复用，不是可选项）

`run_draft_runner.sh` 与 `run_reply_poll.sh` 开头都有：

```bash
unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN
```

理由见 Phase 1 §八①：OpenClaw 会把 `openclaw.json` 里的 `env.ANTHROPIC_API_KEY`
注入派生的每一个进程，`claude` 见到它就优先用它而不是订阅登录，于是每天跑一次
草稿就每天按 token 计费一次。Phase 1 的红字提醒「凡经 OpenClaw 派生、又会调用
claude CLI 的其他自动化，都存在同样问题」——J4 就是那个「其他自动化」。

## skill 产线

草稿正文由 `claude -p` 加载**真 skill** 产出，不用自写 prompt 冒充。

| skill | 状态 | 影响 |
|---|---|---|
| `ai-writing-guideline` | ✅ 可用 | — |
| `vivu-outreach` | ✅ 可用 | — |
| `vivu-linkedin-rewriter` | ❌ **全机器不存在** | `linkedin_post` 环节 refuse，草稿留 `[SKILL MISSING]` 占位 |

两个 `--add-dir` 相关的坑，改之前先读 `config/outreach.yaml` 的注释：

1. `ai-writing-guideline` 是**指针 skill**，规则在仓库外的 `ai_writings.md` 里。
   不给 `--add-dir` 就只能用它自述的 fallback 子集，而这件事只会出现在输出第一行，很容易漏看。
2. prompt 必须走 **stdin**，不能当位置参数——`--add-dir` 是可变长选项会把它吞掉。

## 三段式为什么这么切

`draft_runner` 是 plan（纯 Python）→ claude → assemble（纯 Python）。
窗口判定、闸门、去重、模板全部可离线复算与回归测试，LLM 只负责写英文。
它挂了不会让队列算错。

## 队列口径

「待触达」的窗口判定**不依赖 Notion 视图排序**——Phase 0 已记那个视图表达不了
「剩余时限」，跨 24h/48h 混排会偏。真正的排序在 `draft_runner.py:build_queue` 里算，
且与 `sla_check.py` 规则一、二逐条对齐：

- 来源 = referral → 基准「入箱日期」+ `referral_hours`
- 信号类型 = signal → 基准「触达时限起算」+ `signal_hours`

两个脚本对「同一行什么时候到期」必须给同一个答案，否则会出现 sla 报了警而 J4 不出草稿。

## 回执协议

规则写在 sales agent 的 `AGENTS.md`（`~/.openclaw/workspace-vivu-sales/AGENTS.md` 末节）。
三条同时满足才算数：群 `-5261250225` + 发送者 `6529461266` + 整条正文就是 `sent <行ID>`。
命中后 agent 只许调 `receipt_apply.py --commit`，不许自己拼 Notion 请求。

**这条规则允许漏，不允许错。** 漏掉的行次日 `daily_sla` 会因超时报出来由真人补。

✅ **端到端已实测**（2026-08-05）：群里发 `sent 3b3059d9-6933-81d1-9876-faa73adaa1e7`
→ agent 调 `receipt_apply.py --commit` → 水箱行 `inbox → 触达中`、时间戳写入、
`readback_ok=true`。agent 走的是脚本路径，没有自绕去拼 Notion 请求。

## J0 / J1 / J3 当前是无燃料状态

三个骨架都能跑、都会正确拒绝、都会说清缺什么。当前拒绝原因：

- **J0** —— win/loss 库 0 行，未达触发线 5 条。还差 5 条对话。
- **J1** —— 无证据请求被闸门①拒；对比页被闸门②拒（win/loss 0 < 5 且
  `competitor_list_converged = false`）。
- **J3** —— 台账 0 条已签发资产、`facts.json` 0 条已确认 benchmark，两个事实来源都空。

这是**正确行为**，不是缺陷。没有燃料时产出内容只能靠编，而编造正是这些闸门要防的。

## J2 凭据链（2026-08-05 已验证）

Netlify 环境变量已由真人配置完毕并实测通过，不需要再查一遍：

| 变量 | 值 | 作用域 |
|---|---|---|
| `AEO_NOTION_TOKEN` | 专用 internal integration，只连内容资产台账单库 | Builds，全 context |
| `AEO_DS_LEDGER` | `17847467-bea1-4377-97b6-0d9a3e3bd33b`（**data source ID，不是 database ID**） | Builds，全 context |
| `AEO_NOTION_VERSION` | `2025-09-03` | Builds，全 context |
| `AEO_WRITEBACK` | `commit` | Builds，**仅 Production** |
| `AEO_LINT_REQUIRE_LEDGER` | 未配置（等跑顺再开） | — |

`AEO_WRITEBACK` 只给 Production 的理由：Deploy Preview 也跑完整 build，
预览环境若也 commit，每开一个 PR 台账就被写成「已发布」，可内容根本没上线。

三项实测证据：
- 生产构建日志出现 `aeo-lint: 台账读到 0 行` —— token / DS ID / Connections 全通。
  ⚠️ 注意区分：若日志出现「AEO_NOTION_TOKEN / AEO_DS_LEDGER 未配置，跳过台账比对」，
  构建**照样是绿的**，但整个台账比对没跑。绿色部署不等于凭据配对了。
- `PATCH /v1/data_sources/<DS_LEDGER>` 空 body → 200，说明有 `Update content`。
  回写走的就是这个能力；spec §J2 写的「只读 token」与「CI 回写台账」自相矛盾，
  只读做不了回写。实际按最小可用集配的：Read content + Update content，
  不给 Insert content、不给用户信息，且只连台账一个库。
- 该空 PATCH 已验证是无副作用空操作（前后字段数 9/9、字段名逐字一致），
  可作为将来复查写权限的常规探针。**不要用「建一行测试记录」来测写权限**——
  Phase 0 那六条测试记录当时删不掉，拖到 Phase 1 才用原生 API 逐条 archive。

排查端点时注意：`4a46acc3-c08f-457b-8771-43328b58e896` 是 **database ID**，
拿它去 `/v1/pages/{id}` 查会 404，且**任何权限的 token 都会 404**——
那个 404 不能用来判断权限。

## J2 在另一个仓库

J2 的东西全部落在 `~/project/vivu_web`（vivu.ai 站点仓库）。

| PR | 内容 | 状态 |
|---|---|---|
| #2 `aeo/j2-content-contract` | 内容契约 + facts.json + CI lint + 发布回写 | ✅ 已合并 2026-08-05 |
| #3 `aeo/j2-demo-request-path` | 技术底座审计 + 预约 URL 单一出处 + /contact 次级入口 | ✅ 已合并 2026-08-05 |
| #4 `aeo/j2-lint-redtest` | 红灯测试 | 🚨 **永不合并**，验完即关即删 |

`j3_channel_presence.py` 读的 `data/facts.json` 已随 #2 进 main，
vivu_web 工作树在 main 上就能读到事实层，不再需要 checkout 到分支。

技术底座 checklist 六项全部具备验收条件（详见 vivu_web 的
`docs/aeo-technical-foundation.md`），等真人验收后该职能即可退役。

---

# 每日 10:00 简报（daily_brief）

依据：运行手册页 [📖 AEO Engine v1](https://app.notion.com/p/3b3059d969338114a498df06ba197332)
的「一、运行时刻表」与「二、需要你做的事」——这两节是每日与每周节奏的唯一事实源。
落成日期 2026-08-05。**纯增量**：Phase 1/2/3 的脚本、config 值与三个既有定时任务一行未动。

## 它回答一个问题：今天要干什么

| | |
|---|---|
| 脚本 | `scripts/daily_brief.py`（**零 LLM**，纯 Python） |
| 执行体 | `scripts/run_daily_brief.sh` |
| 文案与上限 | `config/brief.yaml`（脚本内不写死任何一句话、任何一个业务值） |
| 定时任务 | `daily_brief` · `0 10 * * *` @ America/Los_Angeles · vivu-sales · `mode: none` → group `-5261250225` |
| 产出 | `outbox/brief_YYYY-MM-DD.md`，不超过 25 行，手机一屏读完 |

四节 + 固定末行：① 今天机器会跑什么 · ② 你今天的动作 · ③ 本周节点 ·
④ 要 review 的五个库（五个 Notion 库链接）· 末行挂账清单链接。

为什么零 LLM：拼装任务不需要生成。确定性和零额度消耗比漂亮措辞重要，而且这条任务
兼任心跳——它必须在 claude 登录态失效、API 额度耗尽时照样跑得出来。

## 与 08:00 / 08:30 的分工

简报**只给计数和指引**，不含超时详情、不含草稿正文。
08:00 的时限检查和 08:30 的草稿各自已经把详情推过一遍，简报再展开就是第三份噪音。

超时数尤其不重算：直接读 `logs/sla_<date>.json` 里 08:00 留下的 `total_overdue`。
两份实现必然漂移，而且读日志还顺带把「08:00 根本没跑」暴露出来——那种情况下简报
出的是一行 `⚠️ 08:00 时限检查今天没有留下结果`，不是一个假的 0。

「待发草稿」与「最紧一条」复用 `draft_runner.row_window`，
口径与 `sla_check.py` 规则一、二逐条一致，理由同 J4 队列那一节。

## 它是心跳，没有 NO_ALERT 分支

`run_daily_sla.sh` 与 `run_draft_runner.sh` 是「无内容不打扰」。**这个不是。**

简报每天必发。脚本失败、取数失败、渲染为空——三种情况都产出一条推得出去的失败上报，
执行体还兜一层「连文件都没写出来」。**10:00 群里静默 = 故障，不是「今天没事」。**

还有第四种失败在脚本管不到的地方：**Telegram 发送本身失败。**
2026-08-05 实测手动触发时踩到一次 `OutboundDeliveryError: Network request for
'sendMessage' failed!`——脚本一切正常、简报也生成了，agent 发一次失败就收工，
那一次的心跳静默消失。同样内容重发即成功，是瞬时网络问题。

对策写进了任务 prompt 第 4 条：发送失败等约 10 秒重发，最多 3 次；三次都失败要在
最终回复里写明 `SEND_FAILED`。这层只能由 agent 兜——传输挂了的时候，
脚本已经无路可走了。

## 空则省行

某项为 0 时整行省略，固定三件事除外。逐行显示「0 条」是凑格式，不是信息。
行数上限由脚本强制：先压空行，仍超限才截断，且截断留可见标记。

## 行数上限从 25 提到 32（2026-08-05 的取舍）

原始要求是「不超过 25 行，手机一屏读完」。同日追加要求「简报里加上五库链接」，
两条不可能同时成立：五个链接固定占 5 行，加小节标题与空行共 7 行。
Shawn 在已知代价的前提下选了要链接，25 行的一屏目标就此让位。

32 是**实测最坏形态**：周一（① 多一行批扫）且台账待签发与超时两行都出现。
定成 32 而不是更大，是为了让简报**永不触发截断**——截断一条真简报比长一点更糟。
实测：平日 29 行、周一/周五 30 行、最坏 32 行。

想要回一屏：`config/brief.yaml` 里 `sections.review_links.enabled: false`，
上限调回 25，立刻回到 22 行。这一个开关就是全部代价的开关。

## 五库已改英文名（2026-08-05）

| 原名 | 现名 | database ID |
|---|---|---|
| 水箱（Pipeline） | `AEO Pipeline` | `82dc5fc1…` |
| win/loss 库 | `AEO Win-Loss` | `11b73965…` |
| 内容资产台账 | `AEO Content Ledger` | `4a46acc3…` |
| AI 引擎探测记录 | `AEO Probe Log` | `2f9c5c91…` |
| Query 库 | `AEO Query Library` | `291b5c35…` |

**只改了 database 标题，字段一个没动。** 改完逐库回读核对：
pipeline 14 / win-loss 16 / ledger 9 / probe 9 / query 7 个字段，
与 Phase 0 冻结的字段表逐字一致。

脚本不受影响：所有脚本按 `.env` 的 `DS_` / `DB_` ID 取数，从不按库名查找。
仓库里出现的「水箱」「台账」等中文称呼是注释与文档里的口语，不是查询键。
本次只同步了简报正文里指名道姓的那几处（`brief.yaml`），
其余文档与运行手册页的中文称呼未逐处改写——那是一次纯措辞工程，需要时另开。

## ⚠️ 一处待拍板：周末形态

交付要求里写的是「周末只剩探测与回执监听」，但运行手册页的每日表没有工作日限定，
`daily_sla` / `j4_draft_runner` / `j4_reply_poll` 三个 cron 都是 `* * *`，
两个 Claude scheduled task 同理——**七天都跑**。

`config/brief.yaml` 因此按事实配（全部 7 天），周末简报只比工作日少「周一批扫 / 周五复盘」那行。
要让周末真的只剩探测与回执监听，是两步，缺一不可：

1. 把 `schedule.daily` 里 08:00 / 08:30 / 08:45 / 09:30 四条的 `days` 改成 `[1,2,3,4,5]`
2. 同时把那四个定时任务的 cron 也改成工作日

只改配置会让简报说谎：任务照跑，简报说它不跑。

## 本地看形态

```bash
python3 scripts/daily_brief.py --stdout-only                          # 今天，不写 outbox
python3 scripts/daily_brief.py --as-of 2026-08-10 --sample --stdout-only   # 周一形态
```

`--sample` 表示日历按 `--as-of` 走、数字仍取今天的真实数据——否则三份样例全挂着
「08:00 没留下结果」，看不出正常形态长什么样。

---

# Query 库进料链（2026-08-05）

**只加了诊断与一条新链的脚本，没动任何既有脚本、既有定时任务、五库 schema。**

## 为什么单独一节

Query 库是 J1 第 5 类「痛点级 query 的 AEO 内容」的唯一输入。它现在 10 行，
全部 `数据来源 = 探测问题`，月搜索量全空，2026-08-05 一次写入之后再没长过。

更要紧的是它**是个封闭集合**：那 10 条是我们自己写在 `config/scan.yaml` 里的问题。
拿它推断「市场在问什么」，得到的是我们自己假设的回声。

## 两个新脚本（都只读，都零成本）

| 脚本 | 干什么 | 默认 |
|---|---|---|
| `query_intake_health.py` | 四条进料链逐条诊断：能否产新 query / 断在哪 / 修通需要什么 | dry-run，只读 |
| `buyer_quote_queries.py` | win/loss 的「买家原话」→ Query 库候选（逐字，不改写） | dry-run，只读 |

执行体 `run_query_intake.sh` 把两个跑成一次，写 outbox 由 `outbox_sweep` 转发。
**当前未挂任何定时任务**——挂哪个 cron 是真人的决定，建议见下。

## 五种「断」要分开谈

诊断脚本刻意把断点分成 缺脚本 / 缺 schema / 缺凭据 / 缺调度 / 缺真人动作 五类。
混在一起谈只会得出「都做一遍」这种没用的结论——它们修法与代价完全不同。

2026-08-05 首跑结论：**四条链没有一条能产新 query。**

| 数据来源 | 写它的脚本 | 能建行 | 断在哪 |
|---|---|---|---|
| 探测问题 | `probe_questions_sync.py` | 能 | 缺调度 + 缺真人动作（config 里 10 条已全部入库，再跑 `to_create=0`） |
| Keyword Planner | `keyword_volume.py` | 能 | 缺凭据 + 缺调度 + 缺真人动作（`data/kw/` 空，`parsed_rows=0`） |
| SERP 观察 | `serp_scan.py` | **不能** | 缺 schema + 缺凭据 + 缺调度 |
| 买家原话 | `buyer_quote_queries.py`（本次新建） | 能 | 缺真人动作（win/loss 2 行，「买家原话」**0 条**） |

## 补量 ≠ 发现

- **补量**：给我们已经想到的词查搜索量。种子来自 config，config 不变就不产新词。
- **发现**：能带回我们**没想到**的词。

`keyword_volume` 的 28 条种子 = spec 首批 3 条 + `segments.yaml` 五段
`linkedin_keywords` 25 条，两个来源都是我们自己写的。挂上定时任务它每天做的
也只是补量。它唯一的发现面是 CSV 里**不在种子表**的行（`in_seed_list=false`），
那取决于真人怎么导出，不取决于脚本。

真正具备发现能力的只有**买家原话**一条，而它现在燃料是 0。

## 买家原话链的触发线是错的

`gates.yaml` 的 `win_loss_min = 5` 数的是**对话场次**。但这条链吃的是**原话**。
零回复的 cold outbound 也记一行 loss，它产出 0 条原话——攒到 5 行仍可能是 0 条。
当前 win/loss 2 行，两行都是零回复 loss，「买家原话」都为空。

**真正的触发线是「有买家原话的 win/loss 行数」，现在是 0。**

## 两道闸

1. 默认 dry-run，写库必须显式 `--commit`（与 Phase 2 三脚本同一条纪律）。
2. `buyer_quote_queries.py --commit` 还要求 `config/buyer_quotes.yaml`
   的 `meta.status == approved`。该文件整份是【推演待校准】的 marker 词表，
   **当前是 `draft`**，所以 `--commit` 会拒绝执行。先 `--review` 出审核清单，
   真人审过词表再改 `approved`。

## 又撞上同一堵墙

Query 库 7 个冻结字段里没有能装**出处**的列（`关联资产` 是指向台账的 relation，
不是指向 win/loss）。所以「每条 query 回溯到具体 win/loss 行」只能落在
`logs/buyer_quote_queries_*.json` 与 outbox 审核清单里，Notion 侧存不下。

这与 SERP 链装不下占位者名单是**同一堵墙**，同属 Phase 2 §八① 待拍板项。
本次不解冻、不加字段、不另建库，原样报告。

## 建议的调度（未创建，待拍板）

| 链 / 脚本 | 建议 | 频率 | 挂哪 | 为什么 |
|---|---|---|---|---|
| `run_query_intake.sh` | 挂 | 周五 14:00 | OpenClaw · vivu-sales · `mode: none` | 排在 `a1_health` 14:30 与 `friday_review` 15:00 之前，让复盘拿到「query 从哪来」的现状 |
| `keyword_volume.py` | 挂 dry-run | 每月 1 日 11:00 | OpenClaw | 瓶颈是真人导 CSV，不是脚本。月频 dry-run 只为把「`data/kw/` 还是空的」变成可见 |
| `probe_questions_sync.py` | **不挂** | — | — | 它是 config 变更时的一次性同步。挂上每天 `to_create=0`，纯噪音 |
| `serp_scan.py` | **不挂** | — | — | 缺 key 且结构上写不了行。挂上每天以退出码 2 刷屏 |

全部挂 OpenClaw 而不是 Claude scheduled task：这几个脚本是本机 Python、只读、零 LLM，
OpenClaw 能直连 Telegram。Claude 侧任务只能写 outbox 靠 `outbox_sweep`（2026-08-05 才补上）
兜底转发，那条路留给真正需要浏览器与 LLM 的探测和扫描。

---

# 两条新进料路径（2026-08-05 · Shawn 指定）

## 三个拍板

| # | 问题 | 裁决 |
|---|---|---|
| ① | AI 建议的 query 什么时候可以进 Query 库 | **直接进库标「候选」、月搜索量留空**，KP 数据回来再补 |
| ② | `数据来源` 冻结的四个取值装不下新来源 | **解冻，additive 加两个**：`A1 扫描`、`AI 建议`，分开记 |
| ③ | 路径 2 怎么起步（Google Ads 五个凭据全缺） | **先建候选池，KP 环节等凭据** |

裁决① 的代价要写明白：Query 库从此同时躺着**有市场证据的词**和**模型说会有人搜的词**。
两者只靠 `数据来源` 一列区分——**排 AEO 内容优先级前必须先看那一列**。
`月搜索量` 为空 + `数据来源 = AI 建议` = 一条还没有任何证据的假设。

裁决② 已执行：2026-08-05 additive 加了两个 select 取值，
独立回读核对（重新拉线上数据，不看写入回执）：7 个字段不变、
`状态`/`类型`/`面向角色` 三个 select 未波及、现有 10 行数据完好。

## 路径 1：探测追问 → AI 建议

每日探测跑完，在**同一对话的最后一步**追问一句固定措辞，收它给的问法。

- 措辞在 `config/query_candidates.yaml` 的 `followup.text`，**逐字使用不即兴改**。
  刻意不提任何品类词——话题由刚问过的探测问题自己带出来。
  多塞一个词，拿回来的就多一分是我们自己的假设。
- **必须在该条探测记录写完之后**。理由是 playbook §3.2：同一对话的上下文会带偏后续回答。
  放最后，被污染的只有追问自身（它本来就只是待验证假设），探测基线不受影响。
- 频次 6 次/天（3 引擎 × 2 问题集），不是 30 次——追问质量不随次数线性增长，噪音会。
- playbook 的红线「不写水箱、不写 Query 库」**一个字未动**：追问只落本机文件，
  写库是 `query_candidates.py --commit`，另一条命令、由真人执行。

三档写入，dry-run 真的什么都不写：

| 命令 | 写候选池 | 写 Notion |
|---|---|---|
| `query_candidates.py` | ❌ | ❌ |
| `query_candidates.py --stage` | ✅ | ❌ |
| `query_candidates.py --commit` | ✅ | ✅ |

> 为什么 dry-run 连候选池都不写：进池也是写。Phase 3 §七④ 那次
> 「诊断性 dry-run 覆盖了当天的权威 plan」就是诊断与产线共用写路径造成的。

**`caps.max_per_day = 20` 是本链最重要的一道闸**，理由是算术：
6 次追问 × 5 条 = 30 条/天，一个月 900 条未验证的模型建议，
而 Query 库现在只有 10 条真实行。不设上限，两个月后 J1 从库里挑选题，
挑到的 99% 是模型的联想。被截下的**不静默丢**，留在池子里并在输出里点名。

## 路径 2：跑 Keyword Planner 验量

**「每日跑」今天做不到，也不该按每日设计**，两个硬事实：

- Google Ads 五个凭据全缺 → API 路径不可用，只剩真人手动导 CSV，那不可能每日。
- SerpAPI 免费额度 100 次/月（`scan.yaml: serp.monthly_quota`）。
  就算只扫 10 条 query，每日 = 300 次/月，超 3 倍。**这是算术不是取舍。**
  SERP 环节只能周频且只扫 top-N。

缺凭据时能做且有用的那一半已落成：`kp_seeds.py` 导出「Query 库里还没有任何市场证据的词」
（判据只有一条：`月搜索量` 为空），`--paste` 直接打印词表供贴进 Keyword Planner。

闭环：`kp_seeds.py --paste` → 贴进 KP → 导 CSV 放 `data/kw/` →
`keyword_volume.py` 认出这些行已存在，走 `to_update` 补量。**不需要改 keyword_volume。**

## ⚠️ KP 环节上线前必须先处置的一条

`keyword_volume.py:298-301` 的 update 分支同时写 `月搜索量` **和** `数据来源`，
把后者覆写成「Keyword Planner」。于是 AI 追问那批刚标上的 `AI 建议`、
Reddit 那批的 `A1 扫描`，会在补量的一瞬间被抹掉——
**裁决② 那次解冻换来的出处区分，当场归零。**

`kp_seeds.py` 每次运行都统计有多少行处在这个风险里（`overwrite_risk`）。
当前 10 行全部在内。改法是一行（照 `serp_scan.py:184`，只在 `数据来源` 为空时才写），
但那要动 Phase 2 的脚本——**是真人的决定，本次未改。**

## 新增文件

| 文件 | 说明 |
|---|---|
| `scripts/query_candidates.py` | AI 建议摄入 → 候选池 → Query 库 |
| `scripts/kp_seeds.py` | 导出待验量的词（只读、零成本） |
| `config/query_candidates.yaml` | 追问措辞、过滤规则、两道上限。整份【推演待校准】 |
| `prompts/probe_ai_engines_daily.md` §11 | 追问环节（新增章节；§3.6 同步改写） |

## 链现状（`query_intake_health.py` 已同步到 6 条链）

**六条链仍然没有一条在自动产新 query。** 各自差什么：

| 数据来源 | 缺什么 |
|---|---|
| 探测问题 | config 里 10 条已全入库；要新词得真人先加问题 |
| Keyword Planner | Google Ads 五凭据 + `data/kw/` 空 |
| SERP 观察 | schema 装不下 + 缺 key + 10 行来源全非空（补 key 也 0 行可写） |
| **AI 建议** | 候选池空——追问要等探测跑起来，而**探测卡在 Perplexity 未登录** |
| **A1 扫描** | 取值已解冻可用，**脚本还没建**；水箱 `来源 = A1 扫描` 也是 0 行 |
| 买家原话 | 脚本已建；2026-08-05 起有 1 行真实原话，实测抽出 1 条弱候选 |

---

# 2026-08-05 收尾：四条拍板执行 + Reddit 链建成

| # | 事项 | 结果 |
|---|---|---|
| 1 | Perplexity 登录 | Shawn 已恢复。下一次 09:00 探测跑通即验证（今日因未登录整体停机） |
| 2 | `keyword_volume` 覆写 `数据来源` | **已修**（下方） |
| 3 | 两份规则文件审核 | `buyer_quotes.yaml` / `query_candidates.yaml` → `approved` |
| 5 | Reddit 原话 → query | **已建** `scripts/scan_queries.py` |

## 覆写已修（唯一一次改动 Phase 2 脚本）

`keyword_volume.py` 的 update 分支原本无条件同时写 `月搜索量` **和** `数据来源`。
现改为**只在 `数据来源` 为空时才写**，与 `serp_scan.py:184` 同一条口径。

为什么这一条值得破例动 Phase 2 的脚本：它抹掉的是出处，而且是**静默**抹掉——
补量成功了、数字是对的、出处没了，没有任何报警会响。
这与 Phase 3 §七① 那次「草稿数字是对的，东西没了」是同一类失败。

wire 级实测（拦截 `update_page`，看真正发出去的 body）：

```
update p-ai    → {"月搜索量": {"number": 700.0}}                                  ← 来源=AI 建议，未下发 数据来源
update p-blank → {"月搜索量": {"number": 120.0}, "数据来源": {...Keyword Planner}} ← 来源为空，补写
```

`kp_seeds.py` 的 `overwrite_risk` 一节保留，改作**回归哨兵**：
补完量再跑一次，若那些行的来源变回「Keyword Planner」，说明这处改动被回退了。

## Reddit / LinkedIn 原话 → Query 库（`数据来源 = A1 扫描`）

`scripts/scan_queries.py` + `config/scan_queries.yaml`。

这是六条链里**出处最硬**的一条：水箱的 `来源链接` 是帖子永久链接，一个公网 URL，
谁都能点开核对这句话是不是真有人说过。而 `scan_reddit_weekly.md` §5 早就写死了
「`信号原文` = 【帖型：X】+ 原文照抄，不改写不翻译不概括」——
**料一直在进，过去只是没有管子通到 Query 库**，因为 `数据来源` 没有对应取值。

设计要点：

- **只取 `来源 = A1 扫描` 的行。** Apollo 行不取——它们的 `信号原文` 自己写着
  「非原文引用：本行来源是名单条件而非本人发言」，当买家语言写进去就是掺假。
- **标题优先。** Reddit 的标题就是那句问话，正文多是背景交代。标题另给
  `title_max_words: 22`——真实标题常 15–20 词，用正文的 16 词上限会把最有价值的一句判掉。
- **`来源链接` 为空的行直接拒绝**，理由就一条：无出处不写。
- 「工具求推荐」帖带出的 alternative / best / vs 会被判成评估式 / decision maker，
  这是对的——那确实是 decision maker 在评估阶段的搜法。

离线自测（内存构造，不碰 Notion、不落文件）：Apollo 行正确跳过、
无链接行按「无出处不写」拒绝、标题与正文各抽出候选、逐字校验全部为原样子串。

⚠️ **`meta.status` 仍是 `draft`，`--commit` 会拒绝。** 理由不是流程而是事实：
水箱 `来源 = A1 扫描` 当前 **0 行**，Reddit 周批扫一轮都没跑过，
所以这份 config 里关于「`信号原文` 长什么样」的假设**一条都没在真实数据上验证过**。
首轮周批扫出料 → `--review` 出清单 → 真人对着真实行复核规则 → 再改 `approved`。

## 六条链现状

| 数据来源 | 脚本 | 还差什么 |
|---|---|---|
| 探测问题 | `probe_questions_sync.py` | config 10 条已全入库；要新词得真人先加问题 |
| Keyword Planner | `keyword_volume.py` | Google Ads 五凭据 + `data/kw/` 空 |
| SERP 观察 | `serp_scan.py` | schema 装不下 + 缺 key + 10 行来源全非空 |
| AI 建议 | `query_candidates.py` | 等探测跑起来（Perplexity 已恢复，明日 09:00 见分晓） |
| A1 扫描 | `scan_queries.py` | 等首轮周批扫；规则未经真实数据验证 |
| 买家原话 | `buyer_quote_queries.py` | 已 approved，有 1 行真实原话 |

---

# Keyword Planner 首次实跑（2026-08-05）

执行方式：Claude in Chrome（Browser 2）驱动 Google Ads 账号 `215-156-2899`。
产物：一个 draft plan「Plan from Aug 6, 2026」（无投放、无花费、未建 campaign）。

## 结论一：那 10 条 query 在 Google 上没有可测量的搜索量

`Get search volume and forecasts` 跑了 9 条（第 10 条被拒，见结论三），
`Avg. monthly searches` **全部是 `—`**。

这不是账号问题——同一账号在 `Discover new keywords` 里拿得到数：

| 词 | 界面显示 |
|---|---|
| video search | `1K – 10K` |
| reverse video search | `10K – 100K` |
| video search engine | `1K – 10K` |
| video asset management software | `100 – 1K` |
| video asset management | `10 – 100` |

**账号能取数，所以 `—` 是真的没量。**

这条结论直接打到 J1 第 5 类「痛点级 query 的 AEO 内容」的选题依据上：
Query 库里那 10 条是我们自己写的问题，而**市场的词不是我们的词**。
`reverse video search` 有 10K–100K 的量，我们一条都没想到过它。

## 结论二：⛔ 导出的 CSV 会伪造精度

**界面显示区间，导出的 CSV 把区间换成桶中值整数。**

37 条词里只出现过 4 个不同的搜索量取值：`50` / `500` / `5000` / `50000`——
全是 5×10^n。那不是测量值，是桶标签：

```
10 – 100    → 50          100 – 1K   → 500
1K – 10K    → 5000        10K – 100K → 50000
```

`keyword_volume.py:parse_volume` 看到 `5000` 会当成精确值写进 `月搜索量`。
于是 Phase 0 那条「区间不折算成任何具体数字，折算等于伪造精度」的禁令，
**在 Google 的导出层就已经被绕过去了**，而我们的脚本会忠实地把假精度记成基准。
dry-run 实测：37 条全部 `to_create`，`月搜索量` 全是桶中值。

处置：文件扩展名改成 `.csv.hold`，`keyword_volume.py` 的 glob 吃不到它，
并在 `data/kw/READ_ME_FIRST.txt` 留了原因。**本次未 `--commit`，Query 库一行未加。**

### 需要拍板：区间怎么进库

`月搜索量` 是 number 字段，Phase 0 口径是「未知留空」。但区间既不是未知也不是精确值。
三个选项，代价都不一样：

| 选项 | 得到什么 | 失去什么 |
|---|---|---|
| 按现状导入（写桶中值） | 37 条词带数字进库，能排序 | 基准掺假。`5000` 看起来像测量值，实际误差一个数量级 |
| 把桶中值还原成区间串再导 | 37 条词进库，`月搜索量` 留空，出处诚实 | 排不了序——所有词看起来都一样，等于没有量 |
| 先不导，等有投放后拿精确值 | 基准干净 | Query 库继续停在 10 条零证据行 |

我的意见：这一条和 SERP 那条 schema 缺口是同一类问题——
**`月搜索量` 这个 number 字段装不下「量级已知、精度未知」这种状态。**
但改 schema 要解冻 Phase 0，所以它是你的决定，不是我的。

## 结论三：Keyword Planner 量不了长尾对话式 query

`how to search a video library by what was said in it` 被拒：
`Keywords can't contain more than 10 words`（我们那条 12 词）。

**没有改写它**——改写就不是原来那个 query 了。

这是补量链的结构性上限：J1 第 5 类瞄准的正是这种长尾对话式问法
（`how to find a clip in hours of footage` 一类），而 KP 结构上就验不了超过 10 词的。
换句话说，**这条链能验的和我们最想写的，不是同一批词。**

---

# Phase 0 字段表第二次解冻：Query 库加第 8 列（2026-08-05）

Shawn 拍板。当天两次解冻，都是 additive：
① `数据来源` 加 `A1 扫描` / `AI 建议` 两个取值；② 本节这一列。

## 新列：`搜索量区间`（rich_text）

Query 库字段 7 → 8。既有 7 列一个未动，已独立回读核对
（重拉线上：字段名逐字一致、三个 select 取值未波及、原 10 行数据完好）。

**为什么 `月搜索量` 装不下它**：那是 number 字段，而区间是「量级已知、精度未知」——
既不是未知（Phase 0 口径「未知留空」把它当未知，等于丢掉量级信息），
也不是精确值（硬塞桶中值进去就是伪造精度）。这是第三种状态，需要第三个位置。

## 配套：桶化检测（`config/kp_buckets.yaml`）

**不能看到 `5000` 就当区间。** 有投放的账号返回的是任意整数（4830、12100），
那才是真测量值，降级成区间同样是毁数据。

所以判据是**文件级**的：整份文件的每一个取值都落在桶集合里，且行数 ≥ 5，才算桶化。
出现任何一个不在表里的数即证伪。五个边界已实测：

| 输入 | 判定 |
|---|---|
| 全桶值 | ✅ 桶化 |
| 混入一个 `4830` | ❌ 精确值文件，不还原 |
| 全任意值 | ❌ 精确值文件 |
| 只有 4 条 | ❌ 低于行数下限，按精确值处理 |
| 含空值但其余全桶值 | ✅ 桶化 |

映射表是**实测得出**的，不是推演——界面与 CSV 逐条对照过四档：

```
10 – 100 → 50      100 – 1K → 500      1K – 10K → 5000      10K – 100K → 50000
```

`100K – 1M` / `1M – 10M` 两档按同一规律外推，**未实测**，config 里标了注记。

## keyword_volume.py 的第二处改动

本次是这个 Phase 2 脚本第二次被改（第一次是同日的 `数据来源` 覆写修复）：

- 逐文件判桶化；桶化文件 `月搜索量` 一律留空、区间写 `搜索量区间`
- update 分支改成**只写本次真的带回来的列**，不拿 `None` 去盖已有值

## 首次真实导入：Query 库 10 → 47 行

```
文件      : Keyword Stats 2026-08-05 at 16_25_41.csv（utf-16 / TAB / 表头第 3 行）
桶化判定  : True —— 37 条取值全部落在桶集合里（出现过 50 / 500 / 5000 / 50000）
写入      : create=37, update=0, skipped=0
```

独立回读（重拉线上，不看写入回执）：

| 项 | 值 |
|---|---|
| 行数 | 47 |
| 数据来源 | `{Keyword Planner: 37, 探测问题: 10}` |
| 搜索量区间 | `{10 – 100: 22, 1K – 10K: 8, 100 – 1K: 6, 10K – 100K: 1, (空): 10}` |
| 月搜索量非空 | **0** |
| 同时有精确值和区间的行 | **0** |
| 原 10 行是否被动过 | 否（区间全空、来源仍是 `探测问题`） |

## ⚠️ 两件需要真人处理的事

**① Phase 0 字段表那页要补 changelog。** 本次解冻只改了线上 schema 与本仓库，
Phase 0 实现结果页（`3b2059d969338184bcb3f5ab87a7771b`）里的字段表还写着 7 列。
留着不改，下一个人会按那页去核对，然后发现对不上。

**② 37 条里有明显噪音，需要真人筛。** 脚本不做相关性判断（筛选是判断不是计算），
所以 KP 的扩展建议原样入库了。至少这几条与 Vivu 无关：

```
music search (1K – 10K)   search song (1K – 10K)   pim dam system (10 – 100)
www google com search video (1K – 10K)             yahoo video search (1K – 10K)
```

`状态` 的四个取值里没有「不相关」——只有 `无量搁置`，而这些是有量但跑题。
要么用 `无量搁置` 凑合（语义不对），要么归档删行，要么再解冻加取值。**你定。**

## 连带后果（未改，登记）

`serp_scan.py:72` 按 `月搜索量` 降序取 top-N，空值排最后。
这 37 行的 `月搜索量` 全空，所以它们在 SERP 选词里**永远排最后**——
明明有量级信息，只是不在那一列。要不要让 `serp_scan` 也读 `搜索量区间`，是另一个决定。

---

# 2026-08-05 收尾之二：噪音归档 / serp_scan 认区间 / Google Ads token

## ① Phase 0 changelog 已补

Phase 0 实现结果页新增「六、字段表变更 changelog」，记录当天两次解冻的内容、理由、
回读结果，以及连带的三处脚本口径变更。

§一.5 那张「Query 库 7/7」的核对表**刻意没有改写**，只在标题下加了一行警示指向 changelog——
那张表是「2026-08-04 建了什么」的记录，改掉就没有历史了。同 Phase 3 §七⑥ 的处置口径。

## ② 五条噪音已归档，Query 库 47 → 42 行

Shawn 确认全是噪音：`music search` / `search song` / `pim dam system` /
`www google com search video` / `yahoo video search`。

归档走三道闸（文本在确认清单里 + 来源是 Keyword Planner + 状态是候选），
先 dry-run 打印五行再执行，回读确认全部不在库、原 10 行探测问题未被触碰。
**Notion 归档可从回收站恢复，不是永久删除。**

### ⚠️ 还有三条同类的，Shawn 没有裁到

它们和已确认的那批是同一种东西（搜索引擎导航词，不是买家在找解决方案）：

```
yahoo search video                  100 – 1K
www google com search video download 100 – 1K
www google search video             10 – 100
```

**我没有自作主张扩大删除范围**——确认了五条就只删五条。要一并归档说一声。

## ③ serp_scan 改为认 `搜索量区间`

`pick_queries` 此前只看 `月搜索量`，于是桶化来的 37 行（月搜索量全空）
在 SERP 选词里永远排最后，明明有量级信息只是不在那一列。

现在两种量都认，排序用区间**下界**（`config/kp_buckets.yaml` 的 `sort_floor`）：

| 情况 | 排序量级 |
|---|---|
| 有 `月搜索量` | 精确值本身 |
| 有 `搜索量区间` | 下界（`1K – 10K` → 1000） |
| 两者皆无 | 排最后 |

**为什么取下界不取中值**：下界是「至少这么多」，是这条区间能保证的事实。
拿中值（5000）去压一个精确值 200 是拿推断压过测量；拿下界（1000）比，
仍然赢，但赢在一个不会错的数上。**该数只用于排序，永不写库。**

实测选词顺序（`reverse video search` 从「永远最后」变成第 1）：

```
 1. reverse video search              区间下界（10K – 100K）
 2. google video search               区间下界（1K – 10K）
 3. video finder                      区间下界（1K – 10K）
 …
```

这是 `serp_scan.py` 第一次被改动。

## ④ Google Ads developer token 已入 .env

`GOOGLE_ADS_DEVELOPER_TOKEN` 已写入（`.env` 已 gitignore，git 全历史扫描零命中）。
写入未经过 shell 命令行，避免进 shell history。

### ⚠️ 但路径 2 仍然跑不了，两个原因

**一、还缺 4 项凭据。** Google Ads API 要的是 developer token **加** OAuth：

```
GOOGLE_ADS_DEVELOPER_TOKEN   ✅ 已设置
GOOGLE_ADS_CLIENT_ID         ❌ 缺
GOOGLE_ADS_CLIENT_SECRET     ❌ 缺
GOOGLE_ADS_REFRESH_TOKEN     ❌ 缺
GOOGLE_ADS_CUSTOMER_ID       ❌ 缺
```

**二、`--source api` 那条路根本没有实现。** `keyword_volume.py` 的 api 分支
**无条件**调 `missing_credential` 退出——五项全齐它也只会报「缺 0 项」然后退出码 2。
Phase 2 当时写的「接口留着，实现未接」字面为真：**API 调用一行都没写。**

所以凭据到齐之后还有一步工程活：接 `KeywordPlanIdeaService`。

### ⚠️ 还要确认 token 的 access level

Google 的 developer token 分 Test / Basic / Standard。**Test 级只能查测试账号**，
返回的是假数据。若当前是 Test 级，接通了也拿不到真实搜索量——
先在 Google Ads 后台 API Center 确认它的级别，再决定要不要动工。

---

# 第七条链：Search Console（2026-08-05）

Shawn 拍板接。**这是 Phase 0 字段表当天第三次解冻**：`数据来源` 加第 7 个取值
`Search Console`。改后 8 字段 / 7 取值，独立回读核对，42 行数据完好。

## 它和另外两条的分工（别搞混）

| | 回答什么 | 精度 | 长尾 | 成本 |
|---|---|---|---|---|
| Keyword Planner | 市场上有多少人搜 | 桶中值 | ⛔ 拒绝 >10 词 | 要 Basic 审批 |
| **Search Console** | 真人实际搜了什么、我们露没露面 | **精确整数** | ✅ 无限制 | **免费** |
| SERP API | 谁在占这些词的位 | 不给量 | — | 按量计费 |

## ⛔ 两条写死在脚本最前面的纪律

**一、impressions 不是月搜索量。** 它是「我们出现了多少次」，被两件事过滤过：
我们有没有内容、Google 排不排我们。一个词一个月一万人搜、我们从没露面 → impressions = 0。
把它写进 `月搜索量` 比 KP 桶中值更严重——**桶中值至少还在描述市场，impressions 描述的是我们自己。**

所以本链写进 Query 库的行，`月搜索量` 与 `搜索量区间` **都留空**。
clicks / impressions / position 只落运行日志与审核清单。

**二、它看不见我们没有内容的品类，它的沉默不是「没需求」的证据。**
2026-08-05 人工看过一轮：739 条 query 里，「检索已有素材」这个品类**一条都没有**——
而那正是 Query 库整库在讲的东西。那不是市场没需求，是我们没内容。
两者混同就是拿缺席当反证。要找「市场有需求但我们完全不沾边」的词，那是 Keyword Planner 的活。

## 品牌判定：首版是错的，修法记在这里

品牌词不是选题依据，得先滤掉。难点是**拼写变体**——实测有几百条
（`vibu ai` / `vi you ai` / `viyu ai` / `vuvu video` / `vievu` …），「包含 vivu」一条都拦不住。

首版用模糊匹配，词表里放了复合形态 `vivuvideo`。**16 条测试真词误杀 13 条，而且全部同分 0.714。**
同分是线索：`SequenceMatcher("vivuvideo", "video") = 2×5/(9+5) = 0.714`——
拿复合品牌名去跟通用 token `video` 比，**凡是含 video 的真词全被判成品牌**。
那是规则错，不是阈值错，调阈值永远修不好。

改成三段：

1. **子串命中**（不模糊）：`vivu` / `vivuai` / `vivuvideo` / `vivustudio`
2. **模糊匹配只用核心 token** `vivu`，且通用词（video / ai / editor / online …）不参与比较
3. **阈值按长度分档**：去空格后 ≤10 字符用 0.50，更长的用 0.72——
   短串跟品牌名有一半像就几乎必然是拼写变体，长短语则要求高得多

离线自测（样本全部取自当天真实 GSC 返回）：

```
品牌变体 23/24 命中（漏 "vioai" 0.444）
真词     20/20 保住，零误杀
真词最高分 0.333，离 0.50 还有距离 —— 不是压着线过的
```

漏判的那 4% 会出现在候选清单里，由真人加进 `force_brand`。

## 顺带算出 branded search 基线

Concept 里 A7 的度量项之一，**当前没有任何脚本在算**。它和 query 候选是同一次
API 调用带回来的，不额外花任何成本，所以顺手算进报告：branded / non-branded
的 query 数、点击、曝光及占比。

注意：这个比例依赖品牌模糊匹配的阈值。**做趋势时必须锁住阈值**，否则是在量自己的参数不是量市场。

## 新增文件

| 文件 | 说明 |
|---|---|
| `scripts/gsc_queries.py` | 主脚本。默认 dry-run；`--commit` 另需 config approved |
| `scripts/gsc_auth.py` | 一次性换 refresh_token。**不写日志、不落 outbox、不改 .env** |
| `config/gsc.yaml` | 品牌判定与过滤规则，含实测结果留档。`status: draft` |

只用 `requests`，不引 `google-auth` / `google-api-python-client`——
refresh_token 换 access_token 就是一次 POST，为它装一整套 SDK 不值
（同 `aeo_common` 开头那条「不用第三方库，避免多一个依赖」）。

## 现在跑它会怎样

```
exit=2  status=missing_credential  missing=[GSC_CLIENT_ID, GSC_CLIENT_SECRET, GSC_REFRESH_TOKEN]
```

按纪律不降级、不造假，退出码 2 让缺失被看见。

### 真人要做的（GSC API 免费，但这几步脚本代不了）

1. Google Cloud Console 建或选一个项目
2. 启用 **Google Search Console API**
3. OAuth 同意屏：类型 External，把自己加进 Test users，
   scope 加 `https://www.googleapis.com/auth/webmasters.readonly`
4. 凭据 → 建 OAuth client ID → 类型 **Desktop app** → 拿 client_id / client_secret
5. 两个值写进 `.env` 的 `GSC_CLIENT_ID` / `GSC_CLIENT_SECRET`
6. 跑 `python3 scripts/gsc_auth.py`，照它说的授权，把它打印的 `GSC_REFRESH_TOKEN` 贴进 `.env`
7. 跑 `python3 scripts/gsc_queries.py` 看 dry-run，对着 `dropped_as_brand_borderline`
   核一遍品牌判定，再把 `config/gsc.yaml` 的 `status` 改 `approved`，才能 `--commit`

## 一处已知的调参点

`min_impressions: 3` 会滤掉 `premiere pro beat detection`（1 次曝光，但**排名 4.0**）。
曝光低而排名高的词，往往是最精准的长尾——这个阈值把它们一起滤掉了。
首轮真实跑完之后对着 `dropped` 列表调。

## GSC 降级路径与首轮真实 dry-run（2026-08-05）

等 OAuth 凭据就等于把品牌判定的校准无限后推——而那恰恰是 `approved` 之前必须看的。
所以照 `keyword_volume` 的形态加了降级路径：`--source csv`（**默认**），
解析真人从 GSC 界面 EXPORT 的 `.zip` / `.csv`，零凭据。
`--source api` 仍在，缺凭据时退出码 2。

导出包里有 7 个 CSV，脚本只认名字含 `quer` 的那份，不靠顺序。

⚠️ **导出必须不带任何过滤器。** 带着 `-vivu` 之类的过滤导出，
等于拿 Google 已经滤过一遍的样本去验我们自己的滤器。

### 首轮结果：机制通了，但产出 75% 是第三方品牌

```
745 行 → 739 条 query
  判为我方品牌（含拼写变体） 540
  过滤掉                    163（曝光 <3 的 127 条、单 token 34 条、login 2 条）
  待写入                     30（+ 被 max_per_run 截下 6）
```

**branded search 基线**（Concept A7 度量项，此前无人计算）：

| | query 数 | 点击 | 曝光 |
|---|---:|---:|---:|
| 品牌 | 540 | 9,644 | 116,566 |
| 非品牌 | 199 | **14** | 1,722 |

**品牌占点击 99.9%、占曝光 98.5%。** 非品牌 16 个月总共只带来 14 次点击。

### ⛔ 30 条候选里只有 5 条是真的 query

其余 25 条是**别人家的品牌名**——我们排在这些词上，是因为域名/品牌长得像：

```
viyou ai official website / viyou ai video generator / viyou ai online …（同一家 6 条）
vuvido style / vuvido app / vuvido .com     visla ai      vrew video editor
vidifyai studio    vibio ai    virio ai    vibro ai    viloi ai
vidmake tutorial a complete guide to mastering video creation
```

真的 query 只有这 5 条（加上被截下的 `ai that cuts video` / `ai powered video editor` /
`ai video online` / `xyz video generator`，共 9 条）：

```
video editing made easy            27 曝光 / 排名 88.6
interactive fan videos             12 曝光 / 排名 95.5
ai-powered video editing software   8 曝光 / 排名 91.6
ai assistant video editor           8 曝光 / 排名 92.1
ai video editing studio             7 曝光 / 排名 89.9
```

**根因**：品牌过滤器只认**我们自己**的品牌。GSC 里塞满了**别人**的品牌，
因为我们靠相似域名排在那些词上。这一条在人工看那一眼时看不出来——
那时只翻了前 10 行，全是我方品牌。

### 这批第三方品牌名不是垃圾，是放错了地方

`viyou / vuvido / visla / vrew / vidify / vibio / vidmake` —— 这是一份
**「市场把我们和谁归在一起」**的名单，正是 J0 竞替名单要的东西。
但 `gates.yaml: competitor_list_converged = false`，且 Concept 明文
「竞替名单没有从真实对话中收敛出来之前，禁止凭猜测指定对比页对象」。
**所以本次不把它写进任何地方，只在这里留档**，交 J0 与真人。

### 已验证的修法（不需要点任何竞品名）

第三方品牌有个结构特征：**含一个造出来的词**（viyou / vuvido / vrew）。
真 query 全部由词典词组成。所以判据可以是「**所有 token 都是英文词**」——
一个竞品名都不用列，也就不碰 `competitor_list_converged` 那条禁令。

用 `/usr/share/dict/words`（macOS 自带，23 万词）加简单词形还原实测：

```
21 条样本（第三方品牌 10 + 真 query 11）→ 判对 21/21
```

词形还原是必需的：不加时 `ai that cuts video` / `search video by spoken words` /
`how to find a clip in hours of footage` 三条被误杀，因为 web2 词典只收原形，
`cuts` / `spoken` / `hours` 都不在里面。

**尚未实现**——它是个新的过滤维度，且依赖一个系统文件（缺失时要能优雅降级并说出来）。
待拍板。

## 造词过滤落成：候选 30 → 14（2026-08-05）

三道滤器分工，跑在 739 条真实 query 上：

| 滤器 | 拦下 | 拦的是什么 |
|---|---:|---|
| 我方品牌（模糊+词典+独立 token） | 533 | `vivu` 及几百条拼写变体 |
| 曝光 < 3 | 132 | 长尾噪音 |
| 单 token | 34 | `vuvido` / `stagevu` 一类 |
| **造词** | **24** | **第三方品牌**：viyou / vuvido / visla / vrew / vidifyai … |
| 排除词 / 非英文 | 2 | `login`、阿拉伯语 |
| → 待写入 | **14** | |

### 造词判据：不点任何竞品名

第三方品牌都含**造出来的词**，真 query 全由英文词组成 → 判据「所有 token 都认识」。
用 `/usr/share/dict/words`（23 万词）+ 词形还原。**词典读不到时整条规则停用并在报告最上方大声说**——
「没报错」被读成「过滤过了」是 Phase 3 §七⑦ 那类隐患。

### 顺带修好一个首轮没暴露的误杀

加词典之前，品牌模糊匹配的 `short_threshold = 0.50` 会误杀
`live video` / `view count` / `video view` / `live stream` / `visual ai` / `vivid ai`——
`"vivu"` vs `"live"` = 0.50、vs `"visual"` = 0.60、vs `"vivid"` = 0.667。

**这批词碰巧不在 vivu.ai 的数据里，所以跑全量时没暴露**，是补测通用词组才发现的。
根因不是阈值：**品牌拼写变体按定义就不是英文词**。所以加了两条——
英文词永不判为品牌变体；非通用 token 全是英文词时整串也不比
（整串比对本是为逮住被空格拆开的品牌 `vi you ai`，其中 `vi` 不是词）。

重测（品牌变体 30 + 真词 28）：**我方品牌 26/26、真词 28/28 零误杀。**

还加了 `standalone_tokens`：`vu` **在系统词典里**，两道滤器都放行，
而阈值按整串长度选又让 `vu editor`(8字符) 被抓、`vu video editor`(13字符) 漏掉——
同一个证据两种标准。列成显式 token 最干净，且不动已调好的阈值。

### ⚠️ 剩一类挡不住的：恰好是英文词的第三方品牌

残留 4 条含 `viva`（VivaVideo 是真实产品，而 `viva` 是英文词）。
结构判据对它无能为力——要拦必须**点名**。

加了 `brand.third_party_tokens`，**刻意留空**。脚本不自己填，理由是硬约束：
`gates.yaml: competitor_list_converged = false`，且明文禁止凭猜测指定。
从搜索数据认出「viva 是竞品」正是那条禁令要防的推断——**哪怕它看起来很显然**。
每轮报告会把疑似的单列出来（当前 `viva` 4 次居首）供真人点名。

### 14 条候选的成色

真 query 9 条（`video editing made easy` / `interactive fan videos` /
`ai-powered video editing software` / `ai assistant video editor` /
`ai video editing studio` / `ai video online` / `ai that cuts video` /
`ai powered video editor` / `xyz video generator`），
外加 4 条 `viva` 与 1 条 `v i video`。

**全部落在「AI 视频生成/剪辑」品类，排名 88–95。** 与 Query 库整库讲的
「检索已有素材」仍然一条都不重合——这一点跟人工看那一眼的结论一致。

## 第三方品牌单独成桶（2026-08-05）

原实现把 `third_party_tokens` 命中的行并进「我方品牌」，**那会把 branded search 基线虚高**——
那个比例是 A7 的度量项，掺了别人家的流量就不再是我们的基线。已改成三桶：
我方品牌 / 第三方品牌 / 非品牌，第三方那桶不参与基线计算。

同理把 `vivo` 从 `standalone_tokens` 移出——它是第三方（vivo 手机 / vivo AI），
放在「我方品牌形态」那张表里是分类错误，尽管过滤效果一样。

Shawn 2026-08-05 点名：`viva`（VivaVideo）、`vivo`。

结果：我方品牌 527 · 第三方品牌 16 · 待写入 **10**。

### 待确认（2026-08-05 Shawn：先留着，不着急）

15 条「判成我方品牌但接近阈值」的里，**没有一条是真 query**——
所以问题不是要不要捞回候选池，而是**有几条该从我方品牌重标成第三方**，
那会改变 branded 基线。两处最要紧的模糊项：

| 词族 | 规模 | 为什么模糊 |
|---|---|---|
| `vibu` | **11 条 / 3834 曝光** | `vibu ai` 单条就 3753 曝光。若 `vibu studio` 是别人的产品而非 `vivu` 的错拼，我方 branded 基线要掉一大截 |
| `vuvi` / `vuvido` | 31 条 / 1004 曝光 | 倾向是错拼链（`vivu video`→`vivuvideo`→`vuvideo`→`vuvido`），但 `vuvido style` / `vuvido app` / `vuvido .com` 带产品化后缀 |

看着像真第三方产品的三条：`vosu ai`（同族有 `vosu.ai video to video`）、
`vidu ai`（同族有 `vidu online`，Vidu 是真实视频生成模型）、
`vidflux`（单条但排名 3.8——排这么高通常意味着页面上出现过这个名字）。

确认后填进 `config/gsc.yaml` 的 `brand.third_party_tokens` 即可。

---

# 2026-08-05 收尾之三：Query 库写入去人工化（Shawn 拍板）

三处改动，目标是「链路自动写库 + Shawn 定期 review 库」，不再逐次等真人 `--commit`：

| 改动 | 文件 |
|---|---|
| `gsc.yaml` status → `approved` | `config/gsc.yaml` |
| `scan_queries.yaml` status → `approved`（规则仍未经真实数据验证，首轮出料后 review 时复核） | `config/scan_queries.yaml` |
| GSC 默认 `--source api`（CSV 降级路径保留） | `scripts/gsc_queries.py` |

注意：脚本本身仍是默认 dry-run、写库仍要 `--commit` 参数——去掉的是「approved 闸门等真人」，
不是 dry-run 纪律。自动化的写入由定时任务带 `--commit` 调用实现（调度待建，见当日对话记录）。
GSC API 路径缺 `GSC_CLIENT_ID / GSC_CLIENT_SECRET / GSC_REFRESH_TOKEN` 三项，
补齐步骤见 gsc_queries 一节的「真人要做的」。

---

# 2026-08-05 收尾之四：自动运转落成（调度 + GSC 全链 + KP API + sequence 扩段）

## 已验证的链路状态变化

| 链 | 之前 | 现在 |
|---|---|---|
| GSC | CSV 手动导出 | **API 全自动**（OAuth 三项已入 .env，dry-run 实测拉回 739 条、出 10 条候选） |
| Apollo | 缺 key | **已通**（auth/health 200，A 段 dry-run 实测 49 家公司、配对与去重正常） |
| KP API | 「实现未接」 | **已实现** `generateKeywordHistoricalMetrics`（缺 OAuth 4 项，`ads_auth.py` 就绪） |

gsc_auth.py 的 OOB 重定向被 Google 封杀是当天实测踩到的（报「Access blocked:
request is invalid」），已改 loopback；ads_auth.py 生而用 loopback。

## 新增调度（OpenClaw · vivu-sales · mode none → telegram -5261250225）

| 任务 | cron | 干什么 |
|---|---|---|
| `aeo_query_candidates` | `0 11 * * *` | 候选池 → Query 库（AI 建议链） |
| `aeo_gsc_queries` | `30 11 * * 1` | GSC API → Query 库 |
| `aeo_scan_queries` | `0 12 * * 1` | 水箱 A1 扫描原话 → Query 库 |
| `aeo_apollo_poll` | `0 13 * * 1` | Apollo 名单 → 水箱（含招聘反查） |
| `aeo_query_intake` | `0 14 * * 5` | 进料链诊断 + 买家原话 `--commit` 入库 |

执行体 `run_chain_commit.sh`：统一追加 `--commit`（脚本本身保持默认 dry-run 纪律，
「自动写入」这个决定集中在这一个文件，要撤销只改这里）；写 0 条 → NO_ALERT 静默，
写 >0 条 → PUSH 摘要进群，失败（含缺凭据 exit 2）→ PUSH 失败上报。
`run_query_intake.sh` 的 buyer_quote 从 `--review` 改成 `--review --commit`。
`brief.yaml` 时刻表同步加了 5 行，行数上限 32 → 37（原则不变：永不截断）。

## keyword_volume 第三处改动：API 路径落地

词表 = 种子词 ∪ 库里无量证据的词（判据与 kp_seeds 一致）。API 返回值照走
文件级桶化判定——无投放账号 API 给的同样是桶中值，不因「来自 API」就当精确值。
>10 词的先剔除并点名。Test 级 token 会收到 DEVELOPER_TOKEN_NOT_APPROVED，
报错里已写明去 API Center 升 Basic。

还差真人做的：`ads_auth.py` 换 refresh token（client 可复用 GSC 的，但要先在
同一 Cloud 项目启用 Google Ads API）+ `.env` 补 `GOOGLE_ADS_CUSTOMER_ID`。
凭据齐后建议加 cron `aeo_keyword_volume`（周一 11:45，避开 11:30 GSC）。

## Apollo sequence 扩段

`apollo_sequence.py` 加 `--segment B|C|D|E`（覆盖 build_now，名字按 naming 模板）。
正文走既有 skill 产线（skill_check --stage → claude -p + vivu-outreach +
ai-writing-guideline，--add-dir 指向实时规则文件）。B–E 四段建成即暂停，
启动键仍在 Apollo 界面由真人按——零发送红线一字未动。

---

# 2026-08-06：SERP 链落成；KP 堵点确认

## SERP 链（第四次解冻，Shawn 拍板加列）

Query 库 additive 加第 9 列 **`SERP 占位`**（rich_text）。独立回读：9 字段、
既有 8 列未动、42 行完好。写入语义：**每次扫描覆盖写快照**（历史在 logs），
格式 `top3: 域名1, 域名2, 域名3 | 评测站: url | 扫描 YYYY-MM-DD`。
`数据来源` 行为不变：仅在为空时写「SERP 观察」。

`serp_scan.py` 第二次改动：写新列 + `--top-n` 覆盖参数（试跑省额度）。
实测 1 次调用打通全链：`reverse video search` → 占位快照入库，
来源（Keyword Planner）与区间未被触碰。

新 cron：`aeo_serp_scan` 周一 13:30。配额算术：20 次/周 ≈ 87 次/月，
脚本自带的月配额闸（100 次）在 5 个周一的月份会拦住超额部分——拦了会在日志里说。

## KP 链堵点（2026-08-06 实测，只剩真人动作）

代码侧已就绪并修掉一个坑：**v21 API 已停服**（UNSUPPORTED_VERSION），
默认版本改 v25（v22–v25 实测均在服）。OAuth / refresh token 本身工作正常。

剩两个堵点，都在 Google 后台：

1. **developer token 是 Test 级**（v22–v25 一致返回「only approved for use with
   test accounts」）。要在**发 token 的那个账号**的 API Center 申请 Basic。
2. **customer 权限对不上**：OAuth 的 Google 账号能访问的是
   `798-573-1642`（CUSTOMER_NOT_ENABLED，未启用的空壳）与 `416-830-6862`，
   均不是 .env 里的 `215-156-2899`（浏览器跑 KP 用的那个）。
   两条路二选一：给 OAuth 的这个 Google 账号授 215-156-2899 的访问权；
   或换有权限的 Google 账号重跑 ads_auth.py。
   ⚠️ 若 215-156-2899 经 MCC 管理，还要在 .env 配 GOOGLE_ADS_LOGIN_CUSTOMER_ID。

堵点解除后：`keyword_volume.py --source api` dry-run 验证 → 建 cron
`aeo_keyword_volume`（建议周一 11:45）。

## J4 草稿镜像到水箱行页面（2026-08-06 Shawn 反馈驱动）

**痛点**：daily_sla 超时消息（当日 49 项）每行带水箱行链接，从 Telegram 点进
Notion 页只有 LinkedIn 链接、没有草稿、也没有可复制的回执——草稿只活在群历史里，
和 SLA 消息对不上号；发完 LinkedIn 后「怎么更新状态」也没有现成的抓手。

**改动**（不动 Phase 1/2 脚本，`sla_check.py` 零改动）：

- `aeo_common.Notion` 纯追加 `list_children` / `append_blocks` 两方法（不碰 schema，
  不加列——加列要拍板，追加页面正文不用）。
- `draft_runner.py` assemble `--commit` 写完 outbox 后，把草稿正文 + `sent <行ID>`
  回执追加到对应水箱行页面正文。两样都用 code block（Notion 一键复制，手机免圈选）。
  追加后独立回读核对（重列 children 找 heading），结果计入 assemble JSON 的
  `notion_mirror` 节。
- 配置在 `config/outreach.yaml` 新增 `draft_to_page` 节（开关 + 文案，脚本无字面量）。

**口径**：Telegram 群仍是主通道与零发送红线的载体，页面只是镜像。镜像失败不炸 run
（群里草稿是主产品），失败进 stderr 点名 + JSON 留档。按「heading 含日期」去重，
assemble 重跑幂等（实测第二遍 10/10 `already_mirrored`）。

**实测**：重跑当日 assemble，10 条草稿全部 `mirrored` 且 `readback_ok=true`。
此后每天 08:30 起，SLA 消息里点进任一「已有草稿」的行即见草稿与回执。

**已知边界**：每天最多 `max_drafts_per_run`（10）条、冷却 72h，按剩余时限升序
（最逾期优先）。49 项超时意味着页面上暂时没草稿的行是还没轮到或被证据闸门拒绝
（缺信号原文），前者约 5 天轮完一遍，要更快就调 `outreach.yaml` 的
`max_drafts_per_run`（代价：claude 产稿时长 + 群消息 ×3）。

### 2026-08-06 当日操作：49 项超时全量回填（真人指令，一次性）

Shawn 要求把当日 SLA 49 项全部备上草稿。执行路径：

- **历史草稿 10 条**（8/4 Stefanno 1 条 + 8/5 批次 9 条）：从 outbox 存档捞正文，
  按原日期镜像上页面（8/4 那条是 id 截断事故的幸存文件，旧单条格式，按标记行抽正文）。
- **从未产稿的 29 条**：连跑 3 轮正式产线 `run_draft_runner.sh`（10+10+9），
  冷却机制天然分页；证据闸门 0 拒绝（Apollo 行全部走 no_quote 降级）。
- **核对**：逐一回读 49 个页面，草稿 heading + `sent` 回执 code block 全部在位；
  plan 干跑确认队列清零。

**副作用（如实声明）**：① 29 条新草稿只在页面与 outbox，**没有推 Telegram 群**
（手动跑没有 agent 转发 PUSH——页面本来就是这次的交付面，群里不再刷 87 条）；
② 49 行冷却时间戳已盖，**未来 72h 的 08:30 草稿任务大概率 NO_DRAFTS**，
新入箱的行不受影响；③ 当日 08:30 cron 的原始 plan 与 claude 输出备份在
`logs/*_2026-08-06.cron_orig.*`，三轮回填的 claude 输出在 `logs/j4_claude_2026-08-06.backfill_c[123].txt`。

## 水箱链补上地域筛选：非美国 profile 入箱事故（2026-08-06）

**现象**：Shawn 处理当日 J4 草稿时发现水箱里一批非目标地区的 LinkedIn profile
（印尼 ONIC/BOOM Esports、马来西亚 Bonia、德国 G2、印度 AnyMind/Zerodha 系、
菲律宾、立陶宛、埃及……），全部是 8/4 首轮 `apollo_poll --commit` 入箱的行。

**根因**：地域筛选 8/5 只加给了冷链（commit 4a31bc0，当时 outreach.yaml 注释
原话「apollo_poll 的水箱链不受影响」）。暖链 `apollo_poll.py` 的 people search
从未带 `person_locations`，Apollo 全球命中；segments.yaml 的行业筛选又早已降级成
关键词标签（Phase 2 §七 疑点②），宽而糊的命中面叠加无地域约束，东南亚公司大量进箱。
50 行 Apollo 来源里，肉眼可辨的非目标地区约 20 行。

**改动**：

- `config/outreach.yaml`：`person_locations` 从 `sequence.targeting` 提升为顶层
  `targeting`——冷链暖链没有理由用两套地域口径，收口为一处（US/CA/UK/AU/NZ/IE）。
- `scripts/sequence_list.py`：改读顶层 `targeting`。
- `scripts/apollo_poll.py`：`build_payload` 增加 `locations` 参数，segment 发现、
  backfill 定向反查、two_phase 补齐三条路径全部带上——补齐搜索不带的话，
  给一家美国公司配对时仍会补进别国的人。运行留档 JSON 新增 `person_locations` 字段。

**实测**（dry-run + --no-enrich，搜索零 credit）：D 段修复前入箱的是
Bonia（马来西亚）/ONIC（印尼），修复后同条件命中 SharkNinja、
The Pokémon Company International（均美国），payload 被 Apollo 正常接受。

**遗留（待 Shawn 拍板，本次未动数据）**：

- 8/4 已入箱的污染行：14 条已被 Shawn 手动标「淘汰」；仍有约 10 条疑似非目标地区
  的行留在 inbox（Bonia ×2、Ulearn ×2、Turning Red Media ×2、Creator Engine ×2、
  Zero1 by Zerodha ×2），另有 Luthfi Nur（ONIC，印尼）已是「触达中」。
  水箱行没有存地域字段，确认国别要么人工点开 profile，要么花富化 credit 反查。
- `person_locations` 六国口径是冷链当时的推演值，暖链沿用；要收紧到 US-only
  改 `outreach.yaml` 顶层 `targeting` 一处即可。

### 同日追加：三项拍板已执行（2026-08-06）

上节「遗留」三项 Shawn 拍板（原话「淘汰。收进程 us only。存 apollo 返回的 country」），
已全部执行：

1. **存量污染行淘汰**：inbox 里 10 条疑似非目标地区行（Bonia ×2、Ulearn ×2、
   Turning Red Media ×2、Creator Engine ×2、Zero1 by Zerodha ×2）全部
   inbox → 淘汰。改前逐行断言「状态=inbox 且 来源=Apollo」，改后独立回读核对
   10/10 通过，操作留档 `logs/manual_cull_2026-08-06.json`。
   Luthfi Nur（ONIC，印尼，已「触达中」）不在本批——已真实触达的行怎么处理仍待定。
2. **地域口径收紧 US-only**：outreach.yaml 顶层 `targeting.person_locations`
   只留 United States，冷暖两链同时生效。
3. **country 落库**：水箱 schema 新加 `country` select 列（加列有拍板，PATCH 后
   回读核对通过）。apollo_poll 三处配合：person_record 捕获（预览恒打码，实测只有
   has_country 布尔，留字段为兜底）、enrich_rows 用 bulk_match 返回值覆盖
   （1 credit 实测：country="United States" 正常返回，1315 → 1314）、
   pipeline_props 写库（空值走 select None，不脏数据）。

**边界（如实声明）**：country 只对今后新入箱的行有值，存量 74 行为空——补历史行
要按人再付富化费，未拍板不做。冷链 sequence_list 只搜公司不富化，不受 country 列影响。

---

# 2026-08-06:J1 内容产线落成(Shawn 拍板:建立流程,计入台账)

## 先纠正一个误会:闸门没有被放开

Shawn 原话是「J1 闸门放开,等攒够 5 条太久了」。实测结论:**5 条 win/loss 的
触发线只挡对比页(闸门②),从来不挡 AEO 内容**。AEO 内容此前被拒是因为请求
没带证据编号(闸门①),而水箱现在 73 行,其中有真实原话的行足够过闸。
所以本次**没有改任何闸门、任何阈值**——用水箱真实 SIG 证据合法通过,
`gates.yaml` 一字未动,五道闸的语义与顺序原样保留。

## 建了什么

三段式,与 J4 draft_runner 同构(LLM 不进 Python):

| 文件 | 干什么 |
|---|---|
| `config/j1.yaml` | 产线全部业务值:选题类型/来源白名单、证据筛选、事实层路径、篇数上限 |
| `scripts/j1_runner.py` | plan(选题+证据候选+写 prompt)与 assemble(落盘+台账+回读)两个分支 |
| `scripts/run_j1_draft.sh` | 执行体:skill 暂存 → plan → claude -p --model opus → assemble --commit |

纪律逐条继承:unset wrapper(订阅登录,不烧 API)、默认 dry-run、
plan 文件只在 `--emit-prompt` 时写(Phase 3 §七④ 覆写事故的教训)、
**写台账走且只走 `j1_evidence.py` 子进程**(单一写路径,闸门不复制)、
写后独立回读(重新拉台账核对「面向」,不信 create 回执,回读不一致退出码非 0)。

## 选题队列的来源白名单(首轮 dry-run 就踩到的坑)

不加白名单时,队列头两名是 `yahoo search video`、`www google com search video
download`——KP 补量链进来的导航词,不是能回答的问法。这正是裁决①代价那句
「排 AEO 内容优先级前必须先看数据来源列」的具体形态。修法:`j1.yaml` 的
`queue.sources_allowed` 只放「市场在问」形态的来源(探测问题 / A1 扫描 /
买家原话 / AI 建议 / Search Console),KP 与 SERP 观察刻意排除。

## 证据的用法边界

prompt 里只给 SIG 编号 + 原话摘录,**不给人名不给链接**;文章只许拿证据校准
痛点形态,不许出现当事人身份、公司名或原话直引。带「非原文引用」标记的
Apollo 名单行不进候选(与 J4 evidence_gate 同一判定)。配不上证据的选题
要求 claude 输出 REFUSE 包,不许硬写——没有证据的内容只能靠编。

## 事实层约束

正文可引用的产品事实 = `vivu_web/data/facts.json` 里 `status=已确认` 的字段,
plan 阶段解析后逐条注入 prompt,并显式声明三条负面约束:无公开定价、
无已确认 benchmark、无具名客户。待真人补的字段视同不存在。

## 首日产出(全部已实测)

| 产出 | 台账行 | 证据 |
|---|---|---|
| 样稿|search a video library by what was said in it(会话内手写,同一产线纪律) | `3b4059d9…c650` | SIG-ed099b98 |
| 样稿|pull highlights from livestream recordings(同上) | `3b4059d9…a495` | SIG-3dc7579e, SIG-46131a5a |
| 自动|how to find an old brand video we already made(产线端到端) | `3b4059d9…fec9` | SIG-3dc7579e, SIG-ed099b98 |
| 自动|search video by spoken words(产线端到端) | `3b4059d9…30da` | SIG-ed099b98, SIG-3dc7579e |

四行状态全部「草稿」,独立回读全部一致。正文在 `outbox/j1_draft_*.md` 与
`outbox/j1_sample_*.md`。**签发动作 = Shawn 在 Notion 把「状态」改「已签发」
并填「签发日期」——两件事,不是一件**。未签发的行 J2 的 CI lint 会拒绝上线,
J3 也引用不到。

> ⚠️ 2026-08-10 更正:本节原来只写了「改状态」,漏了签发日期。照着做的结果是
> 这四行状态全改了、日期全空,而站点 lint 对这种行两头堵死(填了 `signed_off`
> 报「台账签发日期是空的」,不填报「`signed_off` 为空」),两条路都 build fail。
> 详见文末「2026-08-10:发布链路补全」。

## 新增调度

| 任务 | cron | 干什么 |
|---|---|---|
| `aeo_j1_draft` | `0 9 * * 3` @ America/Los_Angeles · vivu-sales · `mode none` → telegram `-5261250225` | 每周三产至多 2 篇(`j1.yaml: max_per_run`),群里只推通知(标题+路径+台账链接),正文不进群 |

选周三:避开周一的三个周批扫描与 GSC/SERP 链,也避开周五复盘。
每周 2 篇的上限是刻意的:签发是真人动作,产得比签得快只会堆库存。

## 边界(如实声明)

- 选题池现在只剩 1 条痛点级任务式 query 没写(`how to pull highlights…` 与
  `how to search…` 已登记,本次自动跑掉 2 条)。**下周三如果进料链没带来新
  痛点级 query,cron 会正确输出 NO_DRAFTS 静默**——这不是故障,是进料问题,
  进料链现状见「Query 库进料链」各节。
- `search video by spoken words` 与已登记的 `how to search a video library by
  what was said in it` 选题语义相近,产线不做语义去重(只做逐字归一去重)。
  两篇是否都签发、或合并成一篇,是签发环节的真人判断。
- J2 发布环节(签发后怎么上 vivu.ai)本次未动,仍按 Phase 3 §J2 的既有契约走。

# 2026-08-07：Perplexity 移出每日探测（Shawn 拍板）

## 为什么

每日探测（probe_daily）自建成以来反复卡在 §0 自检第 1 条「Perplexity 未登录」。
自检口径是「缺一个引擎的当天数据是残的」——设计上正确，代价是 Perplexity
一家把整条探测线拖停：探测不跑，§11 追问就不发生，AI 建议链的候选池
（data/query_candidates.jsonl）一直是空的。8/7 的进料链诊断把这条因果链
摆到了群里，Shawn 拍板：去掉 Perplexity，探测只跑 ChatGPT / Gemini。

## 改了什么

| 文件 | 改动 |
|---|---|
| `config/scan.yaml` | `probe.engines` 收成两个；删 Perplexity 的 URL / 模型锁定 / model_notes |
| `prompts/probe_ai_engines_daily.md` | 全文两引擎口径：日产出 30→20 条、追问 6→4 次/天、§0 自检不再开 Perplexity |
| `prompts/scheduled_task_probe_daily.md` | 任务材料同步两引擎版（预期条数、自检 URL、上报格式） |
| `config/brief.yaml` | 09:00 探测的 one_liner 更新 |
| `config/query_candidates.yaml` | 注释里的追问算术 6×5=30 → 4×5=20（`max_per_day: 20` 数值未动） |
| `scripts/query_intake_health.py` | AI 建议链断点文案不再指向 Perplexity 登录态 |

## 刻意不改的

- **探测记录库 schema 一个字没动**：`引擎` select 的 `Perplexity` 选项保留
  （Phase 0 冻结字段 + 历史行还引用它），只是不再写入新行。
- spec §四写的是「三引擎」，playbook §1 现在明示这是与 spec 的已知偏差，
  实际引擎数以 `scan.yaml: probe.engines` 为准。
- 2026-08-05 那条「零提问的自检失败不作废当周」裁定（scan.yaml halt 节）
  是历史记录，原文保留。

## ⚠️ 还差一个真人动作才收口

桌面端 Claude 的 probe_daily scheduled task 里存的是**旧版三引擎任务文案**
（建任务时粘贴进去的），仓库里改材料文件不会改到它。Shawn 需要把
`prompts/scheduled_task_probe_daily.md` 的新内容重新贴进那个 task——
否则执行体明天仍会按旧文案去开 Perplexity、仍会卡自检。

# 2026-08-07（下午）：周批扫挪周六晚 + 诊断脚本修两处误报

## 周批扫改期（Shawn 拍板）

linkedin_reddit_weekly（LinkedIn 周批扫 + Reddit 周扫，同一个桌面端 task）
从周一 10:00 挪到**周六 22:00**（America/Los_Angeles），避开工作时段。

- 口径不变：周批扫按 `datePosted=past-week` 取数，落在哪天跑都覆盖过去 7 天。
- 已知的 1.5 天时差：`aeo_scan_queries`（扫描原话入库）仍在周一 12:00，
  周六晚扫到的原话周一中午才进 Query 库。可接受，暂不动那条 cron。
- 材料已同步：scheduled_task_linkedin_reddit_weekly.md / scan_reddit_weekly.md /
  scan_linkedin_weekly.md / brief.yaml（weekly 条目 days 1→6、10:00→22:00）。
- **真人动作**：桌面端 task 的 schedule 要 Shawn 自己改成周六 22:00
  （仓库文件改不到桌面端）。改完最近一场 = 2026-08-08 本周六。

## query_intake_health 修两处误报（8/7 报告暴露）

1. **调度检查漏包装挂载**：原来拿脚本名匹配 cron 名，buyer_quote_queries.py
   由 run_query_intake.sh 包着挂在 `aeo_query_intake` 下，匹配不上被误判
   「缺调度」——当天它明明刚跑完还产了 1 条候选。修法：新增 `wrapper_stems()`
   从 run_*.sh 源码读包装关系一起匹配（不手维护表，表会随下一个 wrapper 过期；
   run_chain_commit.sh 这种 `${SCRIPT}` 转发壳刻意不匹配，其 cron 名本身含脚本名）。
2. **末行结论写死**：「四条进料链里没有一条挂着定时任务」是四链时代的文案，
   七链 + 调度陆续挂上后还在原样输出，与上文表格自相矛盾。改为按当次数据渲染
   （cron 取不到时如实说取不到，不下结论）。

修后实测（dry-run）：买家原话 ✅、无调度的只剩探测问题与 Keyword Planner（均属预期：
前者是一次性同步，后者等 Google Ads 审批后挂 `aeo_keyword_volume` 收口）。

## 补：诊断脚本 SERP 段文案跟上 8/6 的拍板（2026-08-07）

query_intake_health 的 SERP 段还停在 8/6 加列之前：把「7 个冻结字段装不下
产出」记成缺 schema 断点、挂着已经拍掉的 §八① 待拍板项、按「数据来源 为空
才可写」的旧口径说「0 行可写」。实际上 8/6 拍板加列后 `SERP 占位` 对全部行
可写、链在 aeo_serp_scan 下每周运转。

修法：creates_rows=False 不再进断点（不建行是这条链的定位——占位标注器，
不是进料链），表格里"修通需要什么"改显示结构说明而非误导性的"已通"；
serp_writable 的注释改明「仅来源标注口径，0 不构成断点」。
修后 SERP 行：❌（结构如此）· 断点 — · 需真人决定 否。

§八① 那堵墙剩下的另一半（买家原话的出处列装不下）维持原样报告，仍待拍板。

## 补：probe_daily 挪到每日 01:00（2026-08-07 Shawn 拍板）

探测从每日 09:00 挪到 01:00（America/Los_Angeles），避开工作时段——
与周批扫挪周六 22:00 同一动机。三处材料已同步：scheduled_task_probe_daily.md、
probe_ai_engines_daily.md、brief.yaml（01:00 早于 daily_brief 10:00，
当天简报看到的是已跑完的探测，顺序反而更顺）。

前提与真人动作：
- 桌面端 task 的 schedule 需 Shawn 自己改成每日 01:00。
- 凌晨 1 点机器得醒着（桌面端 Claude task 在本机跑浏览器）——
  合盖/睡眠会静默缺席，而探测的验收项是「连续 7 天有记录」。

---

# 2026-08-10：Gemini 误点停止事故 → 中断/卡死处置成文 + 收 0 留痕

## 出事的是什么

8/10 日报里三次「没拿到回答」，全在 Gemini：

1. 两次追问：一次把正在生成的回答**误点停了**（页面留 `You stopped this response`），
   一次提交后 **4 分钟以上零输出**、一直停在 `Stop response`。两次都记 0、未重问。
2. 探测问题 `best video search tool` 也被同样的误点停掉，按 §10「截断后可重试一次」
   在新对话重问，拿到完整回答入库。

直接诱因是 UI：**Gemini 的发送键与 `Stop response` 共用同一个方形按钮位**，
生成中它就是停止键。页面渲染有延迟时，按钮形状本身就是那个正在延迟的东西，
拿它判断「提交了没有」永远判不准。而 playbook §3 原本只写了「等回答完整生成」，
**没写怎么确认已提交**——缺的是一条判据，不是一次手滑。

## 复盘里被写错的两处

**① 第二次不是操作错误。** 提交后零输出那一次没人点它，是引擎侧/会话侧的事，
疑似静默限流。日报结尾「这是操作错误，不是引擎故障」把它一并盖了进去，
等于把一个可能的限流信号从上报里抹掉——与 §10 那句
「把一次已知的数据污染变成一次无人知晓的数据污染」是同一个错误。

**② 记 0 踩在了 §11 的适用范围之外。** §11 那条 0 的前提是
「引擎答了、但答非所问 / 整段是解释性文字」，即**内容不可用**；
这两次是根本没拿到回答。更要命的是**收 0 在数据里不留任何痕迹**：
`query_candidates.py` 只对非空 suggestions 追加行（且 `normalize_input`
的必填校验用 `if not rec.get(key)`，空数组直接被当成缺字段报错，
连喂都喂不进去）。核对当天 `data/query_candidates.jsonl`：只有 ChatGPT 两套各 5 条、
共 10 行，Gemini 一行没有，也没有任何「为什么是 0」的标记。
两周后回看，「Gemini 追问零产出」会被读成引擎特性，
而当天真实原因是我们自己点停了它。

顺带一处不自洽：③ 给了重试、②没给，两者是同一次误点造成的。
差别只来自援引了哪一节，不是原则区分。真正该分的是**中断由谁造成**。

探测记录库当天未受影响：`logs/scan_2026-08-10.json` 记 hits 20 / inboxed 20，
20 条齐全。损失只落在候选池（当天 Gemini 贡献 0 条候选）。

## 改了什么

**1. playbook `prompts/probe_ai_engines_daily.md`**

- §3 操作清单由 6 步扩到 8 步，新增两条硬规则：
  - 确认已提交**只看对话流里自己那条 user 消息**，不看按钮；没出现就等 15 秒重读页面，
    仍没有才重新粘贴提交。
  - **提交之后到生成完毕之间，不点击输入框区域的任何按钮。** 无例外。
  - （§3.2 那条编号未动——README、`config/query_candidates.yaml`、§11 都在引用它。）
- §3 新增小节「中断与卡死怎么处置」，一张表把三件事分开：
  `操作中断`（自己点停）/ `零输出卡死`（提交后长时间零输出）/ `内容不可用`（答非所问），
  各自的处置与记法都不同。
- §10 停机条件新增一条：探测位零输出卡死且刷新后仍零输出，或当天累计 ≥2 次零输出卡死
  → 停机上报。理由：额度用尽会明说，静默限流不会；一个进了生成态却永远不出字的对话，
  行为上与「被截断且重试仍截断」等价。**【推演待校准】** 3 分钟与 2 次两个阈值
  只有 8/10 这一次实测（卡了 4 分钟以上）撑着，攒够样本由真人调。
- §11「收什么」新增：收 0 必须带原因、必须落盘；§9 上报第 8 条要求逐次写明 `zero_reason`，
  新增第 10 条要求把当天所有中断/卡死逐次写清。
- 追问上限 4 次/天明确为**按追问轮次计、不按发送次数计**——
  操作中断后重发同一句措辞是同一轮的补完。

**关于「操作中断可以重问一次」与频率红线**：这不是新开口子。
§10 早已写着「回答明显被截断且重试一次仍截断」，即已承认
「一次没拿到完整回答的提问可以重试一次」这条豁免。我们自己点停与被截断
在效果上是同一件事，适用同一条。**红线原文一字未动**，豁免也只有一次。

**2. `config/query_candidates.yaml`**：新增 `zero_reasons` 三个取值
（`引擎答非所问` / `操作中断` / `零输出卡死`），判定口径指回 playbook §3 那张表。

**3. `scripts/query_candidates.py`**：
- `suggestions: []` 从「报错」改为**合法输入**，但必须带 `zero_reason`，
  取值不在 `zero_reasons` 里直接报错；反过来，给了建议又带 `zero_reason` 也报错（互斥）。
- 零收记录落 **`data/query_candidates_zero.jsonl`**，与候选池**分两个文件**：
  池子里每一行都得有 `query 文本`（去重逻辑按那个键读），
  混一行没有该键的进去会当场炸掉去重。
- 零收与候选走**同一条写闸**：dry-run 一样不写，`--stage` / `--commit` 才落盘。
- 运行报告新增「本次收 0 的追问」一行与逐条清单，并写明
  `引擎答非所问` 是引擎信号、另两种是我们这边的事故，
  **算这条链产出率时后两种要从分母里剔掉**。

实测（scratch 文件，未碰真实数据）：缺 `zero_reason` 报错 ✓；
dry-run 零写入 ✓；`--stage` 下候选进池、零收进零收档、两边互不串 ✓。

## 没做 / 待办

- **8/10 那两条零收不回填**（Shawn 2026-08-10 拍板）。日报没写清哪一次失败对应哪个问题集、
  追问跟在哪条探测问题之后，`asked_after` 是必填字段，凭记忆补等于往出处留档里塞假数据。
  **后果如实记在这里**：零收档的起点是 2026-08-11，8/10 当天 Gemini 的两次 0
  在数据里永久缺席。谁要算这条链的历史产出率，分母从 8/11 起才是干净的。
- `scripts/query_intake_health.py` 尚未读零收档。当前它只在候选池为 0 时报断点
  （池子有 40 行，不误报），但「本周有几次操作中断/卡死」还进不了周度诊断。
  要不要加，等零收档攒出几行真数据再说。

# 2026-08-10：发布链路补全（Shawn 拍板：AEO 内容复用 blog 管线）

## 起因：4 行「已签发」，0 行发布

台账里 4 行 08-06 签发的 AEO 内容，到 08-10 一篇没上线，四天里没有任何
告警、简报或人提过一句。查下来不是「忘了发」，是**发布链路的后半段根本没建**，
外加两处会把台账写成假账的坑。

## 卡点一：站点没有渲染 AEO 内容的代码

原契约把 AEO 内容放 `vivu_web/content/aeo/`、URL 定 `/aeo/<slug>`。
但 `scripts/routes.mjs` 里没有 `/aeo`，`scripts/prerender.mjs` 只跑
`PRERENDER_ROUTES` 加 `/blog/:slug`，App 里也没有对应路由——**那个目录从来
没有任何代码去读它**。J2 的 PR #2 交付了契约、lint 与回写，唯独没交付
「markdown 变成页面」那一步。

危险的不是发不出去。`aeo-ledger-writeback.mjs` 会把发布链接写成
`https://vivu.ai/aeo/<slug>`，而 `netlify.toml` 明确没有 SPA catch-all，
那 URL 直接 404。**台账记着「已发布」，链接是死的**——比压根没发糟。

Shawn 拍板复用站点已经跑通的 blog 管线，而不是再建一套渲染器。改动全部落在
`vivu_web`（分支 `aeo/j2-blog-pipeline`，commit `5cdfe3a`）：

| 文件 | 改动 |
|---|---|
| `src/content/posts.ts` | frontmatter 解析器跳过 YAML 列表项；`category` 变可选；`type` 进 `Post` |
| `src/pages/Blog.tsx`、`BlogPost.tsx` | 有 `category` 才渲染类目 badge |
| `scripts/aeo-lint.mjs` | 改扫 `src/content/posts/`，只校验沾了 AEO 字段的文件；新增 slug 形态与 `draft: true` 判红 |
| `scripts/aeo-ledger-writeback.mjs` | URL 前缀改 `/blog`；slug 取 frontmatter 不取文件名；拒绝 draft 与无 slug 的页 |
| `content/` 目录 | 退役。契约文档改写后落 `docs/aeo-content-contract.md` |

AEO 页与自有博文混住 `src/content/posts/`，靠 frontmatter 的 AEO 字段区分。
lint 的判据刻意宽到「沾一个 AEO 字段就走全套校验」：若只认 `type: aeo`，
漏写 `type` 的 AEO 页会被当成普通博文整条放行，那正是这条 lint 要挡的东西。

**AEO 页不许 `draft: true`。** draft 的页不进 prerender、URL 是 404，而回写只看
`ledger_id` 与 `signed_off`，照样会把台账推成「已发布」——又回到那个最坏形态。
签发日期已经是它的闸门，两套开关并存只会互相打架。

复用管线白拿的三件：进 `/blog` 索引（每篇得到一条站内链接）、进 `sitemap.xml`、
进 `/rss.xml`。零新增路由。

## 卡点二：签发动作的定义漏了一个字段

README 原文与 `j1_notify_*.md` 都只说「把状态改成已签发」。照着做的结果是
四行状态全改了、`签发日期` 全空，而站点 lint 对这种行**两头堵死**：

- frontmatter 填 `signed_off` → 报「台账签发日期是空的」→ build fail
- 不填 → 报「`signed_off` 为空」→ build fail

**签发 = 改状态 + 填签发日期，两件事。** 口径已改：README 首日产出那节、
`j1_runner.py` 的 `notify_file()` 生成文案、`docs/aeo-content-contract.md` 三处同步。

## 卡点三：签发之后没有任何东西盯着

`sla_check.py` 规则四只看「状态=草稿」，`daily_brief` 的 `ledger_pending` 同一口径。
**签发那一刻，这行就从所有雷达上消失了**——这就是它安静躺四天的机制。

- `sla_check.py` 新增**规则五**：状态=已签发 且 发布链接为空。
  签发日期为空的**不等时限立刻报**（那是硬卡点，等 72 小时没有意义）；
  有日期的按 `thresholds.yaml: sla.ledger_signed_hours`（72h，拍的数，待校准）。
- `daily_brief` ②节新增「台账待发布 N 条（已签发未上线）」，`brief.yaml` 加
  `data.ledger_signed_status` 与对应文案，`render.max_lines` 38 → 39。

判据用「发布链接为空」而不是状态字段：发布链接由站点 build 成功后回写，
**有链接是页面真上线过的证据**，比状态可信。

## 新增 `scripts/j1_publish.py`

补签发与发布之间那段真空：读台账「状态=已签发 且 发布链接为空」的行 →
按「面向」匹配 outbox 草稿 → 生成 `src/content/posts/<slug>.md`。

**它不写 Notion，一个字都不写**，也不碰 git。台账推成「已发布」是站点 build 里
`aeo-ledger-writeback.mjs` 的活，且只在页面真的构建成功之后发生；在这里抢先写，
等于用「文件生成了」冒充「页面上线了」。提交、开 PR、合并都是真人动作。

三个字段脚本不猜，缺了就拒绝并说清缺什么：`--segment`（面向哪个 segment 是判断）、
`--anchor-terms`（必须落在 `facts.json` 已确认集合内）、台账的签发日期。
`--description` 不给就从正文首段摘，并在输出里明说「机器摘的，PR 里请真人过一眼」——
它会原样出现在搜索结果里。

正文落盘前剥掉草稿的头部注释与 H1：站点的 `BlogPost.tsx` 自己用 frontmatter 的
title 渲染 `<h1>`，正文里再留一个就是一页两个 H1。

## 实测（本地，未写 Notion）

拿真草稿 `search video by spoken words` 造一页跑完整条链：

- `npm run aeo:lint` 无 token → 通过（1 页 AEO、1 篇博文不校验）
- 带真 token → **复现了预期卡点**：「frontmatter 标了签发日期「2026-08-10」，
  但台账行的签发日期是空的」，台账读到 4 行
- `aeo-ledger-writeback.mjs` dry-run → `would_update … → https://vivu.ai/blog/search-video-by-spoken-words`，
  普通博文静默跳过
- SSR 构建 → posts 清单收录该页、canonical 为 `/blog/<slug>`；渲染 HTML 里
  badge 0 个、`<h1>` 1 个
- `sla_check.py` → 规则五命中 4 行，逐条写明「签发日期为空，站点 lint 会拒绝这一页上线」
- `daily_brief.py` → ②节出现「AEO Content Ledger 待发布 4 条（已签发未上线）」，33 行

## 当天就跑通了：首批三页已生成

Shawn 同日补完三行的「签发日期」（均为 2026-08-07），并把重复选题那行改成
「已下线」。三页由 `j1_publish.py --commit` 生成并进了 `vivu_web` 版本管理
（commit `da739ab`）：

| slug | segment | 证据 |
|---|---|---|
| `search-video-by-spoken-words` | B | SIG-ed099b98, SIG-3dc7579e |
| `how-to-find-an-old-brand-video-we-already-made` | A | SIG-3dc7579e, SIG-ed099b98 |
| `livestream-highlights` | D | SIG-3dc7579e, SIG-46131a5a |

带真台账 token 复跑：`aeo-lint` 通过（3 页 AEO、1 篇博文不校验，台账读到 4 行）；
writeback dry-run 三页全是 `would_update`，URL 为 `/blog/<slug>`；
SSR 渲染三页各 1 个 `<h1>`、0 个类目 badge，`/blog` 索引三页全在。

**description 那条机器摘的警告是有用的**：第一页摘出来只有 44 字符
（「You need a transcript with timestamps on it.」），准确但太薄，而这是最影响
AEO 的字段，已人工重写。另两页摘的是 137 / 111 字符，够用，保留。
这条流程往后照此办理：机器摘 + PR 里真人过一眼。

## 选题去重的拍板

`search video by spoken words` 与 `how to search a video library by what was said in it`
是同一问题的两种问法（08-06 那节「边界」里已标出，留给签发环节判断）。
**Shawn 2026-08-10 拍板：保留前者，后者不发。** 两篇都上等于自己跟自己抢排名。
后者的台账行（`3b4059d9…c650`）同日已改为「已下线」，因此不再进规则五的
待发布清单——**不改的话它会天天出现在那张单子上**。

## 没做 / 待办

- `vivu_web` 的分支 `aeo/j2-blog-pipeline`（两个 commit：管线改造 `5cdfe3a` +
  首批三页 `da739ab`）**尚未 push、未开 PR、未合并**。合并进 main 触发生产构建，
  构建成功后回写才会把这三行推成「已发布」并写上发布链接——
  **在那之前台账停在「已签发」是正确状态，不是故障。**
- 分支是从 `main` 切的，不含手上那个未合并的 `aeo/j2-foundation-signoff`。
- 第一篇真上线之后，Netlify 打开 `AEO_LINT_REQUIRE_LEDGER=true`：
  当前 token 配错时 lint 会静默跳过台账比对而构建照绿，绿色部署不等于比对跑过。
- 台账没有「发布日期」列（回写脚本注释里明说了不写，写进「签发日期」会覆盖真人
  签发的日期）。发布时间目前只留在 git 与 Netlify 里。要不要加一列，等真发过几篇再说。
- `j1_evidence.py` 登记台账时只把 `WL-` 证据写进 `证据编号` relation，`SIG-` 证据
  哪都不写——这 4 行的 `证据编号` 与 `证据链接` 全是空的，**证据编号只存在于
  outbox 草稿顶部的 HTML 注释里**。那个文件删了就无处可查。本次没动，
  因为改法牵涉台账字段，属于 Phase 0 冻结字段的变更。
