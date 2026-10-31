# ctxc: context compiler (2026-10-24)

Goal: a model with a small window works on a 1M-line repo without going blind and without missing a caller.
Proof behind every choice: `~/spaces/bench/results/ctx-agents/RESULT.md` + `~/spaces/bench/results/ctx-trial/RESULT*.txt` + `~/spaces/bench/results/ctx-pi/RESULT.md`.

## Flow

find → sites → (model edits) → check → ship

| module | one job | why (proof) |
|---|---|---|
| `config.py` | read `[ctxc]` from spaces.toml + per-repo overrides; pick the language of a repo | switches, not hardcodes ("anything we are building on") |
| `lsp.py` | talk to one language server: start, references, stop | language server found every real caller in Rust/TS/Python (trials 1-3) |
| `find.py` | the map: where is a name defined (file:line + signature), cheap | the model needs a start point without grepping the repo |
| `sites.py` | every use of one symbol: language-server references + name-search text mentions (comments/docs) | LSP = callers, name search = comments/docs (trial 1-3) |
| `pack.py` | numbered code around each site, whole statements, within a token budget | 2-4k tokens per task, 12/12 pass (agent trial) |
| `check.py` | compile/type check; only NEW errors vs the baseline at HEAD count | system-run check caught both blind-agent bugs |
| `gate.py` | nothing missed: every watched site touched or acknowledged; indent-only changes flagged | both blind failures were re-indents that still parsed |
| `state.py` | per-repo watch list + last check result (keyed by diff hash), outside the repo | generated files inside a repo broke the agent's grep (v1) |
| `ship.py` | commit to a work branch `ctx/<date>-<slug>`, push only if allowed; never main | owner: pass → push to a branch, owner merges |
| `loop.py` | no-agent mode: pack → model → SEARCH/REPLACE → check → feed errors back, 3 tries | arm B of the trial |
| `cli.py` | `ctxc find/sites/check/ship/run/baseline`, `--json` for tools | Pi + pipeline call the same CLI |

Pi extension (`~/.pi/extensions/ctxc/index.ts`): tools `ctx_find`, `ctx_sites`, `ctx_check`, `ctx_ship` that shell out to `ctxc --json`.
Pipeline: engine `local` runs `pi-ctx -p`, so a pipeline piece on the local engine gets these tools. Pi trial: 18/18 with, 15/18 without (bench/results/ctx-pi).

## Language tiers

| tier | callers from | languages now |
|---|---|---|
| 1 | language server + compiler | Rust (rust-analyzer, cargo check), TS/JS (tsc LSP, tsc), Python (pyright, mypy or pyright) |
| 2 | name search + compiler | Kotlin (no language server on the box; gradle compile), anything with a compile command |
| 3 | name search only | everything else: check passes on gate only, says so in its output |

A repo can override any setting: `[ctxc.repos."<abs path>"]` in spaces.toml (never a file inside the repo).

## Pass rule (system, never the model's word)

pass = no new compile/type errors vs baseline AND (tests pass, if a test command is set and `--tests`) AND no indent-only edits
AND every watched site either changed or acknowledged ("checked, no change").

## Decisions log (made autonomously, full-clear run 2026-10-24)

1. No daemon: LSP answers took 0.1-6 s cold in the trials, so each call starts the server. Revisit if a repo needs >30 s.
2. Baseline per HEAD sha, cached in `~/.cache/ctxc/`, computed in a detached git worktree of HEAD inside the cache (node_modules/.venv symlinked, Rust gets its own target dir). Never stash: other agents write into live repos meanwhile.
3. Errors compared without line/column numbers (lines move when code is added).
4. Push default = on when the repo has a remote (owner decision); trial clones of public repos set push = false.
5. Kotlin is tier 2 until a Kotlin language server is installed and trialled.
6. Pi tool output capped at ~12k chars; the full text goes to a file the tool names.
