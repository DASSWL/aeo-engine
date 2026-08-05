# A1 扫描 playbook · LinkedIn 日小扫

依据：Build Spec · Phase 2 §一.1、§二、§四「playbook 文件规范」与「各 playbook 要点」。
执行者：Claude 会话（Claude in Chrome，默认 Browser 2 + Notion 连接）。
频率：**每日一次**，09:30（America/Los_Angeles）。不得加频。
本 playbook 的 `playbook` 标识（写日志用）：`scan_linkedin_daily`

---

## ⛔ 红线（逐字保留，任何情况下不得改写、不得放宽）

- **浏览器自动化只读：零私信、零评论、零点赞、零连接请求。**
- **频率写死**：LinkedIn 周批扫一次加日小扫一次，Reddit 周一次，探测日一次；**不得加频**。
- **遇验证码或风控提示：立即停止、Telegram 上报、不重试；当周剩余扫描作废。**
- 所有写入只进**水箱 inbox**。inbox 转 Named 由真人操作。

本 playbook 扫的是**已经主动接触过我们的人**（互动者、访客）。这类人最容易让人手痒
想「顺手回个赞」或「打个招呼」——**不行**。Phase 2 全程零对外动作，回应动作在 Phase 3。

---

## 0. 第一步自检（**缺一即停，不得带病跑**）

出处：spec §四「会话前置自检」。

1. **Claude in Chrome 可用**，且连的是默认 Browser 2；`https://www.linkedin.com/feed/`
   确认**已登录**。看到登录页 / 验证码页 / 中间页 → 停。
2. **Notion 连接可用**：读一次水箱库能返回（0 行也算通过）。失败 → 停。
3. **配置可读**：`config/segments.yaml` 与 `config/scan.yaml`。读不到 → 停。

任一不过 → 走 §9 上报并结束。不要「先扫着看看」。

---

## 1. 目标

出处 spec §一.1 逐字：**自己帖子的互动者加 profile 访客；「we need this」类评论标高优先级写入水箱。**

日小扫的量天然小（多数日子是 0 条），**0 条是正常结果**，不是失败。
不要为了让今天「有产出」而放宽判定或去扫别人的帖子——那是周批扫的活。

---

## 2. 读配置（不许抄进本文、不许凭记忆）

| 要什么 | 从哪读 |
|---|---|
| 角色 title 判定表 | `segments.yaml: <segment>.titles` |
| segment 归属的判断依据（公司画像、关键词） | `segments.yaml: <segment>.apollo` / `linkedin_keywords` |
| signal / intent 判定表（含「we need this」硬规则） | `scan.yaml: signal_intent` |
| 每 segment 每轮上限 | `scan.yaml: caps.per_segment_per_round` |
| 停机条件 | `scan.yaml: halt` |

---

## 3. 逐步操作清单

### ① 自己近 7 天帖子的互动者

```
https://www.linkedin.com/in/me/recent-activity/all/
```

1. 列出**近 7 天**发布的帖子（超过 7 天的不看——它们在之前的日小扫里已经扫过）。
2. 逐帖打开**互动者清单**：
   - 点赞/反应：点开反应数字，展开列表（这是只读的展开，不是点赞）
   - 评论：直接读评论区
3. 逐人取：姓名、title、公司、个人主页链接。
4. **逐人按 title 判角色**（见 §4）。

### ② profile 访客

```
https://www.linkedin.com/analytics/profile-views/
```

- 只看**近 7 天**的访客。
- LinkedIn 会隐藏一部分访客（匿名浏览、非 Premium 账号的名额限制）——
  **看得到几个就是几个**，看不到的不猜、不推断、不从别处补。
- 取得到姓名与 title 的才进入判定；只显示「某公司的一位…」这类匿名条目直接跳过，
  跳过的计入 `hits` 不计入 `inboxed`。

### ③「we need this」类评论（高优先级）

在 ① 读到的评论里识别。出处 `scan.yaml: signal_intent.hard_rules` 第二条：
这类评论 → `信号类型 = intent`，**优先级：高**。

判定：评论表达了「我们需要这个 / 我们正好缺这个 / 这解决了我们的问题」这类**明示需求**。
- 算：`we need this`、`we've been looking for exactly this`、`this is our problem right now`
- 不算：`nice`、`great post`、`congrats`、纯 emoji、泛泛认同（`so true`）

「高优先级」在冻结字段表里**没有对应字段**（水箱 14 个字段里没有优先级字段）。
落法：在 `下一步动作` 里写「高优先级：we need this 类评论」。
这是借字段用，不是加字段——不改 schema 是硬约束。

---

## 4. 命中判定规则（规则来自 config，本节只说怎么用）

### 是不是命中

互动 ≠ 命中。命中要同时满足：
1. 这个人**能判进某个 segment**（按公司画像与 title），且
2. 这个人**不在水箱里**（见 §5 去重）

排除：同事、已有客户、明显的供应商/招聘中介、无 title 无公司的空壳账号。

### signal 还是 intent

