"""本地 ASR：无字幕时的第三条路。

设计
----
转写引擎是**使用者自备**的，不进仓库（模型体积大、平台相关）。
本模块只做三件事：

1. **探测**有没有可用的转写后端
2. **调用**它，把输出规整成与 B站字幕**完全一致**的 transcript 结构
3. 没有后端时，给出**可执行的安装指引**，并明确标注这条视频的字幕缺口
   —— 绝不静默失败（静默失败 = Agent 以为"这视频没讲"，直接下错结论）

后端优先级（**先复用已有工具，再考虑自建**）
-------------------------------------------
① ``BIG_ASR_CMD`` —— 用户显式指定的命令（最高优先级）
② **共享 ASR 运行时** —— 约定目录（``BIG_ASR_HOME`` → ``~/.local/asr`` 等）下
   自建的、含 ``asr_transcribe.py`` 的独立 venv。**多个项目共用一份**，
   好处是脱离任何产品的受管目录，且版本可锁（见 ``scripts/asr_transcribe.py``）。
③ **``local-audio-transcription`` skill** —— 本机已装的转写 skill。
   它的 ``transcribe.py`` **已内置三个坑的修复**（CUDA DLL 路径 / Blackwell 精度 /
   批处理加速），比自己写一遍 in-process 版本可靠得多。
④ Python 包 ``faster_whisper`` —— 装在**当前解释器**里时可用。
   ⚠️ 注意：很多机器上它是装在**另一个 venv**（受管 venv / conda 环境），
   当前解释器 import 不到——这种情况要走 ② 或 ③，不要重复安装。

实测坑（in-process 路径要自己处理，外部脚本路径已内置）
------------------------------------------------------
* **CUDA DLL 找不到** → 必须把 ``site-packages/nvidia/*/bin`` **前置**进
  ``os.environ["PATH"]``，且必须在 import ctranslate2 之前执行。
  ⚠️ ``os.add_dll_directory()`` **无效**（实测过）。
* **Blackwell（RTX 50 系）** 上 ``compute_type="auto"`` 会选 ``int8_float16``
  → ``CUBLAS_STATUS_NOT_SUPPORTED``。必须显式写 ``float16``。
* **普通 ``transcribe()`` 是最大的性能陷阱**：逐 30 秒窗口串行解码，
  只有 2.6–4 倍实时。``BatchedInferencePipeline`` 是它的 **~10 倍**（27–30 倍实时）。
  长音频不要用普通 transcribe。
* **不要关 VAD**：会直接抛 ``RuntimeError: No clip timestamps found``。
"""

from __future__ import annotations

import glob
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

DEFAULT_MODEL = "large-v3-turbo"     # 本机实测：比 large-v3 快 2–3 倍，中文效果相当
SKILL_NAME = "local-audio-transcription"

# 共享 ASR 运行时的入口文件名（约定，不写死本机路径）
SHARED_ENTRY = "asr_transcribe.py"

# 共享 ASR 运行时的**约定位置**（相对 home）。
# ⚠️ 全部相对 home —— GitHub 用户在 Linux/macOS 上也能命中同一套约定；
# 绝不写死某台机器的绝对路径。
SHARED_DIRS = (
    ".local/asr",                 # POSIX 习惯；Windows 上也可用
    ".local/share/asr",
    "AppData/Local/asr",          # Windows LOCALAPPDATA
)

# 可能装了转写 skill 的位置
SKILL_ROOTS = (
    pathlib.Path.home() / ".workbuddy" / "skills",
    pathlib.Path.home() / ".minimax" / "skills",
    pathlib.Path.home() / ".claude" / "skills",
)

# 外部命令的输出契约：
#   模板含 {outdir} → 目录模式：命令把结果写进这个目录（推荐）
#   模板含 {out}    → 文件模式：命令把结果写到这个文件
#   都不含          → 默认目录模式，命令取 <audio> 作为唯一参数
DIR_MODE = "{outdir}"


# ------------------------------------------------------------------ 探测

