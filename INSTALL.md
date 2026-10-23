# 安装与首次使用

> 本文档同时写给**人**和**AI Agent**。把整个仓库目录放进 Agent 的 skills 目录后，
> AI 会自动读取本文件的内容来完成引导——你也可以自己照着走。
>
> **第二节写给只想用的人。第三节写给要改这份代码的人。** 别读错节。

---

## 〇、这是什么 / 不是什么（先看这个，免得后面看混）

| | 是什么 | 在哪 | 谁维护 |
|---|---|---|---|
| **本仓库的 `SKILL.md`** | 一个 **Agent Skill**：给 Agent 读的**判断层**（三阶段流程、双轴评级、离场规则、冗余度） | 装在 Agent 的 **skills 目录** | 本仓库 |
| **本仓库的 `src/big/`** | 一个 **MCP server**（stdio）+ 一套 CLI：**取数层**（搜索、评论、字幕、弹幕、图片、资产库） | 跑在本仓库自己的 **venv** 里 | 本仓库 |
| **姊妹项目** | `bilibili-video-analysis-summary`：**单条视频精读**，本仓库是**议题级广扫** | 另一个仓库 | 同一个作者 |

**一句话**：**skill 是给 Agent 读的判断，MCP 是它调用的取数服务——两个都在本仓库里，
装一次就都有。**

和姊妹项目的关键差别：

| | 本仓库 | 姊妹项目 |
|---|---|---|
| MCP 代码 | **自带**（`src/big/mcp_server.py`，14 个工具） | **不含** MCP 代码，只含对第三方 MCP 的补丁 |
| 面向的问题 | 「B站上关于 **X** 有什么」（一个**议题**） | 「帮我分析**这条**视频」（一个 **BV 号**） |
| 产物 | 结论 + 信源地图 | 可视化 HTML 报告 + PDF |

> 判断口诀：**给了 BV 号 → 姊妹项目；给了问题 → 本项目。**
> 两者的 MCP 工具名不冲突，可以同时装着。

---

## 一、前置要求

| 项 | 要求 | 说明 |
|---|---|---|
| Agent 客户端 | 支持 `SKILL.md` 规范 **且** 支持 stdio MCP | MiniMax Code / WorkBuddy / Claude Code 等（按通用规范编写，不绑定客户端） |
| Python | **3.10+** | 取数核心只用标准库（`urllib`）；MCP SDK 与 Pillow 才需要 pip 装 |
| B站账号 | 可选 | **仅取字幕**时需要（手机 App 扫码登录拿 SESSDATA） |
| Pillow | 可选 | **仅**「评论区图片联系表」需要（`get_comment_images`） |

> 不装 Pillow 也能用：14 个工具里只有 `get_comment_images` 受影响。

---

## 二、使用者：装上就能用

> **这一节面向只想用的人。装完即用，不需要理解"真源/副本"这类概念。**

### 2.1 装本体

**方式一：git clone（推荐，能随时更新）**

```bash
# 把 <skills 目录> 换成你客户端的对应位置
git clone https://github.com/DavidLearning1023/bilibili-info-gathering.git \
  <skills 目录>/bilibili-info-gathering
```

> ⚠️ **现状（2026-10-21）**：本仓库**尚未公开到 GitHub**，`git clone` 这条路暂时走不通。
> 现阶段装本体用**方式二**（把整个文件夹放进 skills 目录），或者更好：用 **junction 直连**
> 项目所在目录（见 §3.2）——不用复制，改动也零漂移。

**方式二：下载 zip**，解压后把整个文件夹放进同样的位置。

| 客户端 | skills 目录 | MCP 配置文件 |
|---|---|---|
| **MiniMax Code** | `~/.minimax/skills/` | `~/.minimax/mcp.json` |
| **WorkBuddy** | `~/.workbuddy/skills/` | `~/.workbuddy/mcp.json` |
| **Claude Code** | `~/.claude/skills/` | `~/.claude.json`（或项目内 `.mcp.json`） |
| 其他 | 见对应客户端文档（"技能 / Skills"的存放目录） | 见对应客户端文档（MCP servers 配置） |

> Windows 下 `~` 即 `C:\Users\<你的用户名>`；macOS / Linux 同理。

### 2.2 注册 MCP（一条命令）

装完本体后，**进入那个目录**再跑：

```bash
cd <skills 目录>/bilibili-info-gathering
python scripts/install_mcp.py
```

它会自动完成四件事：

