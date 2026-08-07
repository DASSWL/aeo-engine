# A1 扫描 playbook · LinkedIn 周批扫

依据：Build Spec · Phase 2 §一.1、§二、§四「playbook 文件规范」与「各 playbook 要点」。
执行者：Claude 会话（Claude in Chrome，默认 Browser 2 + Notion 连接）。
频率：**每周一次**，周六 22:00（America/Los_Angeles，2026-08-07 起，此前为周一 10:00）。不得加频。
本 playbook 的 `playbook` 标识（写日志用）：`scan_linkedin_weekly`

---

## ⛔ 红线（逐字保留，任何情况下不得改写、不得放宽）

- **浏览器自动化只读：零私信、零评论、零点赞、零连接请求。**
- **频率写死**：LinkedIn 周批扫一次加日小扫一次，Reddit 周一次，探测日一次；**不得加频**。
- **遇验证码或风控提示：立即停止、Telegram 上报、不重试；当周剩余扫描作废。**
- 所有写入只进**水箱 inbox**。inbox 转 Named 由真人在水箱里操作，你不碰。

你在本次会话里唯一被允许的对外动作是：读页面、写 Notion、发 Telegram 上报。
除此之外任何会在 LinkedIn 上留下痕迹的操作都是违规——包括「不小心点到了赞」。
点错了也要停下上报，不要自行撤销后继续（撤销同样是一次写操作）。

---

## 0. 第一步自检（**缺一即停，不得带病跑**）

出处：spec §四「会话前置自检」。按顺序做完三条，任何一条不通过就走 §10 上报并结束。

1. **Claude in Chrome 可用**：确认浏览器工具可调用，且当前连的是默认 Browser 2。
   打开 `https://www.linkedin.com/feed/` 确认**已登录**（能看到自己的头像/信息流）。
   看到登录页、验证码页、或「继续以…身份」的中间页 → 视为不可用，停。
2. **Notion 连接可用**：读一次水箱库，确认能返回行（0 行也算通过，空库是正常状态）。
   读失败或提示无权限 → 停。
3. **配置可读**：能读到 `config/segments.yaml` 与 `config/scan.yaml`。读不到 → 停。

三条都通过，再往下走。自检失败时**不要**「先扫着看看」——带病跑出来的数据没人敢用。

---

## 1. 目标

按 `segments.yaml` 逐 segment 跑三组搜索，把命中写入水箱（状态 inbox）：

| 组 | 搜什么 | 出处 |
|---|---|---|
| ① 痛点词组 | 抱怨帖 / 讨论帖（内容搜索） | spec §四 |
| ② 招聘搜索 | 在招 video producer / editor 的公司 | spec §四 |
| ③ 职位变动 | 目标 title 的换岗宣告 | spec §四 |

每条命中产出：**原文引用、来源链接、signal / intent 标、pain feeler / decision maker 标、
segment 归属**（spec §一.1 逐字）。

本轮是「周 15 个名字候选」这条定额的主力来源，但**定额是上限不是 KPI**
（`config/thresholds.yaml: weekly_inbox_quota`）。扫不满不是问题，凑数才是。

---

## 2. 读配置（**不许把规则抄进本文，也不许凭记忆写**）

每次执行都重新读一遍，用当时的值：

| 要什么 | 从哪读 |
|---|---|
| 有哪些 segment、各自的 `name` | `config/segments.yaml: segments` |
| ① 组的检索词 | `segments.yaml: <segment>.linkedin_keywords` |
| ② 组的招聘检索词 | `segments.yaml: <segment>.hiring_keywords`（为空时回落 `defaults.hiring_keywords_base`） |
| ③ 组的 title 与宣告措辞 | `segments.yaml: <segment>.titles` × `scan.yaml: job_change.announcement_markers` |
| 角色 title 判定表（pain feeler / decision maker） | `segments.yaml: <segment>.titles` |
| signal / intent 判定表 | `scan.yaml: signal_intent` |
| 每 segment 每轮上限 | `scan.yaml: caps.per_segment_per_round` |
| 停机条件 | `scan.yaml: halt` |

> ⚠️ `scan.yaml` 整个文件**未经 Shawn 审核**，其中 `signal_intent` 与 `job_change`
> 两节是推演的。首轮盯跑时这两节的判定要人工抽查。
> spec 原文写的是「signal / intent 判定表从 segments.yaml 读」，但 segments.yaml 里
> 没有这张表——见 `scan.yaml` 顶部注释的说明。

---

## 3. 逐步操作清单

对 `segments.yaml` 里的**每一个 segment**，依次做 ①②③ 三组。做完一个 segment 再做下一个。

### ① 痛点词组（内容搜索）

对该 segment 的每个 `linkedin_keywords` 词：