读 `scan.yaml: signal_intent`：
1. `hard_rules` 优先——「we need this」类评论 → `intent` + 高优先级
2. 其余：评论/互动文字命中 `intent_markers` 任一 → `intent`
3. 都没有 → `default`（当前 `signal`）。**单纯点赞属于这一档**：
   点赞是弱信号，不是意图。

### pain feeler 还是 decision maker

用 title 比该 segment 的 `titles.pain_feeler` 与 `titles.decision_maker`：
- 命中一边取一边；两边都命中或 title 模糊 → `双角色`
- 两边都不命中 → **不入箱**，计入 `hits`，并把这个 title 写进上报
  （title 表待校准，漏掉的 title 是校准材料）

---

## 5. 字段映射表（水箱 · 字段名逐字取自 Phase 0 核对清单）

| Notion 字段 | 类型 | 本 playbook 写什么 |
|---|---|---|
| `人名` | title | 互动者/访客姓名 |
| `公司` | text | 取不到写空，不猜 |
| `角色标签` | select | `pain feeler` / `decision maker` / `双角色` |
| `配对` | relation（单向） | 留空 |
| `segment` | select | `A`–`E`；判不准写 `未分类` |
| `状态` | select | 固定 `inbox` |
| `下一步动作` | text | 高优先级评论写「高优先级：we need this 类评论」；其余留空 |
| `触达时限起算` | date（带时间） | 写入时刻，**必须带时间** |
| `信号类型` | select | `signal` / `intent`，按 §4 |
| `信号原文` | text | 评论原文照抄；只有点赞没有文字时，写「（无文字互动）点赞了帖子：<帖子标题或首句>」并注明无原文 |
| `来源链接` | url | **优先用互动所在的帖子永久链接**；profile 访客用个人主页链接。去重键 |
| `入箱日期` | date | 今天 |
| `来源` | select | 固定 `A1 扫描` |
| `引荐来源` | relation | 留空 |

> `信号原文` 那条要特别说：纯点赞没有原文。Phase 0 差异② 已说明该字段无原生必填约束。
> 这里的处理是**如实写明「无文字互动」**，不是编一句原话。周五复盘看到这类条目
> 应该知道它的证据强度低。

---

## 6. 去重规则

**写入前按 `来源链接` 查水箱，已存在即跳过。**

日小扫有个周批扫没有的问题：**同一个人会连续多天出现**（同一帖的互动者列表天天都在）。
所以除了链接去重，还要：
- 按**人名 + 公司**再查一次水箱。同一个人已在箱（任何状态）→ 跳过，计入 `skipped_dupe`。
- 同一个人在**不同帖子**下互动 → 仍算重复，不重复入箱。
  （水箱是人的箱，不是互动的箱。）

---

## 7. 每轮上限

**每 segment 每轮最多 10 条**（读 `scan.yaml: caps.per_segment_per_round`）。

日小扫正常情况下远达不到上限。**如果某天触到上限，本身就是异常信号**——
要么是帖子爆了，要么是判定放宽了。在上报里显式说明。

---

## 8. 扫描日志

写 `logs/scan_YYYY-MM-DD.json`，每个 segment 一条记录，字段按 spec §四 逐字：

```json
{"date": "YYYY-MM-DD", "playbook": "scan_linkedin_daily",
 "segment": "A", "hits": 3, "inboxed": 1, "skipped_dupe": 2}
```

落盘（同一天多个 playbook 共写一个文件，必须用合并脚本，不要手写）：

```bash
echo '{"date":"…","playbook":"scan_linkedin_daily","records":[…]}' \
  | python3 scripts/scan_log_append.py
```

**当天 0 命中也要写日志**，写 `hits: 0, inboxed: 0, skipped_dupe: 0`。
「连续 7 天有扫描日志」是调度试验的成功判据之一（spec §四），
0 命中不写就等于漏跑，会把试验判成失败。

没有文件系统权限时，把 JSON 原样贴进上报，由真人合并。

---

## 9. 上报

发到绑定的 Telegram group（群 ID 见 `.env` 的 `TELEGRAM_GROUP_ID`）。

**注意：日小扫多数日子是 0 条。0 条也要发一条极简回执**（一行即可：日期 + 0 命中 + 日志已落盘），
理由是调度试验要判「连续 7 天有记录」——静默无法与漏跑区分。
（这与 Phase 1 daily_sla「无超时不打扰」的规矩不同：那边静默=正常，这边静默=可能挂了。）

有命中时补充：逐 segment 计数、高优先级条目清单、角色判不出的 title 清单。

---

## 10. 停机条件与上报（红线）

读 `scan.yaml: halt`。命中任一即执行：

**触发条件**：验证码 / 风控提示（unusual activity、temporarily restricted、账号受限）/
被要求重新登录或二次验证 / 页面结构与本文描述不符。

**动作**
1. **立即停止本次扫描，不重试。**
2. **向 Telegram group 上报**：触发了哪条、停在哪一步、已写入多少条。
3. **当周剩余扫描作废**——包括本周剩下几天的日小扫、还没跑的 Reddit 周扫与 LinkedIn 周批扫。
   不补跑、不追量。
