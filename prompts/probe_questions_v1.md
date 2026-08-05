# 探测问题清单 v1 —— 【草稿待审】

依据：Build Spec · Phase 2 §一.3「探测问题清单 v1（两套，**首轮真人过目后入 Query 库**）」。

> ## 🚧 状态：草稿，**尚未写入 Query 库**
>
> spec 明文要求真人过目后才入库，所以本文件只是待审材料。
> `prompts/probe_ai_engines_daily.md` 从 **Query 库**读问题，不从本文件读——
> 在 Shawn 审核并入库之前，日探测跑起来会取到 0 条问题并上报「等待真人入库」。
> 这是刻意设计的：绕过审核的问题一旦跑出 7 天数据，就会被当成基线用下去。

审核通过后的入库口径（Phase 0 Query 库字段，逐字）：

| Query 库字段 | 任务式那套 | 评估式那套 |
|---|---|---|
| `query 文本` | 下表的问题原文 | 下表的问题原文 |
| `类型` | `痛点级任务式` | `评估式` |
| `面向角色` | `pain feeler` | `decision maker` |
| `月搜索量` | 留空（未知留空，Phase 0 §4） | 留空 |
| `数据来源` | `探测问题` | `探测问题` |
| `状态` | `候选` | `候选` |
| `关联资产` | 留空 | 留空 |

出处标注：
【出处：…】= spec 或 config 里的原文 ｜ 【推演待校准】= 起草人推的，无出处

---

## 一、任务式（pain feeler）—— 5 条

| # | 问题 | 出处 |
|---|---|---|
| 1 | `how to find a clip in hours of footage` | 【出处：spec §一.3 逐字给出的示例】 |
| 2 | `search video by spoken words` | 【出处：spec §一.3 逐字给出的示例】 |
| 3 | `how to search a video library by what was said in it` | 【推演待校准】由 `segments.yaml: B.linkedin_keywords` 的 `searchable transcript` 展开 |
| 4 | `how to find an old brand video we already made` | 【推演待校准】由 `segments.yaml: A.linkedin_keywords` 的 `where did we save that clip` 展开 |
| 5 | `how to pull highlights from hours of livestream recordings` | 【推演待校准】由 `segments.yaml: D.linkedin_keywords` 的 `livestream highlights` 与 `E.tournament footage` 合并展开 |

起草说明：
- 1、2 逐字用 spec 的示例，一个字没改。
- 3–5 全部由已定稿的 `segments.yaml` 关键词展开，好处是入库后可以回溯到 segment；
  坏处是 `segments.yaml` 那几个词本身也没出处（Phase 1 §九②），
  所以这三条是**在待校准的地基上再推一层**，首轮数据出来后要一起校准。
- 五条刻意都写成「how to …」的自然提问形态，不带品牌名、不带工具类目名——
  任务式那套要测的是「用户描述痛点时，引擎会不会自己引出一个工具品类」。

---

## 二、评估式（decision maker）—— 5 条

| # | 问题 | 出处 |
|---|---|---|
| 1 | `best video search tool` | 【出处：spec §一.3 逐字 + 同时是 §一.2 的首批 keyword】 |
| 2 | `{竞品名} alternative` | 【出处：spec §一.3 的问题形态】**⚠️ 空着，见下方阻塞说明** |
| 3 | `how much does video search software cost` | 【推演待校准】spec §一.3 只写了「pricing 等」，具体问法是推的 |
| 4 | `best AI video asset management software` | 【推演待校准】由 §一.2 首批 keyword `video asset management AI` 转成评估式问法 |
| 5 | `video search tool comparison for marketing teams` | 【推演待校准】面向 A 段（`segments.yaml: A` 品牌侧 B2B 营销）的评估式问法 |

### ⚠️ 第 2 条是空的，我没有填，也不该由我填

spec 写的是「具体竞品 alternative」，需要一个**真实的竞品名**。我拿不到：

- `config/gates.yaml` 里 `competitor_list_converged: false` —— 竞替名单**尚未收敛**；
  同一行的注释明文写着「**禁止脚本自动翻转：收敛与否是判断，不是计算**」。
- Phase 0 / Phase 1 两份实现结果页里没有任何竞品名单。
- 我自己编一个竞品名写进探测问题，等于凭空造一个「买家会拿来比的对手」，
  然后连问 7 天，再把结果当 AEO 排期依据——这是在无出处的地基上盖楼，
  正是 Phase 1 §九② 点名要避免的事。

**需要 Shawn 做的**：给 1–3 个真实竞品名（从已有买家对话里出现过的，
或 Marketing 解构里已记的），我把第 2 条展开成对应的 1–3 条。
展开后评估式这套会超过 5 条 —— `scan.yaml: probe.questions_per_set` 要同步调整，
或者从 3–5 里替掉相应条数，这也请一并拍板。

在此之前，评估式这套**实际可用的只有 4 条**。首轮探测按 4 条跑也可以
（`questions_per_set` 改 4），但要在探测记录里留痕说明为什么少一条。

---

## 三、审核时建议重点看的三件事

1. **1、2 两套的角色分界是否成立**：任务式全给 pain feeler、评估式全给 decision maker，
   这是按 spec 的两套问题定义直接对应的。但 `segments.yaml` 已自注 A/B 段两类角色
   会大量塌缩成同一人——如果塌缩严重，两套问题测的可能是同一批人的两种问法，
   那么「按角色分两套」这个设计本身要重新想。
2. **问法是否太像我们自己的话术**：3–5 条里如果出现只有我们内部才这么说的表述
   （例：把「素材检索」说成某个内部术语），探测出来的就不是真实用户的提问分布。
3. **要不要加品牌词那一档**：Query 库 `类型` 有第三个选项 `品牌词`（Phase 0 §2），
   但 spec 的探测只设计了两套。如果要测「有人直接搜我们品牌名时引擎怎么答」，
   需要单开第三套 —— 这是加需求，不是本 Phase 的活，先记在这里。
