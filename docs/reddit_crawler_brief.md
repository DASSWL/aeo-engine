# 启动文档 · Reddit 爬取脚本（交给 coding agent）

你要写一个脚本：**每天定时抓 Reddit 上的存量提问帖，按固定格式落一份 JSON 报告。**
下游有另一套系统读这份报告做判读，它不碰网络，报告是它唯一的输入。

读完这份文档你需要的东西都齐了。**唯一不可协商的是输出契约**——语言、依赖、
项目结构、部署方式随你，但产出的文件必须能通过体检脚本。

---

## 0. 先读这三个文件

| 文件 | 是什么 |
|---|---|
| `~/aeo-engine/docs/reddit_report_spec.md` | **输出契约全文**。字段、失败语义、硬规则都在里面 |
| `~/aeo-engine/docs/examples/reddit_scan_example.json` | 一份合法样例（105 组合 + 2 条帖子），照着长 |
| `~/aeo-engine/scripts/reddit_report_check.py` | 体检脚本。**它就是验收标准**，不是参考意见 |

```bash
python3 ~/aeo-engine/scripts/reddit_report_check.py --report <你的报告>
# 0 = 通过   1 = 有硬伤   2 = 文件不在
```

写完之前先跑它。跑不过就是没做完。

---

## 1. 背景（决定了几条你不能自作主张的地方）

原来这条链是用浏览器自动化跑的，2026-08-15 起 `reddit.com` 被浏览器工具的
服务端域名分类拦掉了，所以改走 Reddit 官方 API。

**不要试图绕过那个拦截**——不要 headless 浏览器伪装、不要拟人化滚动、不要找镜像站。
走官方 API 是正路，也更稳。

另一条历史教训直接变成了契约里最硬的一条：上游曾经连着六天没采到数据没人发现，
因为日志里「扫了但 0 条」和「根本没扫」长得一模一样。所以规范要求
**每个计划中的查询组合都必须在报告里留一行，失败和跳过的也要留**。
这条如果做不到，报告就是有毒的——下游会拿一个没发生的观察当结论。

---

## 2. 抓什么

矩阵 = 5 个 segment × 各自的 subreddit × 各自的检索词，共 **105 个组合**。

**从 `~/aeo-engine/config/segments.yaml` 读，不要抄进代码里**（这份配置会改）：

```python
import yaml
segs = yaml.safe_load(open("/Users/shiyuanniu/aeo-engine/config/segments.yaml"))["segments"]
for name, seg in segs.items():                     # name = "A".."E"
    for sub in seg.get("subreddits") or []:        # "r/podcasting"
        for kw in seg.get("linkedin_keywords") or []:   # "searchable transcript"
            ...
```

爬虫不在同一台机器上就把这个文件同步过去，**但要能察觉它变了**
（比如每轮读一次并把矩阵大小写进 `run.counts.queries_planned`），
不要固化成常量——加一个 subreddit 而爬虫不知道，是最难查的那类错。

搜索口径（**存量**，不是近 7 天新帖）：

```
GET https://oauth.reddit.com/{subreddit}/search
    ?q=<检索词>&restrict_sr=1&sort=relevance&t=year&limit=50
```

一年前的「怎么在几小时素材里找一个镜头」今天依然是有效痛点样本，所以是 `t=year`
按相关性，不是按时间倒序。

---

## 3. Reddit API 接入

以我的知识为准写在这里，**动手前请对一遍官方文档**（`https://www.reddit.com/dev/api`
与 OAuth2 wiki），限速与字段口径它们改过几次：

- **建 app**：reddit.com/prefs/apps → `script` 类型 → 拿 client id / secret。
- **取 token**：`POST https://www.reddit.com/api/v1/access_token`，
  HTTP Basic = (client_id, client_secret)，`grant_type=client_credentials`
  （只读不需要用户态）。token 有效期约 1 小时，到期重取，不要每个请求都换。
- **User-Agent 必须真实且唯一**：`platform:app-id:version (by /u/用户名)`。
  伪装成浏览器 UA 会被限速甚至封 key。
- **限速**：OAuth 客户端约 100 请求/分钟（按 10 分钟窗口平均）。
  读响应头 `X-Ratelimit-Remaining` / `X-Ratelimit-Reset` 自适应，别硬跑。
- **凭据从环境变量读**，不要写死、**不要出现在报告里任何一处**（体检脚本会查这条）。

### 字段映射（Reddit 返回 → 报告字段）

