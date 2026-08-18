# Reddit 爬取报告 · 接口规范 v1

**出处**：2026-08-17 Shawn 拍板——「我用我其他的工具每天定时去爬 reddit 的内容，
把爬到的报告存下来，你只负责分析报告」。

**背景**：2026-08-15 起 `reddit.com` 被 Claude in Chrome 的域名分类拦截
（服务端 `url_hash_check` 判 category1/2 → `navigate_blocked_domain`），
浏览器路线走不通了。08-08 那轮周批扫 Reddit 出了 5 hits / 入箱 2 条，
**是那一周水箱仅有的两行**——这条传感器不能丢，换条路进来。

---

## 〇、分工边界（先说这个，其余全从它推出来）

| | 你的爬虫 | 分析侧（本仓库） |
|---|---|---|
| 干什么 | **采集事实**：帖子原样落盘 | **做判断**：命中/帖型/角色/segment |
| 不干什么 | **不筛、不判、不改写、不去重** | 不碰网络 |

这条线划在这里不是为了省事。`scan_reddit_weekly` playbook §4 的判定要读
`scan.yaml: reddit_post_type` / `signal_intent` 和 `segments.yaml: titles`——
配置随时会改，判断跟着改；而**事实不会因为配置改了就变**。
爬虫里一旦嵌进判断，配置一改就得重爬，且历史报告全部作废。

推论（这三条是硬要求，不是口味）：

1. **原文照抄。** 不摘要、不翻译、不截断、不清洗 emoji/markdown/换行。
   水箱的 `信号原文` 字段规定「原文照抄，不改写、不翻译、不概括」，
   你截断一次，这条证据就永远补不回来了。
2. **不预筛。** 看着不相关的帖子也照落。相关不相关是判断，判断在我这边，
   而且判错了要能从报告里翻回来。
3. **不跨轮去重。** 每轮报告都是当轮的完整快照。去重按 `来源链接` 对**水箱**做，
   在我这边——你那边去过一次重，我就永远看不到「这条上周就在、这周还在」。

---

## 一、交付位置与命名

```
/Users/shiyuanniu/aeo-engine/inbox/reddit/reddit_scan_YYYY-MM-DD.json    # 机器读，必须
/Users/shiyuanniu/aeo-engine/inbox/reddit/reddit_scan_YYYY-MM-DD.md      # 人读，可选
```

**这个目录是硬约定**，已建好、已随 `data/ logs/ outbox/` 同例进 .gitignore。
分析侧只认它，不去别处找。爬虫跑在别的机器/容器里就自己 `mkdir -p`，
或者把这里 symlink 到你的输出目录——两种都行，但**最终路径必须是上面这一条**。

落好之后自检一次（这个脚本是两边共用的合同，不是我单方面的验收）：

```bash
python3 ~/aeo-engine/scripts/reddit_report_check.py            # 取 inbox 里最新一份
python3 ~/aeo-engine/scripts/reddit_report_check.py --report <路径>
# 退出码 0=可用 1=有硬伤不进分析 2=报告不在
```

可直接照抄的合法样例：`docs/examples/reddit_scan_example.json`（105 组合 + 2 条帖子，体检通过）。

- 日期用 **America/Los_Angeles** 的当天，与仓库其余所有链一致。
- 同一天跑第二次：`_2`、`_3` 后缀，**不要覆盖**。覆盖会把上一次的证据抹掉。
- **原子落盘**：先写 `.tmp` 再 `rename`。我可能在任何时刻读这个目录，
  读到半个文件比读不到更糟——它看起来是完整的。
- 编码 UTF-8、无 BOM。超过 20MB 可以交 `.json.gz`（同名加后缀即可）。
- 报告**只增不删**，别做保留期清理。它是证据链，不是缓存。
- `inbox/` 已按 `data/ logs/ outbox/` 同例不入 git（体积 + 含第三方原文）。

---

## 二、文件结构

三段：`run`（这轮干了什么）、`queries`（覆盖矩阵）、`posts`（抓到的帖子）。

