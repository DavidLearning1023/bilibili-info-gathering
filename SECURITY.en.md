<p align="center">
  <a href="SECURITY.md">简体中文</a> · <a href="SECURITY.en.md"><b>English</b></a>
</p>

# Security Policy

**中文版：[SECURITY.md](SECURITY.md)**

This document describes the security boundaries of `bilibili-info-gathering`, the protections
that are already in place, and how to report a problem.

## Supported versions

| Version | Supported | Notes |
|---|:---:|---|
| `v1.0.0` (the `main` branch) | ✅ | Current release |
| < `v1.0.0` | ❌ | Never released (the repo had 0 tags before) |

Fixes land on `main` and ship with the next release. **There are no long-lived maintenance
branches** — this is a solo-maintained project, so please use the latest version.

## Reporting a vulnerability

> **Please do not report security vulnerabilities through public Issues.**

This repository has **private vulnerability reporting** enabled:

**Repository → Security tab → Report a vulnerability**

That opens a **private conversation visible only to the maintainer**, and you can choose to
publish it once the fix is out. Do not paste vulnerability details into a public Issue, a
discussion, or a chat room — **a public Issue spreads the bug before it is fixed**.

If that entry point is unavailable to you: open an Issue that contains **no exploit details**,
titled `security:`, describing only the category of the problem (e.g. "possible path escape"),
and wait for the maintainer to contact you privately before sharing details.

**Please include**:

- The affected version / commit
- Reproduction steps, or a minimal reproducer
- The blast radius (can it read data outside the repo? leak credentials? write to arbitrary paths?)
- Your suggested fix direction (optional, but genuinely helpful)

**Response time**: there is one maintainer, so the target is **a first response within 7 days**
and **a fix or a mitigation plan within 30 days**. That is not an SLA, only a floor. **If 30 days
pass with no response at all, feel free to open a public Issue as a nudge** — keep the nudge
separate from the vulnerability details.

## Protections already in place

| Protection | Status | Notes |
|---|:---:|---|
| Secret scanning | ✅ enabled | GitHub-managed; scans the public repo for suspected credentials |
| Secret scanning push protection | ✅ enabled | Blocks on **push**, not discovered after the fact |
| Private vulnerability reporting | ✅ enabled | This is the entry point in "Reporting a vulnerability" above |
| Dependabot security updates | ❌ not enabled | See "Known gaps" below |

## What counts as a security issue (this project's actual attack surface)

This is a **single-machine, read-only, public-data-only** tool: no server, no multi-tenancy,
no external input. Real security issues therefore fall into a few narrow categories.

### 1. Credential leakage (the one that matters most)

- `BILI_SESSDATA`, `cookies.json`, `*.cookie` — **must never end up in the repository**
- `.registry/` holds your source library, topic history and **uploader ratings** (personal data);
  it is in `.gitignore`
- Artifacts under `分析报告库/` are **not anonymised**: they keep publicly visible video titles,
  BV ids, uploader names and commenter nicknames, because they must stay traceable and
  linkable. **Review them yourself before sharing publicly**, especially IP-region fields
- **IP region, nickname and mid in comments are personal data.** The tools only produce
  aggregate statistics by default, but a `locations` field should not appear in a
  shareable artifact

**If you find a credential in a public repo, Issue, or Release, report it privately using the
channel above** — do not open a public Issue.

### 2. Prompt injection (handled, but never fully eliminable)

- Video titles, descriptions, tags, comments and danmaku are **user-generated content** and may
  contain instructions aimed at the agent
- The tool layer has `scan_untrusted_text` (which flags `injection_risk`) and `get_comments`
  marks matched comments too — but **this is keyword matching: a hint, never evidence**
- **The real defence is structural**: fetching and judging are separated, and the agent must
  never execute imperatives found in comments or danmaku
- Every citation in a report must be traceable back to the original video / image / clip.
  **Do not treat a number you did not re-read at the source as fact**

### 3. Paths and on-disk writes

- The topic name becomes part of the artifact path (`分析报告库/<YYYYMMDD>-<mode>-<topic>/`).
  The code replaces Windows-illegal characters, but **a `/` inside the topic can still escape
  the intended directory**
- Screenshots, extracted frames and downloads land in `.bigout/` (already in `.gitignore`)
- Compute report paths with the code: `python -m big.cli report-path --topic "…" --mode deep --mkdir`.
  Do not hand-assemble them

### 4. Dependency supply chain

- Runtime dependencies are minimal: only `mcp`. `Pillow` (comment contact sheets) and
  `markdown` / `playwright` (HTML/PDF rendering) are optional extras
- **Local ASR lives in its own environment**, outside the dependency tree (the `av` must be
  `<19` constraint is recorded in `references/CHANGELOG.md`)
- The shared ASR runtime is **not a dependency of this repository** — install and pin it yourself

### 5. Usage boundaries (not vulnerabilities, but they get asked about)

- Only **publicly visible** content is accessed; paywalls, premium memberships, regional
  restrictions and private content are never bypassed
- Requests are rate-limited to 400 ms by default (`BIG_RATE_LIMIT_MS`). **A `412` is IP-level
  risk control; switching credentials will not help**
- Do not use it for commercial purposes or large-scale scraping (full analysis in [RISK.md](RISK.md))

## Known gaps

Listed honestly, because it is better than implying everything is covered:

- **Dependabot security updates are off** (`dependabot_security_updates` disabled). The
  dependency surface is tiny and the risk is limited, but off is off.
- **There has been no third-party security audit.** This is a solo-maintained project and no
  security review has been performed.
- **This policy covers this repository only.** The companion project
  [bilibili-video-analysis-summary](https://github.com/DavidLearning1023/bilibili-video-analysis-summary)
  is separate.
- **Bilibili's official AI assistant accounts** (the ones whose names start with `AI`) are
  **not** flagged by `injection_risk`, yet they do pollute the opinion distribution — the tool
  layer cannot detect them; a human has to notice.

## Non-security issues

Functional bugs, methodology disagreements, or a request for methodology for a new topic type
⇒ see [CONTRIBUTING.md](CONTRIBUTING.en.md) or just open an Issue.
**The triage table lives in the README under "30-second fit check".**
