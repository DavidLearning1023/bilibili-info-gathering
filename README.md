<h1 align="center">bilibili-info-gathering</h1>

<p align="center">
  <a href="README.md"><b>简体中文</b></a> · <a href="README.en.md">English</a>
</p>

<p align="center">
  <b>从一个议题出发，把 B站上的散落证据收成一张可核对的地图。</b><br>
  <sub>不写「这条视频讲了什么」，只回答「这件事该怎么看、凭什么」。</sub>
</p>

<p align="center">
  <a href="https://github.com/DavidLearning1023/bilibili-info-gathering/actions/workflows/tests.yml"><img alt="tests" src="https://github.com/DavidLearning1023/bilibili-info-gathering/actions/workflows/tests.yml/badge.svg"></a>
  <img alt="version" src="https://img.shields.io/github/v/release/DavidLearning1023/bilibili-info-gathering?label=version&color=6E56CF">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue.svg">
  <img alt="MCP stdio" src="https://img.shields.io/badge/MCP-stdio-6E56CF.svg">
</p>

<p align="center">
  <a href="#试试看">试试看</a> ·
  <a href="#30-秒判断要不要用">适合谁</a> ·
  <a href="#它解决什么问题">为什么需要</a> ·
  <a href="#和别的-b站-mcp-不是一类东西">有何不同</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#已知限制">已知限制</a> ·
  <a href="#参与与反馈">参与</a>
</p>

<p align="center">
  <img src="assets/preview-report.png" alt="真实产出：报头与「一、结论」——三条挂着具体证据（赞数、楼数）的发现，末尾标注唯一硬数字为单一来源、未交叉验证" width="740">
</p>

<p align="center">
  👀 上图是<b>真实产出</b>的开头，不是示意图。<br>
  <a href="examples/20261021-主流二游社区·B站信息深入报告（第二轮）.html">看完整的一份深入报告（92 KB 单文件 HTML，可直接打开）</a>
</p>

<p align="center">
  <sub>Topic-level evidence gathering from Bilibili: dual-axis source grading, conflicts listed side by side, gaps declared, retrieval protocol replayable. An MCP server plus a methodology skill for agents.</sub>
</p>

---

## 试试看