```json
{
  "schema_version": 1,
  "run": {
    "started_at": "2026-08-18T03:00:12-07:00",
    "finished_at": "2026-08-18T03:14:55-07:00",
    "status": "ok",
    "tool": "shawn-reddit-crawler/0.3.1",
    "auth": "reddit-api-oauth",
    "scope": {"sort": "relevance", "time_filter": "year", "restrict_sr": true},
    "counts": {"queries_planned": 105, "queries_ok": 103, "queries_failed": 2,
               "posts": 412, "posts_unique": 388},
    "errors": [
      {"where": "r/edtech × course video library", "error": "HTTP 429", "attempts": 3}
    ]
  },
  "queries": [
    {"segment": "B", "subreddit": "r/podcasting", "keyword": "searchable transcript",
     "url": "https://oauth.reddit.com/r/podcasting/search?q=…&restrict_sr=1&sort=relevance&t=year",
     "status": "ok", "returned": 7, "post_ids": ["1uepq6o", "…"]}
  ],
  "posts": [
    {
      "id": "1uepq6o",
      "permalink": "https://www.reddit.com/r/podcasting/comments/1uepq6o/how_to_make_transcripts_more_useful_more/",
      "subreddit": "r/podcasting",
      "found_by": [{"segment": "B", "keyword": "searchable transcript"}],
      "title": "How to make transcripts more useful …",
      "selftext": "<正文原样，一个字符都不动>",
      "author": "u/xxxx",
      "author_flair": "Podcast editor",
      "link_flair": "Question",
      "created_utc": 1782345678,
      "score": 34, "upvote_ratio": 0.93, "num_comments": 12,
      "state": {"deleted": false, "removed": false, "locked": false,
                "archived": false, "over_18": false, "stickied": false},
      "comments": [
        {"id": "kx12ab", "author": "u/yyyy", "author_flair": "",
         "body": "<评论原样>", "score": 8, "created_utc": 1782349000, "depth": 0}
      ],
      "comments_truncated": false
    }
  ]
}
```

---

## 三、`queries` 段：这一节比 posts 段更重要

**「这个组合没扫」和「扫了但 0 条」必须在文件里分得开。**

这是本仓库反复踩的那个坑：08-15/08-16 的日小扫日志写着 `hits: 0`，
读起来像「今天没人互动」，实际是**浏览器掉线根本没扫**。两件事在日志里长得一模一样，
于是断料六天没人发现。所以：

- 计划里的**每一个** (segment, subreddit, keyword) 组合都要有一行，**包括失败的和跳过的**。
- `status` 取值：`ok`（跑了，`returned` 可以是 0）、`failed`（试了没成，带 `error`）、
  `skipped`（没试，带 `reason`，例如 sub 被封/私密）。
- `returned: 0` 且 `status: ok` = 真的没有结果，这是有效信息，我会当结论用。
- **绝不允许**：跑挂了的组合从 queries 里消失。缺行 = 我会把它当成「扫了 0 条」，
  然后基于一个没发生的观察下结论。

`run.status` 三态：`ok`（全部组合都 ok）、`partial`（有 failed/skipped）、
`failed`（整轮没起来）。**整轮失败也要落文件**——只有 run 段、queries 全是 failed 也要落。
没有文件我分不出「爬虫挂了」和「你今天没开机」。

---

## 四、`posts` 段字段

### 必须有（缺一条我就判不了，缺了请写 `null` 并在 `run.errors` 说明）

| 字段 | 用途 |
|---|---|
| `id` / `permalink` | **去重键**。permalink 请规范成 `https://www.reddit.com/r/…/comments/<id>/`（去掉 `old.` / `np.` / `redd.it` 短链 / `?` 后参数 / 尾斜杠统一）——水箱按这个形式比对 |
| `subreddit` | 落 segment 用 |
| `found_by[]` | 哪个 segment 的哪个词搜出来的。**同一帖被多组命中就多条**，别只留第一条：同一个 sub 挂在两个 segment 下（r/editors 在 A 和 E、r/Twitch 在 D 和 E），丢了这个我判不出该归哪段 |
| `title` / `selftext` | 判命中、判帖型、抄进 `信号原文` |
| `author` / `author_flair` | **判角色的主要依据**。Reddit 多数匿名，正文或 flair 里自报身份（`I'm a video editor at…`）是唯一线索。flair 空字符串和字段缺失是两回事，请如实区分 |
| `created_utc` | 存量帖的年龄 |
| `state.*` | `deleted` / `removed` / `locked` / `archived` / `over_18` / `stickied`。playbook §4 明文排除已删/已锁/作者注销的帖子——**你标出来，我来排除**，不要替我删掉 |
| `comments[]` | 帖型判定的范围是「标题**或首层评论**」，signal/intent 也常落在评论里。**首层至少取 10 条**（按 score 降序即可），够不够写在 `comments_truncated` |

