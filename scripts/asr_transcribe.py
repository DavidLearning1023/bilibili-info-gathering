#!/usr/bin/env python
"""共享 ASR 运行时的唯一对外入口。

用法（建共享运行时时的标准步骤）
----------------------------------
    python -m venv ~/.local/asr
    ~/.local/asr/Scripts/pip install faster-whisper "av<19" nvidia-cublas-cu12
    cp scripts/asr_transcribe.py ~/.local/asr/

⚠️ **av 必须 <19**：PyAV 19 移除了 ``av.open()`` 的 ``metadata_errors`` 参数，
而 faster-whisper 1.2.1 的 ``audio.py`` 仍在传它
→ ``TypeError: open() got an unexpected keyword argument 'metadata_errors'``
→ 整条转写链路全废（而且是**运行时**才炸：装包、import、模型加载全都正常）。
装完请立刻 ``pip freeze > requirements.lock.txt`` 锁版本。

为什么需要这个文件
------------------
共享目录里装着一堆包（faster_whisper / ctranslate2 / nvidia-cublas…），
但**其它项目不该知道这些包的内部布局**。本脚本把三件容易踩坑的事
一次性封死，调用方只需要知道命令行契约：

  * **CUDA DLL 路径**：``site-packages/nvidia/*/bin`` 必须在
    ``import ctranslate2`` **之前**前置进 ``PATH``。
    ⚠️ ``os.add_dll_directory()`` 实测**无效**，只有改 PATH 管用。
  * **Blackwell（RTX 50 系 / sm_120）**：``compute_type="auto"`` 会选
    ``int8_float16`` → ``CUBLAS_STATUS_NOT_SUPPORTED``。
    必须显式 ``float16``（CUDA）/ ``int8``（CPU）。
  * **批处理**：普通 ``transcribe()`` 逐 30 秒窗口串行解码，只有
    2.6–4 倍实时；``BatchedInferencePipeline`` 是它的 ~10 倍。
    长音频别用普通 transcribe。

命令行契约（与 big.asr 的 ``BIG_ASR_CMD`` 占位符一致）
---------------------------------------------------
    python asr_transcribe.py {audio} --outdir {outdir} --model {model}

输出
----
``<outdir>/<音频stem>.json``  —— {"segments":[{start,end,text},…], …}
``<outdir>/<音频stem>.srt``   —— 标准 SRT

两者都会写，调用方优先读 JSON（时间戳更准）。

失败语义
--------
**不静默失败**：任何异常都打印可读原因并以非 0 退出码结束。
退出码 0 = 确实产出了转写结果。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import site
import sys
import traceback

DEFAULT_MODEL = "large-v3-turbo"
DEFAULT_LANGUAGE = "zh"


def _utf8_stdio() -> None:
    """Windows 控制台默认 GBK，输出中文/符号会炸。统一改 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                       # noqa: BLE001
            pass


def enable_cuda_dll_path() -> list[str]:
    """把 site-packages/nvidia/*/bin 前置进 PATH。

    ⚠️ 必须在 ``import ctranslate2`` 之前调用。
    ⚠️ ``os.add_dll_directory()`` 实测无效，只有改 PATH 管用。
    """
    try:
        sps = [p for p in site.getsitepackages() if p.endswith("site-packages")]
        if not sps:
            return []
        dirs = sorted(glob.glob(os.path.join(sps[0], "nvidia", "*", "bin")))
        if dirs:
            os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")
        return dirs
    except Exception:                                           # noqa: BLE001
        return []


def _srt_timestamp(seconds: float) -> str:
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms >= 1000:                                              # 边界：进位
        seconds, ms = seconds + 1, 0
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: pathlib.Path, segments: list[dict]) -> None:
    blocks = []
    for i, seg in enumerate(segments, 1):
        blocks.append(
            f"{i}\n"
            f"{_srt_timestamp(seg['start'])} --> {_srt_timestamp(seg['end'])}\n"
            f"{seg['text'].strip()}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8")