```
https://www.linkedin.com/search/results/content/?keywords={URL编码的检索词}&datePosted=%22past-week%22&sortBy=%22date_posted%22
```

- `datePosted=past-week` 是周批扫的口径：只看过去 7 天，和「每周一次」对齐，
  避免每周重复扫到同一批老帖（去重规则会拦，但白扫一遍浪费额度也拖慢）。
- 逐条读帖子正文。**只读，不互动。**
- 判定命中：见 §4。命中就按 §5 的字段映射记下来。

### ② 招聘搜索（公司级信号）

对该 segment 的每个 `hiring_keywords` 词：

```
https://www.linkedin.com/jobs/search/?keywords={URL编码的职位词}&f_TPR=r604800&sortBy=DD
```

- `f_TPR=r604800` = 过去 7 天（604800 秒）。
- 命中记的是**公司**不是人：招聘信号是公司级信号（spec §四）。
- 每条招聘命中要做**两件事**（缺一不可）：
  1. 写水箱：**人名留空**、状态 `inbox`、`下一步动作` 写「待 Apollo 反查」。
  2. 追加一行到 `data/apollo_backfill.csv`，供 `scripts/apollo_poll.py` 反查配对角色。
     列顺序固定（`apollo_poll.py` 按列名读，表头必须有）：
     ```csv
     company,segment,source_url,hiring_keyword,seen_date
     ```
     文件不存在时先写表头行再写数据行。

### ③ 职位变动

LinkedIn 的「换工作」筛选器在 Sales Navigator 里，普通账号没有。这里退而用内容搜索
匹配公开的换岗宣告帖：把该 segment 的 `titles`（两类都要）与
`scan.yaml: job_change.announcement_markers` 组合成检索词：

```
https://www.linkedin.com/search/results/content/?keywords={URL编码的 "宣告措辞" + 空格 + "title"}&datePosted=%22past-week%22
```

> ⚠️【推演待校准】这组的机制是推的，不是 spec 给的。**首轮盯跑时专门看这一组的产出量**：
> 如果基本无产出，说明机制不成立，应该改走 Apollo 侧的 job-change 信号或直接砍掉这一组，
> 而不是每周继续白跑。把观察结果写进当次上报。

---

## 4. 命中判定规则（规则本身全部来自 config，本节只说怎么用）

### 是不是命中

命中 = 这条内容**指向一个具体的人或公司，且透出「找素材/检索视频」这件事上的痛点或选型意图**。

排除（不算命中，不入箱）：
- 供应商自己的营销帖、招聘中介的批量转发、纯新闻转载
- 没有任何人名也没有公司名的内容（连谁都不知道，入箱没有意义）
- 与视频素材检索无关，只是词面撞上（例：`live commerce` 撞到一条讲直播话术的帖）

### signal 还是 intent

读 `scan.yaml: signal_intent`：
1. 先看 `hard_rules` —— 命中即定，不再往下看。
   （招聘命中 → `signal` + `decision maker`，这条有出处。）
2. 再看 `intent_markers` —— 帖子正文命中任一即 `intent`。
3. 都没命中 → 取 `default`（当前是 `signal`）。

### pain feeler 还是 decision maker

拿这个人的 LinkedIn title，去比该 segment 的 `titles.pain_feeler` 与 `titles.decision_maker`：
- 只命中一边 → 取那一边
- 两边都命中，或 title 模糊到两边都像（小公司常见）→ 打 **`双角色`**
- 两边都不命中 → **不要硬塞**。这条不入箱，计入 `hits` 但不计入 `inboxed`，
  并在上报里列出这个 title——title 表本身就是待校准的（Phase 1 §九②），
  漏掉的 title 是校准 `segments.yaml` 的原始材料，比硬塞进一个错角色有价值得多。

---

## 5. 字段映射表（水箱 · 字段名逐字取自 Phase 0 核对清单，一个字都不许改）

| Notion 字段 | 类型 | 本 playbook 写什么 |
|---|---|---|
| `人名` | title | 帖子作者姓名。**招聘命中留空** |
| `公司` | text | 公司名。取不到写空，不猜 |
| `角色标签` | select | `pain feeler` / `decision maker` / `双角色`，按 §4 |
| `配对` | relation（**单向**） | 本 playbook **留空**。配对由 `apollo_poll.py` 反查后写 |
| `segment` | select | `A`/`B`/`C`/`D`/`E`；判不准写 `未分类`，不硬塞 |
| `状态` | select | 固定 `inbox` |
| `下一步动作` | text | 招聘命中写「待 Apollo 反查」；其余留空 |
| `触达时限起算` | date（带时间） | 写入时刻，**必须带时间**（Phase 1 的 24h SLA 靠它算） |
| `信号类型` | select | `signal` / `intent`，按 §4 |
| `信号原文` | text | **原文引用**，照抄不改写、不翻译、不概括。取不到原文的不入箱 |
| `来源链接` | url | 帖子/职位的**永久链接**，去重键。取不到永久链接的不入箱 |
| `入箱日期` | date | 今天 |
| `来源` | select | 固定 `A1 扫描` |
| `引荐来源` | relation | 留空 |