### 有就给（没有不阻塞）

`score`、`upvote_ratio`、`num_comments`、`link_flair`、`edited`、`crosspost_parent`。

### 明确不要

- **不要爬用户主页、不要拼用户历史、不要跨帖聚合同一个人。**
  我需要的判断材料只到「这条帖子本身展示了什么」。水箱是名字箱，
  但那是靠帖子里自报的身份进的箱，不是靠把一个匿名 ID 的全网足迹拼出来。
- 不要把 token / client_secret / cookie 写进报告任何一处。

---

## 五、抓取口径

| 项 | 值 | 出处 |
|---|---|---|
| 矩阵 | 每 segment 的 `subreddits` × 该 segment 的 `linkedin_keywords` | `config/segments.yaml`（Reddit 侧无独立词表，复用） |
| 规模 | 5 段 / 17 个唯一 sub / 105 个组合 | A 4×5、B 5×5、C 4×5、D 4×5、E 4×5 |
| 排序 | `sort=relevance&t=year`，`restrict_sr=1` | playbook §3：**扫存量提问帖，不是近 7 天新帖**。一年前的「怎么在几小时素材里找一个镜头」今天依然是有效痛点样本 |
| 只读 | 零 DM、零回帖、零 upvote/downvote、零关注 | playbook 红线，逐字保留，日频也不放宽 |

两个建议从矩阵里删的（08-08 实地校准结论，`segments.yaml` 还没改）：
**r/DTC**（661 订阅，是空房间不是社区）、**r/livestreamfail**（495 万观众看主播翻车，
不是做直播电商的商家，订阅数大反而是噪音源）。删不删你定，删了就在报告里
按 `skipped` + `reason` 留一行，别让它从矩阵里静默消失。

---

## 六、我收到报告之后做什么

1. 判命中（真人发的、找素材/检索视频有痛点或选型意图的提问帖；排除软文、`[Hiring]`、已删已锁）
2. 判帖型（`scan.yaml: reddit_post_type`）→ 写成 `【帖型：工具求推荐】` 前缀
3. 判 signal / intent（`scan.yaml: signal_intent`）
4. 判角色（`segments.yaml: titles`）——**判不出的一律不入箱**，计 `hits` 不计 `inboxed`
5. 按 `来源链接` 对水箱去重 → 写 14 个字段（playbook §5 映射表）
6. 落 `logs/scan_YYYY-MM-DD.json`（`playbook: scan_reddit_weekly`，六字段）

第 4 步的比例会明显低于 LinkedIn，**这是正常的**，我不会为了拉高它放宽判定。

---

## 七、日频爬取，周频入箱（2026-08-17 Shawn 已定）

爬虫**每天**跑、每天落一份报告；**入箱仍是每周一次**，配额一个字不动
（`scan.yaml: caps.per_segment_per_round = 10`、`weekly_inbox_quota = 15`）。

周六合并当周 7 份报告做一次判读入箱。日频的价值是**抓得更早、删帖前抓得到**，
不是入箱更多——水箱是名字箱，撑爆定额只会稀释周定额里真正能触达的行。

对爬虫的含义：**每天各写各的文件，不要自己合并、不要跨天去重。**
合并是分析侧周六的活，你把 7 天的快照原样留着就行。

## 八、给爬虫作者的检查清单

- [ ] 每个计划中的组合都在 `queries` 里有一行，失败/跳过的也在
- [ ] `status: ok` + `returned: 0` 与 `status: failed` 严格区分
- [ ] 整轮失败也落文件
- [ ] `selftext` / `comments[].body` 一个字符都没动
- [ ] `permalink` 已规范化
- [ ] `found_by` 是数组，多组命中不丢
- [ ] 已删/已锁/NSFW 只标记不删除
- [ ] 首层评论 ≥10 条，不够时 `comments_truncated` 如实
- [ ] `.tmp` → `rename` 原子落盘
- [ ] 报告里没有任何凭据

---

## 九、版本

`schema_version: 1`。要加字段直接加（我按名取，多余字段忽略）；
**改语义或删字段要提前说**——报告是我唯一的输入，改了它等于改了我的眼睛。
