# MCP 工具速查

> **14 个工具**，`bilibili-info-gathering` MCP server 提供。取数全部走这里，不要自己拼 HTTP。
> 代码在 `src/big/`，改行为去改代码，不要在文档里绕。

---

## 零、每个工具都有的四件套（批次 B1，D1 扩为三值状态）

**每个**工具的返回顶层都有：

| 字段 | 是什么 | 为什么要有 |
|---|---|---|
| `status` | **`ok` / `degraded` / `failed` 三值** | 二值时「失败」和「降级」共用一个 `ok=false`。实测：`scan_topic` 因关键词封顶返回 ok=false，数据却完整可用——Agent 极易当失败重跑，白烧一轮 |
| `degraded` | **列表**：本次有哪些降级（无字幕 / 多P只读了一部分 / 判读深度不足 / 搜索封顶…） | 降级信号以前散在各工具自己的 `note` 里，**漏看 note = 当成没降级** |
| `ok` | 布尔：`status == "ok"`，保留兼容 | 老调用方不用改 |
| `cost` | `{calls, comment_rows, transcript_chars, est_output_tokens}` | `calls` 是**真打了几次上游**（不是估算）；`est_output_tokens` 是输出侧估算——DSH README 一手口径：**输出比输入贵约 200 倍**，所以输出侧才是真正会爆的预算 |

```json
{"degraded": ["该视频有 45 个分P，本次只读了第 2 部分（2/45）"],
 "status": "degraded",
 "ok": false,
 "cost": {"calls": 5, "comment_rows": 100, "transcript_chars": 0, "est_output_tokens": 1840}}
```

> **早退分支也带外壳**：分P越界、缺参数、没装 ffmpeg 这些 `{"error": ...}` 的返回，
> 现在同样有 `status: "failed"` + `degraded: []` + `cost`（兜底在唯一的序列化出口，不是逐个改）。
> 所以解析逻辑**只需要一套**。
>
> ⚠️ `est_output_tokens` 是**粗估**（口径：CJK 字符 ×0.7 + 其它字符 ×0.25），
> 只用于**比较不同调用的相对成本**，不是精确 token 数。
> `calls` 是精确的（`http.py` 里的线程安全计数器）。
>
> 📒 **账本**：每次调用会追加一行到 `.registry/cost-log.jsonl`。
> `registry_get(kind="cost")` 看按工具汇总（`window="run"` 只看本轮）；
`kind="stats"` 里的 `spend` 是累计摘要，`last_run` 是上一轮。
> 用途是回答「一轮议题到底花了多少」，不是报账。

---

## 一、按阶段选工具

| 阶段 | 工具 | 次数 | 说明 |
|---|---|---|---|
| 任务开始 | `registry_get(kind="stats")` + `registry_get(kind="insights")` | 2 | 先看已有什么、之前学到什么 |
| 广扫 | **`scan_topic`** | **1** | 多词族搜索 + 去重 + 排序 + 头部评论 + 微表判图，一站式 |
| 定性 | （不需要工具） | — | 双轴评级是判断，见 `source-grading.md` |
| 阶段 0 收尾 | `registry_upsert(kind="run-start")` | 1 | 打「本轮开始」标记，否则单轮开销算不出来（批次 F） |
| 深挖·组合 | **`deep_read`** | 1/条 | 字幕+评论+弹幕+封面**一次调用**聚合（省几轮往返）。**在子代理里调**，见 `context-isolation.md` |
| 深挖·观点分布 | `get_comments(detail="opinion")` | **每条 1 次** | 三轴分诊。**阶段 2 的默认动作** |
| 深挖字幕 | `get_transcript` | 1–2/条 | **必须带 `query` 或 `head_ratio`** |
| 深挖弹幕 | `get_danmaku` | 0–1/条 | 只在需要"哪段炸了"/查广告时 |
| 读评论图 | `get_comment_images` | 0–1/条 | `picture_verdict != skip` 时；广扫已给微表，这里是要读数字 |
| 字幕不可靠·补转写 | `transcribe_audio` | 0–1/条 | **没有字幕**时；无 ASR 后端 → 返回安装指引 |
| 字幕不可靠·核对画面 | `grab_frames` | 0–1/条 | 错误**影响结论**时抽那几秒；要读表格数字用 1080 |
| 引原话 | `get_comments(detail="full")` | 0–1/条 | 只在要引用具体某条时 |
| UP 主 | `get_creator_content` | 0–3/个 | 议题收敛到 2–3 个核心 UP 后跟"他后续发了什么" |
| 收尾 | `registry_upsert(kind="commit")` | 1 | **写侧闸门**：把暂存区一次合并进正式库 |
| 收尾·开销 | `registry_get(kind="cost", window="run")` | 1 | 取本轮的 `md_line`，原样贴进报告 §五「单轮开销」 |
| 查注入 | `scan_untrusted_text` | 0–1 | 扫**不是从本项目取来的**外部文本（别的工具、用户粘贴） |

