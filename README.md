# Continual Harness for Codex and Claude Code

Two skills for work that spans sessions and for learning from completed tasks:

- **horizon** saves a bounded goal, verification results, and the next action.
- **refine** proposes a small instruction or memory improvement from real task
  evidence, applies authorized edits with undo, and records later outcomes.

The agents have separate skill instructions and state, backed by **one shared
Python implementation**. This keeps tool-specific behavior accurate while fixes
to verification, budgets, and rollback reach both agents.

| | Codex | Claude Code |
| --- | --- | --- |
| Skills in this checkout | [.agents/skills](.agents/skills) | [.claude/skills](.claude/skills) |
| Invocation | `$horizon`, `$refine` | `/horizon`, `/refine` |
| Project state | `.codex/harness` | `.claude/harness` |
| Project instructions | `AGENTS.md` | `CLAUDE.md` |
| Custom agents | `.codex/agents/*.toml` | `.claude/agents/*.md` |
| Memory | `.codex/memory`, read with `recall` | Existing Claude project-memory route |

The shared [CLI](harness/cli.py) uses only Python's standard library; run it with
Python 3.10+ on a POSIX system. No separate model API or service is required.

## Use it

Open this project in either agent. Example requests:

> Use horizon to finish this refactor. Run the relevant tests and have a fresh
> reviewer check the requested behavior. Stop after five increments or 30 minutes.

> Use Horizon state mode for this parser fix. Give each fresh worker one saved
> increment, preserve its real host handle, and require independent review.

> Refine what we learned from this task. Propose the smallest reusable change
> and show me the evidence and diff.

The skill locates its own helper and selects the correct runtime. To inspect
the setup yourself, run from this checkout:

```bash
python3 .agents/skills/horizon/scripts/harness.py --runtime codex doctor
python3 .claude/skills/horizon/scripts/harness.py --runtime claude doctor
```

For another project, pass `--project "/absolute/project/path"` before the
subcommand. The skill's location determines the implementation, while the
working project determines state. See the [CLI reference](docs/harness-cli.md)
for conversation and state-mode lifecycle commands, packet/update schemas,
proposal JSON, recall, and undo. State mode is explicit; goals created without
`--execution state` keep the existing conversation behavior.

## Optional personal installation

Keep the whole source checkout at a stable location. Skill entrypoints and
references are relative symlinks into its shared implementation; copying just a
skill folder is insufficient. To use these versions across projects, link the
appropriate skill folders into the agent's personal skill directory:

```bash
REPO="/absolute/path/to/this/checkout"
mkdir -p "$HOME/.agents/skills" "$HOME/.claude/skills"
ln -s "$REPO/.agents/skills/horizon" "$HOME/.agents/skills/horizon"
ln -s "$REPO/.agents/skills/refine" "$HOME/.agents/skills/refine"
ln -s "$REPO/.claude/skills/horizon" "$HOME/.claude/skills/horizon"
ln -s "$REPO/.claude/skills/refine" "$HOME/.claude/skills/refine"
```

Run only the pair for the agent you want. If those names already exist, inspect
the existing installation first; these commands do not intentionally replace it.
Avoid maintaining conflicting personal and project versions of the same skill.
Codex supports skill symlinks and discovers `.agents/skills`; Claude uses
`.claude/skills` and gives personal skills precedence over matching project
skills. [Codex skills](https://learn.chatgpt.com/docs/build-skills),
[Claude Code skills](https://code.claude.com/docs/en/skills).

`CODEX_HOME` changes Codex's global configuration and refinement ledger path;
the personal skill directory remains `~/.agents/skills`. Existing imports through
`.claude/harness/bin` remain compatible links to the shared modules. Active goals
from the old implementation must rerun verification before completing.

## What changed

- Skill entrypoints work from unrelated projects and paths containing spaces.
- Completion checks current file content and the configured gate. A stale pass
  cannot complete after an artifact or gate changes.
- Command-plus-review goals require a recorded passing review of the same
  checkpoint; command success alone leaves the goal active.
- Mutable goal and undo records do not themselves trigger another gate run.
- Refinement protects both runtimes' control files and the shared implementation.
- History shows later helpful, ineffective, or harmful observations for each
  lesson, while preserving its undo record.
- Runtime dispatch stays in concise skill entrypoints; shared state protocol,
  schemas, recovery rules, and limits live in the linked CLI reference.
- Horizon v2 goals save the request verbatim with a quoted, numbered requirement
  list (`start --gate-path`), and the reviewer answers each line.
- `review` refuses a pass that the reviewer's own reply does not support: a FAIL
  or UNVERIFIED line, a FAIL headline, a missing requirement line, or no echo
  of the reviewed checkpoint.
- `check` returns a neutral reviewer brief filled from the goal's files. `status`
  lists goals as short rows and prints each goal's deadline.

## Practical boundaries

Checkpoints make resumption possible; an agent still needs to read and continue
the goal. They do not wake an app. Scheduled continuation requires an explicit
user request and the host's scheduling feature. Claude `/loop` belongs to a live
session; Desktop scheduling and cloud routines have different environments.
[Claude scheduling](https://code.claude.com/docs/en/scheduled-tasks).

Verification covers the chosen watched files and supplied criterion. External
state needs a deliberate rerun. Review handles and approval flags record what the
calling agent asserts; they are not identity verification or a security sandbox.
Codex notes require explicit recall. These skills do not reproduce Prime Agent's
persistent Python RLM or establish a measured productivity improvement.

The inspiration is Prime Intellect's trajectory-driven refinement of prompts,
skills, subagents, and memory. [Prime Agent](https://www.primeintellect.ai/blog/prime-agent).
The [evaluation protocol](docs/evaluation.md) separates correctness checks from
experiments measuring task success and cost.

## Development

```bash
python3 -m pytest -q
```

Tests require pytest. Runtime execution has no third-party dependencies. Tests
cover state, locks/concurrency, rollback, command execution, content fingerprints,
both runtime entrypoints, and real CLI workflows. The old pasted-Python tests
were replaced with process-level coverage of their behavioral invariants.

- [CLI and schemas](docs/harness-cli.md)
- [Verification results](docs/runtime-separation-results.md)
- [Evaluation protocol](docs/evaluation.md)
- [Implementation plan](docs/superpowers/plans/2026-09-04-runtime-separation.md)
- [Historical design and findings](docs/history-before-runtime-split.md)
