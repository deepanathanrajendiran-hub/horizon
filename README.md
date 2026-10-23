# Horizon: long-horizon goal tracking for Claude Code and Codex

Horizon is a skill for work that must survive across turns or sessions, such as
"keep working until the tests pass". It keeps the objective, evidence, budget and
next action on disk, so a later session can pick up exactly where the last one
stopped.

- **Bounded goal.** Each goal saves the request verbatim, a numbered requirement
  list, and a turn and time budget.
- **Verification gate.** A command (such as the test suite) or a review criterion
  decides whether the work is done. A stale pass cannot complete a goal after an
  artifact or the gate changes.
- **Independent review.** When a goal asks for review, a fresh reviewer answers each
  requirement line. A pass the reviewer's own reply doesn't support is refused.
- **Checkpoints.** Every increment records what was done, the gate result and the
  next action.
- **State mode (experimental).** Instead of carrying the conversation forward, each
  increment goes to a fresh worker that gets only a saved state packet.

**Status:** Horizon has no measured improvement yet. State mode is experimental.

Claude Code and Codex have separate skill instructions and state, backed by one
shared Python implementation.

| | Codex | Claude Code |
| --- | --- | --- |
| Skill in this checkout | [.agents/skills/horizon](.agents/skills/horizon) | [.claude/skills/horizon](.claude/skills/horizon) |
| Invocation | `$horizon` | `/horizon` |
| Project state | `.codex/harness` | `.claude/harness` |

The [CLI](harness/cli.py) uses only Python's standard library. It needs Python 3.10+
on a POSIX system. No separate model API or service is required.

## Use it

Open this project in either agent. Example requests:

> Use horizon to finish this refactor. Run the relevant tests and have a fresh
> reviewer check the requested behavior. Stop after five increments or 30 minutes.

> Use Horizon state mode for this parser fix. Give each fresh worker one saved
> increment, preserve its real host handle, and require independent review.

The skill finds its own helper and selects the correct runtime. To check the setup
yourself, run from this checkout:

```bash
python3 .agents/skills/horizon/scripts/harness.py --runtime codex doctor
python3 .claude/skills/horizon/scripts/harness.py --runtime claude doctor
```

For another project, pass `--project "/absolute/project/path"` before the
subcommand. The skill's location decides the implementation; the working project
decides where state lives. The [CLI reference](docs/harness-cli.md) covers the goal
lifecycle, requirements and review, and the state-mode commands and schemas.
State mode is explicit: goals created without `--execution state` work in the
conversation.

## Install for all your projects

Keep the whole checkout at a stable location. The skill's entrypoint and reference
are relative symlinks into the shared implementation, so copying only the skill
folder won't work. Link the skill into your agent's personal skill directory:

```bash
REPO="/absolute/path/to/this/checkout"
mkdir -p "$HOME/.agents/skills" "$HOME/.claude/skills"
ln -s "$REPO/.agents/skills/horizon" "$HOME/.agents/skills/horizon"   # Codex
ln -s "$REPO/.claude/skills/horizon" "$HOME/.claude/skills/horizon"   # Claude Code
```

Run only the line for the agent you use. If `horizon` already exists there, inspect
it first; these commands don't replace it. Claude Code gives personal skills
precedence over project skills with the same name, so avoid keeping two different
versions. [Codex skills](https://learn.chatgpt.com/docs/build-skills),
[Claude Code skills](https://code.claude.com/docs/en/skills).

## Practical boundaries

- **Checkpoints make resuming possible; they don't wake anything up.** An agent
  still has to read the goal and continue it. Scheduled continuation needs an
  explicit request and the host's scheduling feature.
  [Claude scheduling](https://code.claude.com/docs/en/scheduled-tasks).
- **Verification covers only the watched files and the given criterion.** External
  state needs a deliberate rerun (`check --force`).
- **Review handles and approval flags record what the calling agent asserts.** They
  are not identity verification or a security sandbox.
- **State mode can't tell whether a worker is still running.** If a session is
  killed while a worker holds an attempt, the next session must establish that the
  worker stopped before running `step-recover`.

## Development

```bash
python3 -m pytest -q
```

Tests need pytest; runtime execution has no third-party dependencies. They cover
goal state, locks and concurrency, content fingerprints, gates, review parsing,
state-mode steps, both runtime entrypoints, and real CLI workflows.

- [CLI and schemas](docs/harness-cli.md)
- [Runtime separation results](docs/runtime-separation-results.md)
- Design history: [specs](docs/superpowers/specs) and [plans](docs/superpowers/plans)

## Provenance

Horizon was split, with its filtered commit history, from
[continual-harness](https://github.com/deepanathanrajendiran-hub/continual-harness)
(archived), which held this skill and a companion skill together. That repository
keeps the evaluation records.