def main() -> int:
    _utf8_stdio()
    ap = argparse.ArgumentParser(description="共享 ASR 运行时入口")
    ap.add_argument("audio", help="音频文件路径")
    ap.add_argument("--outdir", default=None, help="输出目录（推荐）")
    ap.add_argument("--out", default=None, help="输出 .srt 文件（文件模式）")
    ap.add_argument("--model", default=os.environ.get("BIG_ASR_MODEL", DEFAULT_MODEL))
    ap.add_argument("--device", default=os.environ.get("BIG_ASR_DEVICE", "auto"),
                    choices=["auto", "cuda", "cpu"])
    ap.add_argument("--language", default=os.environ.get("BIG_ASR_LANG", DEFAULT_LANGUAGE))
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--beam-size", type=int, default=5)
    args = ap.parse_args()

    audio = pathlib.Path(args.audio)
    if not audio.exists():
        print(f"[ASR] 音频文件不存在：{audio}", file=sys.stderr)
        return 2

    # ---- 输出路径 ----------------------------------------------------
    if args.outdir:
        outdir = pathlib.Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        json_path = outdir / f"{audio.stem}.json"
        srt_path = outdir / f"{audio.stem}.srt"
    elif args.out:
        srt_path = pathlib.Path(args.out)
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        json_path = srt_path.with_suffix(".json")
    else:
        outdir = audio.parent
        json_path = outdir / f"{audio.stem}.json"
        srt_path = outdir / f"{audio.stem}.srt"

    # ---- 设备与精度（Blackwell 安全）--------------------------------
    dll_dirs = enable_cuda_dll_path()          # 必须在 import ctranslate2 之前
    try:
        import ctranslate2
    except ImportError as exc:
        print(f"[ASR] 无法导入 ctranslate2：{exc}", file=sys.stderr)
        return 3

    device = args.device
    if device == "auto":
        try:
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:                                       # noqa: BLE001
            device = "cpu"
    # ⚠️ 不用 "auto"：Blackwell 上会选 int8_float16 → CUBLAS_STATUS_NOT_SUPPORTED
    compute_type = "float16" if device == "cuda" else "int8"

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        print(f"[ASR] 无法导入 faster_whisper：{exc}", file=sys.stderr)
        return 3

    print(f"[ASR] 模型={args.model} 设备={device} 精度={compute_type} "
          f"CUDA_DLL目录={len(dll_dirs)} 语言={args.language}", flush=True)

    try:
        model = WhisperModel(args.model, device=device, compute_type=compute_type)
    except Exception as exc:                                   # noqa: BLE001
        print(f"[ASR] 模型加载失败：{exc}", file=sys.stderr)
        return 4

    segments: list[dict] = []
    used = "transcribe"
    try:
        if device == "cuda":
            try:
                from faster_whisper import BatchedInferencePipeline
                pipe = BatchedInferencePipeline(model=model)
                segs, _ = pipe.transcribe(str(audio), batch_size=args.batch_size,
                                          beam_size=args.beam_size,
                                          vad_filter=True,      # ⚠️ 绝不要关 VAD
                                          language=args.language)
                segments = [{"start": float(s.start), "end": float(s.end),
                             "text": (s.text or "").strip()} for s in segs]
                used = "BatchedInferencePipeline"
            except Exception as exc:                           # noqa: BLE001
                print(f"[ASR] 批处理失败（{exc}），回退到普通 transcribe", file=sys.stderr)
                traceback.print_exc()
                segments = []
        if not segments:
            segs, _ = model.transcribe(str(audio), vad_filter=True,   # ⚠️ 绝不要关 VAD
                                        beam_size=args.beam_size, language=args.language)
            segments = [{"start": float(s.start), "end": float(s.end),
                         "text": (s.text or "").strip()} for s in segs]
            used = "transcribe"
    except Exception as exc:                                   # noqa: BLE001
        print(f"[ASR] 转写失败：{exc}", file=sys.stderr)
        return 5

    segments = [s for s in segments if s["text"]]
    if not segments:
        print("[ASR] 转写返回 0 段（音频可能为空或纯静音）", file=sys.stderr)
        return 6

    payload = {
        "segments": segments,
        "model": args.model,
        "device": device,
        "compute_type": compute_type,
        "pipeline": used,
        "language": args.language,
        "duration": segments[-1]["end"],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_srt(srt_path, segments)

    print(f"[ASR] 完成：{len(segments)} 段，时长 {segments[-1]['end']:.1f}s，"
          f"管线 {used} → {json_path.name} / {srt_path.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