def _find_local_skill() -> tuple[pathlib.Path | None, pathlib.Path | None]:
    """找 local-audio-transcription skill 的 transcribe.py + 配套解释器。"""
    for root in SKILL_ROOTS:
        script = root / SKILL_NAME / "scripts" / "transcribe.py"
        if not script.exists():
            continue
        for py in _managed_pythons():
            if _has_faster_whisper(py):
                return script, py
        # 没找到带 faster-whisper 的受管解释器，就用当前解释器试
        # （脚本本身可能只需要标准库入口，或当前解释器已装）
        return script, pathlib.Path(sys.executable)
    return None, None


# 探测脚本：同时验证 faster-whisper 可导入、CUDA DLL 能不能加载、有几张卡
_PROBE = (
    "import os,glob,site;"
    "sp=[p for p in site.getsitepackages() if p.endswith('site-packages')];"
    "d=sorted(glob.glob(os.path.join(sp[0],'nvidia','*','bin'))) if sp else [];"
    "os.environ['PATH']=os.pathsep.join(d)+os.pathsep+os.environ.get('PATH','');"
    "import faster_whisper,ctranslate2;"
    "print('PROBE',len(d),ctranslate2.get_cuda_device_count())"
)


def _probe_env(py: pathlib.Path) -> tuple[bool, bool]:
    """探测一个解释器：返回 (有 faster-whisper, CUDA 真能用)。

    ⚠️ **存在不等于能用**。受管 venv 有多个（asr / default / ...），
    其中可能有的装了 faster-whisper 却**没装 nvidia CUDA 运行库**——
    选错解释器的症状是脚本跑到一半才报
    `Library cublas64_12.dll is not found or cannot be loaded`，
    而错误信息完全指不到"你选错了解释器"。

    所以判据不是"装了 faster_whisper"，而是"**能不能真的用 GPU**"：
    nvidia bin 目录前置进 PATH 后，ctranslate2 能看到几张卡。
    """
    try:
        r = subprocess.run([str(py), "-c", _PROBE], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False, False
    if r.returncode != 0:
        return False, False
    for line in (r.stdout or "").splitlines():
        if line.startswith("PROBE"):
            parts = line.split()
            try:
                n_dll, n_gpu = int(parts[1]), int(parts[2])
            except (IndexError, ValueError):
                return True, False
            return True, n_gpu > 0 and n_dll > 0
    return True, False


def _has_faster_whisper(py: pathlib.Path) -> bool:
    """解释器里是否装了 faster-whisper。"""
    return _probe_env(py)[0]


def _managed_pythons() -> list[pathlib.Path]:
    """受管 venv 的解释器（装着 faster-whisper 的那个）。

    **按 GPU 能力排序**：能真用 CUDA 的排前面，只装了包但缺 nvidia 运行库的排后面。
    排序依据是 `_probe_env` 的实测，不是目录名。
    """
    cands: list[pathlib.Path] = []
    base = pathlib.Path.home() / ".workbuddy" / "binaries" / "python" / "envs"
    if base.exists():
        cands += sorted(base.glob("*/Scripts/python.exe"))
        cands += sorted(base.glob("*/bin/python"))
    scored = []
    for i, c in enumerate(cands):
        has, gpu = _probe_env(c)
        scored.append((0 if (has and gpu) else 1 if has else 2, i, c))
    scored.sort()
    return [c for _, _, c in scored]


# ------------------------------------------------------- 命令解析 & 共享运行时

def _parse_exe(cmd: str) -> str | None:
    """从命令串里取可执行文件，**正确处理引号与空格**。

    ⚠️ 早前这里用 ``cmd.split()[0]``，路径带引号或带空格时会被切碎：
    ``"C:\\Program Files\\x\\python.exe" foo`` 取出来的是
    ``"C:\\Program Files\\x\\python.exe"``（**连引号一起**）→ 判「文件不存在」
    → 用户永远用不了带空格的路径，而错误信息完全指不到真因。
    """
    s = cmd.strip()
    if not s:
        return None
    if s[0] in "\"'":
        quote = s[0]
        end = s.find(quote, 1)
        if end < 0:
            return None                      # 引号不闭合 = 命令本身就写错了
        return s[1:end]
    parts = s.split(None, 1)
    return parts[0] if parts else None


def _shared_asr_homes() -> list[pathlib.Path]:
    """共享 ASR 运行时的候选目录。

    顺序：``BIG_ASR_HOME``（用户显式指定）→ 约定位置。
    ⚠️ 约定位置全部**相对 home**，不含任何本机绝对路径 ——
    GitHub 用户在 Linux/macOS 上也能命中同一套约定。
    """
    out: list[pathlib.Path] = []
    env = os.environ.get("BIG_ASR_HOME", "").strip()
    if env:
        out.append(pathlib.Path(env).expanduser())
    home = pathlib.Path.home()
    out += [home / rel for rel in SHARED_DIRS]
    seen, uniq = set(), []
    for p in out:
        key = os.path.normcase(str(p))
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def _shared_runtime_template(home: pathlib.Path) -> str | None:
    """这个目录像不像一个可用的共享 ASR 运行时？像就返回命令模板。

    判据三条全过才算数（缺一即视为"没有"——**宁可不认，也不要认错**）：
      ① 有解释器（Windows ``Scripts\\python.exe`` / POSIX ``bin/python``）
      ② 有入口脚本 ``asr_transcribe.py``
      ③ 解释器的 site-packages 里**真的有** ``faster_whisper``（不是空壳目录）

    ③ 是为了挡住"目录建了但没装包"的半成品——那种目录一旦被认出来，
    就会在真正转写时才炸，而错误信息还指不到真因。
    """
    pys = [home / "Scripts" / "python.exe", home / "bin" / "python",
           home / "bin" / "python3"]
    py = next((p for p in pys if p.is_file()), None)
    if py is None:
        return None
    script = home / SHARED_ENTRY
    if not script.is_file():
        return None
    sps = (list(home.glob("Lib/site-packages"))
           + list(home.glob("lib/python3*/site-packages")))
    if not any((sp / "faster_whisper").is_dir() for sp in sps):
        return None
    return f'"{py}" "{script}" {{audio}} --outdir {{outdir}} --model {{model}}'


def _resolve_cmd_template() -> tuple[str | None, str]:
    """返回 (命令模板, 说明)。模板里含 {audio}/{outdir}/{model} 占位符。"""
    env_cmd = os.environ.get("BIG_ASR_CMD", "").strip()
    if env_cmd:
        exe = _parse_exe(env_cmd)
        if not exe:
            return None, ("BIG_ASR_CMD 格式无法解析（引号不闭合？）。"
                          "它应该是一条命令行，例如："
                          'python C:/path/transcribe.py {audio} {outdir}')
        found = exe if pathlib.Path(exe).is_file() else shutil.which(exe)
        if not found:
            return None, (f"BIG_ASR_CMD 指向的命令不存在：{exe}。"
                          "检查环境变量，或改用共享 ASR 运行时 / "
                          f"{SKILL_NAME} skill。")
        return env_cmd, f"用户指定：{found}"

    for home in _shared_asr_homes():
        tmpl = _shared_runtime_template(home)
        if tmpl:
            return tmpl, f"共享 ASR 运行时：{home}"
    return None, ""


def detect_backend() -> tuple[str | None, str]:
    """返回 (后端名, 详情/失败原因)。后端名为 None 表示不可用。"""
    tmpl, detail = _resolve_cmd_template()
    if tmpl:
        return "cmd", detail

    script, py = _find_local_skill()
    if script is not None:
        return "local_skill", f"{script}（解释器 {py}）"

    try:
        import faster_whisper                                    # noqa: F401
        return "faster_whisper", "当前解释器已安装 faster-whisper"
    except ImportError:
        return None, ("未找到可用的本地转写后端："
                      "既没有共享 ASR 运行时，也没有 "
                      f"{SKILL_NAME} skill，当前解释器也没装 faster-whisper")


def install_hint() -> str:
    """没有后端时给的可执行指引。"""
    return (
        "本地 ASR 后端未就绪，四选一：\n"
        f"  ① 装 {SKILL_NAME} skill（脚本已内置 CUDA/Blackwell/批处理的坑修复）\n"
        "  ② pip install faster-whisper"
        "（首次运行会自动下载模型；用 BIG_ASR_MODEL=medium 可以轻一点）\n"
        "  ③ 指向你已有的转写命令：\n"
        "     set BIG_ASR_CMD=\"python C:/path/transcribe.py {audio} {outdir}\"\n"
        "     （{audio}=音频文件，{outdir}=输出目录，期望写出 .srt 或 .json）\n"
        "  ④ 建一个**共享 ASR 运行时**（多个项目共用一份，推荐长期用）：\n"
        "     python -m venv ~/.local/asr\n"
        "     ~/.local/asr/Scripts/pip install faster-whisper \"av<19\" nvidia-cublas-cu12\n"
        "     把本仓库的 scripts/asr_transcribe.py 复制到 ~/.local/asr/\n"
        "     （装完会自动被探测到；也可用 BIG_ASR_HOME 指向别的位置）\n"
        "     ⚠️ **av 必须 <19**：av 19 移除了 av.open() 的 metadata_errors 参数，\n"
        "        而 faster-whisper 1.2.1 仍在传它 → 一解码就 TypeError，转写全废。\n"
        "        只装 CPU 版可省掉 nvidia-cublas-cu12（但会慢很多）。\n"
        "装好后重试即可；不装的话，这条视频的字幕就是缺口，"
        "需要在结论里明确声明「该视频无可用字幕，仅依据视频元数据与评论区」。"
    )


# ------------------------------------------------------------------ SRT 解析

_SRT_TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
                     r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _ts_to_sec(h: int, m: int, s: int, ms: int) -> float:
    return h * 3600 + m * 60 + s + ms / 1000


def parse_srt(text: str) -> list[dict]:
    """SRT -> lines[{from,to,content}]。"""
    lines: list[dict] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        m = _SRT_TS.search(block)
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x) for x in m.groups())
        body = block[m.end():].strip()
        body = re.sub(r"<\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}>", "", body)   # 去内嵌时间码
        body = re.sub(r"<[^>]+>", "", body)
        body = body.replace("\n", " ").strip()
        if not body:
            continue
        lines.append({"from": _ts_to_sec(h1, m1, s1, ms1),
                      "to": _ts_to_sec(h2, m2, s2, ms2),
                      "content": body})
    return lines


