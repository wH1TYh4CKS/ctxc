# ctxc — context compiler for AI coding on large codebases

A small AI model has a small context window, but real codebases are millions of lines. ctxc finds every place a change touches, hands the model exactly that code, checks that nothing was missed, compiles and tests, and only then commits to a work branch. The model never reports "done" on its own word.

## Results

**Choosing the method (before building).** The question: what finds every caller of a function? Tested on ruff (843k lines, Rust), VS Code (3.9M lines, TypeScript) and mypy (Python), using the compiler or test suite as the answer key. Language-server lookups missed **0** real callers in all three. A code-graph (AST) approach found only 16–67%. So ctxc uses language servers. Details in [results/](results/).

**Agent trial.** 6 change-every-caller tasks across the three repos, local 27B models:

| Setup | Correct | Tokens read |
|---|---|---|
| ctxc: packed context, 32k window, no tools | 12/12 | 25–26k |
| Agent with tools, biggest window that fits | 10/12 | 1.2–2.1M |

- 25–80× fewer tokens read, 3–6× faster. Both failures were real logic errors the agent missed and ctxc's check catches.
- Limits: these tasks are mechanical; one run per cell. [results/agent-trial-2026-10-24.md](results/agent-trial-2026-10-24.md)

## Built with

Python, Language Server Protocol (rust-analyzer, TypeScript, pyright), Git. Tests: `python3 -m pytest -q tests/`.

## Usage


## Use it

```bash
cd <any git repo>
pi-ctx                      # Pi + ctxc tools, lean prompt; pick the local model with /serve as usual
pi-ctx -p "rename X to Y everywhere"
```

Plain `pi` has the same tools too (global extension in `~/.pi/agent/settings.json`), with its full 10.3k-token prompt.
`pi-ctx` drops the skills list and `~/AGENTS.md` (2.2k tokens) and keeps the AGENTS.md/CLAUDE.md of every dir from the git root down to cwd.
The spaces pipeline's `local` engine runs `pi-ctx`.
Proof: `~/spaces/bench/results/ctx-pi/RESULT.md` (local 32k models: 18/18 with the tools, 15/18 without).

The model gets four tools:

| tool | what it does |
|---|---|
| `ctx_find <name>` | where a name is defined |
| `ctx_sites <name> [at]` | every use with code: definition, imports, callers (language server), comments/docs |
| `ctx_check [ack] [tests]` | new compile errors vs HEAD + every watched site changed or acked + no stray re-indents; a function whose signature changed is watched automatically |
| `ctx_ship <message>` | commit on `ctx/<date>-<slug>`, push if the repo allows; refuses unless the last check passed on these exact files |

Same commands by hand: `ctxc find|sites|check|ship|baseline|run` (`--json` for scripts).
`ctxc run "<task>" --symbol name[@file:line]` = no-agent mode: one prompt, edit blocks, check, 3 tries.

## Settings

`[ctxc]` in `~/spaces/spaces.toml`: language table (`[ctxc.langs.*]`: server, check command, error pattern) and
per-repo overrides (`[ctxc.repos."<abs path>"]`: `push`, `check`, `check_env`, `check_skip`, `error`, `tsconfig`, `test`).
`check_skip` = path prefixes the project never type-checks (e.g. `tests/` in attrs/pydantic).
State and baselines live in `~/.cache/ctxc/<repo>/`, never inside the repo.

Languages: Rust, TS/JS, Python = tier 1 (language server). Kotlin = tier 2 (name search + gradle compile, compile untested).
Anything else with a check command = tier 2; no check command = tier 3 (gate only, says so).

## Tests

`python3 -m pytest -q tests/`
