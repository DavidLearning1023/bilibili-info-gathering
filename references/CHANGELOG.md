# 变更记录（CHANGELOG）

> 每次改动追加一条，最新在最上面。
>
> **关于文中引用的过程文档**（`MCP-优化方案-*.md` / `MCP-优化开发任务书-*.md` /
> `MCP-优化批次*-交付-*.md` / `议题分析方法论评估-*.md`）：
> 2026-10-22 起它们已归入 **`_devdocs/archive/`**（开发者内部目录，**不入库**）。
> 本文件保留当时的引用原文，**不做路径改写**——CHANGELOG 是历史快照，
> 改成今天的路径会让"当时引用了什么"失真。

---

## 2026-10-23 · 未发布 · 批次 K：main 分支保护

**类型**：仓库配置 + 文档（**不改任何取数与判断逻辑**）

**触发**：v1.0.1 发布后 GitHub 在仓库页提示
「Your main branch isn't protected. Protect this branch from force pushing or deletion,
or require status checks before merging.」
——而这次发 v1.0.1 时**确实动过 force push**（模板结构不合官方 schema，amend 后强制推的）。
提示是对的，防护确实缺。

### K1 · 保护规则（`main`）

| 开关 | 值 | 作用 |
|---|---|---|
| `allow_force_pushes` | `false` | **禁止强推**——已发布的历史改不动 |
| `allow_deletions` | `false` | **禁止删分支** |
| `enforce_admins` | `true` | **对管理员同样生效**（默认是管理员绕过一切限制） |
| `required_status_checks` | 未设 | 取舍见 K2 |
| `required_pull_request_reviews` | 未设 | 不强制走 PR |

### K2 · 为什么没设 required status checks（**实测结论，不是猜的**）

在一次性测试分支上实测过三种情况：

- 设上 `required_status_checks`（context = `test (3.10)` / `test (3.13)`——
  **matrix 的每条腿都是独立 context**，填 `test` 会永远卡住）
  ⇒ **普通推送被拒**：`GH006 ... 2 of 2 required status checks are expected`。
  本项目既有的工作方式是「直接推 main」，开了这条就推不动了
- 不设 ⇒ 普通推送放行、真正的非快进强推被拒（`Cannot force-push to this branch`）、
  删分支被拒（`Cannot delete this branch`），三条都**真推**验证过

⇒ 选「保工作方式、弃 CI 闸门」：**直接推 main 照旧，强推与删分支一律拒绝**。
CI 每次 push 仍然跑、徽章红绿照旧，只是不再由 GitHub 强制卡合并。

### K3 · 文档口径同步

- **SECURITY.md（中英）**防护表加一行「分支保护」
- **SECURITY.md（中英）**「已知未覆盖」加一条：**分支保护没有卡 CI**
  （如实写，不写成「都覆盖了」）

**未做**：不强制走 PR（单人维护项目，PR 流程是负担）、不设 required status checks、
不锁分支（locked branch 连 hotfix 都推不进去）、不改 v1.0.1 的 tag 与 Release。

---

## 2026-10-23 · v1.0.1 · 批次 J：Dependabot 安全更新 + Issue 模板

**类型**：仓库配置 / 文档（**不改任何取数与判断逻辑**）

**触发**：v1.0.0 发布后，SECURITY.md 里如实写着「Dependabot 安全更新未开」——
**既然写出来了，就把它开掉**，而不是留着一条自己承认的缺口。

### J1 · 开启 Dependabot（三层开关，缺一层都不生效）

只开 `dependabot_security_updates` 是不够的，实际要三个一起：

| 端点 | 作用 | 状态 |
|---|---|:---:|
| `vulnerability-alerts` | Dependabot 告警的**前置开关** | ✅ 已开（返回 204） |
| `dependabot_security_updates` | 已知漏洞时自动开安全更新 PR | ✅ 已开 |
| `automated-security-fixes` | 自动合并安全修复 PR | ✅ 已开 |

### J2 · `.github/dependabot.yml`

仓库设置里的开关只管**依赖树里**的东西；显式配置文件才能把可选依赖与 CI 也纳进来：

- **pip**（`/`）：`pyproject.toml` 的 `dependencies` 与 `optional-dependencies` 都会被扫
  ⇒ `mcp`（运行时）、`Pillow` / `markdown` / `playwright` / `pytest`（可选组）全覆盖
  - 分组：`dev-and-docs` 把四个可选依赖合成一个 PR，**`mcp` 单独出来**——
    它是唯一影响面大的那个，不该混在大包里
  - 频率：每周一 09:00（Asia/Hong_Kong），最多 5 个 PR 同时开着
- **github-actions**（`/`）：CI 用的 `actions/checkout` / `actions/setup-python` 也会自动升级

### J3 · Issue 模板（`.github/ISSUE_TEMPLATE/`）

把 CONTRIBUTING.md 那张「该往哪儿提」的分诊表**变成可执行的形式**：

- `bug_report.yml` —— 必填**问题类别**（取数 / 渲染 / 命令 / MCP 工具 / 安装 / 资产库）、
  **版本或 commit**、**复现步骤**；可选「实际输出」与「环境」
- `methodology.yml` —— 专给判据异议与「想要某类议题的方法论」，
  必填**讨论类型**、**涉及哪份判据**与**具体情境**三项，
  并明确提示「没有证据的判据改动很难讨论」
- `feature_request.yml` —— 必填**要解决的问题**与**使用场景**，
  并提醒「工具数变了要同步所有文档，有 lint 会直接判失败」
- `config.yml` —— 空白 issue 允许（很多人只想问一句），但给三个 **contact_links**：
  **安全漏洞（请勿用 Issue 报告）** / 贡献流程 / 文档索引

三个模板的字段标签都是**中英双语**（`问题类别 / Category`）——仓库本身中文优先，
但 Issue 表单没有镜像文件的机制，双语标签是唯一能让两边都读得动的做法。

### J4 · 标签

新建 `methodology`（判据讨论专用）、`dependencies`、`ci`；
`bug` / `enhancement` 是 GitHub 默认标签，把描述改成双语。

### J5 · 文档口径同步

- **SECURITY.md**：防护表里 Dependabot 一行 ❌ → ✅；「已知未覆盖」里删掉那条，
  换成**更准确的一条**：Dependabot 只能扫依赖树里的东西，
  **本地 ASR 那套独立环境（`~/.local/asr`）不在依赖树里，扫不到**
- **CONTRIBUTING.md**：分诊表补一句「开 Issue 时 GitHub 会给三个模板」

### J6 · 版本号

`pyproject.toml` 与 `big/__init__.py` 同步 **1.0.0 → 1.0.1**（patch：
本版只有文档与仓库配置，**没有一行取数或判断逻辑的改动**）。

### J7 · 发布前自查抓到：三个模板的表单结构不合官方 schema

写完模板后，用 **GitHub 官方的 issue-forms JSON Schema**
（`json.schemastore.org/github-issue-forms.json`，draft-07）跑了一遍 ajv 校验——
**三个模板全挂，而且是同一类错误**：

| 错在哪 | 官方要求 | 实际写成了 |
|---|---|---|
| `markdown` 的正文 | `attributes.value` | item 层的 `value`（与 `attributes` 平级） |
| `checkboxes` 的选项 | `attributes.options` | item 层的 `options` |

官方 schema 对这两处的规定是**必须嵌在 `attributes` 下**，且各分支
`additionalProperties: false`。后果不是「少显示一段文字」——
**GitHub 根本不认这个表单**，模板页要么不显示、要么直接报错。

**这类错误肉眼查不出来**：结构看着完全合理，YAML 也能正常解析，
自写的结构检查还给了绿灯。**唯一可信的口径是官方 schema。**

### J8 · 补两道回归锁 + 修掉一处「假装有测试」

- **新增** `tests/test_units.py::test_issue_templates_match_github_form_schema`
  —— 零依赖（不引 PyYAML），按缩进解析这几个固定结构，锁死 J7 那两条规则。
  **反向验证过**：把模板改回错的写法，这条测试确实会失败
  （不会失败的测试等于没有测试）
- **顺带修掉**：`tests/test_units.py` 里 `test_run_ids_do_not_collide_within_one_second`
  **写了两遍**——后一份把前一份覆盖掉，pytest 只跑一份，另一份是**死代码**
  （看着像有测试，实际从未被独立执行）。两份逐字节相同，删掉重复的那份。
- **基线随之从 112 → 113 passed / 3 skipped**，CONTRIBUTING.md（中英）同步

**未做**：不发版（本批随 **v1.0.1** 一起发布）、不写 CODE_OF_CONDUCT.md
（行为准则写在 CONTRIBUTING.md 里并指向 GitHub 社区准则）、不开 Discussions、
不写 PR 模板（PR 自查表已在 CONTRIBUTING.md 里）。

---

## 2026-10-23 · v1.0.1 · 批次 I：动态版本徽章 + CONTRIBUTING / SECURITY

**类型**：文档 / 仓库配置（**不改任何取数与判断逻辑**）

**触发**：v1.0.0 发布后做的一轮收尾——把「在维护」「可参与」这两件事从**文字承诺**变成
**仓库里真实存在的东西**。

### I1 · 版本徽章改成动态的

- 旧：静态 `img.shields.io/badge/version-v1.0.0-6E56CF.svg`——**每次升版都要记得手改**，
  忘了就是一个谎报版本号的徽章
- 新：`img.shields.io/github/v/release/DavidLearning1023/bilibili-info-gathering?label=version&color=6E56CF`
  ⇒ **永久跟随最新 Release**，且**保住了原来的紫色配色**（`color=6E56CF`）
- 代价：fork 场景下会显示 "no release"（因为 fork 没有 Release）。这是**唯一**的取舍，
  换来的是主线永远不需要再手改版本号

### I2 · 补 CONTRIBUTING.md / CONTRIBUTING.en.md

按行业规范写，但内容**全部按这个项目的真实情况**填，不写通用模板：

- **分诊表**「该往哪儿提」：改判据要先讨论 / 修 bug 可直接提 / 改文档可直接提 / 安全问题走
  另一条路——**因为判据是 Agent 的判断依据，改一个阈值可能让另一个失效**
- 开发环境、依赖组、离线与联网两套测试命令（含**真实基线**：112 passed / 3 skipped；
  `check_setup.py` 预期输出行）
- **文档 lint 表**：工具数一致 / 错工具名 / 版本号一致——这三条是本项目的特色，写清楚了
- commit 规范（Conventional Commits + 中文描述）、CHANGELOG 约定、
  **「每句『我做了什么』都要能机械回查」**（附本项目自己栽过的两个实例）
- 体积口径统一 KiB、**不提交的东西**清单（凭据 / `.registry/` / `分析报告库/` …）
- PR 自查表、锚点别凭记忆写（给出 `github-slugger` 的本地校验法）、行为准则、**不接受的贡献**
- 明确写了**不要求 CLA / DCO**（个人维护项目，流程越简单越好）

### I3 · 补 SECURITY.md / SECURITY.en.md

同样按行业规范，但**攻击面写的是这个项目的真实攻击面**，不是通用模板：

- 报告渠道：**已开启 GitHub 私有漏洞报告**（`Security → Report a vulnerability`），
  并明确写「**不要用公开 Issue 报漏洞**」+ 给备用路径（只说类别，细节私信）
  + 给了**具体的信息清单**与**响应时限下限**（7 天首响 / 30 天修复或缓解）
- **已启用的防护**逐条列出：secret scanning ✅、push protection ✅、私有漏洞报告 ✅、
  Dependabot 安全更新 ❌
