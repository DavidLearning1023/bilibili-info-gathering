<p align="center">
  <a href="CONTRIBUTING.md"><b>简体中文</b></a> · <a href="CONTRIBUTING.en.md">English</a>
</p>

# 参与贡献

**English version: [CONTRIBUTING.en.md](CONTRIBUTING.en.md)**

感谢你愿意花时间。这个项目**只有一个人维护**，所以下面几条能帮你省掉来回：
**先看「该往哪儿提」——不同类型的贡献，走的路完全不同。**

> **不要求 CLA / DCO 签署。** 个人维护项目，流程越简单越好。
> 唯一的硬要求是：改动有据、测试全绿、变更记录写清楚。

## 该往哪儿提

| 你要做的事 | 走哪条路 |
|---|---|
| **改方法论判据**（双轴评级、离场规则、观点分布…） | **先开 Issue 讨论**，别直接改 PR |
| **修一个 bug**（取数出错、报告渲染不对） | 直接开 Issue 或 PR，**附复现步骤** |
| **补文档 / 翻译 / 改错别字** | 直接 PR（改 `references/` 里的**说明性文字**可以；改**判据**要先讨论） |
| **加一个工具 / 改取数逻辑** | 先开 Issue 说清**动机**与**成本**（会多几次上游请求吗？） |
| **报告安全问题** | **不要开公开 Issue** ⇒ 见 [SECURITY.md](SECURITY.md) |
| **想要某个议题类型的方法论** | 开 Issue 描述议题类型与你的预期 |

> **为什么方法论要讨论先行**：判据写在 `references/` 里，是 Agent 判断的依据。
> 改一个阈值可能让另一个判据失效——这类改动需要放在同一张桌子上看。
> 讨论的成本，远低于事后回滚。

**本仓库没有开 Discussions**，所以走 Issue + PR 两条路就行。

## 开发环境

```bash
git clone https://github.com/DavidLearning1023/bilibili-info-gathering.git
cd bilibili-info-gathering

python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
# macOS / Linux
# python3 -m venv .venv && source .venv/bin/activate

pip install -e ".[images,docs,dev]"
```

依赖组：`images` = Pillow（评论区联系表）· `docs` = markdown + playwright（HTML / PDF 渲染）·
`dev` = pytest。

**让 Agent 认这个 skill**（改代码时需要，方法见 [INSTALL.md](INSTALL.md) 第三节）：
Windows 用 junction，macOS / Linux 用符号链接，指向仓库根目录。

## 跑测试

```bash
# 离线（不需要网络与登录态）—— 提交前必须过
$env:BIG_SKIP_NETWORK='1'        # macOS / Linux: export BIG_SKIP_NETWORK=1
python -m pytest tests -q
```

当前基线：**112 passed / 3 skipped**。3 个 skipped 是 `tests/test_mcp_smoke.py` 里的
**联网冒烟测试**（需要网络与 B站登录态）。

**联网冒烟**（需要你自己的登录态，**不要在 CI 或任何公开环境跑**）：

```bash
python scripts/check_setup.py
```

它会真起一个 stdio server 握手、核对 14 个工具、再跑一次真实联网搜索。
预期输出：

```
结果: [OK] 17 项  [WARN] 0 项  [FAIL] 0 项
```

## 文档有 lint，改完必须跑

这个项目把「文档说的」和「代码做的」绑在一起，有几条 lint 会直接判失败：

| lint | 锁的是什么 |
|---|---|
| `test_docs_tool_count_is_consistent` | 文档里写的工具数 == `mcp_server.py` 里真实的 `@mcp.tool()` 数（**中英文 README 都算**） |
| `test_docs_no_stale_tool_names` | 文档里不能出现**本项目并不存在**的上游工具名——照着做会调错工具 |
| `test_version_is_consistent_across_pyproject_and_package` | `big.__version__` == `pyproject.toml` 的 `version` |

**所以**：

- 加了 / 删了工具 ⇒ 同步改 README.md、README.en.md、SKILL.md、INSTALL.md、
  `references/mcp-tools.md` 里的**工具数**
