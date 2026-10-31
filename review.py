"""Reviewer: a second model judges whether the change does what the task asks (the logic check that compile + tests
cannot give: 2026-10-25 gh trial had 4 ctxc passes that were wrong fixes). Input = task + diff + every changed
function shown whole after the change. Output = VERDICT: PASS|FAIL with reasons."""
import re, subprocess
import pack, state
from config import Repo

PROMPT = """You are a senior engineer reviewing a change before it ships. Judge LOGIC, not style.

TASK:
{task}

DIFF (working tree vs HEAD):
{diff}

CHANGED FUNCTIONS, whole, after the change:
{funcs}

Do this:
1. List every requirement the task states or clearly implies (one line each).
2. For each: MET or NOT MET, citing the diff line. Also check for leftovers (old behaviour still reachable),
   wrong edge cases, and tests that were changed to fit the code instead of the task.
3. Last line exactly `VERDICT: PASS` (every requirement met) or `VERDICT: FAIL: <the main reason>`."""
VERDICT = re.compile(r"VERDICT:\s*(PASS|FAIL)\b:?\s*(.*)", re.I)
MAX_FUNC_LINES = 200


def diff_text(repo: Repo) -> str:
    subprocess.run(["git", "add", "-N", "."], cwd=repo.root, capture_output=True)   # new files show in the diff
    return subprocess.run(["git", "diff", "HEAD"], cwd=repo.root, capture_output=True, text=True).stdout


def changed_functions(repo: Repo, diff: str) -> str:
    """Whole function around each changed hunk (new side), each function once."""
    out, seen = [], set()
    for f in state.changed_files(repo):
        p = repo.root / f
        if not p.is_file(): continue
        rows = p.read_text(errors="replace").split("\n")
        for m in re.finditer(rf"^\+\+\+ b/{re.escape(f)}\n(.*?)(?=^diff --git|\Z)", diff, re.S | re.M):
            for start in re.findall(r"^@@ -\d+(?:,\d+)? \+(\d+)", m.group(1), re.M):
                line = min(int(start) + 3, len(rows))
                d = pack.enclosing_def(rows, line) or (line if pack.TEST_DEF.match(rows[line - 1]) else None)
                if not d or (f, d) in seen: continue
                seen.add((f, d)); end = min(pack.body_end(rows, d), d + MAX_FUNC_LINES)
                out += [f"=== {f} (lines {d}-{end}) ==="] + [f"{i}| {rows[i - 1]}" for i in range(d, end + 1)]
    return "\n".join(out) or "(no enclosing functions found; see the diff)"


def ask(text: str, url: str = "", model: str = "trial", cmd: list | None = None, max_tokens: int = 16000) -> str:
    """url = OpenAI-style server; cmd = any CLI that takes the prompt as its last argument (e.g. opencode run)."""
    if cmd:
        return subprocess.run([*cmd, text], capture_output=True, text=True, timeout=1800).stdout
    from loop import chat
    r = chat(url, model, [{"role": "user", "content": text}], max_tokens)
    return r["choices"][0]["message"]["content"] or ""


def review(repo: Repo, task: str, url: str = "", model: str = "trial", cmd: list | None = None) -> dict:
    diff = diff_text(repo)
    if not diff.strip(): return {"pass": False, "reason": "no changes to review", "reply": ""}
    reply = ask(PROMPT.format(task=task, diff=diff, funcs=changed_functions(repo, diff)), url, model, cmd)
    m = VERDICT.findall(reply)
    if not m: return {"pass": False, "reason": "reviewer gave no verdict", "reply": reply}
    v, why = m[-1]
    return {"pass": v.upper() == "PASS", "reason": why.strip(), "reply": reply}