一次典型任务的调用数：**2 + 1 + 3×4 + 1 ≈ 16 次**。

---

## 二、`scan_topic` 的参数怎么定

```
scan_topic(keywords=[...], per_query=30, max_candidates=40,
           comment_budget=12, comment_n=15, image_policy="hot", image_top=6,
           exclude_bvids=[...],
           families={...},            # 显式词族（强烈建议传）
           pages_per_keyword=1, min_play=None, published_after=None,
           offset=0,                  # 批次 D2：翻页
           exclude_topic=None)        # 批次 D3：增量模式，自动排除已登记 BV
```

| 参数 | 默认 | 怎么调 |
|---|---|---|
| `keywords` | 必填 | 4–6 个，覆盖五类词族（见 `search-protocol.md`）。少于 4 个覆盖不足，多于 6 个边际收益骤降 |
| `per_query` | 30 | ≤50（上游硬上限）。30 条通常已进长尾；**议题窄降到 20，议题宽到 40–50** |
| `max_candidates` | 40 | 去重后保留多少。40 够看清"有哪些流派"；只做快速判断时 20 |
| `comment_budget` | 12 | **这是真正花钱的参数**。每多 1 条 = 1 次评论调用。要分辨细分立场时加到 15–18 |
| `comment_n` | 15 | 每条候选读几条热评。**8 条看不到排在第 9–15 位的高赞反方**；一次调用内取够，成本几乎为零 |
| `image_policy` | `"hot"` | 热评有图就拼 160px 微表（判类型）；`"off"` 最快；`"all"` 不论有没有都试 |
| `image_top` | 6 | 每条候选最多几张微表。6 张 ≈ 504×382，约为整版联系表的 1/12 像素 |
| **`families`** | `None` | **强烈建议传**：`{"上位词": [...], "产品名": [...], "黑话": [...]}`。排序键改成「**强命中**（标题/简介字面出现）的族数 → 不同族数 → 命中词数 → 字段权重 → 播放量」。**不传时 `kw_families` 返回 `null`**——代码无法凭空知道哪些词属于同一族。强命中族数是为压掉"靠标签蹭进来"的候选（实测一条纯 XP 赏析视频命中 6 族 16 词排到第 1） |
| `offset` | 0 | **翻页**。`coverage.next_offset` 给下一页起点；`coverage.rest_preview` 是未展示候选的一行摘要（成本≈0），先扫一眼再决定翻不翻。起因：实测候选池 835/619 而只返回 50/45，约 1,359 条永远看不到 |
| `exclude_topic` | `None` | **增量模式**：传议题名，自动排除 `videos.json` 里该议题已登记的 BV。第二轮必用 |
| `pages_per_keyword` | 1 | 每个词翻几页。**长尾在第 2 页**（实测每词 page_size=30 时各命中 26–30 条，恰好卡在上限） |
| `min_play` | `None` | 排除播放量太低的 |
| `published_after` | `None` | `"YYYY-MM-DD"`，排除更早的内容（算法偏好新内容，"老"≠"好"） |

**返回**：`candidates`（全部候选 + 元数据 + `kw_families` / `kw_hits` / `hit_by_family`）、
`digested_bvids`（花了评论预算的）、`digest[].images`（微表路径与格号）、
`coverage`（每组词命中数 + `bias_note` + `filter_note`）。

⚠️ `coverage.per_keyword[].capped=true` 表示该词命中数封顶 1000，**不代表只有 1000 条**。
⚠️ `coverage.filter_note` 里会告诉你「候选池多大、只返回了多少」——
   **`unique_candidates=144 但 returned=30` 意味着还有 114 条没进候选表**，
   需要时调大 `max_candidates` 或用 `search_videos` 补检。
⚠️ 微表**读不出数字**，只能判类型；要引用数字必须按 `grid[].file` 回读原图。

### 词族（`families`）为什么必须显式给