def parse_asr_json(text: str) -> list[dict]:
    """转写器可能输出 JSON：
    接受 `[{start,end,text}]`（本地 ASR skill 的实际格式）或 `{segments:[...]}`。
    """
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return []
    segs = d.get("segments") if isinstance(d, dict) else d
    if not isinstance(segs, list):
        return []
    out = []
    for s in segs:
        if not isinstance(s, dict):
            continue
        st = s.get("start", s.get("from"))
        en = s.get("end", s.get("to"))
        tx = s.get("text", s.get("content")) or ""
        if st is None or en is None:
            continue
        out.append({"from": float(st), "to": float(en), "content": str(tx).strip()})
    return [x for x in out if x["content"]]


# ------------------------------------------------------------------ 外部命令

def _collect_outputs(outdir: pathlib.Path, stem: str) -> tuple[list[dict], str]:
    """从输出目录里取转写结果。优先 JSON（时间戳更准），退回 SRT。"""
    js = outdir / f"{stem}.json"
    if js.exists():
        lines = parse_asr_json(js.read_text(encoding="utf-8", errors="replace"))
        if lines:
            return lines, f"{js.name}"
    srt = outdir / f"{stem}.srt"
    if srt.exists():
        lines = parse_srt(srt.read_text(encoding="utf-8", errors="replace"))
        if lines:
            return lines, f"{srt.name}"
    # 兜底：目录里任意一个 srt / json
    for p in sorted(outdir.glob("*.json")):
        lines = parse_asr_json(p.read_text(encoding="utf-8", errors="replace"))
        if lines:
            return lines, p.name
    for p in sorted(outdir.glob("*.srt")):
        lines = parse_srt(p.read_text(encoding="utf-8", errors="replace"))
        if lines:
            return lines, p.name
    return [], ""


