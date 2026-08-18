# A1 扫描 playbook · Reddit 周扫

依据：Build Spec · Phase 2 §一.1、§二、§四「playbook 文件规范」与「各 playbook 要点」。
执行者：Claude 会话（Claude in Chrome，**扫描机 deviceId `0bd1b6a8-…`**，认 deviceId 不认显示名 + Notion 连接）。
频率：**每周一次**，周六 22:00（America/Los_Angeles，2026-08-07 起，此前为周一 10:00），与 LinkedIn 周批扫同场次。
本 playbook 的 `playbook` 标识（写日志用）：`scan_reddit_weekly`

---

## ⛔ 红线（逐字保留，任何情况下不得改写、不得放宽）

- **浏览器自动化只读：零私信、零评论、零点赞、零连接请求。**
  （Reddit 侧同义：零 DM、零回帖、零 upvote/downvote、零关注。）
- **频率写死**：LinkedIn 周批扫一次加日小扫一次，Reddit 周一次，探测日一次；**不得加频**。
- **遇验证码或风控提示：立即停止、Telegram 上报、不重试；当周剩余扫描作废。**
- **Phase 3 之前两类帖都只入库不回帖**（spec §一.1 逐字）。

Reddit 的诱惑比 LinkedIn 更大：你会看到一堆「求推荐工具」的帖子，回一句就能拿到线索。
**不回。** 多数技术/创作社区禁自我推广，一次违规够封号，赔掉的是整个 Reddit 传感器。

---

## 0. 第一步自检（**缺一即停，不得带病跑**）

1. **Claude in Chrome 可用**，且连的是**扫描机** `deviceId 0bd1b6a8-ae15-46f0-a60c-3a6071387138`（`list_connected_browsers` 逐台比对 deviceId，**不要认显示名**——「Browser 1 / Browser 2」是按连接顺序排的序号，2026-08-17 实测两台的名字已经对调过一次：正确那台当时叫 Browser 1，错的那台叫 Browser 2。认名字会扫错机器，而扫错机器比停机更糟：它会产出看起来正常的错数据）；打开 `https://www.reddit.com/`
   确认**已登录**。看到登录墙 / 验证码 / `Blocked` 页 → 停。
2. **Notion 连接可用**：读一次水箱库能返回（0 行也算通过）。失败 → 停。
3. **配置可读**：`config/segments.yaml` 与 `config/scan.yaml`。读不到 → 停。

任一不过 → 走 §9 上报并结束。

---

## 1. 目标

出处 spec §一.1 逐字：**按 segment 的 subreddit 清单扫存量提问帖，帖型标注：
纯痛点求解法 / 工具求推荐。**

注意是**存量**提问帖，不是过去 7 天的新帖——这点和 LinkedIn 周批扫相反。
Reddit 的提问帖生命周期长，一年前的「怎么在几小时素材里找一个镜头」今天依然是有效痛点样本。

---

## 2. 读配置（不许抄进本文、不许凭记忆）

| 要什么 | 从哪读 |
|---|---|
| 各 segment 的 subreddit 清单 | `segments.yaml: <segment>.subreddits` |
| 检索词 | `segments.yaml: <segment>.linkedin_keywords`（复用，Reddit 侧无独立词表） |
| 帖型判定 marker | `scan.yaml: reddit_post_type` |
| signal / intent 判定表 | `scan.yaml: signal_intent` |
| 角色 title 判定表 | `segments.yaml: <segment>.titles` |
| 每 segment 每轮上限 | `scan.yaml: caps.per_segment_per_round` |
| 停机条件 | `scan.yaml: halt` |

> ⚠️ `segments.yaml` 的 `subreddits` 是**按主题相关性列的，没核过活跃度与版规**
> （该文件顶部注释里 Shawn 已被明确交代过，也是 Phase 1 §九② 点名的三处疑点之一）。
> 本轮扫描同时承担校准这份清单的任务，见 §9 的上报要求。

---

## 3. 逐步操作清单

对每个 segment 的每个 subreddit：