- 升版本 ⇒ 同步改 `pyproject.toml` 与 `src/big/__init__.py`

## Commit 规范

用 [Conventional Commits](https://www.conventionalcommits.org/) 的类型前缀，**描述用中文**：

```
feat: 新增 XXX 工具
fix: 修复 XXX 场景下的 YYY
docs: 补充 XXX 判据的说明
refactor: 重构 XXX
test: 补 XXX 的回归测试
release: vX.Y.Z 正式版
```

**一条 commit 只做一件事。** 混在一起的改动以后没法单独回滚。

## 变更记录（CHANGELOG）

**每次改动追加一条到 `references/CHANGELOG.md`，最新在最上面。**
格式：`日期 · 版本 · 批次名`，然后写清「改了什么 / 为什么 / 怎么验证的」。

> **写变更记录时，每一句「我做了什么」都要能机械回查**：
> 提到某个文件里有某个字符串，先 `grep` 定位它在哪一行；
> 要写章节顺序，先 `grep -n '^## '` 读一遍；
> 数字用**被引用文件自己的口径**（本仓库用 KiB）。
>
> 变更记录的价值全在准确——写不准，它就是负资产。
> （这个项目自己在这上面栽过：把「客户端配置里的说明文字」写成「server 代码里的参数」，
> 把「22 KB」写成十进制而仓库用 KiB。都是复核时被抓出来的。）

## 数字口径

本仓库的体积一律按 **KiB**（`round(bytes / 1024)`）。
写「92 KB」之前，先确认对应文件确实是 94,312 字节。

## 不要提交的东西

`.gitignore` 已经挡住了大部分，但请你**再确认一次**：

| 东西 | 为什么 |
|---|---|
| `.registry/` | 你的信源库、议题历史、UP 主评级（个人信息） |
| `分析报告库/` | 你的产出报告（**未做匿名化**，含昵称与 UP 主名） |
| `.bigout/` | 截图、抽帧、下载的临时文件 |
| `.workbuddy/` | Agent 会话工作区（含本机绝对路径） |
| `BILI_SESSDATA` / `cookies.json` / `*.cookie` | 凭据，**任何情况下都不行** |

## 提 PR 之前自查

- [ ] `BIG_SKIP_NETWORK=1 python -m pytest tests -q` 全绿
- [ ] 新增 / 删除工具 ⇒ **所有文档**里的工具数都同步了
- [ ] 升版本 ⇒ `pyproject.toml` 与 `big/__init__.py` 都同步了
- [ ] 追加了 `references/CHANGELOG.md` 条目
- [ ] 没提交凭据、`.registry/`、`分析报告库/`、`.bigout/`
- [ ] 改判据 ⇒ **已经开过 Issue 讨论**（或 PR 里写清了理由）
- [ ] 一条 commit 只做一件事
- [ ] 文档里的锚点没死链（见下）

## 锚点别凭记忆写

改了标题就会改锚点。GitHub 的规则是：转小写 → 去标点 → 空格变连字符 → 中文保留。
**不要靠记忆写 `#锚点`**，改完标题本地校验一次：

```bash
npm i github-slugger        # GitHub 自己用的那个 slug 算法
node -e "const s=require('github-slugger');console.log(s.default('你的标题'))"
```

## 行为准则

- 尊重他人：讨论针对**论点**，不针对人
- 一次提一个话题，方便聚焦
- 接受别人不同意你，并把它当成信息
- 对自己的判断有把握，但也**主动说出它的边界**——这正是本项目的方法论

更完整的社区准则见
[GitHub 社区准则](https://docs.github.com/site-policy/github-terms/github-community-guidelines)。

## 不接受的贡献

- 绕过登录 / 付费 / 地区限制的代码
- 大规模高频抓取（绕过默认限速）
- 把 `references/` 里的判据改成更宽松、但没有证据支撑的版本
- 夹带与本次改动无关的重构（拆成单独的 commit）
