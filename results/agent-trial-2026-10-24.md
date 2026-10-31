# ctx-agents 2026-10-24: map-packed small window vs blind agent

Task (x6, Rust ruff / TS vscode / Python mypy): add required last param `trace`, every caller passes false.
Pass = system check: compiler/type-checker clean (TS: no new errors vs baseline), def has `trace` with no default, use count unchanged.

Arms:
- B  = map-pack: language-server site list + code around each site, one prompt, 32k window, no tools, compile errors fed back, max 3 tries.
- As = opencode agent with tools, 32k window, task text + check command.
- A  = same agent, biggest window that fits (qwen27 262k, laguna 98k).

| arm | model | pass | biggest prompt | tokens read (6 tasks) | prefill computed | generated | time |
|---|---|---|---|---|---|---|---|
| B  | qwen27 | 6/6 | 4.3k | 26k | 18k | 3.9k | 3.3 min |
| B  | laguna | 6/6 | 4.4k | 25k | 21k | 5.9k | 2.2 min |
| As | qwen27 | 6/6 | 16.9k | 660k | 187k | 30.0k | 21.9 min |
| As | laguna | 6/6 | 21.6k | 1,385k | 178k | 35.6k | 11.9 min |
| A  | qwen27 | 5/6 | 22.1k | 1,229k | 55k | 14.6k | 10.9 min |
| A  | laguna | 5/6 | 68.6k | 2,108k | 117k | 17.9k | 7.6 min |

Failures (both real model errors, both found by the check, not by the agent):
- A qwen27 py-use_fine_grained_cache: re-indented an 8-line block in mypy/build.py load_graph out of its loop (logic change; mypy flags None + list).
- A laguna py-with_normalized_var_args: indented one line in mypy/plugins/functools.py:166 (code unreachable; still valid Python).

Meaning:
- Small window + map = as good as big window blind (12/12 vs 10/12), with 25-80x fewer tokens read, ~9x less prefill compute, 4-8x less generation, 3-6x faster.
- Big window gave the blind agent nothing: peak use 22k of qwen 262k; laguna once filled 68k reading files (TS task).
- Every B task fit in under 4.5k tokens.

Limits: tasks are mechanical (script oracle solves all 6 from the map alone). Not yet proven for new logic / bug fixes. One run per cell.
Setup bugs fixed before v2 (v1 archived in work/.../runs/_v1): pack window cut a 15-line call; graphify-out inside repos crashed opencode grep; mypy --pretty crash hid errors.
Raw: per-run row.json, diff.patch, logs in this folder.