- 五类真实安全问题：**凭据泄露**（含「`分析报告库/` 产物未做匿名化」这条容易漏的）、
  **提示词注入**（含「`injection_risk` 只是关键词匹配、不能当证据」）、**路径逃逸**
  （议题名里的 `/`）、**依赖供应链**（运行时只有 `mcp`；ASR 在依赖树外）、**使用边界**
- **已知未覆盖**诚实列出：Dependabot 未开 / 无第三方审计 / 只覆盖本仓库 /
  B站官方 AI 小助理不会被 `injection_risk` 标出
- 非安全问题分流回 CONTRIBUTING

### I4 · 仓库配置与 README 接入

- **私有漏洞报告已开启**（`private-vulnerability-reporting`）——不开启的话，
  SECURITY.md 里写的报告渠道就是**不能用的**，那份文件等于没写
- README.md / README.en.md 的**文档索引**各加两行（CONTRIBUTING / SECURITY，含中英双语入口）
- README.md 的「参与与反馈」与 README.en.md 的「Contributing」末尾各加一句指向
  CONTRIBUTING 与 SECURITY；「隐私与合规」一节指向 RISK.md（未动）
- 顶部语言切换条的样式与两个 README **完全一致**（当前语言加粗）

**未做**：本批本身不发版（随 **v1.0.1** 一起发布，见批次 J）；不开 Dependabot 安全更新、
不写 Issue 模板（这两项在**批次 J** 里做了）。

---

## 2026-10-23 · v1.0.0 · 正式版：首次打 tag 发布

**类型**：正式版发布（**首个带 tag 的版本**）

**背景**：仓库上线 GitHub 后做了一轮对外材料重构，代码侧已稳定
（CI 双版本绿、离线测试 111 passed / 3 skipped），因此把版本从 `0.9.1` 提到
**`1.0.0`**，作为**首个正式发布版**。

**关于版本号的一处说明（免得读者去找一个不存在的版本）**：
此前 `pyproject.version` 停在 `0.9.1`，但**仓库从未打过 tag、也没有任何 Release**
⇒ **`v0.9.1` 从未真正发版**。下面两个批次（批次 G、批次 H）都是在这段"未发版状态"
里完成的，它们随本次 `v1.0.0` 一并首次发布，因此这两个条目的版本号**已一并改为
`v1.0.0`**。更早的 v0.9.0 / v0.8.0 / v0.5.0 / v0.1 同样只存在于本文件的历史记录里。

**本版包含**：

- **批次 G**（2026-10-22）：两轮实跑验收暴露的四个"数字看着对但是错"的坑
  ——测试污染真实账本、单轮开销低报 6.8%、简要轮覆盖深入轮的视频记录、跨轮评级不一致
- **批次 H**（2026-10-23）：对外材料重构 —— 首图换成"结论"、新增 `README.en.md`、章节重排

**本版同步的版本口径**（此前有 3 处对不上）：

| 位置 | 改前 | 改后 |
|---|---|---|
| `pyproject.toml` | `0.9.1` | **`1.0.0`** |
| `src/big/__init__.py` 的 `__version__` | **`0.2.0`**（早没人同步过） | **`1.0.0`** |
| README / README.en.md 的 version 徽章 | `v0.9.1` | **`v1.0.0`** |
| README 里指向 CHANGELOG 的版本引用 | `v0.9.1` | **`v1.0.0`** |
| INSTALL.md 故障速查「v0.5.0 起已能正确解析」 | `v0.5.0`（从未发版，读者无从获取） | **`v1.0.0`**（读者真正拿得到的版本） |

`__version__` 此前**定义了但全仓无人引用**，所以它烂在 `0.2.0` 也没人发现——
和 `pyproject.version` 差了 7 个小版本。

**本版新增的一条 lint**（`tests/test_units.py`）：`big.__version__` 必须等于
`pyproject.toml` 里的 `version`。**同一个版本号写两处，迟早会漂**——这次就漂了。
lint 从此把它锁死。

**新增**：仓库打 `v1.0.0` tag + GitHub Release（此前 0 个 tag、0 个 Release）。

---

## 2026-10-23 · v1.0.0 · 批次 H：对外材料重构（首图换成"结论"、新增 README.en.md、章节重排）

**类型**：文档 / 对外材料（**不改任何取数与判断逻辑**；唯一的代码改动是扩展文档 lint 的覆盖面，见 H7）

**触发**：仓库发布到 GitHub 后做了一次自查。技术内容已经到位，但 README 的"专业"是
**内向的**——它主要说服已经在读细节的人（审阅者、未来的贡献者），没有为
「只是路过、想判断要不要装」的人做优化。具体三处：

1. 首屏给的是**读图口径**（方法论说明），不是**结论**（产物本身）；
2. 最值钱的真实产出只以**目录形式**出现在文档索引里，没有一处直接链接；
3. 全篇没有一处告诉访客**回去之后第一句话该对 Agent 说什么**。

### H1 · 首图从"方法论"换成"结果"

- 旧首图 `assets/preview-report.png` = 报头 + 读图口径 + 评级分布。访客第一眼看到的是
  一堆 A–F / 1–4 的图例条，印象是"哦，一个评级系统"，不是"哦，它替我读出了什么"。
- 新首图 = 报头 + **一、结论**（3 条挂着具体证据（赞数、楼数）的发现，末尾标注唯一硬数字为
  「单一来源，未交叉验证」）。**主动降级自己的结论**是这套流程的核心纪律，现在它出现在第一屏。
- 新增 `assets/preview-conflict.png` = **三、冲突与未收敛处 3-A** 的三列对照
  （A 方说法 / B 方说法 / 我的处理），放在对比表后面。**这是取数型 MCP 不会给的东西**，
  用真实产物本身当证据，比任何形容词都有力。
- 两张图都从**真实渲染产物**截取，**不是示意图**；截图脚本在 `_devdocs/shot_hero.py`
  （维护者内部目录，不入库）。

### H2 · 新增 §试试看 + 章节重排

- 新增 **§试试看**：一段可复制的真实 prompt +「你不需要指定深入还是简要，**措辞决定模式**」
  + 深入模式的产物开头长什么样。此前访客读完 21 KB 仍不知道回去该按哪个键。
- 章节顺序改为：**试试看 → 30 秒判断 → 它解决什么问题 → 和别的 MCP 不同 → 架构 →
  两种用法 → 产出 → 快速开始 → 14 个工具 → 三阶段流程 → 实测 → 隐私 → 限制 → 索引 →
  参与 → License**。即「两种用法」「产出」**上移**到「快速开始」之前，「14 个工具」**下移**——
  访客还没决定用不用之前，不该先让他 clone；先看到能得到什么，再看怎么装。
- 对比表**点名竞品** `@xzxzzx/bilibili-mcp`（原来写"市面上的 B站 MCP"，对比列是匿名的）。
- 新增 **§参与与反馈**：Star / Issue / 贡献方法论 / 交叉验证四条明确入口。
- 文档索引补上 `references/CHANGELOG.md` 与 `references/audience-profiles.md`
  （原来正文提了 CHANGELOG 但索引表没有，audience-profiles 完全没出现）。

### H3 · README.en.md

- 新增**英文版全文**（`README.en.md`），按优化后的中文版**逐节翻译**，不是摘要。
  此前 README 只有一行英文副标题，而 GitHub 的主要流量在英文语境下。
- 顶部加语言切换（`English` / `简体中文` 双向）。

### H4 · 顺带修掉的几处

- badge 行加 **version** 徽章。此前 commit 写了 v0.9.1（**从未发版**），但仓库**没有任何 tag**、
  GitHub Releases 也是空的，"这是个在维护的项目"只能靠读者相信。
- 第 27 行 `<sub>` 前的多余 `*`：GitHub 上因为它跟在行内 HTML 里会被吃成斜体，
  但 VS Code 预览与部分静态站生成器会露出字面星号。
- 所有内部锚点用 GitHub 自己的 slug 算法（`github-slugger`）**离线校验**通过：
  新增 9 个标题锚点 + 2 个跨文件锚点（`INSTALL.md#四配置详解env-里那五个变量`、
  `INSTALL.md#六与上游-xzxzzxbilibili-mcp-的关系`），**0 处死链**；全部相对路径与图片
  也逐一验证存在。

### H5 · 同步修正的一处对外口径（客户端配置，不是 server 代码）

MCP server **代码里没有 description 参数**（`mcp = _Server("bilibili-info-gathering")`，
`src/big/mcp_server.py:44`）——写「9 工具」的是**客户端配置里的一段说明文字**。
v0.6.2 已经修过 WorkBuddy 侧（`~/.workbuddy/mcp.json`，见本文件 v0.6.2 表格）；
**本次复查发现 MiniMax Code 侧的配置还写着「9 工具」**，已改为「14 工具」。

这行文字会直接暴露给 Agent 与用户（`mcp list` 的输出、工具选择时的提示词），
而代码（`@mcp.tool()` 计数 = 14）与文档（README / SKILL.md / INSTALL.md /
references/mcp-tools.md）都是 14——**唯一一处口径不一致**，现已消除。

### H6 · 顺手统一的体积口径（KiB）

README 的产物体积表用的是 **KiB**（38 KB = 39,230 B、73 KB = 75,250 B、
22 KB = 22,042 B、46 KB = 47,532 B，全部按 ×1024 并四舍五入），
但首图链接沿用了 `examples/README.md` 的**十进制**写法「94 KB」（94,312 B = 92.1 KiB）。
同一个文件里混两种口径 ⇒ 首图链接与 `examples/README.md` 统一改为 **KiB 口径**：
「92 KB 单文件 HTML」；`examples/README.md` 的「56 KB 报告」同步改为「55 KB」
（56,105 B = 54.8 KiB）。

### H7 · 把英文版纳入文档 lint