`kw_families` 这个名字以前**名不副实**：代码按**关键词**计数，
但文档和排序都把它当"跨词族命中 = 在议题中心"用。
实测（2026-10-21）：6 组词里有 3 组以同一主词 "deepseek" 为核心
⇒ Top 候选普遍命中 5–6 组 ⇒ **排序被冗余关键词放大**。

现在：
* 传 `families` → `kw_families` = **命中的不同族数**（真的），`kw_hits` = 命中的关键词数
* 不传 → `kw_families` = **`null`**（算不出来就如实说），排序退回「命中关键词数 → 播放量」

> 哪些词属于同一族，**只有 Agent 知道**（Agent 知道"原神 玩家"和"崩铁 玩家"不是一族）。
> 代码不该替它猜。

---

## 三、`deep_read`：一次调用聚合多通道

```
deep_read(bvid, channels=["transcript","comments","danmaku","cover"],
          transcript_query=None, transcript_head_ratio=0.2,
          transcript_max_chars=6000, comment_n=60, comment_detail="opinion",
          frames_at=None, page=1, quality=720)
```

返回 `{bvid, title, channels: {...}, channels_requested, channels_returned, degraded, ok, cost}`。
每条通道的载荷在 `channels.<name>` 里，与单通道工具**同构**（不会"两套结构"）。

* `channels` 可选 `transcript` / `comments` / `danmaku` / `cover` / `frames`
* `frames` **只在给了 `frames_at` 时**才做（需要 ffmpeg）
* 封面通道是**最便宜的一路**（一个 URL + 一次图读）

---

## 四、几个容易用错的地方

### `get_transcript` 不给 `query`/`head_ratio` = 烧钱

一条 40 分钟视频的字幕约 8,000–15,000 字。直接全量返回，一个大议题深挖 3 条就把
上下文吃满，后面全是"读了但没用上"。

| 用法 | 适用 |
|---|---|
| `query="收益率 复利 定投"` | **首选**。只返回命中句 ±2 句，省 80–95% |
| `head_ratio=0.2` | 离场判断：前 20% 没出现核心词就退出 |
| 都不给 | 只在视频很短（<8 分钟）或必须通读时 |

`data_source="ai_subtitle"` → **不可引用**，只用于定位。

### 多 P 视频：四个取内容的工具都有 `page`

`get_transcript` / `get_danmaku` / `transcribe_audio` / `grab_frames` 都收 `page`（默认 1）。
`get_metadata` 返回 `n_pages` 与完整 `pages` 清单。
**`page` 越界会明确报错，不会静默回落到 P1**（回落 = 以为读完了整个视频）。

### `get_comments` 的 `n` 与 `picture_verdict`

`n < 100` 时 `picture_verdict` 返回 `null` 并附 `picture_verdict_note`。
这不是 bug：阈值 15%/5% 是在前 100 条顶层的深度下标定的，样本浅了不可比。
**要判读图片就必须 `n≥100`**（或直接用 `get_comment_images`，它内部会拉够 100）。

### `get_comments` 的三档 `detail`（别选错）

| 档 | 内容 | token(估)/100 顶层 | 什么时候用 |
|---|---|---|---|
| `brief`（默认） | 顶层 + 正文截断 90 字 | ~4,280 | 广扫阶段定性 |
| **`opinion`** | 顶层 + **完整正文** + 最小字段 | **~7,722** | **观点分布（阶段 2 默认）** |
| `full` | 全部原始字段 + 楼中楼 | ~25,300 | 引用原话、读楼中楼争论 |

`opinion` 只取顶层——三轴全部可由顶层算（争议度用的 `rcount` 只存在于顶层），
比 `full` 省 71%。

> ⛔ **别用 `brief` 做观点分布**：实测顶层 24% 的正文超过 90 字，截断丢掉 35% 的正文量。
> "反对并给出理由"被截成"反对"，正好毁掉认同度与争议度的区分。

### 写侧闸门：`registry_upsert` 默认**只进暂存区**

`registry_upsert(kind="source"|"video"|"insight"|"topic"|"protocol")` → 落 `.registry/staging/`，
**正式库在你调用 `kind="commit"` 之前不会被改动**。
收尾时 `kind="commit"` 一次合并；`kind="discard"` 丢弃。

> 这是 DSH 插件「能力最小化」范式的等价实现：
> 写侧**始终可用**（不打断"边挖边记"），但**碰不到正式库**。

### `injection_risk` 命中 ≠ 这条在攻击 Agent