1. 在项目目录下建 `.venv`（不存在才建）
2. `pip install -e ".[images]"` 装依赖
3. 把 MCP server 写进你客户端的 MCP 配置（**先自动备份**，同名条目覆盖不重复）
4. 跑一遍自检，把结果打出来

常用参数：

```bash
python scripts/install_mcp.py --client minimax   # 只装 MiniMax Code
python scripts/install_mcp.py --client all       # 装所有检测到的客户端
python scripts/install_mcp.py --print            # 只打印配置 JSON，自己手动贴
python scripts/install_mcp.py --check            # 只看现状，不改任何文件
```

> **不想用脚本？** `--print` 会打出下面这段 JSON，手动贴进 MCP 配置即可：
>
> ```json
> {
>   "mcpServers": {
>     "bilibili-info-gathering": {
>       "type": "stdio",
>       "command": "<装好的目录>/.venv/Scripts/python.exe",
>       "args": ["-m", "big.mcp_server"],
>       "env": {
>         "BIG_OUT_DIR": "<装好的目录>/.bigout",
>         "BIG_REGISTRY_DIR": "<装好的目录>/.registry",
>         "PYTHONPATH": "<装好的目录>/src",
>         "PYTHONIOENCODING": "utf-8",
>         "PYTHONUNBUFFERED": "1"
>       }
>     }
>   }
> }
> ```
>
> macOS / Linux 上 `command` 用 `.venv/bin/python`。
> **`env` 那五个变量别删**——原因见第四节。

### 2.3 装完即用

1. **重启或重连一次客户端**（MCP 是 stdio 子进程，**不热加载**，这一步最容易漏）
2. 在对话里发一个**议题**：

```
B站上关于「普通人如何投资」有什么
```

3. Agent 会先查资产库、再广扫、再定向深挖，最后给你**信源地图 + 结论**。

**两种模式用措辞切换**（详见 README「两种用法」）：

```
B站上关于「普通人如何投资」有什么          → 深入：10–20 分钟，md + HTML + PDF
帮我简要了解一下 B站上「普通人如何投资」    → 简要：3–5 分钟，只出 md
```

> 不需要回答"你要哪一种"——**措辞就是答案**。想切换随时重说一句即可。

### 2.4 可选：取字幕需要登录态

只有**取字幕**（`get_transcript`）需要登录。元数据、评论、弹幕、搜索**都不需要**。

把 SESSDATA 写进 `~/.bilibili-mcp/config.json`：

```json
{ "sessdata": "你的 SESSDATA 值" }
```

怎么拿：浏览器登录 B站 → F12 → Network → 任意请求的 Cookie 里找 `SESSDATA`。

> ⚠️ **不要用环境变量 `BILI_SESSDATA` 配 MCP**。MCP 客户端启动子进程时只透传
> 一小部分环境变量（`PATH` / `USERPROFILE` / `SYSTEMROOT` 等白名单），
> `BILI_SESSDATA` **不会**被继承。**必须用上面的配置文件方式。**
> （CLI 直接在终端跑时环境变量是有效的，但 MCP 场景不行。）

### 2.5 更新

```bash
cd <skills 目录>/bilibili-info-gathering
git pull          # zip 用户：重新下载并覆盖
```

> 更新后**重启客户端**。
>
> ⚠️ **本项目目录当前不是 git 仓库**（无 `.git`），所以 `git pull` 不适用。
> 用 junction 直连的用户**根本不需要"更新"这一步**——链接指向的就是你正在开发的那个目录，
> 改完跑一次 `python scripts/check_setup.py` 即可；但 **MCP 子进程不热加载**，
> 改的是 `src/big/` 下的代码时，仍须重启客户端才会生效。

### 2.6 可选：本地 ASR（视频没有字幕时的第三条路）

只有当视频**没有字幕**、或 **AI 字幕差到不能作为依据**时，才需要本地转写。
**绝大多数议题用不到**——先确认真的需要再装。

四种装法：

| 装法 | 适合谁 | 成本 |
|---|---|---|
| ① 复用已有的转写 skill | 机器上已经装了 `local-audio-transcription` 之类 | 0 |
| ② `pip install faster-whisper` 装进本项目 venv | 只想试一次 | ~200 MB |
| ③ **共享 ASR 运行时** | **多个项目共用一份**（推荐） | ~1.2 GB |
| ④ `BIG_ASR_CMD` 指向你自己的命令 | 已有转写工具 | 0 |

#### ③ 共享 ASR 运行时（推荐）

