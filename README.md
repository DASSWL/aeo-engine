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
