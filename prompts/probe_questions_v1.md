# 探测问题清单 v1 —— ✅ 2026-08-04 Shawn 审核通过

依据：Build Spec · Phase 2 §一.3「探测问题清单 v1（两套，**首轮真人过目后入 Query 库**）」。

> ## 本文件的定位：审核记录，不是运行来源
>
> **机器可读的唯一来源是 `config/scan.yaml` 的 `probe.questions`。**
> 两处内容若不一致，以 `scan.yaml` 为准——同一份问题存两份必然漂移。
> 本文件留的是**审了什么、为什么这么定**，供日后校准时回看。
>
> 运行时 `prompts/probe_ai_engines_daily.md` 从 **Query 库**读问题（spec §四 明文），
> 既不读本文件也不读 `scan.yaml`。灌库由 `scripts/probe_questions_sync.py` 完成。

入库口径（Phase 0 Query 库字段，逐字）：

| Query 库字段 | 任务式那套 | 评估式那套 |
|---|---|---|
| `query 文本` | 下表问题原文 | 下表问题原文 |
| `类型` | `痛点级任务式` | `评估式` |
| `面向角色` | `pain feeler` | `decision maker` |
| `月搜索量` | 留空（未知留空，Phase 0 §4） | 留空 |
| `数据来源` | `探测问题` | `探测问题` |
| `状态` | `候选` | `候选` |
| `关联资产` | 留空 | 留空 |

出处标注：【出处：…】= spec 或 config 原文 ｜【推演待校准】= 起草人推的，无出处

---

## 一、任务式（pain feeler）—— 5 条

| # | 问题 | 出处 |
|---|---|---|
| 1 | `how to find a clip in hours of footage` | 【出处：spec §一.3 逐字给出的示例】 |
| 2 | `search video by spoken words` | 【出处：spec §一.3 逐字给出的示例】 |
| 3 | `how to search a video library by what was said in it` | 【推演待校准】由 `segments.yaml: B.linkedin_keywords` 的 `searchable transcript` 展开 |
| 4 | `how to find an old brand video we already made` | 【推演待校准】由 `segments.yaml: A.linkedin_keywords` 的 `where did we save that clip` 展开 |
| 5 | `how to pull highlights from hours of livestream recordings` | 【推演待校准】由 `segments.yaml: D.livestream highlights` 与 `E.tournament footage` 合并展开 |

五条刻意都写成「how to …」的自然提问形态，不带品牌名、不带工具类目名——
任务式那套要测的是「用户描述痛点时，引擎会不会自己引出一个工具品类」。

---

## 二、评估式（decision maker）—— 5 条

| # | 问题 | 出处 |
|---|---|---|
| 1 | `best video search tool` | 【出处：spec §一.3 逐字 + 同时是 §一.2 的首批 keyword】 |
| 2 | `twelve labs alternative` | 【出处：spec §一.3「具体竞品 alternative」形态 + 竞品名由 Shawn 2026-08-04 给出】 |
| 3 | `chatcut alternative` | 【同上】 |
| 4 | `how much does video search software cost` | 【推演待校准】spec §一.3 只写了「pricing 等」，具体问法是推的 |
| 5 | `best AI video asset management software` | 【推演待校准】由 §一.2 首批 keyword `video asset management AI` 转成评估式问法 |

全部小写是刻意的：探测问题模拟真人在引擎里打字的样子，多数人不会打大写。
引用判定不受影响（`scan.yaml: probe.brand_match` 大小写不敏感）。

---

## 三、2026-08-04 审核裁决（三条，逐条留档）

### ① 10 条照单通过
两套各 5 条，符合 spec §四「两套问题各 5 条起步」。无逐条改词。

### ② TwelveLabs 用官方两词写法 `twelve labs`
起草时按口头给的 `twelvelabs` 写成一个词，审核裁决改为 **`twelve labs alternative`**。
理由：官方写法是两个词，AI 引擎分词不同可能给出不同回答，探测要贴近真实用户输入。

> 注意：`scan.yaml: probe.known_competitors` 里**两种写法都留着**
> （`TwelveLabs` 与 `Twelve Labs`）。那张表是用来**匹配引擎回答里出现的名字**的，
> 引擎怎么拼我们控制不了，两种都得认。这与探测问题用哪种写法是两回事，不矛盾。

### ③ 砍掉 `video search tool comparison for marketing teams`
原草稿评估式第 5 条，已删除。理由：三条【推演待校准】里它出处最弱，
且与第 1 条 `best video search tool` 检索意图高度重叠（都是「给我一个工具名单」），
留着等于用一条额度测同一件事。

想换回来的话：得从现评估式第 4 或 5 条里替掉一条，
或把 `scan.yaml: probe.questions_per_set` 从 5 改成 6——
后者会偏离 spec 的「各 5 条起步」，且因为该值两套共用，任务式那套也得凑到 6 条。

---

## 四、这两个竞品名**不构成**「竞替名单收敛」

`config/gates.yaml` 的 `competitor_list_converged` **仍然是 `false`**，本次未改动，
同一行注释明文写着「禁止脚本自动翻转：收敛与否是判断，不是计算」。

两个竞品名进入探测问题与观察名单，意思只是「这两个名字值得每天问一次引擎」，
**不等于**竞替名单已收敛，**更不等于**邻域闸门可以开。
闸门开不开是你在攒够 win/loss 场次后的判断，与本文件无关。

---

## 五、留给下一轮校准的三件事（首轮数据出来后回看）

1. **两套的角色分界是否成立**：任务式全给 pain feeler、评估式全给 decision maker，
   是按 spec 的两套问题定义直接对应的。但 `segments.yaml` 已自注 A/B 段两类角色
   会大量塌缩成同一人——若塌缩严重，两套问题测的可能是同一批人的两种问法，
   「按角色分两套」这个设计本身要重新想。
2. **问法是否太像我们自己的话术**：3–5 条里若出现只有内部才这么说的表述，
   探测出来的就不是真实用户的提问分布。看首轮回答的答非所问率能大致判断。
3. **要不要加品牌词那一档**：Query 库 `类型` 有第三个选项 `品牌词`（Phase 0 §2），
   但 spec 的探测只设计了两套。若要测「有人直接搜我们品牌名时引擎怎么答」，
   需单开第三套——这是加需求，不是本 Phase 的活，先记在这里。