装好之后（[快速开始](#快速开始)），你对 Agent 说的**第一句话**长这样——把引号里的换成你的议题就行：

```
帮我在B站了解「普通人的投资建议到底靠不靠谱」。
```

**你不需要指定"深入"还是"简要"**——**措辞决定模式**：

| 你说 | 它做什么 |
|---|---|
| 「帮我在B站了解 X」 | **深入**：10–20 分钟，产出 md + HTML + PDF |
| 「**简要**了解一下 B站的 X」 | **简要**：3–5 分钟，只出 md |

> 背景里可以贴 BV 号当线索（`帮我在B站了解X。背景：BV1fxni6XEwD`），
> 但**不构成**分析请求——那是议题的线索，不是"帮我总结这条视频"。

深入模式的产物开头长这样（上面那张图）：**3–5 句结论，每条都挂着具体证据**（赞数、楼数，需要时给到 BV 号）；
最后那句「本轮唯一可核验的硬数字…**单一来源，未交叉验证**」——**主动降级自己的结论**，是这套流程的核心纪律。

---

## 30 秒判断要不要用

| 你想做的事 | 它做吗 | 该用什么 |
|---|:---:|---|
| 「B站上大家对 **X** 怎么看」「有没有人做过 **X**」 | ✅ **这就是它** | 本项目，深入或简要 |
| 「这条视频讲了什么，帮我总结」 | ❌ 不做 | [bilibili-video-analysis-summary](https://github.com/DavidLearning1023/bilibili-video-analysis-summary) |
| 「帮我把几个 B站 视频的评论抓下来」 | ⚠️ 能抓，但你不需要方法论 | 现成的取数型 MCP 更轻 |
| 「按天监控某个 UP 主 / 关键词的热度」 | ❌ 不做 | 定时日报类工具 |

**一句话边界**：它服务的是「**要拿结论去争论、或写进给第三方看的材料**」的人，
不服务「三分钟想要个答案」的人。后者用它只会觉得慢。

---

## 它解决什么问题

直接用「搜一下、读几条」的方式在 B站上做调研，会以五种很具体的方式失败：

| 你会撞上的坑 | 本项目的对策 |
|---|---|
| **样本有偏** —— 搜 10 条就以为看全了 | 多词族广扫 + 声明检索范围与偏差（工具返回 `bias_note`） |
| **评分失焦** —— 大 UP 跨领域发言 vs 无名用户贴政府统计原文，都变成「中等」 | **双轴评级**：来源 A–F × 可用度 1–4 |
| **伪共识** —— 10 条搬运互相复述，看起来像「大家都这么说」 | **冗余度标记**：原创 / 冗余 / 冲突 |
| **停不下来** —— 「广度优先」退化成「一直搜下去」 | **离场规则三档**（列表级 / 内容级 / 议题级） |
| **重复劳动** —— 同一个视频被分析三遍 | 视频索引 + 信源库，跨议题复用 |

方法论不是自创的，是从四个成熟体系里拼出来的：REA 快速循证评估（英国 GSR / Cochrane）、
Admiralty / NATO 情报评级、信息觅食理论（Pirolli & Card, 1999）、SIFT / 侧读（Caulfield）。
推导过程在 `references/`，不是一句「参考了业界最佳实践」。

---

## 和别的 B站 MCP 不是一类东西

市面上的 B站 MCP 都做得不错——以 [`@xzxzzx/bilibili-mcp`](https://github.com/xzxzzx/bilibili-mcp)
为代表，它们**都在取数层**：把搜索、评论、字幕变成结构化 JSON。
本项目也做这件事（14 个工具），但真正的差别在后面一层——

| 维度 | 取数型 MCP（如 `@xzxzzx/bilibili-mcp`） | 本项目 |
|---|---|---|
| 回答的问题 | 「把数据拿给我」 | 「**这件事该信谁、还差什么**」 |
| 输出 | 原始 JSON | 结论 + 信源地图 + 冲突 + 缺口声明 |
| 评级 | 无，或单一分数 | **双轴**：来源可信度 × 议题可用度 |
| 冲突 | 混在正文里，读者自己发现 | **单列一节**，两边都摆，不下结论 |
| 不知道的部分 | 通常不说 | **三类缺口**，每一类都能行动 |
| 可复现 | 难 | 检索协议 + 调用账本落盘，数字可复算 |

**「冲突单列」长什么样**——这是取数型 MCP 不会给你的东西。同一份真实产出里的 3-A：

<img src="assets/preview-conflict.png" alt="冲突与未收敛处：A 方说法 / B 方说法 / 我的处理 三列对照，两边都列出证据来源与赞数，右侧只写处理方式不裁决" width="740">

两者的关系是**可以同时装**：14 个工具名与上游**没有重名**。
分工上，MCP 只负责「拿到干净的数据」，判断全部留给方法论 skill——
因为评判标准取决于议题和用途，写死在代码里只会僵化。

> 单条视频精读继续用上游那个；本项目一律用本项目的 MCP。详见
> [INSTALL.md 第六节](INSTALL.md#六与上游-xzxzzxbilibili-mcp-的关系)。

---

## 架构：MCP 做手，skill 做脑

```
        ┌──────────────────────────────┐
        │  SKILL.md  判断层（方法论）   │   双轴评级 / 离场规则 / 冗余度 / 综合
        └──────────────┬───────────────┘
                       │ 调用
        ┌──────────────▼───────────────┐
        │  MCP server  取数层（确定性） │   搜索 / 评论 / 字幕 / 弹幕 / 图片 / 资产库
        └──────────────────────────────┘
```

这个分层有一个可验证的好处：**换模型不掉证据标准**。
判断由强模型做，但每一层的输入输出都是结构化字段，所以结论能被别人拿着账本复核。

---

## 两种用法：深入 / 简要

**用措辞决定，不用回答问卷。** 系统按你的说法自动切换，**不会反问你要哪一种**。

| | **深入**（默认） | **简要** |
|---|---|---|
| **你怎么说** | 「帮我在B站了解 **X**」<br>「B站上关于 **X** 有什么」 | 「帮我**简要**了解一下 B站的 **X**」<br>「**快速**说说 B站上关于 X 的风向」 |
| **你会得到** | 决策层结论 + 完整证据链<br>（信源地图 / 发现 / 冲突 / 缺口 / 偏差声明）<br>**md + HTML + PDF** | 结论 + 关键发现 + 主冲突 + 最大缺口<br>**默认只出 md**（要 HTML/PDF 说一声） |
| **大约** | 10–20 分钟 / 约 40 次工具调用 | 3–5 分钟 / 约 10 次工具调用 |

> **「工具调用」不等于「上游请求」**：一次 `scan_topic` 内部要按关键词逐个打上游，
> 所以 40 次工具调用会对应 300 次左右的上游请求。两个口径都在报告 §五 和账本里分开列，
> 不做换算，也不混用。

两者**不是「质量高低」，是「样本多少」**：

- 简要模式砍的是**通道**（不拉字幕、不抽帧、不跑 ASR）和**流程**（不做反证、不做归簇、不落检索协议）
- 它**不砍证据标准**——评级、交叉验证标记、偏差声明照样给

**样本少，但话要算数。**

> **什么时候用简要**：你只需要一个可信的风向，用来判断「值不值得再花十几分钟做深入」。
> **什么时候必须用深入**：这份产物要拿去跟人争论，或者写进要给第三方看的材料。

---

## 产出：长什么样、放在哪

**报告不进 `.registry/`**（那是机器资产，以点开头、默认隐藏）。所有给人读的产物
落在项目根目录的 **`分析报告库/`** —— 中文名，因为它的读者是**你**，不是代码。

```
分析报告库/
└── 20261022-深入分析-主流二游社区/
    ├── 主流二游社区·B站信息深入报告.md      ← 唯一真源（Agent 手写）
    ├── 主流二游社区·B站信息深入报告.html    ← 脚本生成，单文件、无外部依赖
    └── 主流二游社区·B站信息深入报告.pdf     ← A4 打印
```

| 命名位 | 规则 | 例 |
|---|---|---|
| 日期 | `YYYYMMDD`，同议题多轮各占一个目录，**不覆盖** | `20261022` |
| 模式 | `深入分析` / `简要分析` | `深入分析` |
| 议题 | 照录你的说法（Windows 非法字符替换成 `-`） | `主流二游社区` |
| 文档标题 | 深入 → `·B站信息深入报告`；简要 → `·B站信息简报` | `主流二游社区 ·B站信息深入报告` |

> 路径**由代码算**：`python -m big.cli report-path --topic "…" --mode deep --mkdir`。
> 手拼的三种典型错误（`2026-10-22` 而不是 `20261022`、`深入` 而不是 `深入分析`、
> 议题里带 `/`）都不会报错，只会让报告散落在两三个目录里。
> 怎么装见下方[快速开始](#快速开始)。

### 报告的结构（顺序不可乱）

| 节 | 装什么 | 为什么在这个位置 |
|---|---|---|
| **一、结论** | 3–5 句判断，不是综述 | 先给「所以呢」 |
| **二、发现** | 按主题聚类；每条带 BV + 时间戳 + 交叉验证。态度类议题末尾附**阵营表** | 证据的主体 |
| **三、冲突与<br>未收敛处** | 两边都列，不下结论 | 本节的存在本身就是结论 |
| **四、缺口与建议** | 三类缺口（结构性边界 / 检索未覆盖 / 本次未做）+ 下一步去哪补 | 让「没查到的」可行动 |
| **五、检索范围<br>与偏差声明** | 词族命中、漏斗数字、算法偏差、**单轮开销** | 让结论可被质疑 |
| **六、信源地图** | 深挖对象 / 入选未深挖 / 淘汰清单（每行带 BV 直链） | 当附录用，核对时才翻 |

> **信源地图为什么在最后**：初版放在 §二，读者读完第一屏就撞上一张 140 行的候选表，
> 真正要看的判断被推到第三屏。**核对发生在读完判断之后**，所以它当附录。
> 它也**同时是可追溯清单**——每行都有直链，不再单列一节。

### 评级怎么读（`B2` 是什么意思）

报告顶部有一块**读图口径**，由渲染脚本自动生成（所以不会各篇不一致）。核心是双轴：

| 轴 | 码 | 含义 |
|---|---|---|
| **来源**（信不信得过这个来源） | A / B / C / D / F | 一手权威 / 专业二手 / 一般二手 / 观点型 / 无法判断 |
| **可用度**（对本议题有多少用） | 1 / 2 / 3 / 4 | 直接可用 / 可参考 / 仅背景 / 无关 |

`B2` = 来源 B（专业二手）× 可用度 2（可参考）。两轴**不一定同向**：
大 UP 跨领域发言是 `A4`，无名用户贴出政府统计原文可以是 `F1`。

完整判据见 [`references/source-grading.md`](references/source-grading.md)。

> 另有三个标记：`✅` 多源独立 / `◐` 部分印证 / `⚠️` 单一来源；
> 缺口三类在表里带不同边框。全部含义都在报告顶部那块口径里。

---

## 快速开始

```bash
git clone https://github.com/DavidLearning1023/bilibili-info-gathering.git
cd bilibili-info-gathering
python scripts/install_mcp.py     # 建 venv + 装依赖 + 注册 MCP + 自检，一步到位
```

要求 **Python ≥ 3.10**。装完有两个入口：

| 命令 | 用途 |
|---|---|
| `big` | 命令行：`big scan` / `big video` / `big registry` / `big setup` |
| `big-mcp` | MCP server（stdio） |

**验一下装对了没有**：

```bash
python scripts/check_setup.py
```

它不 mock——会真起一个 stdio server 握手、核对 14 个工具、再跑一次真实联网搜索。
看到这一行就是好了：

```
结果: [OK] 17 项  [WARN] 0 项  [FAIL] 0 项
```

> 完整安装说明见 **[INSTALL.md](INSTALL.md)**：客户端配置写到哪、登录态怎么配、
> 维护者怎么用 junction 直连开发、以及那五个不能删的 `env` 变量。

<details>
<summary><b>MCP 客户端配置（手写时用）</b></summary>

<br>

`scripts/install_mcp.py` 会自动写好。需要手写时，结构如下：

```json
{
  "mcpServers": {
    "bilibili-info-gathering": {
      "type": "stdio",
      "command": "<装好的目录>/.venv/Scripts/python.exe",
      "args": ["-m", "big.mcp_server"],
      "env": {
        "BIG_OUT_DIR": "<装好的目录>/.bigout",
        "BIG_REGISTRY_DIR": "<装好的目录>/.registry",
        "PYTHONPATH": "<装好的目录>/src",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
```

> `env` 那五项**不是可选项**：客户端启动 stdio 子进程时未必带 cwd，
> 而图片目录与资产库路径依赖锚定；编码与缓冲两项则是 Windows 管道下的实测坑。
> 路径示例是 Windows 的 `.venv/Scripts/python.exe`；macOS / Linux 换成 `.venv/bin/python`。
> 详见 [INSTALL.md 第四节](INSTALL.md#四配置详解env-里那五个变量)。

</details>

---

## 14 个工具

工具全部走 MCP，**不做判断**。判断在哪一步做、用什么标准，见 `SKILL.md`。

| 工具 | 用在哪 | 说明 |
|---|---|---|
| `scan_topic` | **阶段 1 入口** | 多关键词搜索 → 去重 → 排序 → 头部候选拉评论，一次调用替代几十次 |
| `search_videos` | 补搜 | 单词族试探 |
| `deep_read` | **阶段 2 主力** | 一次调用聚合四通道：字幕 + 评论 + 弹幕 + 封面，深挖不必逐个试 |
| `get_transcript` | 只取字幕时 | 支持「只读前 20%」与「关键词定位」，省 80–95% token |
| `get_comments` | **观点分布** | 三档 `detail`：`opinion`（只取顶层，省 71%）/ `brief` / `full`；定性、挖评论区也用它，附评论区结构指标 |
| `get_danmaku` | 位置信号 | 密度曲线 / 高重复句 / 披露线索 |
| `get_comment_images` | 读图 | 阈值判读 → 下载 → 联系表（带编号，按号回读原图） |
| `transcribe_audio` | 无字幕时的补路 | 下载音轨 → 本地 ASR 自建转写。**没有后端时返回可执行的安装指引**，不静默失败 |
| `grab_frames` | 字幕错误时的核对 | 段级抽帧（只给时间点，不抽全片）+ 联系表 + 定位自检 |
| `get_metadata` | 兜底 | 广扫用不到（搜索结果自带元数据），只在需 cid 时用 |
| `get_creator_content` | 判断搬运 | 查 UP 主作品列表 / 合集，比对原作者的 mid |
| `scan_untrusted_text` | 评论区加固 | 扫描提示词注入风险，标 `injection_risk`（命中 ≠ 这条在攻击你） |
| `registry_get` | 资产积累 | 读信源库 / 视频索引 / 经验库 / 调用账本 |
| `registry_upsert` | 资产积累 | 写信源库 + 视频索引 + **经验库 `insights`**（跨议题认知，越用越准）+ 调用账本（`kind="cost"`） |

---

## 三阶段流程

<details>
<summary><b>展开：阶段 0 → 1 → 2 → 3 各做什么</b></summary>

<br>

```
阶段 0  议题定义   核心问题 / 关键词族 / 纳入排除 / 预算
阶段 1  广扫       scan_topic 一次调用 → 20–40 条候选 + 头部评论摘要
                   ⛔ 不拉字幕（这是"能广"的唯一原因）
阶段 2  定向深挖   3–9 条：字幕（关键词定位）、弹幕、评论区图片
阶段 3  综合       交叉验证 / 冲突单列 / 结论 / 缺口声明
```

### 两种议题，两套判据

阶段 0 必须先问清意图，这不是礼貌问题——**猜错会让整套流程朝反方向优化**：

| | 态度类（what / why） | 方法类（how） |
|---|---|---|
| 典型问法 | 「大家对 X 怎么看」 | 「X 到底该怎么做」 |
| 点赞率 | **反指标**（火的通常最好消化，不是最有料） | 正指标 |
| 评论区 | **主证据**（常与视频立场相反） | 辅助 |
| 高赞图片 | 多为梗图，只能当情绪证据 | 多为截图实测，可当一手材料 |
| 结论形态 | **阵营表**（谁说什么、各占什么位置） | 方案对比表 |

详细规则见 `SKILL.md` 与 `references/`。

</details>

<details>
<summary><b>展开：它不做什么</b></summary>

<br>

- 不做**跨议题的 UP 主横向对比**（同一批人在不同议题上的表现）——简报的目的是理解这一个议题
- 不做**单条视频的深度拆解**（那是 [bilibili-video-analysis-summary](https://github.com/DavidLearning1023/bilibili-video-analysis-summary) 的事。
  本项目的 HTML/PDF 是**议题简报**的排版形态，不是视频分析报告）
- 不绕过登录、付费、地区限制

</details>

---

## 实测：同一议题，深入 vs 简要

同一天、同一个议题（「社区对 AI 模型娘化与各类二创的热衷」）跑了两种模式，
数字来自调用账本 `registry_get(kind="cost", window="runs")`，可复现：

| | 深入模式 | 简要模式 |
|---|---|---|
| 深挖条数 | 7 条（全部由子代理读） | 3 条（只读评论，不读字幕/图） |
| 工具调用 | 43 次 | 11 次 |
| 上游请求 | 323 次 | 71 次 |
| 输出估算 | 94,916 token | 28,611 token |
| 产物 | 38 KB md / 73 KB HTML / 20 页 PDF | 22 KB md / 46 KB HTML / 12 页 PDF |
| 主动交代的缺口 | 7 类（含「鱼灾」无法量化这类结构性边界） | 5 类 |

**这两轮也暴露了四个真问题**（后来都修了，见 `references/CHANGELOG.md` v1.0.0）：
测试往真实账本里记账（116 行假数据）、单轮开销低报 6.8%、简要轮覆盖深入轮的视频记录、
同一议题两轮 14 条可比 BV 里 10 条评级不一致。

> 换句话说：**工具已经能产出可用的报告，但「数字自洽」需要专门验收**。
> 现在这几条都由测试和 `--check` 兜住了，不再靠人眼。

<details>
<summary><b>展开：一个实测带来的设计变化</b></summary>

<br>

原始设计假设「每条候选要单独调一次元数据接口」，于是 20 条候选 ≈ 60–90 次调用，
预算被元数据吃光。实测发现 `/x/web-interface/wbi/search/type` **返回的每条记录自带
aid / mid / 播放 / 弹幕数 / 评论数 / 收藏 / 标签 / 简介**，且 `page_size` 上限是 50。

| | 原设计 | 现在 |
|---|---|---|
| 元数据 | 每条 1 次调用 | **0 次**（随搜索返回） |
| 20 条候选的定性 | 约 60–90 次调用 | 约 13 次 |
| 单次搜索覆盖 | 10 条（某 MCP 工具的 schema 限制） | 50 条 |

省下来的预算不是攒着，而是**换成了更多的样本和更深的评论**。

</details>

---

## 隐私与合规

- 本工具只访问**公开可见**内容：视频元数据、公开评论、公开弹幕、公开字幕。
  不绕过付费、大会员、地区限制或私密内容——读不到就是读不到。
- 默认**不内置任何凭据**。取字幕需要你自己的登录态，通过环境变量
  `BILI_SESSDATA` 或本地文件提供，不写进仓库。
- 评论里的 **IP 属地、昵称、mid 属个人信息**。默认只做聚合统计；
  产物若要对外分享，请去掉 IP 属地分布这类字段。
- `.registry/` 存的是你自己的使用积累，已在 `.gitignore` 里，不要提交。
- 请求默认限速 400ms（`BIG_RATE_LIMIT_MS`）。调快是你自己的选择，412 是 IP 级风控，
  换凭据没用。

**免责声明**：本项目与哔哩哔哩无任何关联，非官方工具。仅供个人研究与学习使用。
请自行确认你的使用方式符合《哔哩哔哩用户协议》与当地法律；**请勿用于商业用途或
大规模数据抓取**。相关风险分析见 [RISK.md](RISK.md)。

---

## 已知限制

| 限制 | 影响 |
|---|---|
| 搜索结果是算法排序，不是随机抽样 | 样本永远有偏，必须在结论里声明（工具会返回 `bias_note`） |
| 楼中楼每层最多取 3 条 | 深层争论看不到；`reply_intensity` 用真值 `rcount`，不受影响 |
| 弹幕无法识别发送者 | 「p[6]=crc32(uid)」已实测否证，只做内容启发式 |
| 字幕多为 AI 字幕 | **不可当引用**，只用于定位；术语错一个后面全错 |
| 本地 ASR 要自己装 | **可选能力**，不装只是无字幕视频转不了；共享运行时是加速路径不是依赖 |
| 接口随时可能变 | 412 / 字段改名会静默降级，见 `references/troubleshooting.md` |
| 同一视频两次分析的评级**可能不同** | 来源字母（A–F）有规则约束 + `--check` 跨报告提醒；**可用度数字相对议题与证据深度，本来就会变**（深入 D2 vs 简要 C2 不一定是错） |
| 「单轮开销」是**输出侧估算** | 用于横向比较（深入 vs 简要、哪个工具最费），**不是账单金额**：它不含输入侧 token，也没乘单价 |
| 简要模式不读字幕/图 | 结论偏「评论区侧」；涉及「视频说了什么」的判断只能记为缺口，不能补脑 |

---

## 文档索引

| 文档 | 什么时候看 |
|---|---|
| [INSTALL.md](INSTALL.md) | 装不上、想让 Agent 认这个 skill、维护者怎么开发、**本地 ASR 怎么装** |
| [SKILL.md](SKILL.md) | Agent 的判断层规则（三阶段流程、双轴评级、离场规则） |
| [RISK.md](RISK.md) | 风险与合规边界（用之前建议读一遍） |
| [CONTRIBUTING.md](CONTRIBUTING.md) / [English](CONTRIBUTING.en.md) | 想参与贡献（开发环境、测试、commit 规范、PR 自查表） |
| [SECURITY.md](SECURITY.md) / [English](SECURITY.en.md) | 安全边界与漏洞报告方式（**安全问题不要开公开 Issue**） |
| [examples/](examples/) | 一份完整产物的 HTML 渲染样例 |
| `references/mcp-tools.md` | 14 个工具怎么用、参数怎么定、哪些坑 |
| `references/search-protocol.md` | 关键词族怎么设计（阶段 0 决定成败） |
| `references/source-grading.md` | 双轴分级卡、冗余度标记 |
| `references/exit-rules.md` | 什么时候该停手（列表级 / 内容级 / 议题级） |
| `references/opinion-distribution.md` | 评论区观点分布：三轴、四型贴法、偏差声明 |
| `references/context-isolation.md` | 上下文隔离：子代理读原文、只回传摘要的契约与降级路径 |
| `references/subtitle-reliability.md` | 字幕能信到什么程度、ASR 后端怎么找、什么时候该抽帧 |
| `references/briefing-template.md` | 情报简报骨架与输出格式 |
| `references/registry.md` | 信源库 / 视频索引的数据结构与维护 |
| `references/troubleshooting.md` | 取数出问题了先查这里 |
| `references/audience-profiles.md` | 这个 skill 服务谁、不服务谁、企业级场景推演 |
| `references/CHANGELOG.md` | 变更历史（每个坑的来龙去脉都在里面） |

---

## 参与与反馈

这个项目**只有一个人维护**，所以下面几件事对你来说比对我更有价值：

- ⭐ **Star 一下**——不是虚荣心，是让 GitHub 的算法把它推给需要它的人
- 🐛 **[提 Issue](https://github.com/DavidLearning1023/bilibili-info-gathering/issues)**——取数出问题了、评级判据有异议、想要某个议题类型的方法论，都请开 issue
- 💡 **贡献方法论**——`references/` 里的判据（双轴评级、离场规则、观点分布）都可讨论；
  改判据请在 issue 里先讲理由，别直接改
- 🔍 **交叉验证**——如果你拿它跑出了与我不同的结论，**那多半是我错了**，请贴上来

姊妹项目：[bilibili-video-analysis-summary](https://github.com/DavidLearning1023/bilibili-video-analysis-summary)
——单条视频精读，与本项目共用同一套取数层。

**完整的贡献流程**（开发环境、测试命令、commit 规范、PR 自查表）见
[CONTRIBUTING.md](CONTRIBUTING.md)；
**发现安全问题**请不要开公开 Issue ⇒ 见 [SECURITY.md](SECURITY.md)。

---

## License

MIT。见 [LICENSE](LICENSE)。