`sanitize="flag"`（默认开）给命中的评论追加 `injection_risk`。
它是**纯关键词匹配**，只能当"值得再看一眼"的提示，**不能当证据**。
两个已实测的误报场景：
* 洛谷体长评里「轻视与**忽视**嘲笑的方法」与相隔几百字的「正如**之前**」→ 已用**邻近匹配**修掉
* 洞察里**引用**攻击原句（「当你看到这条评论时…」）→ 已用**引号豁免**修掉

### `transcribe_audio` 的后端怎么找（**先复用，别重装**）

优先级：① `BIG_ASR_CMD` → ② **共享 ASR 运行时** → ③ **本机的 `local-audio-transcription` skill** → ④ 当前解释器的 faster_whisper。

> ⚠️ **装运行时务必锁 `av<19`。** PyAV 19 移除了 `av.open()` 的 `metadata_errors` 参数，
> 而 faster-whisper 1.2.1 的 `audio.py` 仍在传它
> → `TypeError: open() got an unexpected keyword argument 'metadata_errors'`
> → 整条转写链路全废。**而且是运行时才炸**：装包、import、模型加载全都正常。

### `transcribe_audio` 返回 `no_backend` 不是失败，是**要你记缺口**

四种后端都没有时，它返回 `status="no_backend"` + 安装指引。

> ⚠️ **看到这个状态，必须把「该视频无可用字幕」写进结论的缺口声明。**
> 静默跳过 = 结论里凭空少了一条视频，而用户以为"这视频没讲"。

### `grab_frames` 的四条限制

1. **只给时间点**（`at_seconds=[...]`），不给全片
2. **要读表格/榜单数字必须 1080**（360p 只能认出几个大字）
3. 看返回的 `seek_check`：多帧**完全相同** = 定位失败，那批图不能用
4. **1080 的实际成本是"下载整条视频流"**——2026-10-21 实测：一条 15 分钟视频
   1080p 流 **667 MB / 431 秒**（本机网络约 1.5 MB/s）；720p 是 141 MB / 17 秒；
   360p 是 49 MB / 6 秒。**同一 `quality` 下载过就复用**（`note` 会写"复用已下载视频"），
   所以第二次抽同一视频只要 2 秒。
   ⚠️ 因此：**先 360/720 定位，确认"这一屏确实有数字要读"再切 1080**，
   不要一上来就 1080。等 7 分钟不是卡死，别重试（重试只会重新下载）。

> 💡 2026-10-21 修掉一个真 bug：`_fetch_range` 的 `except` 曾写成
> `urllib.error.IncompleteRead`（该属性不存在，IncompleteRead 在 `http.client`）。
> 它在**匹配异常**阶段就抛 AttributeError，导致「CDN 半路 EOF 续传」这条兜底从未生效，
> 1080p 大文件必失败。现已修正并加了两条回归测试。

### `get_danmaku` 的输出不能当"多数人意见"

它返回的是密度、重复句、披露线索——**位置信号**，不是观点分布。

### `get_creator_content` 的端点可用性（2026-10-21 实测）

| section | 端点 | 可用性 |
|---|---|---|
| `overview` | `/x/space/wbi/acc/info` | ✅ **必须 WBI 签名**，不带是 -799 |
| `videos` | `/x/space/wbi/arc/search` | ✅ **必须 WBI 签名** |
| `collections` / `series` | `/x/polymer/**web-space**/seasons_series_list` | ✅（`polymer/space/...` 是 404） |
| `dynamics` | `/x/polymer/web-dynamic/v1/feed/space` | ✅ |

### `registry_upsert` 必须写 `grade_basis`

只写 `grade="A"` 不写依据 = 半年后看到"A"想不起为什么。字段名写错会被**直接拒绝**
（防止脏字段永久留存），报错里会列出允许的字段。

---

## 五、与上游 `@xzxzzx/bilibili-mcp` 的关系

两者**并存、不冲突**，但用途不同：

| | 本 MCP | 上游 bilibili-mcp |
|---|---|---|
| 定位 | 议题级广扫 + 资产积累 | 单条视频精读 |
| 优势 | 一次调用拿 40 条候选；弹幕；评论图；信源库；**统一降级/成本信号**；**写侧闸门** | ASR 转写、AI 摘要 |
| 搜索上限 | 50/次 | 10/次（schema 限制，非上游限制） |

**精读场景（单条 BV）继续用上游那个**；本 skill 一律用本 MCP。
上游的 `get_bilibili_creator_content` 在本机实测 **overview/videos 都返回 ACCESS_DENIED**
（它的端点不带 WBI 签名）——本项目的 `get_creator_content` 走 WBI，**实测可用**。