```bash
# 1. 建独立 venv —— base 用你自己的 Python，不要用某个产品的受管目录
python -m venv ~/.local/asr

# 2. 装包 —— ⚠️ av 必须 <19
~/.local/asr/Scripts/pip install faster-whisper "av<19" nvidia-cublas-cu12

# 3. 放入口脚本
cp scripts/asr_transcribe.py ~/.local/asr/

# 4. 锁版本（很重要：以后升级只改 lock，不在环境里直接 pip install -U）
~/.local/asr/Scripts/pip freeze > ~/.local/asr/requirements.lock.txt
```

装完**不用配任何环境变量**——工具会按约定位置自动找到它：
`BIG_ASR_HOME` → `~/.local/asr` → `~/.local/share/asr` → `%LOCALAPPDATA%\asr`。
判据是**三信号齐**（解释器 + 入口脚本 + site-packages 里真的有 `faster_whisper`），
缺一不认，避免"目录建了但没装包"的半成品在真正转写时才炸。

> ⚠️ **`av` 必须锁 `<19`。** PyAV 19 移除了 `av.open()` 的 `metadata_errors` 参数，
> 而 faster-whisper 1.2.1 仍在传它 → 一解码就 `TypeError` → 转写全废。
> **而且是运行时才炸**：装包、import、模型加载、GPU 识别全都正常。

> ⚠️ **没有 NVIDIA 卡就跳过 `nvidia-cublas-cu12`**（省 736 MB），只是转写会慢很多。
> **cuDNN 不用装**——实测不需要（ctranslate2 自带转发桩，faster-whisper 用不到重引擎）。

> 💡 **模型不装**：首次运行从 HuggingFace 下载（`large-v3-turbo` 约 1.6 GB），
> 下过之后缓存在 `~/.cache/huggingface`，换环境照样命中，**不重复下载**。

> 💡 **为什么值得单独建一个**：受管 venv（如 `~/.workbuddy/...`）被产品升级/重装清掉时，
> 转写能力会跟着消失。放在自己的目录里就不会——而且多个项目能共用一份。

---

### 2.7 可选：报告的 HTML / PDF（深入模式默认，简要模式按需）

**简要模式默认只出 md**；只有你明确要 HTML/PDF 时才需要这一节。

```bash
cd <skills 目录>/bilibili-info-gathering
python -m pip install -e ".[docs]"
python -m playwright install chromium-headless-shell   # 只在要 PDF 时跑
```

用法：

```bash
# ① 先算落盘路径（日期 + 模式 + 议题，别手拼）
python -m big.cli report-path --topic "<议题>" --mode deep --mkdir
# 产物目录：分析报告库/<YYYYMMDD>-深入分析-<议题>/

# ② 写完 md 再渲染
python scripts/render_briefing.py --md "<报告>.md" --pdf
# 产物：同目录 .html（单文件、无外部依赖）+ .pdf（A4）
python scripts/render_briefing.py --md "<报告>.md" --check   # 交付前结构自检
```

| 情况 | 结果 |
|---|---|
| 没装 `markdown` | HTML 出不来，脚本直接报错——它是**硬依赖** |
| 没装 `playwright` | **HTML 照出**，PDF 跳过并打印安装指引 |
| 没跑 `playwright install` | 同上 |
| md 写法不合规 | `--check` 会指出硬错（章节顺序 / 阵营表位置 / 缺单轮开销 / 标题与目录模式不符）与提醒（缺上下文隔离声明 / 缺 6.1 子节 / 同一 BV 跨报告评级不一致） |

> **为什么 PDF 是可选的**：HTML 单文件已能满足阅读与分享，PDF 只是印刷/存档形态。
> 缺 PDF 不该挡住整条交付链——所以脚本在缺 playwright 时**降级而不是失败**。

> **版式固定在 `assets/template/briefing.css`**，模型只写 md。
> 要调样式改那个文件，**不要改生成的 HTML**（下次渲染会覆盖）。
> 这样做的三个好处：CSS 不进每次的输出 token（每篇省 4–5k）、版式不会漂移、
> md/HTML/PDF 三者天然一致。

---

## 三、维护者：在项目目录里开发，再部署到各 Agent

> **这一节只写给要改这份代码的人**（也就是本仓库的维护者）。普通使用者跳过。

### 3.1 为什么需要这层

```
真源      项目仓库目录（git 仓库）              <- 唯一的编辑目标
             │  scripts/install_mcp.py --link-skill
             ↓
部署副本   ~/.minimax/skills/<name>/     MiniMax Code 读这里
           ~/.workbuddy/skills/<name>/    WorkBuddy 读这里
```