def _run_external(cmd: str, audio: pathlib.Path, workdir: pathlib.Path) -> tuple[list[dict], str]:
    """跑外部转写命令，返回 (lines, 描述)。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    stem = audio.stem

    if DIR_MODE in cmd:
        outdir = workdir / "asr"
        outdir.mkdir(parents=True, exist_ok=True)
        full = cmd.replace("{audio}", str(audio)).replace("{outdir}", str(outdir))
        full = full.replace("{out}", str(outdir / f"{stem}.srt"))
        full = full.replace("{model}", os.environ.get("BIG_ASR_MODEL", DEFAULT_MODEL))
        r = subprocess.run(full, shell=True, capture_output=True, encoding="utf-8",
                           errors="replace", env=env, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(f"转写命令失败（退出码 {r.returncode}）："
                               f"{(r.stderr or '').strip()[-500:]}")
        lines, src = _collect_outputs(outdir, stem)
        if not lines:
            raise RuntimeError(f"转写命令成功但没找到输出（{outdir} 里没有 .srt/.json）")
        return lines, f"外部命令，输出 {src}"
    if "{out}" in cmd:
        out = workdir / f"{stem}.srt"
        full = cmd.replace("{audio}", str(audio)).replace("{out}", str(out))
        full = full.replace("{model}", os.environ.get("BIG_ASR_MODEL", DEFAULT_MODEL))
        r = subprocess.run(full, shell=True, capture_output=True, encoding="utf-8",
                           errors="replace", env=env, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(f"转写命令失败（退出码 {r.returncode}）："
                               f"{(r.stderr or '').strip()[-500:]}")
        if not out.exists():
            raise RuntimeError(f"转写命令成功但没产出 {out.name}")
        text = out.read_text(encoding="utf-8", errors="replace")
        return parse_asr_json(text) or parse_srt(text), f"外部命令，输出 {out.name}"
    # 无占位符：把音频当唯一参数，结果去默认目录找
    outdir = workdir / "asr"
    outdir.mkdir(parents=True, exist_ok=True)
    full = cmd.replace("{audio}", str(audio)).replace("{outdir}", str(outdir))
    full = full.replace("{out}", str(outdir / f"{stem}.srt"))
    r = subprocess.run(full, shell=True, capture_output=True, encoding="utf-8",
                       errors="replace", env=env, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"转写命令失败（退出码 {r.returncode}）："
                           f"{(r.stderr or '').strip()[-500:]}")
    lines, src = _collect_outputs(outdir, stem)
    if not lines:
        raise RuntimeError(f"转写命令成功但没找到输出（{outdir}）")
    return lines, f"外部命令，输出 {src}"


def _run_local_skill(script: pathlib.Path, py: pathlib.Path, audio: pathlib.Path,
                     workdir: pathlib.Path) -> tuple[list[dict], str]:
    """调用 local-audio-transcription skill 的 transcribe.py。

    它的脚本已内置三个坑的修复（CUDA DLL 路径前置 / Blackwell 显式 float16 /
    批处理加速），所以这里**不传** --compute-type，让脚本按设备自己选
    （cuda→float16，cpu→int8），并保留它的 cuda 失败回退 cpu 的能力。
    """
    outdir = workdir / "asr"
    outdir.mkdir(parents=True, exist_ok=True)
    model = os.environ.get("BIG_ASR_MODEL", DEFAULT_MODEL)
    cmd = [str(py), str(script), str(audio),
           "--model", model, "--device", "auto", "--language", "zh",
           "--batch-size", "8", "--beam-size", "5",
           "--formats", "srt", "json", "--outdir", str(outdir)]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                       errors="replace", env=env, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"本地转写失败（退出码 {r.returncode}）："
                           f"{(r.stderr or r.stdout or '').strip()[-500:]}")
    stem = audio.stem
    lines, src = _collect_outputs(outdir, stem)
    if not lines:
        raise RuntimeError(f"本地转写成功但没找到输出（{outdir}）")
    dev = ""
    for line in (r.stdout or "").splitlines():
        if "设备" in line and "精度" in line:
            dev = line.strip()
            break
    return lines, f"{model}｜{src}｜{dev or '设备信息未知'}"


# ------------------------------------------------------------------ in-process

def _enable_cuda_dll_path() -> int:
    """把 site-packages/nvidia/*/bin **前置**进 PATH。

    ⚠️ 必须在前置后、import ctranslate2 之前执行；
    ⚠️ ``os.add_dll_directory()`` 实测**无效**，只有改 PATH 管用。
    """
    try:
        import site
        sps = [p for p in site.getsitepackages() if p.endswith("site-packages")]
        if not sps:
            return 0
        dirs = sorted(glob.glob(os.path.join(sps[0], "nvidia", "*", "bin")))
        if not dirs:
            return 0
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")
        return len(dirs)
    except Exception:                                          # noqa: BLE001
        return 0


def _run_faster_whisper(audio: pathlib.Path) -> tuple[list[dict], str]:
    _enable_cuda_dll_path()          # 必须在 import ctranslate2 之前
    from faster_whisper import WhisperModel

    model_name = os.environ.get("BIG_ASR_MODEL", DEFAULT_MODEL)
    device = os.environ.get("BIG_ASR_DEVICE", "auto")
    if device == "auto":
        device = "cuda" if _cuda_available() else "cpu"
    # Blackwell 上 auto 会选 int8_float16 → CUBLAS_STATUS_NOT_SUPPORTED，必须显式
    compute = os.environ.get("BIG_ASR_COMPUTE") or ("float16" if device == "cuda" else "int8")

    model = WhisperModel(model_name, device=device, compute_type=compute)
    segs = None
    used = "transcribe"
    if device == "cuda":
        try:
            from faster_whisper import BatchedInferencePipeline
            pipe = BatchedInferencePipeline(model=model)
            segs, _ = pipe.transcribe(str(audio), batch_size=8,
                                      vad_filter=True,   # 绝不要关 VAD
                                      language="zh")
            used = "BatchedInferencePipeline"
        except Exception:                                      # noqa: BLE001
            segs = None
    if segs is None:
        segs, _ = model.transcribe(str(audio), vad_filter=True, language="zh")
        used = "transcribe"

    lines = []
    for s in segs:
        tx = (getattr(s, "text", "") or "").strip()
        if not tx:
            continue
        lines.append({"from": float(getattr(s, "start", 0.0)),
                      "to": float(getattr(s, "end", 0.0)),
                      "content": tx})
    return lines, f"{model_name} / {device} / {compute} / {used}"


def _cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:                                          # noqa: BLE001
        return False


# ------------------------------------------------------------------ 对外

def transcribe(audio: pathlib.Path, workdir: pathlib.Path) -> dict:
    """转写音频，返回与 B站字幕同构的 transcript。

    返回 {"data_source": "asr", "language": "zh", "lines": [...], "engine": ...}
    没有后端时抛 RuntimeError，消息里带**可执行的安装指引**。
    """
    backend, detail = detect_backend()
    if backend is None:
        raise RuntimeError(f"{detail}\n\n{install_hint()}")

    workdir.mkdir(parents=True, exist_ok=True)
    if backend == "cmd":
        tmpl, _ = _resolve_cmd_template()
        if not tmpl:
            raise RuntimeError("命令后端在转写前消失了（环境变量被改或目录被删）。")
        lines, engine = _run_external(tmpl, audio, workdir)
    elif backend == "local_skill":
        script, py = _find_local_skill()
        lines, engine = _run_local_skill(script, py, audio, workdir)
    else:
        lines, engine = _run_faster_whisper(audio)
        _write_srt(workdir / "transcript.srt", lines)

    if not lines:
        raise RuntimeError("转写返回 0 行（音频可能为空或纯静音）")
    return {"data_source": "asr", "language": "zh", "lines": lines, "engine": engine}


def _write_srt(path: pathlib.Path, lines: list[dict]) -> None:
    def ts(sec: float) -> str:
        ms = int(round((sec - int(sec)) * 1000))
        s = int(sec) % 60
        m = (int(sec) // 60) % 60
        h = int(sec) // 3600
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    out = []
    for i, l in enumerate(lines, 1):
        out.append(f"{i}\n{ts(l['from'])} --> {ts(l['to'])}\n{l['content']}\n")
    path.write_text("\n".join(out), encoding="utf-8")