### ① 确认这个 subreddit 值不值得扫（每轮都要做一次，**不要跳过**）

打开 `https://www.reddit.com/{subreddit}/`（`segments.yaml` 里写的是 `r/xxx` 形式，
直接拼在域名后即可）。记录三件事：

- **是否存在 / 是否私密 / 是否已封**（404、`private`、`banned` → 记下来，跳过）
- **订阅数** 与 **最近一条帖子的时间**（判活跃度）
- **版规**里是否明文禁止自我推广（读 sidebar / rules）——本阶段不回帖所以不受影响，
  但这条决定了 Phase 3 能不能在这里回帖，现在顺手记下

这三件事就是 §9 要上报的 subreddit 校准材料。

### ② 搜存量提问帖

对该 segment 的每个检索词：

```
https://www.reddit.com/{subreddit}/search/?q={URL编码的检索词}&restrict_sr=1&sort=relevance&t=year
```

- `restrict_sr=1` = 只在本 subreddit 内搜
- `sort=relevance&t=year` = 存量口径：近一年里最相关的，不是最新的
- 逐条打开帖子读正文与首层评论。**只读，不投票、不回复。**

---

## 4. 命中判定规则（规则来自 config，本节只说怎么用）

### 是不是命中

命中 = **一条真人发的、在找素材/检索视频这件事上有痛点或选型意图的提问帖**。

排除：
- 供应商软文、工具作者自荐帖、`[Hiring]` / `[For Hire]` 类招工帖
- 已删除 / 已锁 / 作者注销（`[deleted]`）的帖子
- 词面撞上但内容无关的

### 帖型标注（本 playbook 特有）

读 `scan.yaml: reddit_post_type`。判定范围是**标题或首层评论**：
- 命中 `tool_recommendation_markers` 任一（`which tool` / `alternative` / `recommend` / `vs`）
  → **工具求推荐**
- 否则 → **纯痛点求解法**（`default`）

两类**都只入库不回帖**（Phase 3 之前）。

帖型在冻结字段表里**没有对应字段**。落法：写进 `信号原文` 的开头，格式固定：

```
【帖型：工具求推荐】<原文照抄>
```

这是借字段用，不改 schema。周五复盘要按帖型分布做判断时，从这个前缀取。

### signal 还是 intent

读 `scan.yaml: signal_intent`。经验对应（仍以 config 为准，不要写死在脑子里）：
- 工具求推荐帖通常命中 `intent_markers`（`which tool` / `recommend a` / `alternative to`）→ `intent`
- 纯痛点求解法帖通常落 `default` → `signal`

### pain feeler 还是 decision maker

**这是 Reddit 侧最弱的一环，要如实处理。** Reddit 用户多数匿名，没有 title 也没有公司。

- 帖子正文或用户 flair 里明确写了职位/身份（例：`I'm a video editor at a small agency`）
  → 按该 segment 的 `titles` 比对判角色
- **判不出的一律不入箱**，计入 `hits` 不计入 `inboxed`。
  理由：水箱是「名字箱」，一个没有名字、没有公司、判不出角色的匿名 ID
  入了箱也没法触达，只会稀释周定额。
- 预期结果：Reddit 的 `inboxed / hits` 比例会明显低于 LinkedIn。**这是正常的**，
  不要为了拉高比例而放宽判定。这个比例本身就是「Reddit 值不值得作为传感器」的证据。

---

## 5. 字段映射表（水箱 · 字段名逐字取自 Phase 0 核对清单）