| | 在哪 | 谁写它 |
|---|---|---|
| **真源** | 项目仓库目录（**当前非 git 仓库**，开源后再接 GitHub 远端） | **你 / Agent**，所有改动落这里 |
| **部署副本** | 各客户端 skills 目录下的同名文件夹 | 链接指向真源，或由脚本覆盖 |

**为什么不能从 GitHub 直接拉副本**：Agent 直接接管项目文件夹后，**本地项目目录才是最新的**
（含还没提交的改动）。从 GitHub 同步会把本地已做但未推送的改动**悄悄回滚掉**——
典型的静默错误。方向永远是 **本地真源 → 部署副本**。

### 3.2 接法一：junction / 符号链接直连（零漂移 · 推荐）

让客户端的 skills 目录**直接指向真源**，客户端读到的就是项目目录本体——
**结构上不可能漂移**，改了立刻生效：

```powershell
# Windows
New-Item -ItemType Junction `
  -Path "$env:USERPROFILE\.minimax\skills\bilibili-info-gathering" `
  -Target "<你的项目目录>"
```

```bash
# macOS / Linux
ln -s ~/projects/bilibili-info-gathering \
      ~/.claude/skills/bilibili-info-gathering
```

或者直接让脚本做：

```bash
python scripts/install_mcp.py --link-skill
```

> ⚠️ Windows 上**必须**用 Junction（`mklink /J`）：Git Bash 的 `ln -s` 会**退化成
> 一个空目录**，不报错，只是链接失效。
>
> ⚠️ junction 的代价：真源目录一旦移动或改名，链接就断（重建即可）。

### 3.3 接法二：拷贝部署

不想用链接（或者客户端不支持）：

```bash
python scripts/install_mcp.py --client all   # 装依赖 + 注册 MCP
# 拷贝由你自己的同步脚本负责（本仓库不含同步脚本，见姊妹项目）
```

### 3.4 什么时候跑

| 时机 | 命令 |
|---|---|
| **动手改之前**（确认环境是好的） | `python scripts/check_setup.py` |
| **改完代码之后** | `python scripts/check_setup.py` + `python tests/test_mcp_smoke.py` |
| **改了依赖之后** | `python scripts/install_mcp.py` |
| **客户端加载的还是旧规则** | 同步后**重启/重连客户端** |

改完记得在 `references/CHANGELOG.md` **追加一条**（最新在最上面）。

---

## 四、配置详解：`env` 里那五个变量

它们不是可选项，少一个就会出**静默**问题：

| 变量 | 不设会怎样 |
|---|---|
| `BIG_OUT_DIR` | 图片联系表写到 **cwd 里的 `.bigout`**。MCP 客户端启动子进程时**未必带 cwd**（配置层通常只有 `command`/`args`/`env`），cwd 一变，文件就散到别处去了 |
| `BIG_REGISTRY_DIR` | 资产库同理。`registry.py` 本来用 `__file__` 推项目根（不受 cwd 影响），但显式设置更保险，换目录也方便 |
| `PYTHONPATH` | editable 安装坏掉时仍能 `import big` |
| `PYTHONIOENCODING=utf-8` | MCP 客户端按 UTF-8 解码；Windows 下 stdout 走管道时用系统 ANSI 代码页(GBK)，不设会乱码，碰上生僻字符还会抛 `UnicodeEncodeError` **让 server 静默死掉** |
| `PYTHONUNBUFFERED=1` | stdout 被管道接走时是块缓冲，不设会延迟响应，表现为工具调用"卡住不动" |

> 这五条是**实测踩出来的**，不是防御性编程。
> `scripts/check_setup.py` 里有一项专门验证「换一个完全无关的 cwd 启动，目录仍然落在项目内」。

---

## 五、自检

```bash
python scripts/check_setup.py
```

它会跑五项，**真起一个 stdio server 握手**（不 mock）：

| 项 | 检查什么 |
|---|---|
| 1 依赖 | Python / mcp / Pillow / `big` 包是否指向本仓库 |
| 2 目录锚定 | 换个无关 cwd 启动，图片与资产库是否仍落在项目内 |
| 3 MCP 握手 | initialize → tools/list（应正好 14 个）→ tools/call（含一次真实联网搜索） |
| 4 资产库 | 信源库 / 视频索引 / 经验库是否就位 |
| 5 凭据 | 登录态是否配置（只影响取字幕） |

输出解读：