两条继承自 Phase 0 的注意事项：
- `信号原文` spec 标必填，但 Notion 没有原生必填约束（Phase 0 差异②）——**靠你保证**。
  没有原文就不要入箱，宁可少一条。
- `配对` 是单向 relation（Phase 0 差异①），A 指向 B 不会让 B 自动指回 A。本 playbook 不写它。

---

## 6. 去重规则

**写入前按 `来源链接` 查水箱，已存在即跳过**（spec §四 逐字）。

- 比对前先归一化：去掉 `https://` / `www.` / 尾部斜杠 / `?` 之后的追踪参数。
  LinkedIn 同一条帖子在不同入口会带不同参数，不归一化会重复入箱。
- 跳过的计入 `skipped_dupe`，**不计入** `inboxed`。
- 同一轮内也要自查：同一个链接在两个 segment 的检索里都撞到，只入一次。

---

## 7. 每轮上限

**每 segment 每轮最多 10 条**（读 `scan.yaml: caps.per_segment_per_round`，不写死）。

- 上限是防膨胀的闸，不是目标。
- 用满上限就停下这个 segment，**不要**为了凑满去放宽 §4 的判定。
- 超出上限而被丢弃的命中：计入 `hits`，不计入 `inboxed`，并在上报里注明该 segment 溢出。
  溢出量本身是有用信号——说明这个 segment 的填补速度快。

---

## 8. 扫描日志

每轮结束写 `logs/scan_YYYY-MM-DD.json`。这是 **Phase 1「信号命中率」的分母来源**，
漏写等于让那个指标继续是 `null`。

每个 segment 一条记录，字段按 spec §四 逐字：

```json
{"date": "YYYY-MM-DD", "playbook": "scan_linkedin_weekly",
 "segment": "A", "hits": 12, "inboxed": 8, "skipped_dupe": 4}
```

- `hits` = 判定为命中的总数（**含**被上限截断的、被去重跳过的、角色判不出的）
- `inboxed` = 实际写进水箱的条数
- `skipped_dupe` = 因来源链接已存在而跳过的条数

**落盘方式**（同一天会有多个 playbook 写同一个文件，不能各写各的覆盖掉别人）：

```bash
echo '{"date":"…","playbook":"scan_linkedin_weekly","records":[{"segment":"A","hits":12,"inboxed":8,"skipped_dupe":4}]}' \
  | python3 scripts/scan_log_append.py
```

该脚本负责合并同一天的多份记录，并额外维护一个 `by_segment` 汇总键——
Phase 1 的 `metrics.py` 只认这个键，缺了它信号命中率就是 `null`。不要手写这个文件。

> 如果本次会话没有本机文件系统权限（`scan_log_append.py` 跑不了），
> **不要跳过这一步**：把上面那段 JSON 原样贴进本次运行上报里，
> 由真人执行一次合并。日志缺失要被看见，不能默默没有。

---

## 9. 上报（每轮固定发一次，无论有没有命中）

发到绑定的 Telegram group（群 ID 见 `.env` 的 `TELEGRAM_GROUP_ID`）。内容：

1. 本轮日期与 playbook 名
2. 逐 segment：`hits / inboxed / skipped_dupe`，以及是否触到上限
3. 角色判不出而未入箱的 title 清单（校准 `segments.yaml` 用）
4. ③ 组（职位变动）的产出量单独说一句——它在观察期
5. 扫描日志是否已落盘；未落盘则附上待合并的 JSON

---

## 10. 停机条件与上报（红线）

读 `scan.yaml: halt`。命中任一 `triggers` 立刻执行全部 `actions`：

**触发条件**
- 出现验证码（captcha / 人机验证 / security check）
- 出现风控提示（unusual activity / temporarily restricted / 账号受限）
- 被要求重新登录或二次验证
- 页面结构与本 playbook 描述不符，无法确认在读的是不是目标数据

**动作（三条都要做，顺序不能变）**
1. **立即停止本次扫描，不重试。** 不换关键词继续、不等几分钟再试、不换标签页绕开。
2. **向 Telegram group 上报**：贴出触发了哪条、停在哪个 segment 的哪一组、
   已经写进水箱多少条（已写的不回滚，但要说清楚数到哪儿）。
3. **当周剩余扫描作废。** 包括本周还没跑的日小扫与 Reddit 周扫。
   下一周照常开始，不补跑、不追量。

自检失败（§0）走同一套上报，只是原因写「前置自检未通过」。
