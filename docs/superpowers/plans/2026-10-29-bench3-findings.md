# Fixes from the bench3 transcripts

**Goal:** make Horizon at least as fast as plain Claude Code in a spec build's first session, and let it catch the missing public names that zeroed structlog. Findings are in the fun repo, `docs/evaluations/nl2repo-bench3/report.md`. The rerun is on structlog, pyjwt and unidecode.

**Branch:** `fix/bench3-findings`, from `fix/bench2-findings` (1e61e37). Stdlib only. Every code change gets a test that fails first.

## Tasks

### 1. `surface`: a gate of the public names a spec gives (structlog)
- `harness.py surface --spec SPEC.md --package NAME --out $GATES/test_surface.py` writes a pytest file with one test per name.
- Names come from dotted references (`NAME.mod.Name`) and from `from NAME.mod import a, b` / `import NAME.mod` lines.
- **Each test:**
  - imports the deepest module it can;
  - checks the next name on that module, and no deeper;
  - fails when a module on the path is a namespace package (no `__init__.py`).
- **Left out:**
  - URLs and file names (`www.NAME.org`, `NAME/...`);
  - Python keywords;
  - `test*` segments;
  - names that appear only on lines mentioning `AttributeError`.
- **Tests:**
  - the extraction cases above;
  - a generated file fails on a missing top-level name and passes once it is added;
  - it fails on a namespace package.

### 2. The session-start message hands over the gate (all three tasks)
- **Latest result:** show the newest of the last observation and the last checkpoint. A `check` as the last record now shows its result.
- **Gate command:** included in the message.
- **Failing checks:** when the result names them (`FAILED x::y` / `ERROR x::y` lines), list up to 12 by test id, with the summary line. Otherwise give the last 300 characters.
- **Files line:** tracked files outside `.claude/` and `.agents/`, with line counts, up to 40 (code files first when there are more).
- **Tests:**
  - after `check`, the next message shows that result;
  - the gate command and failing ids are shown;
  - the files line is present.

### 3. SKILL.md, both copies, plus docs
- **Large inputs:** read with the Read tool, up to about 800 lines per call. Never `cat` a large input or read it in small slices. Read a spec whole in session 1.
  - Notes keep only line ranges and target modules, never what is implemented.
  - Notes are written in the same call as an edit.
- **Building from a spec:**
  - Read the spec.
  - Then, in one Bash call, write request.md and requirements.md, run `surface`, and run `start` with that gate.
  - Then write complete modules.
  - When a probe fails on a spec name, add the name to the library.
  - Grow the gate only for behaviour about to be written or a spec example.
- **A passing gate is not done:** modules and public names. Docs, CI and config files only when asked, and after the library.
- **Resuming:** the message has the gate command, the failing checks and the files, so don't run `status`, `ls` or `wc` for them.
- **Contract tests** for the new phrases.

## Then
A fresh reviewer checks the branch, and its critical and important findings are fixed. Then:
1. Bundle.
2. **Behaviour pilot:** 2 sessions per Horizon task. Read the transcripts against a checklist before the full run:
   - session 1's first code edit by call 10;
   - setup in at most 2 calls;
   - the surface gate generated;
   - no `status` in session 2;
   - the gate shown in session 2's message.
3. Pre-register bench4 (same three tasks, same rule).
4. Smoke check.
5. Run.
