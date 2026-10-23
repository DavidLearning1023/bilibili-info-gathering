<p align="center">
  <a href="CONTRIBUTING.md">简体中文</a> · <a href="CONTRIBUTING.en.md"><b>English</b></a>
</p>

# Contributing

**中文版：[CONTRIBUTING.md](CONTRIBUTING.md)**

Thanks for taking the time. This project has **one maintainer**, so the notes below exist to
save you a round trip: **read "Where to send what" first — different kinds of contributions
take completely different paths.**

> **No CLA or DCO signature required.** For a solo-maintained project, the simpler the process
> the better. The only hard requirements are: evidence behind the change, green tests, and an
> honest changelog entry.

## Where to send what

| What you want to do | Which path |
|---|---|
| **Change a methodology rubric** (dual-axis grading, exit rules, opinion distribution…) | **Open an Issue and discuss first** — don't open a PR directly |
| **Fix a bug** (fetching goes wrong, report rendering is broken) | Issue or PR directly — **include reproduction steps** |
| **Docs / translation / typo fixes** | PR directly (editing *explanatory prose* in `references/` is fine; changing a *rubric* needs discussion first) |
| **Add a tool / change fetching logic** | Open an Issue first and spell out the **motivation** and the **cost** (does it mean more upstream requests?) |
| **Report a security problem** | **Do not open a public Issue** ⇒ see [SECURITY.en.md](SECURITY.en.md) |
| **Want methodology for a new topic type** | Open an Issue describing the topic type and what you expect |

> **Why methodology needs discussion first**: the rubrics live in `references/` and are what the
> agent judges with. Changing one threshold can invalidate another — such changes need to be
> looked at together. Discussing first costs far less than rolling back later.

> **When you open an Issue, GitHub offers three templates**: `Bug report` /
> `Methodology discussion` / `Feature request`. Pick the matching one — the fields already
> spell out what needs saying (the methodology template will also nudge you to bring
> evidence). A blank Issue works too, but the templates ask the questions the triage table
> above only gestures at.

**Discussions are not enabled on this repository**, so Issues and PRs are the only two paths.

## Development setup

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

Extras: `images` = Pillow (comment contact sheets) · `docs` = markdown + playwright
(HTML/PDF rendering) · `dev` = pytest.

**Point your agent at this skill** (needed when you change code; see
[INSTALL.md](INSTALL.md) §3): a junction on Windows, a symlink on macOS / Linux, both aimed at
the repository root.

## Running the tests

```bash
# Offline (no network, no login needed) — this must pass before you push
$env:BIG_SKIP_NETWORK='1'        # macOS / Linux: export BIG_SKIP_NETWORK=1
python -m pytest tests -q
```

Current baseline: **113 passed / 3 skipped**. The 3 skipped are the **networked smoke tests**
in `tests/test_mcp_smoke.py` (they need network access and a Bilibili login session).

**Networked smoke test** (needs your own login session — **never run it in CI or any public
environment**):

```bash
python scripts/check_setup.py
```

It starts a real stdio server handshake, verifies the 14 tools, and runs one real networked
search. Expected output:

```
结果: [OK] 17 项  [WARN] 0 项  [FAIL] 0 项
```

## The docs are linted — run the tests after editing them

This project binds "what the docs say" to "what the code does", and several lints fail the
build outright:

| Lint | What it pins down |
|---|---|
| `test_docs_tool_count_is_consistent` | The tool count written in the docs must equal the real number of `@mcp.tool()` decorators in `mcp_server.py` (**both the Chinese and the English README count**) |
| `test_docs_no_stale_tool_names` | The docs must not mention upstream tool names **this project does not have** — following them would call the wrong tool |
| `test_version_is_consistent_across_pyproject_and_package` | `big.__version__` must equal `version` in `pyproject.toml` |

**So**:

- Adding or removing a tool ⇒ update the tool count in README.md, README.en.md, SKILL.md,
  INSTALL.md and `references/mcp-tools.md`
- Bumping the version ⇒ update **both** `pyproject.toml` and `src/big/__init__.py`

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) type prefixes, and write the
description **in Chinese**:

```
feat: 新增 XXX 工具
fix: 修复 XXX 场景下的 YYY
docs: 补充 XXX 判据的说明
refactor: 重构 XXX
test: 补 XXX 的回归测试
release: vX.Y.Z 正式版
```

**One commit does one thing.** Mixed changes cannot be reverted individually later.

## Changelog

**Append an entry to `references/CHANGELOG.md` for every change, newest on top.**
Format: `date · version · batch name`, then state what changed, why, and how it was verified.

> **When you write a changelog entry, every sentence about "what I did" must be mechanically
> re-checkable**: if you mention a string living in some file, `grep` for its line number first;
> if you describe a section order, read it with `grep -n '^## '`; use **the number format of
> the file you are citing** (this repo uses KiB).
>
> A changelog's entire value is accuracy — an inaccurate one is a liability. (This project has
> slipped here itself: writing a client-config description as a server-code parameter, and
> writing "22 KB" in decimal while the repo uses KiB. Both were caught in review.)

## Number conventions

File sizes in this repository are always **KiB** (`round(bytes / 1024)`).
Before writing "92 KB", confirm the file really is 94,312 bytes.

## Never commit these

`.gitignore` already covers most of it, but please **double-check**:

| Thing | Why |
|---|---|
| `.registry/` | your source library, topic history, uploader ratings (personal data) |
| `分析报告库/` | your produced reports (**not anonymised** — they contain nicknames and uploader names) |
| `.bigout/` | screenshots, extracted frames, downloaded temp files |
| `.workbuddy/` | the agent session workspace (contains absolute local paths) |
| `BILI_SESSDATA` / `cookies.json` / `*.cookie` | credentials — **never, under any circumstance** |

## Before you open a PR

- [ ] `BIG_SKIP_NETWORK=1 python -m pytest tests -q` is green
- [ ] Adding / removing a tool ⇒ the tool count is updated in **every** document
- [ ] Bumping the version ⇒ `pyproject.toml` and `big/__init__.py` are both updated
- [ ] An entry was appended to `references/CHANGELOG.md`
- [ ] No credentials, `.registry/`, `分析报告库/` or `.bigout/` in the diff
- [ ] Changing a rubric ⇒ **discussed in an Issue first** (or the PR spells out the reasoning)
- [ ] One commit does one thing
- [ ] No dead anchors in the docs you touched (see below)

## Never write anchors from memory

Changing a heading changes its anchor. GitHub's rule: lowercase → strip punctuation → spaces
become hyphens → CJK is kept. **Don't guess `#anchor`**; verify locally after changing a
heading:

```bash
npm i github-slugger        # the exact slug algorithm GitHub uses
node -e "const s=require('github-slugger');console.log(s.default('your heading'))"
```

## Code of conduct

- Respect other people: argue about the **argument**, not the person
- One topic per Issue, so discussion can stay focused
- Accept that someone may disagree with you, and treat that as information
- Be confident in your own judgement, but **state its boundaries out loud** — that is precisely
  what this project's methodology does

For the full version see the
[GitHub Community Guidelines](https://docs.github.com/site-policy/github-terms/github-community-guidelines).

## Contributions that will be declined

- Code that bypasses login, paywall or regional restrictions
- Large-scale high-frequency scraping (circumventing the default rate limit)
- Loosening a rubric in `references/` without evidence to back it
- Unrelated refactors bundled into the change (split them into their own commit)