| 报告字段 | Reddit 侧 | 注意 |
|---|---|---|
| `id` | `data.id` | 去掉 `t3_` 前缀 |
| `permalink` | `"https://www.reddit.com" + data.permalink` | 必须是 `https://www.reddit.com/r/…/comments/<id>/…` 且无 `?` 参数；`old.` / `np.` / `redd.it` 一律换算过来 |
| `subreddit` | `data.subreddit_name_prefixed` | `r/xxx` 形式 |
| `title` / `selftext` | 同名 | **原样，不清洗不截断** |
| `author` | `data.author` | 加 `u/` 前缀；注销的是 `[deleted]`，如实写 |
| `author_flair` | `data.author_flair_text` | **判角色的主要线索**，空字符串和字段缺失要分清 |
| `link_flair` | `data.link_flair_text` | |
| `created_utc` | 同名 | 秒级整数 |
| `score` / `upvote_ratio` / `num_comments` | 同名 | |
| `state.deleted` | `author == "[deleted]"` | |
| `state.removed` | `selftext == "[removed]"` 或 `removed_by_category` 非空 | |
| `state.locked` / `archived` / `over_18` / `stickied` | 同名 | **只标记，不要过滤掉这些帖子** |
| `comments[]` | `GET /comments/{id}?depth=1&limit=25&sort=top` | 只要首层（`depth: 0`）；每条给 `id/author/author_flair/body/score/created_utc/depth` |

**性能提示**：先把 105 个查询的结果按 post id 去重，**再**去拉评论——
同一帖会被多个组合搜到（`found_by` 就是记这个），按查询逐条拉评论会白跑好几倍请求。

---

## 4. 红线（不是建议）

- **只读。** 零投票、零评论、零私信、零关注、零加入。这个 app 只应该发 GET。
- **不碰用户主页。** 不抓 `/user/xxx`、不拼用户历史、不跨帖聚合同一个人。
  需要的判断材料只到「这条帖子本身展示了什么」。
- **不预筛、不改写、不跨天去重。** 判断在下游，你只交事实。
  看着不相关的帖子也照落；正文一个字符都不要动。
- **凭据不落报告、不落日志。**

---

## 5. 输出与失败处理

- 落 `~/Library/Application Support/Reddit Browser Crawler/reports/reddit_scan_YYYY-MM-DD.json`
  （crawler 自己的输出目录；America/Los_Angeles 当天；同日重跑用 `_2` `_3` 后缀，**不要覆盖**）。
  下游从 `config/scan.yaml: reddit_reports.dirs` 读这个位置，换目录只改那一处。
- **原子落盘**：先写 `.tmp` 再 `rename`。下游可能在任何时刻读这个目录，
  读到半个文件比读不到更糟——它看起来是完整的。
- 单个组合失败：重试（建议 3 次、指数退避），仍失败就写
  `{"status": "failed", "error": "HTTP 429"}`，**继续跑其余组合**，别整轮放弃。
- **整轮失败也要落文件**：只有 `run` 段、`queries` 全是 `failed` 也要落，
  `run.status: "failed"`。没有文件下游分不出「爬虫挂了」和「今天没开机」。
- `run.status` 三态：全 ok → `ok`；有 failed/skipped → `partial`；整轮没起来 → `failed`。
- **没跑的组合标 `skipped`，不要标 `ok`。** 体检脚本会拦两种自相矛盾：
  「status=ok 但有 failed 组合」，以及「N 个组合标 ok 但整轮耗时不够跑完 N 个」。

---

## 6. 定时

每天跑一次，**建议 03:00–05:00 America/Los_Angeles**（Reddit 低峰，且早于下游任何链）。
每天各写各的文件，**不要自己合并、不要跨天去重**——下游是周六合并当周 7 份做一次判读，
它需要 7 份独立快照。

---

## 7. 验收

做完请自己跑这几条，全过再交：

1. `reddit_report_check.py --report <今天的报告>` → **退出码 0**
2. 造一个「某个组合中途挂掉」的场景 → 报告里那个组合仍有一行且是 `failed`，
   `run.status` 是 `partial`
3. 断网重跑 → 仍然产出文件，`run.status: "failed"`，退出码非 0，日志说得清为什么
4. 同一天跑两次 → 出现 `_2` 文件，第一份没被动过
5. `grep -i -E "client_secret|refresh_token|bearer " <报告>` → 无输出
6. 任取一条 `selftext` 与 Reddit 页面上的原文逐字对比 → 一致（没有被 strip/转义/截断）

第 6 条是最容易悄悄做错、也最难在下游发现的一条：报告里的原文会被逐字抄进
下游的证据字段，改一个字就是伪造一次买家原话。

---

## 8. 问题反馈

契约有歧义或做不到的地方，**先问、不要自己发挥**——尤其是想改字段名、
想省掉 `queries` 里的失败行、想帮下游先筛一遍这三类念头。
规范里每一条硬要求后面都写了它的来历，看一眼就知道能不能动。