| Notion 字段 | 类型 | 本 playbook 写什么 |
|---|---|---|
| `人名` | title | Reddit 用户名（判得出真名就写真名，否则写 `u/xxx`） |
| `公司` | text | 帖子里明说了才写，否则留空。**不从用户名推测** |
| `角色标签` | select | `pain feeler` / `decision maker` / `双角色`，判不出**不入箱** |
| `配对` | relation（单向） | 留空 |
| `segment` | select | `A`–`E`；判不准写 `未分类` |
| `状态` | select | 固定 `inbox` |
| `下一步动作` | text | 留空（Phase 3 前不回帖，没有下一步） |
| `触达时限起算` | date（带时间） | 写入时刻，**必须带时间** |
| `信号类型` | select | `signal` / `intent`，按 §4 |
| `信号原文` | text | `【帖型：X】` + 原文照抄。不改写、不翻译、不概括 |
| `来源链接` | url | 帖子永久链接（`https://www.reddit.com/r/…/comments/…`）。去重键 |
| `入箱日期` | date | 今天 |
| `来源` | select | 固定 `A1 扫描` |
| `引荐来源` | relation | 留空 |

---

## 6. 去重规则

**写入前按 `来源链接` 查水箱，已存在即跳过。**

- 归一化：去 `https://` / `www.` / 尾斜杠 / `?` 后参数。
  Reddit 同一帖有 `old.reddit.com`、`np.reddit.com`、短链 `redd.it` 多种形式——
  **一律换算成 `www.reddit.com/r/…/comments/<id>/` 的规范形式再比对**，只按帖子 ID 认同一性。
- 扫**存量**帖的必然后果：第二周开始，大部分命中都是上周扫过的。
  `skipped_dupe` 高是正常的，不是错误。
- 同一个用户在不同帖子下的痛点 → 仍按人去重（水箱是人的箱），
  已在箱的用户不重复入箱，把新链接补进已有行的 `信号原文` 也**不做**（那是真人的活）。

---

## 7. 每轮上限

**每 segment 每轮最多 10 条**（读 `scan.yaml: caps.per_segment_per_round`）。

用满即停该 segment，不放宽判定凑数。溢出计入 `hits` 并在上报里注明。

---

## 8. 扫描日志

写 `logs/scan_YYYY-MM-DD.json`，每 segment 一条，字段按 spec §四 逐字：

```json
{"date": "YYYY-MM-DD", "playbook": "scan_reddit_weekly",
 "segment": "B", "hits": 9, "inboxed": 2, "skipped_dupe": 5}
```

落盘（与 LinkedIn 周批扫同一天，必须用合并脚本，不要手写覆盖）：

```bash
echo '{"date":"…","playbook":"scan_reddit_weekly","records":[…]}' \
  | python3 scripts/scan_log_append.py
```

没有文件系统权限时把 JSON 贴进上报，由真人合并。

---

## 9. 上报（含 subreddit 校准材料，**本 playbook 的重点产出之一**）

发到绑定的 Telegram group（群 ID 见 `.env` 的 `TELEGRAM_GROUP_ID`）：

1. 逐 segment：`hits / inboxed / skipped_dupe`
2. **subreddit 校准表**（§3① 的记录，逐个列）：

   | subreddit | 存在/私密/已封 | 订阅数 | 最近帖时间 | 版规禁自我推广 | 本轮命中数 |
   |---|---|---|---|---|---|

   这张表是回答「`segments.yaml` 的 subreddit 清单拍没拍错」的直接证据，
   首轮跑完就能删掉明显不该在清单里的（例：`r/livestreamfail` 在 D 段，
   `segments.yaml` 里已自注「社区性质偏娱乐，可能应删」）。
3. 帖型分布：工具求推荐 vs 纯痛点求解法 各多少条
4. 因判不出角色而未入箱的条数（Reddit 侧预期偏高，要有数）

---

## 10. 停机条件与上报（红线）

读 `scan.yaml: halt`。

**触发条件**：验证码 / 风控提示 / 被要求重新登录或二次验证 / 页面结构与本文描述不符。
Reddit 侧还要加一条：**出现 `You've been blocked by network security` 或速率限制页**。

**动作**
1. **立即停止本次扫描，不重试。** 不换 subreddit 继续、不切 `old.reddit.com` 绕开。
2. **向 Telegram group 上报**：触发了哪条、停在哪个 subreddit、已写入多少条。
3. **当周剩余扫描作废**——含本周剩余日小扫与还没跑的 LinkedIn 周批扫。不补跑、不追量。