| 结果 | 含义 | 处理 |
|---|---|---|
| 全 `[OK]` | 就绪 | 直接开始用 |
| `[FAIL] 缺少工具` | server 起来了但工具不全 | 跑 `python tests/test_mcp_smoke.py` 看详情 |
| `[FAIL] MCP 握手失败` | server 起不来 | 手动 `python -m big.mcp_server` 看报错 |
| `[FAIL] 目录跑到了项目外` | `env` 没配全 | 回到第四节 |
| `[WARN] 未配置登录态` | 只影响取字幕 | 见 2.4 |
| `[WARN] 缺少 Pillow` | 只影响图片联系表 | `pip install -e ".[images]"` |

退出码：`0` = 没有 FAIL；`1` = 有 FAIL。

> 脚本**不会**因为输出编码问题而误报失败——这是 Windows 管道下的真实坑，
> 脚本内部已做防护（管道下强制 UTF-8）。

---

## 六、与上游 `@xzxzzx/bilibili-mcp` 的关系

两者**并存、不冲突**，但用途不同：

| | 本仓库的 MCP | 上游 `bilibili-mcp` |
|---|---|---|
| 定位 | 议题级广扫 + 资产积累 | 单条视频精读 |
| 优势 | 一次调用拿 40 条候选；弹幕；评论图；信源库 | ASR 转写、AI 摘要、UP 主历史、收藏夹 |
| 搜索上限 | 50/次 | 10/次（schema 限制，非上游限制） |

**单条视频精读继续用上游那个**；本仓库一律用本仓库的 MCP。
14 个工具名与上游**没有重名**，可以同时装。

---

## 七、故障速查

| 症状 | 大概率原因 | 处理 |
|---|---|---|
| 客户端里看不到工具 | MCP server 没被 Trust，或没重启客户端 | 信任该 server；重启客户端 |
| 工具调用报 `ModuleNotFoundError: big` | editable 安装失效 | `pip install -e ".[images]"` |
| 工具调用卡住不动 | stdout 缓冲 | 确认 `PYTHONUNBUFFERED=1` 在 |
| 输出乱码 / server 静默死 | 编码 | 确认 `PYTHONIOENCODING=utf-8` 在 |
| 图片散落在奇怪目录 | `BIG_OUT_DIR` 没设 | 回到第四节 |
| 拿不到字幕但其它功能正常 | 没登录 / 登录态失效 | 见 2.4；`check_setup.py` 会报 |
| HTTP 412 | IP 级风控 | 降频（`BIG_RATE_LIMIT_MS`）或等几分钟；换 cookie 没用 |
| 搜出来的结果大量跑题 | 关键词族没覆盖全 | 见 `references/search-protocol.md` |
| `transcribe_audio` 报 `metadata_errors` | PyAV 升到了 19 | `pip install "av<19"`（见 2.6） |
| `transcribe_audio` 报 `cublas64_12.dll not found` | 环境缺 nvidia CUDA 运行库 | 装 `nvidia-cublas-cu12`；或用 `BIG_ASR_CMD` 指向能用的解释器 |
| `transcribe_audio` 报「BIG_ASR_CMD 指向的命令不存在」 | 路径带引号/带空格 | v1.0.0 起已能正确解析；旧版请升级，或把路径改成不带空格 |

更细的排障见 `references/troubleshooting.md`。

---

## 八、隐私与合规

- 本工具只访问**公开可见**内容：视频元数据、公开评论、公开弹幕、公开字幕。
  不绕过付费、大会员、地区限制或私密内容——读不到就是读不到。
- 默认**不内置任何凭据**。凭据由你自己提供（`~/.bilibili-mcp/config.json`），
  项目不写、不缓存、不上传。
- 评论里的 **IP 属地、昵称、mid 属个人信息**。默认只做聚合统计；
  产物若要对外分享，请去掉 IP 属地分布这类字段。
- `.registry/` 存的是**你自己的**使用积累（信源评级、议题历史、调用账本），
  已在 `.gitignore` 里，**不要提交**。
- `分析报告库/` 存的是**你生成的报告**（md/HTML/PDF），同样已在 `.gitignore` 里。
  它是给你自己找文件用的中文目录名——**不是项目的一部分**，分享报告时请单独拷文件。
- 请求默认限速 400ms（`BIG_RATE_LIMIT_MS`）。**请勿用于商业用途或大规模抓取。**

**免责声明**：本项目与哔哩哔哩无任何关联，非官方工具。仅供个人研究与学习使用。
相关风险分析见 [RISK.md](RISK.md)。

---

## License

MIT。见 [LICENSE](LICENSE)。