`README.en.md` 是新增文件，而**原有的两条文档 lint 看不到它**：
`test_docs_tool_count_is_consistent` 只扫 SKILL.md / README.md / INSTALL.md /
references/mcp-tools.md；`test_docs_no_stale_tool_names` 只扫 SKILL.md + references/*.md。
⇒ 英文版里的**工具数漂移**与**错工具名**会完全静默。

* `test_docs_tool_count_is_consistent`：新增读 `README.en.md`（并断言文件存在），
  英文用 `(\d+)\s*tools\b`——**只匹配复数 "tools"**，所以「40 tool calls」
  （工具调用次数）不会被误判成工具数。
* `test_docs_no_stale_tool_names`：扫描目标加上 `README.en.md`。
* 改动后测试仍 **111 passed / 3 skipped**（离线）。

**未做**：不发版（仓库至今无 tag / Release）、不写 CONTRIBUTING.md / SECURITY.md、
不动任何取数与判断逻辑、不改任何结论与数字。

---

## 2026-10-22 · v1.0.0 · 批次 G：两轮实跑验收暴露的四个"数字看着对但是错"的坑

**类型**：缺陷修复（数据完整性 / 计量口径 / 可视化准确性）+ 自检补强
**触发**：用同一议题（社区对 AI 模型娘化与各类二创的热衷）先跑深入、再跑简要，
回头查记录时发现四处不一致。

### G1 · 测试在往真实账本里记账（数据污染）

`_finish()` 每次返回都 `append_cost`，工具名取调用者函数名；单元测试直接调
`_finish()` / `scan_untrusted_text()`，于是 `.registry/cost-log.jsonl` 里出现
**116 行 `test_finish_adds_envelope` 之类的假记录**（占全文件 59%）。
后果：`window="all"` 的上游调用数与输出估算被抬高近一倍，而任何一次 `big cost`
都看不出来——它长得和真实记录一模一样。

* 新增 `tests/conftest.py`：autouse fixture 把 `registry.COST_LOG` 指向 `tmp_path`。
  **默认隔离**，新增测试的人不用记得加。
* 清理已有噪声 116 行（备份 `cost-log.jsonl.bak-20261022`），保留 81 行真实记录 + 1 行补写的 `run_end`。
* 加测试 `test_cost_log_is_not_written_by_tests` 锁住。

### G2 · 单轮开销不可复现（低报 6.8%）

报告 §五 的那一行取自 `registry_get(kind="cost", window="run")`，但取数发生在
**commit / 渲染之前**——那几步本身也记账，所以贴进报告的数字永远小于账本后来
的读数：深入轮 **88,515 → 94,916**，简要轮 28,336 → 28,611。两次查询都"正常返回"。

* 新增 `RUN_END_TOOL`（`__run_end__`）与 `mark_run_end()`：窗口**闭合**，此后数字不变。
* `commit` 自动打 run-end，并在返回里给 `final_cost.md_line`；
  收尾顺序改为 **commit → 贴 §五 → 渲染**。
* `window="run"` 未闭合时 `md_line` 自带「⚠️ 本轮未收尾，数字可能偏低」；
  新增 `window="runs"`，一轮一段，用来对比深入 vs 简要（此前只能查最近一轮）。
* 两份报告已按账本最终值回填。

### G3 · 简要轮把深入轮的视频记录整体覆盖（数据丢失）

`upsert_video` 旧实现 `vids[bvid] = rec` 整体替换。两轮都 commit，
后跑的简要轮（没读字幕、没读图）把深入轮的 `key_findings` 与 `grade` 一起冲掉，
`transcript_pulled` 还从 `true` 变回 `false`。

* 改为**合并**：findings 并集（上限 12）、transcript 只升不降、topics 并集；
  **证据更少的一轮不覆盖评级**（旧读过本体 + 新没读 + 议题是子集 ⇒ 保留旧值，
  新值进 `grade_history` 并标 `kept:false`）。
* 返回带 `changes`，commit 时就能看到"这一轮改了什么"。
* 按深入报告回补了 7 条深挖对象的评级与事实（findings 平均 2 → 9 条），
  被覆盖的值留在 `grade_history`。

### G4 · 评级没有锚点：同一议题两轮 14 条可比 BV，10 条评级不同

其中 **7 条来源字母都不同**（`BV1W6nq6AEm5` D2 vs C2、`BV1pGnn67EC2` A2 vs B1…）。
根因不是"第二次判断更准"，是简要轮没查库、把"没读到"当成"来源不可靠"。

* `source-grading.md` 新增第九节：**字母轴不随模式变，数字轴可以**（可用度相对议题与证据深度）。
* SKILL 阶段 1 加前置动作：评级前先 `registry_get(kind="video")` 看已存 grade。
* `--check` 新增跨报告比对：同一议题内同一 BV 的来源字母不一致 → 提醒。

### G5 · 自检与可视化的三处补强

* `--check` 新增：§五 缺「上下文隔离」声明、§六 缺 `6.1` 子节 → 提醒。
* **概览条的「信源评级分布」以前按徽章出现次数算**：简要轮 25 条信源被算成 43 条
  （正文引用行也带评级，同一条视频的评论与本体可以是两个等级）。改为按
  **信源地图去重后的 BV** 统计，并在标题后注明口径。
* 协议 `run-<日期>.json` 同日第二次运行会覆盖第一次的漏斗 → 内容不同则另存 `-2`；
  预算里出现「调用上限」时，`write_protocol` 会在文件里加废弃提醒。

### G6 · 预算口径的单位错误（文档源头）

`search-protocol.md` 写「调用上限 30」，而账本 `calls` 报的是**上游请求数**
（一轮 323）。两者差 10 倍，报告据此判「超支 10 倍」——**是单位误读**：
真实工具调用 43 次，超的是 13 次。

* `search-protocol.md` / `exit-rules.md` 全部改为「**工具调用上限**（默认 40 次）」，
  并加两个单位的对照表与算术（12 个词 = 1 次调用 = 24 次上游）。
* 修掉一处错误参考值（"二游一轮约 26 次调用"——26 是工具调用，上游是 84）。
* 报告与 `protocol.md` 里的对应表述已按正确单位改写。

### 关键数字

| 项 | 值 |
|---|---|
| `pytest` | **114 passed**（105 → 114，新增 9 条）；离线 `BIG_SKIP_NETWORK=1` 为 111 passed / 3 skipped |
| `check_setup` | **17 OK / 0 WARN / 0 FAIL** |
| 账本 | 197 行 → 82 行（删 116 行测试噪声，补 1 行 run-end） |
| 两轮成本（可复现） | 深入 43 次工具调用 / 323 上游 / 94,916 token；简要 11 / 71 / 28,611 |

---

## 2026-10-22 · v0.9.0 · 批次 F：产出库 / 报告重排 + 读图口径 / 上下文隔离 / 账本轮转

**类型**：结构调整（产出位置与章节编排）+ 方法论新增（上下文隔离）+ 工程补齐（账本）
**触发**：用户当日的四条优化要求 + `_devdocs/01` 路线图 P0/P1。

### F1 · 产出迁出 `.registry/`，新建「分析报告库」

**问题**：报告和机器资产混在 `.registry/topics/`。那个目录以点开头、默认隐藏、
已在 `.gitignore` 里——它是给代码和下一次运行的 Agent 看的，不是给人翻的。
后果很具体：用户在文件管理器里找不到自己刚生成的报告；清理资产时容易连报告一起删。

**修法**：

```
分析报告库/<YYYYMMDD>-<深入分析|简要分析>-<议题>/
├── <议题>·B站信息深入报告.md      ← 唯一真源（Agent 手写）
├── 同名 .html                     ← scripts/render_briefing.py
└── 同名 .pdf
```

* 新增 `src/big/reports.py` —— 路径与标题的**唯一出口**：`folder_name` / `doc_title` /
  `paths` / `locate`；模式别名归一（认不出**报错**，不静默回落）；Windows 非法字符替换。
* 新增 CLI：`big report-path --topic … --mode deep --mkdir`。
* `.gitignore` 加 `分析报告库/`。
* 4 份历史报告已迁入并改标题；`.registry/topics/<议题>/` 只留 `protocol*` / `run-*.json` / `clusters.json`。

> **为什么不放 skill 目录**：报告是使用者数据，升级 skill 不该动它；而且它要能被
> 文件浏览器/网盘直接看到（`BIG_REPORT_DIR` 可改到任意可写目录）。

### F2 · 报告六节重排 + 自动读图口径 + 标题分模式

**编排改成**：结论 → 发现 → 冲突与未收敛处 → 缺口与建议 → 检索范围与偏差声明 → 信源地图。

| 变化 | 为什么 |
|---|---|
| 信源地图从 §二 移到 §六（当附录） | 初版读完第一屏就是 140 行候选表，判断被推到第三屏；**核对发生在读完判断之后** |
| 阵营表（原「二·D」）并入 §二 发现 | 它不是信源清单，是对发现的归并 |
| 「可追溯清单」并入信源地图 | 每行本就带 BV 直链；单列一节是重复 |
| 「五、缺口」→「四、缺口与建议」 | 缺口不落到"下一步做什么"就只是抱怨 |

**读图口径（这是本批最直接的用户反馈）**：评级 `B2` 以前在报告里没有任何解释。
现在是两层——① 报头下方一块**由脚本生成**的口径块（A–F / 1–4 / 证据强度 / 缺口三类 + 读法示例）；
② 每个徽章带 `title`（「来源 B · 专业二手｜可用度 2 · 可参考」）。
**md 里不写**：写进去每篇重复 200 字，且各篇迟早不一致。

**标题分模式**：深入 → `B站信息深入报告`，简要 → `B站信息简报`；kicker 同步切换。
理由：两种模式耗时与样本量差一个量级，同名会让读者无法判断能不能当依据。

**`--check` 升级**：六节齐全且**顺序正确** / 阵营表在 §二 内 / 深入报告必须有「单轮开销」/
标题后缀与目录模式一致 / 旧版编排降级为提醒（六项指纹组合判定——单条标记会让
"新报告写错顺序"被当成旧版放过）。

### F3 · README 展示产出结构

新增「产出：长什么样、放在哪」：目录树、命名四位、六节结构表、双轴评级怎么读。
顺带修掉一句 v0.8 之后就不成立的话（原文写"它不生成报告、不排版、不做可视化"）。

### F4a · 上下文隔离（路线图 P0）

新增 `references/context-isolation.md`：**子代理读、主代理判**。

| 项 | 内容 |
|---|---|
| 问题 | 9 条深挖 × 7.7k token 评论 ≈ 69k，加字幕/弹幕约 130k；整轮 30–50 万，而进结论的不到 5% |
| 契约 | 子代理回传 ≤700 token 固定模板（事实 ≤8 / 簇 ≤6 / 冲突 ≤3 / 降级 / 评级建议 / 成本） |
| 边界 | **评级不给子代理**（可用度是相对本议题的，它只看得到一条视频）⇒ 只给 `grade_proposal` + 依据 |
| 降级 | 宿主无子代理 → 深挖降为 4–6 条，**不把原文堆进来**，并在 §五 声明 |
| 验收 | 主上下文无成段评论原文（引用 ≤40 字）/ 单条 ≤1.2k token / 9 条 ≤11k |

`SKILL.md` 阶段 2 改为「先派子代理，再谈读什么」，并加硬性规则 16 与红线 6。

### F4b · 账本轮转 + 单轮开销（路线图 P1）

* 超 512 KB 按月归档（`cost-log-YYYYMM.jsonl`，同月**追加**、保留 12 份）
* `mark_run_start()` 写 `__run_start__` 标记；`cost_summary(window="run")` 取标记之后的行
* 返回 `md_line`（千分位 + 口径），报告 §五 原样粘贴；新增 `scripts/cost_report.py`
* `registry_get(kind="stats")` 增加 `last_run` / `reports`；MCP 增 `kind="run-start"`
* **不用时间切片判轮次**：ASR 一次可能跑 10 分钟，切片会把一轮切成两半

### 验收

| 项 | 结果 |
|---|---|
| `pytest tests -q` | **104 passed**（85 → 104） |
| `scripts/check_setup.py` | **17 OK / 0 WARN / 0 FAIL**（新增报告库可写检查） |
| 真实长文 | 56 KB md → 92 KB HTML / 3.5 MB PDF（33 页 A4），肉眼验收 |
| 旗舰重排 | 行级多集比对 + 26 个 BV 覆盖校验，0 未覆盖 |

**验收中修掉的真 bug**：`apply_badges` 里 `⚠️`(=⚠+U+FE0F) 被四轮 `str.replace`
数了两遍 ⇒「证据强度分布」的"单一来源"永远是真实值的 2 倍，而 HTML 看起来完全正常。
改为一次正则替换最长优先 + 计数断言。

**契约变更**：`registry_get` 增 `window` 参数；`registry_upsert` 的 `records` 变可选
（`kind="run-start"` 不需要）；`registry_upsert` 增 `kind="run-start"`；
报告目录从 `.registry/topics/` 迁到 `分析报告库/`（**破坏性**：旧路径不再产出）。
`pyproject.version` 0.8.0 → **0.9.0**。

---

## 2026-10-22 · v0.8.0 · 批次 E：双模式 + 简报 HTML/PDF + 文档分区

**类型**：能力新增（交付形态）+ 产品定位澄清（两种模式）+ 仓库治理

### E1 · 双模式：默认深入，说「简要」才简

**问题**：此前只有一套深入流程。用户问「B站上关于 X 有什么」要等 30–90 分钟，
而有些时刻他只需要一个可信的风向——却没有任何快速通道，只能退到通用搜索（那是**降级**）。

**修法**：`SKILL.md` 新增「两种模式」一节，**由措辞决定，不反问**：

| | 深入（默认） | 简要（用户说「简要/快速/简单了解」） |
|---|---|---|
| 阶段 0 | 五样齐全 + 落 `protocol.md` | 只定核心问题 + 2–3 词族，不落协议 |
| 意图类型 | 启发式判定并**回显**，只在真模糊时问一次 | 不问 |
| 广扫 | 40 条 / 12 条评论预算 / 判图 | 15 条 / 5 条 / 不判图 |
| 深挖 | 3–9 条，字幕+评论+弹幕+图 | **不做** |
| 反证 / 归簇 / 协议 | 必须 | 不做 |
| 资产 | 全写 | **只写 video** |
| 交付 | 对话决策层 + **md + HTML + PDF** | 决策层 + 可选 md |
| 耗时 / 调用 | 30–90 分钟 / 30–90 次 | 3–5 分钟 / 5–10 次 |

> **关键设计**：意图类型（态度类/方法类）也从「必须问」改成「启发式 + 回显」。
> 问"你是想搞清楚大家怎么看，还是想知道该怎么做"在方法上正确，但在体验上是一次**硬中断**。
> 现在的做法是**先按态度类处理并说出来**，用户随时纠正——代价是一次重开阶段 0 的风险，
> 收益是不再打断。

### E2 · 简报 HTML/PDF（深入模式默认产出）

新增 `scripts/render_briefing.py` + `assets/template/briefing.css` + `shell.html`。

**架构**（与姊妹 skill 同源）：**md 是唯一真源**，HTML 与 PDF 机械生成。
样式固定进 CSS 模板 ⇒ ① CSS 不进每次输出 token（每篇省 4–5k）② 版式不漂移
③ 三者天然一致。

**可视化（全部零 token，靠约定文本触发）**：

| 约定写法 | 渲染 |
|---|---|
| 独立单元格 `A2`/`B2`/`C3` | 灰度评级徽章（A–F，**不引入第二色相**） |
| `✅` / `◐` / `⚠️` | 三档证据强度徽章 |
| `结构性边界` / `检索未覆盖` / `本次未做` | 三种边框的缺口徽章 |
| — | 自动生成「信源评级分布 + 证据强度分布」概览条（脚本统计） |
| 「冲突与未收敛」节 | 表格自动加 `.clash`：A 方深色竖线、处理列赭红竖线 + 底色 |

**降级行为**：缺 `markdown` → 报错（硬依赖）；缺 `playwright` 或浏览器 → **HTML 照出，
PDF 跳过并给安装指引**——缺 PDF 不该挡住整条交付链。

**实测**：57 KB 的二游第二轮简报 → HTML 87 KB / PDF **33 页 A4**（MediaBox 594.96×841.92 已验证）。

### E3 · 仓库治理：过程文档不再堆根目录

* 新建 **`_devdocs/`**（写入 `.gitignore`，**发布时不带**）：
  `01-项目综合评估.md`、`02-竞品对比-DSH刷B站插件.md`、`archive/`（6 份历史过程文档）
* 根目录只留四个对外文档：`README` / `INSTALL` / `SKILL` / `RISK`
* `_devdocs/README.md` 写清了维护规则：**过程文档一律进 archive，不进根目录**

### E4 · 顺手修的三个渲染问题（都是自测截图发现的）

| 症状 | 根因 | 修法 |
|---|---|---|
| 元信息里的「核心问题」被右侧裁切 | `.meta span{white-space:nowrap}` 套在长行上 | 去掉 nowrap，`inline-block + max-width:100%` |
| 日期 `2026-05` 被拆成两行 | 连字符是合法断行点 | 脚本识别日期/时长单元格，加 `.n`（nowrap） |
| `.clash` 双栏样式从未生效 | **死 CSS**：脚本从没给元素加过这个类 | `style_tables()` 按 h2 切块，给冲突节的表格加类 |

> E4 的三条都**不是**"生成失败"型 bug——HTML 与 PDF 都正常产出，
> 只有**实际截图看**才能发现。这条经验值得记住：**渲染类改动必须看图验收。**

### 验证

| 项 | 结果 |
|---|---|
| `python -m pytest tests -q` | **85 passed**（82 → 85，新增 3 条渲染测试：徽章/概览/转义、结构自检拦截、标题转义） |
| `python scripts/check_setup.py` | 16 OK / 0 WARN / 0 FAIL |
| 真实长文渲染 | 57 KB 简报 → HTML 87 KB / PDF 33 页，A4 尺寸与徽章、冲突对照、概览条均肉眼验收 |
| 依赖 | `pip install -e ".[docs]"`（markdown 必需 / playwright 仅 PDF） |

**契约变更**：无破坏性。新增 `assets/`、`scripts/render_briefing.py`、`_devdocs/`；
`pyproject.version` 从 0.5.0 追平到 **0.8.0**（此前一直没同步）。

---

## 2026-10-22 · v0.7.0 · 批次 D：三值状态 / 翻页 / 归簇落盘 / 成本账本 / 反证

**类型**：契约变更（`status` 新增）+ 能力补齐（D1–D8）+ 方法论修订

> 来源：2026-10-21 的方法论评估（`议题分析方法论评估-20261021.md`）。
> 用户补充了两条前提后按 P0–P2 全做：
> ① 目标用户是「愿意为议题投入 30–90 分钟、要拿证据链」的人，**不是**「三分钟要答案」的人；
> ② 判断必须由强模型做，但**必须可复核**（去黑箱化）。

### D1 · `status` 三值 + 早退分支统一外壳

**症状**：`ok = not degraded` 把「失败」和「降级」压成一个布尔。实测
`scan_topic` 因关键词封顶返回 `ok=false`，而数据完整可用 —— Agent 极易当失败重跑。
另有 13 个 `return _j({"error": ...})` 的早退分支（分P越界、缺参数、无 ffmpeg）
**完全不经过 `_finish`**，返回里没有 `degraded`/`cost`，调用方要写两套解析。

**修法**：
* `_finish` 出 `status: ok|degraded|failed`，`ok` 保留为 `status == "ok"` 的快捷方式
* 兜底放在**唯一的序列化出口 `_j`**（不是改 13 处）：含 `error` 且无 `status` 的 dict
  自动补 `failed` + `degraded: []` + 零值 `cost`
* `_err` 显式传真实调用者名（否则记账会记到 `_err` 头上）

### D2 · `scan_topic` 翻页 + 排序强化

**症状**：二游第二轮 `unique_candidates` 835/619，实际只返回 50/45 ⇒
**约 1,359 条候选永远看不到**，且当时没有翻页参数。
另一个实测：一条纯 XP 赏析视频靠**标签**命中 6 个族、16 个词，排到第 1 名。

**修法**：
* 新增 `offset`；`coverage` 增 `ranked_total` / `offset` / `next_offset` / `rest_preview`
* `rest_preview` 是未展示候选的一行摘要（不含评论，成本≈0）——**先扫一眼再决定翻不翻**
* 排序键改为：**强命中族数**（标题/简介字面出现的族数）→ 不同族数 → 命中词数 → 字段权重 → 播放量
* 新增 `_field_weight`：title×3 / desc×2 / tag×1 / 都没有 0
* `_term_variants` 过滤两字母英文片段（否则 `AI Agent` 拆出的 `ai` 会让几乎所有标题"强命中"）

### D3 · `exclude_topic` 增量模式

同议题第二轮自动排除 `videos.json` 里已登记的 BV；读不到资产库时**降级但不失败**
（`incremental.excluded_known=null` + `degraded` 说明），不装作排除了。

### D4 · 观点簇回写（去黑箱化）

`registry_upsert(kind="cluster")` → 暂存 → commit → `.registry/topics/<议题>/clusters.json`。
主键 `(topic, bvid, cluster)` 三元组（为此给 `stage_record` 加了复合主键支持）。
字段：`cluster / stance / member_rpids / evidence / axis`。
读回 `registry_get(kind="clusters", topic=…)`。

> **这是本轮最重要的改动**：不改变「谁做判断」，只改变「判断有没有证据链」。

### D5 · 成本账本

每次调用追加 `.registry/cost-log.jsonl`（失败静默）。
`registry_get(kind="cost")` 按工具汇总；`kind="stats"` 里 `spend` 是摘要。
⚠️ `est_output_tokens` 是输出侧估算，用于横向比较，**不是账单金额**。

### D6 · 反证从「偶然」变「流程」

`SKILL.md` 新增**阶段 2.5**：每个准备进结论的事实必须尝试找反证；
找到→进冲突节，找不到→写「未找到反证」（**不写「已证实」**）。
起因：三个议题里最值钱的冲突全是读评论时**碰巧撞上**的。

### D7 · 交付分层 + 证据强度三档 + 缺口三类 + 用户画像

* `briefing-template.md`：默认先给**决策层（≤25 行）**，证据层展开；并明确
  「分层 ≠ 给普通用户看简版」
* **证据强度三档**（✅多源独立 / ◐部分印证 / ⚠️单一来源）替代原来的 ✅/⚠️ 二分——
  原二分把「两个二手互相抄」和「两个一手独立一致」都标成 ✅
* **缺口三类**：检索未覆盖 / 结构性边界 / **本次未做**（新）——
  旧三份简报里约一半「缺口」其实是"路径有、没走完"，却被写成和"B站没有"同一种语气
* 新增 `references/audience-profiles.md`：明确本 skill **不服务「三分钟要答案」的用户**；
  企业级四类场景（投研 / 公关 / 平台运营 / 调研）标注为**推演，未验证**

### D8 · 旧简报回填 + 超时

* 三份旧简报 §五 补「类型」列（共 19 条缺口重新分类）
* `mcp.json` / `install_mcp.py` 的 `timeout` 300000 → **600000**
  （1080p 抽帧实测 431 秒，300s 会让**客户端**先超时）

### 验证

| 项 | 结果 |
|---|---|
| `python -m pytest tests -q` | **82 passed**（77 → 82，新增 5 条：外壳兜底 / status 三值 / 字段权重 / 强命中排序 / 账本汇总） |
| `python tests/test_mcp_smoke.py` | 新增 3 组真调用断言：status 三值、簇往返（含复合主键覆盖语义）、offset 两页不重叠 |
| `python scripts/check_setup.py` | 16 OK / 0 WARN / 0 FAIL |

**契约变更（破坏性）**：返回顶层多了 `status`；`ok` 的语义从 `not degraded`
变成 `status == "ok"`（**对 `failed` 而言是 False，以前不会有 failed 这个值**）。
按 `ok` 做判断的代码不用改，但应该改看 `status`。

---

## 2026-10-21 · v0.6.2 · 🔴 修 `grab_frames` 的 1080p 必崩（`IncompleteRead` 路径错）

**类型**：bug 修复（取数层 1 处）+ 文档校正（3 处数字 / 2 处 git 现状 / 1 处成本提示）

### 1. 🔴 `_fetch_range` 的 except 写错模块 → 分段续传整体失效

**症状**：`grab_frames(quality=1080)` **必失败**，且要等 **377 秒**（下载完才发现）：

```
AttributeError: module 'urllib.error' has no attribute 'IncompleteRead'
```

720p 偶发（网络抖动时），360p 基本不出现 —— 因为大文件更容易触发 CDN 半路 EOF。

**根因**：`IncompleteRead` 定义在 `http.client`，**不在 `urllib.error`**。
更糟的是它出现在 `except` 子句里：Python 只有在**异常真正发生、开始匹配 except**
时才求值 `urllib.error.IncompleteRead` → 抛 AttributeError。这个 AttributeError
出自 except 子句本身，同一 try 里下面的 `except Exception` **接不住**，直接穿透到工具层。
⇒ 不只是"报错信息难看"，而是**「半路 EOF 保留 partial 字节继续推」这条优化从未执行过**
（`stream.py` 顶部坑点 4 写的就是它）。

**修法**：`import http.client` + `except http.client.IncompleteRead as e`。

**回归测试**：
* `test_fetch_range_keeps_partial_on_incomplete_read` —— 断言 partial 字节不丢、错误原样带回
* `test_segment_resumes_after_partial` —— 断言断流后从已收字节处续推，不整块重下

**实测对照**（同一视频 `BV1faGX65EgK`，本机约 1.5 MB/s）：

| quality | 修复前 | 修复后 |
|---|---|---|
| 360 | ✅ 49 MB / 6 s | ✅ |
| 720 | ⚠️ 偶发 AttributeError | ✅ 141 MB / 17 s |
| **1080** | ❌ **377 s 后崩，0 帧** | ✅ **667 MB / 431 s，2 帧 + 联系表落盘，seek_check ok** |

> 💡 这类 bug 的教训：**模块属性写在 except 子句里是"带引信的哑雷"**——
> 平时完全静默、tools/list 正常、单元测试不覆盖断流就永远发现不了，
> 一旦触发就是"报错指向明明无关的模块"。排查时不要信报错里的模块名，去查异常的真实归属。

### 2. 文档校正

| 位置 | 原来 | 现在 |
|---|---|---|
| `~/.workbuddy/mcp.json` 的 description | 9 工具 | 14 工具 |
| `INSTALL.md` §五 | tools/list（应正好 9 个） | 14 个 |
| `SKILL.md` 文件地图 | mcp-tools.md「11 个 MCP 工具」 | 14 个 |
| `INSTALL.md` §2.1 / §2.5 / §3.1 | 假设"真源是 git 仓库、可 git pull" | 注明当前非 git 仓库，junction 场景无需更新 |
| `references/mcp-tools.md` | `grab_frames` 三条限制 | 四条（新增 1080 的真实成本：667 MB / 431 s） |

---

## 2026-10-21 · v0.6.1 · 方法论优化 + `get_transcript` 的 query 静默失败修复

**类型**：方法论文档修订 + bug 修复（**取数层只改了 1 处**）

### 1. 🔴 `get_transcript` 的 `query` 零命中是**静默失败**（第二轮实测发现）

**症状**：一条 **387 行**字幕，用 6 个词组查询（`退坑 回坑 40金 体力 抽卡 成本`），
返回 `n_returned=0` 而 `degraded` **完全为空** —— Agent 看到空结果无法区分
「视频没讲」与「我的用词不对」，而这两种情况的后续动作完全相反。

**修法**：
* 返回里加 `query_matched` / `query_missed`（哪些词命中、哪些没命中）
* 零命中时 `degraded` 明确说明，并给出下一步（换单个词/同义词重试，或 `head_ratio` 通读）
* `ok` 随之为 `false` —— 让 Agent 一眼看出"这次是降级结果"

**回归测试**：`test_transcript_query_zero_match_is_not_silent` +
`test_transcript_query_reports_which_words_matched`（用假数据，不联网）。

> 💡 **这一类 bug 只有真跑才会暴露**：它不报错、返回结构完全正常，
> 只是内容是空的。**"空结果"必须带信号**——否则它和"确实没有"无法区分。

### 2. 方法论文档修订（10 类，17 处编辑，跨 5 文件）

全部来自**三个真实议题的产物 + 两轮隔离子对话的独立观察**。
**零处收紧已有判据**（全部是补分支、纠错、补可核验口径）。详见
`MCP-方法论优化-交付-20261021.md`。

最值钱的三条：

| # | 改动 | 证据 |
|---|---|---|
| 1 | **「没有字幕」不再是淘汰理由** → 补 `transcribe_audio` 的触发判据 | 三议题共 5–6 条候选无字幕导致"视频本体零证据"，而 `transcribe_audio` **一次都没被调用过** |
| 2 | **L2 严格筛改成两条路**（字幕侧 + 评论区侧） | 三议题的证据重心**全在评论区**（12,581 条评论 / 620 条评论的具体数字 / 踩坑报告赞占比 4% 但密度最高） |
| 3 | **预算参考表重写** | 旧表是 **CLI 时代**算的（工具名全是上游的，调用上限虚报成 90 次），实际一轮约 26 次 |

其余：④「搜索上限 10 条」→ 50（那是上游的 schema 限制）⑤修上游工具名 +
**2 条文档 lint 测试**防回归 ⑥同族关键词主词要不同 ⑦词族探针制度化
⑧缺口表区分「检索未覆盖」/「结构性边界」⑨评论区图片的引用纪律 ⑩`user_adopted` 的使用时机。

### 3. 验证

| 项 | 结果 |
|---|---|
| `python scripts\check_setup.py` | 16 OK / 0 WARN / 0 FAIL |
| `python tests\test_mcp_smoke.py` | 14 工具，真调用 + 定向断言全过 |
| `python -m pytest tests -q` | 73 → **75 passed**（+2 条 query 回归） |
| 真实复现 | BV1uebL6TESo（387 行）→ `n_returned=0` + `degraded` 报出 6 个未命中词 + `ok=false` ✅ |

---

## 2026-10-21 · v0.6.0 · MCP 优化批次 B（P1）+ 批次 C（P2/P3）落地

**类型**：能力扩展 + 契约变更 + bug 修复

**起点**：`MCP-优化方案-20261021.md`（分析）与 `MCP-优化开发任务书-20261021.md`（执行契约）。
批次 A（P0）见 `MCP-优化批次A-交付-20261021.md`。

### 1. 工具数 11 → **14**

新增三个：`deep_read`（组合通道）、`get_creator_content`（UP 主通道）、
`scan_untrusted_text`（注入扫描）。

### 2. 批次 B（P1）

| 编号 | 做了什么 | 为什么 |
|---|---|---|
| **B1** | **所有工具**的返回顶层统一带 `degraded` / `ok` / `cost` | 降级信号以前散在各工具自己的 `note` 里，**漏看 note = 当成没降级**。`cost.calls` 是**真打了几次上游**（`http.py` 新增线程安全计数器），不是估算 |
| **B2** | `kind="protocol"` + `protocol.md` / `protocol.sha256` / `run-<日期>.json` | 阶段 0 的五样产物以前只存在于当轮上下文 ⇒ 简报 §六 是**手写**的、同议题重复劳动。现在**从 protocol.md 读出来**。升级规矩同 DSH 的 `duty.md`：没改过就自动升级，改过就保留用户那份、新版另存 `protocol.md.new` |
| **B3** | `scan_topic(families={...})` + `kw_families` 修正 | `kw_families` 以前**名不副实**（按关键词计数却当族数用）。现在传了 `families` 它就是真的族数；不传则**返回 `null`**（算不出来就如实说），并新增 `kw_hits` 与 `hit_by_family` |
| **B4** | `kind="topic"` 议题档案（`topics.json`）+ `VIDEO_FIELDS` 扩三个字段 | 跨任务记忆 + 反馈闭环。`user_adopted` 记录**用户采纳了什么**（等价于"你点了赞"），让"下轮往哪深挖"有外部信号；`selected_because` 让"为什么这条被选进来"半年后仍可查 |

### 3. 批次 C（P2/P3）

| 编号 | 做了什么 | 为什么 |
|---|---|---|
| **C1** | **写侧闸门（延迟合并）**：`registry_upsert` 默认只进 `.registry/staging/`，收尾时 `kind="commit"` 一次合并；另有 `kind="discard"` | DSH 插件的「能力最小化」等价实现：写侧**始终可用**（不打断"边挖边记"），但**碰不到正式库**。任务书给了两个方案，选了推荐的延迟合并，**没有两种都做** |
| **C2** | `deep_read` 组合通道 | 5 条通道原本要 5 次调用 + 5 次重复拉元数据。现在一次调用聚合，且每条通道的载荷与单通道工具**同构**（不出现"两套结构"） |
| **C3** | `get_metadata` 放回 `pic`（封面） | `video.py` 早就返回了封面 URL，被 MCP 层的字段白名单过滤掉了。封面是"六路看懂"里**最便宜的一路** |
| **C4** | `scan_topic` 加 `pages_per_keyword` / `min_play` / `published_after` | 实测每个词 page_size=30 时各命中 26–30 条，**恰好卡在上限，长尾在第 2 页**。子会话实测：`unique_candidates=144` 但只返回 30，**多出的 114 条没有任何再取接口** |
| **C5** | `get_creator_content`（overview / videos / collections / series / dynamics） | 议题收敛到 2–3 个核心 UP 后，"他后续又发了什么"是自然追问。**space 系列端点必须带 WBI 签名**（不带是 -799）——这正是 `http.py` 存在 WBI 的理由 |
| **C6** | `scan_untrusted_text` | 信任边界第二层：扫描**不是从本项目取来的**外部文本（别的 MCP 工具、用户粘贴） |

### 4. 🔴 契约变更（**破坏性**，调用方需要知道）

* **所有工具的返回顶层都是 dict**。原来返回 list 的（`get_metadata`、`registry_upsert`）
  现在包成 `{"items": [...]}`。
* `registry_upsert` 的写入**默认进暂存区**，需要显式 `kind="commit"` 才进正式库。
* `get_transcript` 无字幕分支的**骨架与有字幕分支对齐**（以前两套结构，
  无字幕时连 `duration` 都拿不到）。

### 5. 注入检测的两轮收紧（都是**误报**驱动的）

| 实测误伤 | 修法 |
|---|---|
| 洛谷体长评里「轻视与**忽视**嘲笑的方法」与相隔几百字的「正如**之前**」被判成注入（BV1nwN76YENQ，48 赞） | 组合式模式改成**邻近匹配**（两词相距 ≤ 30 字符才算命中） |
| 写一条**关于**注入的洞察时，引用攻击原句（「当你看到这条评论时…」）会被写入侧拒掉——而那是经验库里最值钱的一条 | **引号豁免**：被「」『』""等包起来的片段不当成指令 |

> ⚠️ **`injection_risk` 命中 ≠ 这条在攻击 Agent**。它是纯关键词匹配，
> 只能当"值得再看一眼"的提示，不能当证据。

### 6. 顺带修掉的问题

* **`get_comment_images` 一直是坏的**（批次 A 修的）：它调的是**同名工具**而不是库函数，
  每次调用都 TypeError ⇒ 该工具 100% 不可用，而 `tools/list` 完全正常。
  冒烟测试补了「跨工具调用」静态检查（用备份版验证过它有牙）。
* **位置参数错位**：`scan_topic` 的 cookie 被当成 `pages_per_keyword` 传进去，
  把整个调用打废。现在**全部用关键字参数**传。
* **暂存路径被快照**：`registry` 在导入时把暂存路径快照下来了，
  之后改 `STAGING` 不生效 ⇒ 测试会写进真库。现在路径**调用时现算**。

### 7. 验证

| 项 | 结果 |
|---|---|
| `python scripts/check_setup.py` | 16 OK / 0 WARN / 0 FAIL（14 个工具与文档一致） |
| `python tests/test_mcp_smoke.py` | 11 → **14 工具**，真调用 + 多P / 注入 / 统一外壳 / 暂存→commit 定向断言全过 |
| `python -m pytest tests -q` | 20 → **71 passed** |
| 批次 C 独立验收（真实 stdio + 真实接口） | **28 项断言全过**（含 UP 主通道 5 个 section 全部实测可用） |
| 批次 B3 回归（用上一批的 6 组原词重跑） | 127 条候选里 **15 条名次改变**；冗余命中集中的候选下降、跨族命中广的上升（详见交付文档 §三） |

> ⚠️ **B3 的原验收标准没有复现**：任务书写「模型评测类应当下降、真插件视频应当上升」，
> 实测两条**名次都没变**（旧第 5 / 第 2 → 新第 5 / 第 2）。
> 原因：真插件视频的关键词命中数本来就高于模型评测视频，**它在旧排序里就已经更高**——
> 冗余放大从来不是它排高的原因。机制本身有效（15 条名次改变，方向符合设计），
> 但**那条预测的因果链是错的**。详见 `MCP-优化批次BC-交付-20261021.md` §三。

### 8. 本次同时产出的文件

| 文件 | 内容 |
|---|---|
| `.registry/topics/20261021-主流二游社区·B站信息简报.md` | 隔离子会话产出的议题简报（60 条候选 + 9 条深挖 + 冲突/缺口/偏差声明） |
| `MCP-优化批次BC-交付-20261021.md` | 批次 B + C 的交付文档 |

**未做**：批次 A 已交付；本轮无其它未做项。
**仍未 `git init`、未提交**（维护者决定先积累使用体验）。

---

## 2026-10-21 · v0.5.1 · 方法论定稿：观点分布 → 阵营/簇的映射（关闭 ⏳ 待补项）

**类型**：方法论定稿 —— **纯文档，零代码改动**（`pyproject.toml` 版本未动，仍为 0.5.0）

### 1. 关闭 `briefing-template.md` §二·C 的 ⏳ 待补项

阶段 2 产出的簇是**视频内**的 4–8 个，阵营表是**跨视频**的 2–4 个，中间缺一层映射。
**本次用两个议题各实跑一次后定稿**：态度类「赴港工作」+ 方法类「AI Agent 自主学习与 B 站」。

| 问题 | 答案 |
|---|---|
| 一个簇要不要单独成行？ | **看议题类型**：态度类先归并成 2–4 个跨视频阵营；方法类**不产出阵营表**，簇只作「发现」「冲突」两节的取材来源 |
| 「占据什么位置」用哪一层的量？ | **两层并列、不嵌套**：阵营表用**视频级**指标（播放/赞/赞播比/评论数），簇级三轴**不折算**进阵营（折算＝伪精度） |
| 「冲突」吃的是簇还是阵营？ | 冲突的**最小单位是簇**，写进简报的是**议题级**冲突行（双方都列，不下结论） |

**归并依据**：看簇的**核心主张**是否自洽于同一个框架，**不是**看三轴数值。
三轴只描述"这个簇多响"，不描述"它是什么"。

### 2. `opinion-distribution.md` §五 升级为**跨议题类型的通用纪律**

原来只有一次证据（态度类「赴港工作」：最值钱的通勤成本测算/中介路径警示簇三轴**全部垫底**），
容易被当成"态度类的注意事项"。本次在**方法类**议题上独立复现：

- 样本：`BV1d8pU6xEZX` 前 100 条顶层评论（`judge_depth=100`）
- 最值钱的簇 = **真实踩坑报告**（桌面版自动跑卡住、Python 3.15 编译失败、`aisubtitle` 直连 403、
  AI 字幕人名同音乱码）⇒ 赞占比约 **4%**（全表最低）
- 认同度最高的簇 = 玩梗 / 到报 / 身份扮演 ⇒ 约 **63%**

⇒ **「热度用来选样本，内容密度用来排序」不是态度类专属，是所有议题通用。**
§八 补上方法类的取材规则：不产出阵营表，且**情绪簇必须明确标为"不做证据"**，
否则它们会凭高认同度挤进「发现」。

### 3. `SKILL.md` 同步

- 阶段 3 的 ⏳ 待补标记 → 改为 ✅ 已解决，并指向 `briefing-template.md` §二·C
- 通道 A 的 ⛔ 提示补上第二次复现的具体数据

### 4. 已知未做（明确记下，避免以后误以为已实现）

- **`references/registry.md` 尚未补 `kind="topic"` 的数据结构**（议题档案层）。
  本次只定了方法论；数据结构落地属于 MCP 侧 P1 改动，见 `MCP-优化方案-20261021.md` §4.2。
- **代码侧零改动**：`registry_upsert` 入参被 MCP 客户端层改形（数组→`{"item":…}`、布尔→字符串）、
  多 P / 分 P 全链路只读 P1 等 P0 缺陷**尚未修**，见 `MCP-优化方案-20261021.md` §3.6 / §3.1。
  （本次已用脚本把被污染的 13 条记录按原始载荷重写并复核通过，但**代码不修，下次写入会再坏**。）

### 5. 本次同时产出的文件

| 文件 | 内容 |
|---|---|
| `.registry/topics/20261021-如何让AI Agent自己在B站中畅游并学习·B站信息简报.md` | 议题简报（40 条信源地图 + 6 条深挖 + 冲突/缺口/偏差声明 + 观点分布映射留档） |
| `MCP-优化方案-20261021.md` | MCP 优化方案（能力对照 + 4 个可借鉴机制 + 3 个 P0 代码缺陷 + 优先级与验收标准） |

---

## 2026-10-21 · v0.5.0 · 共享 ASR 运行时 · 修 `BIG_ASR_CMD` 切碎带空格路径

**类型**：能力扩展 + bug 修复

### 1. 🔴 `BIG_ASR_CMD` 里带引号/带空格的路径**永远无法使用**

`detect_backend()` 用 `cmd.split()[0]` 取可执行文件：

```python
exe = cmd.split()[0]   # '"C:\Program Files\x\python.exe" arg' -> '"C:\Program Files\x\python.exe"'
found = shutil.which(exe) or (exe if pathlib.Path(exe).exists() else None)
```

切分后**连引号一起**被当成文件名的一部分 → 判「文件不存在」→ 用户永远用不了
`C:\Program Files\...` 这种路径，而错误信息完全指不到真因（看着像"路径写错了"）。

**修法**：新增 `_parse_exe()`——引号开头就取到配对的引号为止（不闭合判 `None`），
无引号才按空白切。**回归测试已加**：`test_big_asr_cmd_with_quoted_spaces_is_recognized`。

### 2. 新增「共享 ASR 运行时」后端（优先级 ②）

一个**自包含的独立 venv**（含 `asr_transcribe.py`），多个项目共用一份。
放在用户自己的目录 ⇒ 脱离任何产品的受管目录（WorkBuddy 重装不影响它），且版本可锁。

- 探测位置：`BIG_ASR_HOME` → `~/.local/asr` → `~/.local/share/asr` → `%LOCALAPPDATA%\asr`
  ——**全部相对 home**，GitHub 用户在 Linux/macOS 上命中同一套约定
- 判据是**三信号齐**：解释器 + 入口脚本 + site-packages 里**真的**有 `faster_whisper`。
  缺一不认——否则"目录建了但没装包"的半成品会在真正转写时才炸
- 新增 `scripts/asr_transcribe.py`：把三个坑封在入口里——CUDA DLL 前置 PATH
  （`os.add_dll_directory` 实测无效）、Blackwell 显式 `float16`、批处理加速
- 命令契约与 `BIG_ASR_CMD` 占位符一致：`{audio} --outdir {outdir} --model {model}`

> ⚠️ **GitHub 红线**：共享运行时**只是探测目标**，绝不进 `pyproject.toml` 依赖、
> 绝不进 `install_mcp.py` 的自动安装流程。GitHub 用户不该被迫下载 736 MB nvidia 包。

### 3. 装运行时踩到的坑：`av` 必须锁 `<19`

按 `pip install faster-whisper` 装完，**装包、import、模型加载、GPU 识别全绿**，
但一真正解码音频就炸：

```
TypeError: open() got an unexpected keyword argument 'metadata_errors'
```

真凶：`faster_whisper/audio.py:46` 调 `av.open(..., metadata_errors="ignore")`，
而 **PyAV 19.0.1 已移除该参数**（18.1.0 还有）。faster-whisper 1.2.1 声明 `av>=11` **无上界**。

⇒ 修法 `pip install "av<19"`，并**立刻 `pip freeze` 存 lock**。
错误信息离真因隔了两层（看着像"av 的参数用错了"，实际是"PyAV 改了 API"），
且**失败发生在运行时而非安装时**——这类坑不跑一次真实数据查不出来。

### 4. 实测基线（本机）

| 项 | 值 |
|---|---|
| 共享运行时 | `~/.local/asr`（base = `miniconda3` Python 3.14.6，**与 WorkBuddy 无关**） |
| 体积 | **1,191 MB**（nvidia/cublas 736 MB 不可省；**cuDNN 1,092 MB 实测不需要**） |
| 速度 | **78 倍实时**（1824 秒音频 → 23.4 秒出稿；v0.4.1 的受管 skill 是 38.7 倍） |
| 自动探测 | 端到端实测通过（不设任何环境变量也能命中并转写出真实中文） |

> 💡 **cuDNN 不用装**：只把 `nvidia/cublas/bin` 前置进 PATH，ctranslate2 照样
> `get_cuda_device_count() == 1` 并跑完整个前向——它自带 0.3 MB 的 `cudnn64_9.dll` 转发桩。

### 5. 其它

- `install_hint()` 补第 ④ 条（建共享运行时）与 `av<19` 警告
- `README.md` 文档索引修掉陈旧的"9 个工具"（实际 11 个），补三份新 reference
- `INSTALL.md` 新增 §2.6「本地 ASR（可选）」+ 故障速查补三行
- 测试 15 → **20 passed**

**未做**：本仓库**仍未 `git init`、未提交**（维护者决定先积累使用体验）。

---

## 2026-10-20 · v0.4.1 · ASR 接上本机已有的转写 skill · 双 skill 路由补漏

**类型**：能力接入修正 + 路由补漏

### 1. 🔴 ASR 后端找错了地方

v0.4.0 的 `asr.py` 只在**当前解释器**里 `import faster_whisper`——
本机根本没装，于是报"未安装"。**实际上本机早就有本地 ASR 工具**，
只是装在**受管 venv** 里：

| | |
|---|---|
| 工具 | `local-audio-transcription` skill（WorkBuddy skills 目录） |
| 脚本 | `~/.workbuddy/skills/local-audio-transcription/scripts/transcribe.py` |
| 解释器 | `~/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（受管 venv） |
| 模型 | `large-v3-turbo`（HF 缓存已有） |

⇒ **不是"没装"，是"装在别处"。重复安装是错的。**

改法：后端优先级改为
① `BIG_ASR_CMD` → ② **复用 `local-audio-transcription` skill** → ③ 当前解释器的 faster_whisper。
②的脚本**已内置**三个坑的修复（CUDA DLL 路径 / Blackwell 精度 / 批处理加速），
比自己写一遍 in-process 版本可靠。

**实测**：1824 秒音频 → 47.1 秒出稿 = **38.7 倍实时**（cuda / float16 / large-v3-turbo），
输出与 B站字幕同构。

### 2. 选解释器的判据不能是"存在"或"装了 faster_whisper"

受管 envs 有多个（`asr` / `default` / …）。实测 **`asr` 装了 faster-whisper 1.2.1
但没装 nvidia CUDA 运行库**，选中它会在跑到一半时炸
`Library cublas64_12.dll is not found or cannot be loaded`——
错误信息完全指不到"你选错了解释器"。

⇒ 改用**能力探测**判据：把 `site-packages/nvidia/*/bin` 前置进 PATH 后，
`ctranslate2.get_cuda_device_count()` 能看到几张卡。按 GPU 能力排序选。

### 3. 双 skill 的路由补了三层漏

姊妹 skill 的 `SKILL.md` 正文改了触发边界，但**三处更高杠杆的地方没改**：

| 层 | 原来 | 后果 |
|---|---|---|
| **frontmatter description** | 没提议题级例外 | 运行时做 skill 匹配时看不到 ⇒ 路由靠运气 |
| **INSTALL.md §0** | 「这是什么/不是什么」表里只有本项目 | 读者/Agent 的心智模型里没有议题级能力 |
| **INSTALL.md §7 首次使用引导** | 直接引导「给我一个 BV 号」 | **AI 主动把用户往单视频路线上引**，SKILL.md 的边界规则根本没机会生效 |

⇒ 三层全补：frontmatter 加议题级不适用场景；§0 表加姊妹项目行；
§7 加**第 0 步「先判意图」**（议题 → 转交 `bilibili-info-gathering`）。

### 4. 记下一个待补的设计缺口

**观点分布的簇 → 跨视频阵营的映射**还没定。
阶段 2 产出的簇是**视频内** 4–8 个，阵营表是**跨视频** 2–4 个，中间缺一层。
已把要回答的三个问题 + 临时做法写进 `references/briefing-template.md` §二·C，
并在 `SKILL.md` 阶段 3 标了 ⏳。**第一次真跑议题时按实际产出形态重写。**

---

## 2026-10-20 · v0.4.0 · 方法论迁移（观点分布 + 字幕可靠性）· 触发边界重写

**类型**：方法论迁移 + 能力新增 + 既有 bug 修复

**起因**：与姊妹 skill `bilibili-video-analysis-summary` 的分工定案——
**不调用它，但迁移它的判读方法**。用户确认了两条工作流：
「先广泛略读找到精华，再详细精读」。

### 1. 评论区观点分布 —— 阶段 2 的默认动作

姊妹 skill 第 4 步的三轴方法（普遍度/认同度/争议度，**不合成分数**）迁入本项目，
新文档 `references/opinion-distribution.md`。

**不按议题类型区分**（用户明确要求）：态度类和方法类的评论区都是该议题的一部分。

成本实测（一条 2014 评论的视频，取前 100 顶层 + 150 楼中楼）：

| 口径 | 字符 | token(估) |
|---|---|---|
| `full`（全字段 250 条） | 71,922 | ~25,300 |
| `brief`（正文截断 90 字） | 7,374 | ~4,280 |
| **`opinion`（仅顶层 + 完整正文）** | **14,611** | **~7,722** |

- **时间成本可忽略**：1.76 s / 5 次请求。
- **token 是瓶颈，但最优解恰好也是最正确的做法**：
  三轴全部只依赖顶层（`rcount` 只存在于顶层），所以**只取顶层就够算**——
  比 `full` 省 71% 不是因为省内容，是因为省了 JSON 结构开销（full 里正文只占 16%）。
- **为什么不用 `brief` 省更多**：顶层 24% 的正文超过 90 字，截断丢掉 35% 的正文量，
  而观点簇判断依赖完整语义——"反对并给出理由"被截成"反对"，
  正好毁掉认同度与争议度的区分。**省 token 不能省在语义上。**

⇒ 新增 `get_comments` 的 `detail="opinion"` 档位。

### 2. 字幕可靠性 —— 迁入并按议题级裁剪

新文档 `references/subtitle-reliability.md`：AI 字幕 5 类错误模式 /
说话人分离缺失（三方交叉反推）/ 反向核实（术语不规范 ≠ 误转）/ 数字以画面为准。
判据「有没有字幕必须带登录态」也一并写入。

### 3. 两条补路：本地 ASR 与段级抽帧

| 能力 | 工具 | 设计要点 |
|---|---|---|
| 本地 ASR | `transcribe_audio` | 引擎**使用者自备**（不进仓库）。**没有后端时返回 `status="no_backend"` + 可执行安装指引**，并要求把「该视频无可用字幕」记进缺口声明——**不静默失败** |
| 段级抽帧 | `grab_frames` | 只给时间点，不抽全片。**要读表格数字必须 1080**。带**定位自检**（多帧完全相同 = 定位失败） |

新增模块 `src/big/stream.py`（dash 取流 + 并行分段下载）、
`src/big/asr.py`、`src/big/frames.py`。

### 4. 🔴 修复既有 bug：`get_comments` 工具一直是坏的

`@mcp.tool()` 的函数名 `get_comments` 与 `from .comments import get_comments`
**同名**，模块级 `def` 把导入名重新绑定 ⇒ 工具内部调自己 ⇒ 缺参数 TypeError。

**症状**：工具能注册、能列参数，但**每次调用都失败**。
`tools/list` 完全正常，所以只验「能启动 + 能列参数」的验收**查不出来**。

⇒ 导入改别名 `_fetch_comments`；**冒烟测试从「只验 tools/list」升级为「真调用 + 静态查自我递归」**。

### 5. 触发边界重写（两个 skill 对称）

| | 本 skill | 姊妹 skill |
|---|---|---|
| 关键信号 | **用户明确说了「B站」** + 给的是**议题** | **给了 BV 号** + **要"分析这个视频"** |
| 固定用法 | 「帮我在B站了解 X。背景：…」 | 「帮我分析总结这个视频，链接：xxx。」 |
| 常见误触发 | 没提 B站却用本 skill（应该走网页检索） | **只要有 BV 号就触发** |

明确写入的三条**不**触发姊妹 skill 的情形：
① 用户**背景**里贴了 BV 号 ② 阶段 1 广扫返回的 BV 号 ③ 阶段 2 内部精读取到的 BV 号。

### 6. 资产库保持分开

两个 skill 的分析视角、方式、产物都不同 ⇒ 资产库**不合并**，各自沉淀。

---

## 2026-10-20 · v0.3.0 · MCode 侧直连接入 + GitHub 分发形态

**类型**：接入形态 + 分发工具链

**起因**：项目要同时服务两个身份——**本地开发者**（在 MCode 里改代码、跑 MCP），
和 **GitHub 用户**（装在自己 Agent 上用）。两种身份需要的安装说明、脚本完全不同，
硬塞进一份 README 会让两边都难受。

### 1. MCode 侧直连（junction + stdio 注册）

- skills 目录建 junction 直指项目目录 → 改代码即生效，结构上不可能漂移
- MCP server 注册为 stdio：`python.exe -m big.mcp_server`

### 2. 目录锚定：`OUT_ROOT` 的 cwd 依赖（真踩到的坑）

`media.py` 的 `OUT_ROOT` 默认值是 `Path.cwd() / ".bigout"`，而客户端的 stdio server
**未必带 cwd 参数**（配置层通常只有 `command`/`args`/`env`）。cwd 一变，
评论区联系表就写到别处去了。⇒ 注册时必须显式钉死 `BIG_OUT_DIR` / `BIG_REGISTRY_DIR`。
`registry.py` 本来就用 `__file__` 推项目根，不受 cwd 影响，但一并显式设置更保险。

### 3. stdio 环境的两个隐性要求

| 变量 | 为什么 |
|---|---|
| `PYTHONIOENCODING=utf-8` | MCP 客户端按 UTF-8 解码；Windows 下 stdout 走管道时用系统 ANSI 代码页(GBK)，不设会乱码甚至抛 `UnicodeEncodeError` 让 server 静默死掉 |
| `PYTHONUNBUFFERED=1` | stdout 被管道接走时是块缓冲，不设会延迟响应，表现为工具调用"卡住" |

另外实测确认：MCP 进程**不会继承** `BILI_SESSDATA`（SDK 只透传白名单环境变量），
所以取字幕的凭据必须走 `~/.bilibili-mcp/config.json` 文件，不能靠环境变量。

### 4. 新增分发工具链

- `INSTALL.md`：使用者 / 维护者**两条路径分开**，维护者路径单独成节
- `scripts/install_mcp.py`：建 venv + 装依赖 + 注册 MCP（自动识别客户端，备份原配置）
- `scripts/check_setup.py`：五项自检，**真起 stdio server 握手**（不 mock）
- 清理仓库内硬编码的本地绝对路径（`D:\...`），改为与环境无关的表述

---

## 2026-10-19 · v0.3 意图二分类 · 评论预算 15 · 广扫判图 · 经验库

**类型**：方法论修订 + 预算调整 + 新资产类型

**起因**：v0.2 首次真跑（「普通人如何投资」）后用户提了五条意见，其中四条改变了设计。

### 1. 议题意图二分类（用户提出，比我原来的说法准确）

我原来笼统写"加议题类型分支"，含义模糊。用户指出要区分两件事：

| | 是什么 | 要不要问用户 |
|---|---|---|
| **议题意图**（态度 / 方法） | 这次要 what+why 还是 how | **必须问**，不能猜 |
| **阵营划分** | 检索阶段把视频/观点分组 | 不用问，态度类的必然产物 |

采纳。`SKILL.md` 阶段 0 新增「意图类型」为第五项产出，并附反问话术；
新增两类判据的方向对照表（**点赞率在态度类是反指标、在方法类是正指标**）；
阶段 3 新增「阵营表」，并把"占据什么位置"定为该表的唯一价值所在。

> 两件事分开之后，"分支"就不再是给议题贴标签，而是**决定判据方向的前提**——
> 猜错会让整套流程朝反方向优化，所以它必须由用户拍板。

### 2. 评论预算 8 → 15

8 条只能看到主流声音，**看不到排在第 9–15 位的高赞反方**。而反方恰恰是态度类议题的主证据。
成本几乎为零（同一次调用内，一页 30 条），所以直接改默认值。

### 3. 广扫阶段判图（用户要求）

用户指出：若热评里有图（纯图或图文），都应该判——高热度本身代表价值。
采纳，但用**低成本形态**：

| | 深挖 `get_comment_images` | 广扫 `scan_topic` 内置 |
|---|---|---|
| 触发 | `picture_verdict != skip` | `image_policy="hot"`：热评有图就判 |
| 尺寸 | 320px / 5 列 / 最多 20 张 | **160px / 3 列 / 最多 6 张** |
| 用途 | 读数字、看内容 | **只判类型**（梗图/截图/图表/照片） |
| 像素量 | 1636×1406 | **504×382（约 1/12）** |

`media.py` 新增 `micro_sheet()`、`contact_sheet()` 加 `force` 参数（跳阈值闸门）。
顺手修掉一个 bug：`_build_sheets()` 原先按 `per_sheet=20` 预留画布行数，
微表只填 6 格却画出 7 行高，**下面 5 行全空白**——视觉上没坏，但白占 4 倍像素。

### 4. 经验库 `insights.md`（用户："越用越实用"）

新增第四类资产。与另外三个的区别：

| 文件 | 存什么 | 回答 |
|---|---|---|
| `sources.json` / `videos.json` | 结构化记录 | 这个 UP/视频怎么样 |
| `topics/` | 单议题简报 | 这次查到了什么 |
| **`insights.md`** | **跨议题认知** | **下次能省什么** |

三类条目：`[领域]` 事实理解 / `[信源]` 谁在什么问题上可信及为什么 / `[方法]` 流程哪里失准。
写入标准一句话：**它对下一个议题有用吗？不会就别写。**

配套：`registry_get(kind="insights")`、`registry_upsert(kind="insight", topic=...)`；
`stats()` 返回 `insights_kb` 与超过 64 KB 的压缩提醒（只增不压的经验库会烂掉）。
**读取刻意不做成按议题过滤**——跨领域的经验（如"态度类判据方向"）往往比同议题结论更有用。

### 5. 两条用户决定（记录在案，不再讨论）

- **不做跨议题的 UP 主横向对比**：简报目的是加深对单一议题的理解，不是评估 UP。
- **项目不改名**：保留 `bilibili`，商标风险用 README 的「非官方、无关联」声明承担。

**怎么验证的**

| 用例 | 结果 |
|---|---|
| `python -m big.cli scan ... --comment-n 15 --image-policy hot` | 4 条候选读满 15 条热评；3 条候选自动生成微表（6/5/2 张），其余 0 请求 |
| 微表尺寸 | 504×382；6 格类型全部可辨（大字梗图可读，截图内容不可读——符合设计定位） |
| MCP 协议自检 | 仍 9 个工具，`registry_get(kind="insights")` 待首条写入后验证 |

---

## 2026-10-19 · v0.2 从 skill 升级为 MCP 项目，接通弹幕与评论图片

**类型**：架构变更 + 两条数据通道接线 + 开源准备

**用户拍板的四件事**

| 问题 | 决定 |
|---|---|
| 产物形态 | **做成 MCP** |
| 任务规模上限 | 由我定（用户不知道 `search_bilibili_videos` 的参数含义）→ 定为每词 30、候选 40、评论预算 12 |
| 资产积累 | **必须** |
| 「绝不能漏」的议题类型 | 暂时没有 |

**代码从 skill 迁到独立项目**：独立的项目仓库（git 仓库待初始化），
skill 目录改为指向它的 junction（Windows 上 `ln -s` 在 Git Bash 里会退化成空目录，
必须用 `mklink /J`，且用 Python `shell=True` 传参才不被 MSYS 转义）。

**一个实测发现，直接改变了流程形态**

`/x/web-interface/wbi/search/type` 返回的每条记录**自带完整元数据**：
`aid` / `mid` / `play` / `video_review`（弹幕数）/ `review`（评论数）/ `favorites` /
`like` / `pubdate` / `duration` / `tag` / `description` 全都有，且 `page_size` 上限是 **50**。

| | 原设计 | 现在 |
|---|---|---|
| 元数据 | 每条候选 1 次调用 | **0 次**（随搜索返回） |
| 20 条候选的定性 | 约 60–90 次调用 | 约 13 次（4 次搜索 + 12 条评论预算） |
| 单次搜索覆盖 | 10 条 | **50 条** |

⚠️ 上一版文档写的「一次最多 10 条」来自某个 MCP 工具的 **JSON schema 限制**，
不是上游限制。已在 `references/mcp-tools.md` 更正——这条错误认知曾直接决定了
「关键词组数量 = 样本量上限」这个设计前提，现在前提松了一层。

**新增/重写的代码**

| 文件 | 作用 |
|---|---|
| `src/big/http.py` | WBI 签名（缓存 6 小时）、限速、BV 解析、凭据加载 |
| `src/big/video.py` | 元数据 / 标签 / 字幕（人工 CC 优先） |
| `src/big/comments.py` | 游标分页 + 图片/表情/等级/IP 属地保留 + 结构指标 |
| `src/big/danmaku.py` | 密度曲线 / 高重复句 / 披露关键词（raw deflate 的坑保留） |
| `src/big/media.py` | 阈值判读 → 并行下载 → 联系表（带编号） |
| `src/big/search.py` | 搜索 + **`scan_topic` 一站式广扫** |
| `src/big/registry.py` | 信源库 / 视频索引（原子写 + 字段校验 + 评级变动检测） |
| `src/big/mcp_server.py` | 9 个 MCP 工具 |
| `src/big/cli.py` | `big setup` / `scan` / `video` / `registry` |

**弹幕与评论图片怎么接进流程**（这是用户明确要求的）

不再只是"脚本在那儿放着"，而是写进 `SKILL.md` 的阶段 2 与硬规则：

1. **弹幕**：只在「延伸发散」与「评论区结构性偏差」两处用，**不单开一章**；
   明令**不得当作"多数人意见"**（弹幕是即时反应、发弹幕的人和看视频的人不是同一批）。
2. **评论区图片**：判据固定为「图片评论占总赞」（不是含图条数）；样本不足 100 条不判读；
   联系表只用于定位，**数字与结论只认原图**。
3. 两条通道都加进硬规则第 11 条，避免以后被当成"可有可无的附加项"。

**踩到的坑（会再踩，先记下）**

| 坑 | 现象 | 处置 |
|---|---|---|
| `mcp` 2.x 把 `FastMCP` 改名为 `MCPServer` | `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`，但报错文本自己说明了改名 | 双版本 shim，两个都能跑 |
| Git Bash `ln -s` 建目录链接 | 静默退化成空目录（`ls -la` 显示是普通目录） | 改用 `mklink /J`，且经 Python `shell=True` 调用 |
| `subprocess.run(['cmd','/c','mklink','/J',...])` | `无效参数 - "Users"`：`/c` 之后的路径被 MSYS 折成 `/Users` | 用 `shell=True` + 完整引号字符串 |

**怎么验证的**

| 用例 | 结果 |
|---|---|
| `big setup` | 依赖 OK / 凭据已配置 / WBI 签名 OK / 搜索 OK，全绿 |
| MCP 协议级自检（真实 stdio，非 mock） | `tools=9`；`registry_get` 正常返回 2 源 2 视频 |
| 搜索接口 | `page_size=50` 实回 50 条；`numResults=1000`（封顶值） |
| `scan_topic` | 见下一节实跑记录 |

**资产库迁移**：旧的 `.registry`（2 条信源 + 2 条视频）已复制进项目 `.registry/`，
原位置（旧的 B站视频总结目录）保留不动。默认目录改为项目内
（已被 `.gitignore` 排除），可用 `BIG_REGISTRY_DIR` 覆盖。

**已知缺口（未解决）**

| 缺口 | 影响 | 优先级 |
|---|---|---|
| 楼中楼每层只取 3 条 | 深层争论看不到（`reply_intensity` 用真值 `rcount`，不受影响） | 中 |
| 弹幕发送者不可识别 | 只能做内容启发式 | 低（已证明做不到） |
| 无页面级缓存 | 同一议题二次扫描会重复打接口 | 中 |
| 议题简报尚未落盘为文件 | `topics/` 目录还是空的 | 低 |

---

## 2026-10-17 · 初始版本（v0.1）

**类型**：新建 skill

**为什么做这个 skill**

用户提出：B站是有价值的信息获取渠道，现有的「视频分析总结」skill 是**站在视频读者角度的高效率视频数据分析**，
但「从海量视频中提取关键信息」是另一个场景——**需要广度效率优先，先定性再深挖，且不产出笔记**。

**与精读 skill 的分工**

| | bilibili-video-analysis-summary | **bilibili-info-gathering（本 skill）** |
|---|---|---|
| 输入 | 一个 BV 号 | 一个议题/问题 |
| 单位 | 视频 | 议题 |
| 核心动作 | 拆解、纠偏、挖评论区 | **筛选、收敛、交叉验证** |
| 产物 | 可视化 HTML 报告 + PDF | 对话内结论 + 信源地图（可选 markdown 简报） |

**方法论来源（本轮调研）**

| 框架 | 出处 | 用在哪 |
|---|---|---|
| Rapid Evidence Assessment（REA） | 英国 GSR 方法指南 / Cochrane 快速综述指引 / PRISMA | 三阶段漏斗、检索协议、纳入排除标准 |
| Admiralty / NATO 情报评级 | NATO 联合情报条令；英国海军情报处（二战） | 双轴分级卡 |
| 信息觅食理论 | Pirolli & Card, 1999（Xerox PARC） | 离场规则、预算管理 |
| SIFT / 侧读 | Mike Caulfield；斯坦福历史教育组 | 侧读动作、追一手来源 |

**文件清单**

| 文件 | 作用 |
|---|---|
| `SKILL.md` | 流程 + 触发边界 + 维护入口 |
| `references/search-protocol.md` | 阶段 0：核心问题拆解 + 关键词族五类 + 纳入排除标准 + 预算 |
| `references/source-grading.md` | 双轴分级卡（来源 A–F × 可用度 1–4）+ 冗余度标记 + 评论区特例 |
| `references/exit-rules.md` | 离场规则三档 + 预算参考 + 三种失效模式 + 样本偏差 |
| `references/briefing-template.md` | 对话内输出格式 + markdown 简报模板 + 写作规则 |
| `references/registry.md` | 信源库/视频索引/议题存档的数据结构与维护规则 |
| `references/mcp-tools.md` | 工具速查（含 `search` 的 10 条硬约束、字幕省 token 用法） |
| `references/troubleshooting.md` | 流程类故障 10 问 |
| `scripts/registry.py` | 信源库读写工具（原子写 + 字段校验 + 评级变动检测） |
| `scripts/check_setup.py` | 自检（转发精读 skill + 检查资产库） |
| `scripts/fetch_video.py` | 取数脚本（转发精读 skill，避免两份实现分叉） |

**关键设计决策**

1. **阶段 1 不拉字幕。** 这是整个流程能"广"的唯一原因——20–30 条候选里，20+ 条只花 2 次调用，3–6 条花 10 倍精力。
2. **双轴独立评级。** 单一"好/坏"排序无法区分「大 UP 跨领域发言」（A4）和「无名用户贴出已证实的一手数据」（F1）。
3. **加冗余度标记（第三个维度）。** B站的搬运/二创生态导致复述内容远多于论文库——**"10 条互相复述"和"1 条独家 + 9 条复述"是完全不同的证据强度**，不标出来用户会误以为"这么多人都在说 = 可信"。
4. **离场规则写死。** 没有离场规则的"广度优先"会退化成"一直搜下去"。
5. **资产积累型。** 信源库（哪个 UP 主在哪个领域靠谱）是**跨任务复用**的先验，是本 skill 最值钱的资产。
6. **共用基础设施用转发而非复制。** `fetch_video.py` / `check_setup.py` 转发到精读 skill——两份实现必然分叉，改了一边忘另一边会很难查。

**怎么验证的**

1. `python scripts/check_setup.py` → 取数基础设施全绿；资产库检查正常
2. `registry.py` 全流程实测：
   - `init` / `upsert-source` / `upsert-video` / `get-*` / `list-*` / `stats` 全部通过
   - **评级变动检测**：重复写入同一 UP 主且评级不同 → 自动追加 `conflicts` 记录（实测 A→B 被正确捕获）
   - **字段名校验**：写入含未知字段的记录被拦下（防止字段名写错后永久留存）
   - 已用本次会话真实分析过的 2 条视频（KVMem A1 / 段子 D4）入库作为初始数据

**已知缺口**

| 缺口 | 影响 | 优先级 |
|---|---|---|
| **弹幕** | B站独有的**秒级**群体反应信号完全没用上（弹幕密度可定位信息峰值）。MCP 与 `fetch_video.py` 都不支持，需另写 protobuf 分段接口 | 高 |
| 搜索结果总数 | 无法知道"一共多少条相关视频"，只能靠多加关键词组逼近 | 中 |
| 批量元数据 | 20+ 条候选逐个调用较慢，未做并发/缓存 | 中 |

**未做（明确记下，避免以后误以为已实现）**

- 弹幕抓取
- 信源库的进阶分析（议题关联图、可信度时间演化、关键词有效性统计）——**数据量不够时全是噪声，先跑 20–30 次任务再看**
- 跨平台信源（知乎/公众号）——需要先有跨平台取数能力
